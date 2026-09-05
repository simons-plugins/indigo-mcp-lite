"""Lamplighter zone tools (PRD 5.12) — one family, three mechanisms
that must never be confused with one another.

Lamplighter (``com.simons-plugins.indigo-lamplighter``) is a sibling
plugin we own. What a caller thinks of as "a zone" is spread across
three places, and writing to the wrong one is the failure this module
exists to prevent — a call that reports success while nothing about
the running plugin changed:

- **The config file** is the only home of a zone's *definition*:
  lights, presence devices, hold, lux gate, override timing, periods
  and levels. It lives at
  ``Preferences/Plugins/com.simons-plugins.indigo-lamplighter/lamplighter.json``
  — note this is NOT the ``Preferences/<plugin id>/config/`` layout
  Auto Lights uses; the two are one directory level apart and a
  hardcoded guess at the wrong one reads as "not installed".
  ``lamplighter_list_zones`` / ``lamplighter_get_zone`` read it and
  ``lamplighter_update_zone`` writes it.

- **The plugin's own Actions.xml actions** back everything that is
  *runtime* state: overrides, locks, a zone's enable, a reconcile
  pass. None of those are config-file fields — writing them into the
  JSON would be silently ignored forever. ``reset_override``,
  ``lock_zone``, ``set_zone_enabled`` and ``reconcile_now`` are called
  through ``indigo.server.getPlugin(...).executeAction(...)``, the
  same cross-plugin mechanism ``restart_plugin`` uses.

  Confirmed against Lamplighter's live ``Actions.xml`` and
  ``plugin.py``: every zone-taking action reads one prop, ``zone_name``
  (never a numeric id — Lamplighter has none), and every one of them
  routes through ``Plugin._action_zone``, which **logs a warning and
  returns** for a name it does not know. So an unmatched zone name is
  a silent no-op, and validating the name against the config file
  before dispatch is the only thing standing between "no such zone"
  and a quiet success. ``reset_override``'s picker additionally has an
  "All zones" entry whose value is the literal ``"__all__"``
  (``plugin.ALL_ZONES``), reproduced here as ``ALL_ZONES``.

- **The live zone/controller devices** carry everything a config file
  cannot: which state the machine is in, why (``explain``), whether
  presence is active, what the lux reading is, what is overridden and
  until when, and the daily counters. ``lamplighter_zone`` devices are
  matched back to their zone by Lamplighter's own ``zone_name`` prop,
  never by device name — Lamplighter itself does the same, because a
  user may rename a device freely. That prop is read through
  ``ownerProps``/``globalProps`` and never through ``pluginProps``,
  which Indigo scopes to the calling plugin and which is therefore
  empty from this process; see ``_device_zone_name`` for the live
  evidence.

Two Lamplighter actions RETURN a value rather than logging one, and
both are reachable only with ``waitUntilDone=True``:

- ``validate_config`` (hidden) takes the whole document as JSON text
  in a ``config_json`` prop and answers
  ``{"ok": true, "zones": [...], "enabled": [...]}`` or
  ``{"ok": false, "path": "zones/0/hold_seconds", "message": "..."}``.
  This module never re-implements that check. lite is stdlib-only
  (workspace ADR-0003) and in another process, so it cannot import
  ``lamplighter.config.load_config``; a second implementation of "is
  this valid" is a second opinion, and the wrong one is always the one
  the author is holding.
- ``explain_zone`` answers ``{"ok", "zone", "at", "explain",
  "desired": [{"device", "name", "level"}, ...]}`` or
  ``{"ok": false, "message": ...}``. With an ``at`` it is a dry run
  that decides nothing and writes nothing.

Unlike Auto Lights, Lamplighter needs **no restart** after a config
write: its worker stats the file every ``CONFIG_CHECK_SECONDS`` (5 s
in its plugin.py) and hot-reloads, carrying overrides and presence
across. ``lamplighter_update_zone`` therefore polls for evidence of
that reload rather than restarting anything — and reports what it saw
rather than asserting a reload it cannot observe (see
``_wait_for_reload``).
"""

import copy
import json
import os
import time
from datetime import datetime, timezone

from tools.lookup import _reject_unknown_args
from tools.zwave import _json_safe

LAMPLIGHTER_PLUGIN_ID = "com.simons-plugins.indigo-lamplighter"

#: Lamplighter's own value for "every zone" — ``plugin.ALL_ZONES``, and
#: the ``defaultValue`` of ``reset_override``'s picker in Actions.xml.
#: Sent verbatim; ``_action_zone(allow_all=True)`` matches on it (and on
#: the empty string, which this module never sends because an empty
#: zone name is indistinguishable from a caller mistake).
ALL_ZONES = "__all__"

ZONE_DEVICE_TYPE = "lamplighter_zone"
CONTROLLER_DEVICE_TYPE = "lamplighter_controller"

#: PRD 5.11. Deliberately NOT the Auto Lights shape
#: (``Preferences/<plugin id>/config/<file>``) — Lamplighter keeps its
#: file one level differently, under ``Preferences/Plugins/``.
_CONFIG_SUBPATH = os.path.join(
    "Preferences", "Plugins", LAMPLIGHTER_PLUGIN_ID, "lamplighter.json"
)

#: How long to watch for evidence that Lamplighter reloaded the file
#: after a write, and how often to look. The plugin stats the file
#: every 5 s (its ``CONFIG_CHECK_SECONDS``), so a 10 s window covers
#: one missed tick without making a failed write wait forever.
_RELOAD_POLL_SECONDS = 10.0
_RELOAD_POLL_INTERVAL = 0.5

#: How long to give the lamplighter_controller device to report back
#: the on/off a plugin-wide enable just commanded. Short, because the
#: only thing being waited on is one local state publish -- but not
#: zero: see ``_confirm_controller_state``.
_ENABLE_CONFIRM_SECONDS = 2.0
_ENABLE_CONFIRM_INTERVAL = 0.5

#: The zone-device states worth putting in a list row. The full state
#: dict goes out from ``lamplighter_get_zone`` instead.
_ZONE_SLIM_STATES = (
    "state", "explain", "presence_active", "period",
    "override_device", "override_expires",
    "evaluations_today", "writes_today", "overrides_today",
)

#: ``config_loaded_at`` (ISO local timestamp of the last SUCCESSFUL
#: load/reload, untouched by a rejected edit) and ``config_zone_count``
#: are the two states the reload check leans on. Both are read with
#: ``.get`` and degrade to ``None`` on a Lamplighter that has not
#: shipped them yet, in which case the check falls back to the
#: longer-standing ``zones`` count and to weak evidence.
_CONTROLLER_STATES = (
    "config_status", "config_loaded_at", "config_zone_count",
    "zones", "zones_enabled", "zones_overridden",
    "evaluations_today", "writes_today", "overrides_today",
)


# -- path / read -------------------------------------------------------


def _config_path(indigo_module):
    """Resolve the Lamplighter config path from the live install
    folder — never hardcode the Indigo version in the path."""
    try:
        install_path = indigo_module.server.getInstallFolderPath()
    except Exception as exc:
        raise ValueError(
            f"Indigo install folder path unavailable ({exc}); is the "
            "Indigo server running?"
        ) from exc
    if not install_path:
        raise ValueError(
            "Indigo install folder path unavailable (server returned "
            "no path)"
        )
    return os.path.join(install_path, _CONFIG_SUBPATH)


def _stat(path):
    """Thin wrapper over ``os.stat`` so tests can control exactly the
    two calls the write path makes (initial read, pre-write recheck)
    without needing a real concurrent writer."""
    return os.stat(path)


def _sleep(seconds):
    """Indirection over ``time.sleep`` so the reload poll can be driven
    instantly in tests without a ten-second wait per case."""
    time.sleep(seconds)


def _read_config(indigo_module):
    """Read + parse ``lamplighter.json``.

    Returns ``(config, path, raw_bytes, stat_result)``. Every way this
    can fail short of a genuine parse raises a friendly ``ValueError``
    naming the likely cause rather than degrading to an empty-but-ok
    result (workspace convention, ADR-0002): an unusable precondition
    is a failed call, not "this house has no zones".

    A zones array that is EMPTY is accepted — that is the starter
    document Lamplighter writes on a fresh install
    (``plugin.is_starter_document``), and calling it corrupt would tell
    a new user their file is broken when it is merely unconfigured.
    """
    path = _config_path(indigo_module)
    try:
        stat_result = _stat(path)
    except FileNotFoundError as exc:
        raise ValueError(
            f"Lamplighter config file not found at {path!r}; is the "
            f"Lamplighter plugin ({LAMPLIGHTER_PLUGIN_ID}) installed "
            "and has it run at least once? It writes a starter file on "
            "first startup."
        ) from exc
    except OSError as exc:
        raise ValueError(
            f"Lamplighter config file is not readable ({exc}); check "
            f"permissions on {path!r}"
        ) from exc

    try:
        with open(path, "rb") as fh:
            raw_bytes = fh.read()
    except OSError as exc:
        raise ValueError(
            f"Lamplighter config file could not be read ({exc})"
        ) from exc

    try:
        config = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Lamplighter config file at {path!r} is not valid JSON "
            f"({exc}); it may be mid-write or hand-edited incorrectly. "
            "Retry, or inspect the file directly before trying again."
        ) from exc

    if not isinstance(config, dict) or not isinstance(config.get("zones"), list):
        raise ValueError(
            f"{path!r} does not look like a Lamplighter config file "
            "(missing or malformed 'zones' array); refusing to treat it "
            "as one."
        )

    return config, path, raw_bytes, stat_result


# -- validation ----------------------------------------------------------


def _require_str(args, key):
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _zone_names(config):
    return [
        z.get("name") for z in config.get("zones", [])
        if isinstance(z, dict) and isinstance(z.get("name"), str) and z.get("name")
    ]


def _zone_index(config, zone_name):
    """Index of ``zone_name`` in ``config['zones']``, or None."""
    for i, z in enumerate(config.get("zones", [])):
        if isinstance(z, dict) and z.get("name") == zone_name:
            return i
    return None


def _zone_lookup(config, zone_name):
    """The zone block, or a ValueError naming the known zones.

    Every zone-taking Lamplighter action silently no-ops on a name it
    does not know (``Plugin._action_zone`` logs and returns), so this
    is the check that turns "no such zone" into a failed call instead
    of a success that changed nothing.
    """
    index = _zone_index(config, zone_name)
    if index is None:
        raise ValueError(
            f"no Lamplighter zone named {zone_name!r}; known zones: "
            f"{sorted(_zone_names(config))}"
        )
    return config["zones"][index]


def _merge_patch(target, patch):
    """Apply an RFC 7386 JSON merge patch to ``target`` in place.

    ``null`` removes a key; an object merges recursively; anything else
    (including a LIST) replaces wholesale — so patching ``lights`` or
    ``periods`` replaces the whole array rather than appending to it,
    which is the one merge-patch behaviour a caller regularly expects
    to be otherwise.
    """
    if not isinstance(patch, dict):
        return patch
    if not isinstance(target, dict):
        target = {}
    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        else:
            target[key] = _merge_patch(target.get(key), value)
    return target


# -- live devices ---------------------------------------------------------


def _device_zone_name(dev):
    """The zone a ``lamplighter_zone`` device belongs to.

    Read from the OWNER plugin's props. NEVER ``pluginProps``: Indigo
    scopes that to the CALLING plugin -- the IOM base class defines it
    as "the name/value pairs defined by YOUR plugin for the device" --
    so from lite's process it is EMPTY for a device Lamplighter
    created. The failure it produces is silent and total: every zone
    device reads as unnamed, so every configured zone reads as
    device-less, ``lamplighter_get_zone`` reports ``skipped_device``,
    and ``lamplighter_update_zone`` can never find reload evidence.
    Confirmed live on jarvis 2026-09-05, running in-process under the
    Indigo plugin host against the real ``indigo`` module: the Hallway
    zone device came back with ``zone_name: ''`` and was reported as an
    orphan.

    ``ownerProps`` (API 1.20) is the right one -- "the name/value pairs
    defined by the plugin that created the device ... a shortcut into
    the owner plugin's globalProps". ``globalProps[<plugin id>]`` is
    the long way round to the same dictionary, readable by anyone, and
    covers both an Indigo older than API 1.20 and an ``ownerProps``
    that answers nothing. ``tools/lookup.py`` reads ``globalProps`` for
    exactly this reason.

    Only a genuine non-empty ``str`` counts. An ``indigo.Dict`` holds
    strings, so anything else here is a broken read rather than a zone
    name, and coercing it with ``str()`` would invent one that matches
    no zone -- which is how a test double silently passes while the
    live join is broken.
    """
    sources = [getattr(dev, "ownerProps", None)]
    global_props = getattr(dev, "globalProps", None)
    if global_props is not None and hasattr(global_props, "get"):
        sources.append(global_props.get(LAMPLIGHTER_PLUGIN_ID))
    for props in sources:
        if props is None or not hasattr(props, "get"):
            continue
        value = props.get("zone_name")
        if isinstance(value, str) and value:
            return value
    return ""


def _states_of(dev):
    states = getattr(dev, "states", None)
    if states is None or not hasattr(states, "get"):
        return {}
    return states


def _serialize_zone_device(dev, keys=None):
    """One ``lamplighter_zone`` device. ``keys=None`` emits every state
    it publishes; a tuple emits just those, flattened onto the row."""
    states = _states_of(dev)
    out = {
        "id": _json_safe(getattr(dev, "id", None)),
        "name": _json_safe(getattr(dev, "name", None)),
        # The relay's on/off IS the zone's enable (Lamplighter's
        # Devices.xml says so), so it is reported under that name
        # rather than as an opaque onState.
        "enabled": _json_safe(getattr(dev, "onState", None)),
        "zone_name": _device_zone_name(dev),
    }
    if keys is None:
        out["states"] = {
            str(k): _json_safe(v) for k, v in states.items()
        } if hasattr(states, "items") else {}
    else:
        for key in keys:
            out[key] = _json_safe(states.get(key))
    return out


def _serialize_controller(dev):
    states = _states_of(dev)
    out = {
        "id": _json_safe(getattr(dev, "id", None)),
        "name": _json_safe(getattr(dev, "name", None)),
        # The global enable — "all automation off" is this one relay.
        "enabled": _json_safe(getattr(dev, "onState", None)),
    }
    for key in _CONTROLLER_STATES:
        out[key] = _json_safe(states.get(key))
    return out


def _sweep_devices(indigo_module):
    """Index Lamplighter's live devices by zone name.

    Returns a dict with ``zone_devices`` (name -> device object),
    ``unnamed`` (zone devices carrying no ``zone_name`` prop),
    ``duplicates`` (name -> ids, when two devices claim one zone),
    ``controllers`` (device objects) and ``attr_read_errors``.

    A failure to READ THE DEVICE LIST AT ALL raises, rather than
    returning an empty index: an empty index would report every
    configured zone as having no device and every zone's live state as
    unknown-but-fine, which is exactly the confident wrong answer
    ADR-0002 is about. A failure on ONE device is counted into
    ``attr_read_errors`` and surfaced in the payload instead.
    """
    try:
        devices = list(indigo_module.devices)
    except Exception as exc:
        raise ValueError(
            f"the Indigo device list could not be read ({exc}); "
            "Lamplighter's live zone state is therefore unknown and this "
            "call was NOT completed. Retry, or check the Indigo server."
        ) from exc

    zone_devices = {}
    unnamed = []
    duplicates = {}
    controllers = []
    attr_read_errors = 0
    for dev in devices:
        try:
            type_id = str(getattr(dev, "deviceTypeId", "") or "")
            if type_id == CONTROLLER_DEVICE_TYPE:
                controllers.append(dev)
                continue
            if type_id != ZONE_DEVICE_TYPE:
                continue
            zone_name = _device_zone_name(dev)
            if not zone_name:
                unnamed.append(dev)
                continue
            if zone_name in zone_devices:
                duplicates.setdefault(zone_name, [
                    _json_safe(getattr(zone_devices[zone_name], "id", None))
                ]).append(_json_safe(getattr(dev, "id", None)))
                continue
            zone_devices[zone_name] = dev
        except Exception:
            # Counted rather than swallowed: a systemically broken IOM
            # must not read as "Lamplighter has no devices".
            attr_read_errors += 1

    return {
        "zone_devices": zone_devices,
        "unnamed": unnamed,
        "duplicates": duplicates,
        "controllers": controllers,
        "attr_read_errors": attr_read_errors,
    }


def _controller_payload(sweep):
    """``(controller_dict_or_None, skipped_reason_or_None)``.

    Zero controllers and two controllers are both reported as "no
    controller, and here is why" rather than by guessing at one —
    Lamplighter keeps exactly one, so either count means something a
    caller should see.
    """
    controllers = sweep["controllers"]
    if not controllers:
        return None, (
            "no lamplighter_controller device exists, so the plugin-wide "
            "enable and the summed counters could not be read"
        )
    if len(controllers) > 1:
        ids = [_json_safe(getattr(d, "id", None)) for d in controllers]
        return None, (
            f"{len(controllers)} lamplighter_controller devices exist "
            f"(ids {ids}); Lamplighter keeps exactly one, so none is "
            "reported rather than guessing which is live"
        )
    return _serialize_controller(controllers[0]), None


# -- cross-plugin actions -------------------------------------------------


def _require_lamplighter_plugin(indigo_module, what):
    """Lamplighter's plugin handle, or a ValueError naming BOTH causes
    and BOTH remedies.

    ``indigo.server.getPlugin()`` never raises -- it returns a handle
    for a plugin id that names nothing at all, and that handle's
    ``isEnabled()`` is simply false. So a false ``isEnabled()`` means
    EITHER "not installed" OR "installed but disabled/stopped", and
    nothing available here can tell the two apart. The old wording
    picked one ("installed but not enabled ... Enable it in Indigo"),
    which sends somebody who has never installed Lamplighter to a
    Plugins menu that does not list it. Both are named instead.

    ``what`` is the thing that did not happen, capitalised, so the
    sentence reads "Action 'lock_zone' was NOT performed."
    """
    try:
        plugin = indigo_module.server.getPlugin(LAMPLIGHTER_PLUGIN_ID)
    except Exception as exc:
        # Documented never to happen. Kept because a friendly error
        # costs nothing, while a bare AttributeError out of a future
        # or mocked IOM costs a confusing bug report.
        raise ValueError(
            f"Lamplighter ({LAMPLIGHTER_PLUGIN_ID}) could not be looked "
            f"up ({exc}); {what} was NOT performed."
        ) from exc

    if not plugin.isEnabled():
        raise ValueError(
            f"Lamplighter ({LAMPLIGHTER_PLUGIN_ID}) is unavailable: it is "
            "either NOT INSTALLED at all, or installed but not "
            "enabled/running. Indigo's getPlugin() returns a handle "
            "either way and never raises, so these two cannot be told "
            f"apart from here. {what} was NOT performed. If Lamplighter "
            "is listed in Indigo's Plugins menu, enable it there; if it "
            "is not listed, install it first."
        )
    return plugin


def _execute_lamplighter_action(indigo_module, action_id, props=None,
                                *, side_effect_free=False):
    """Invoke one of Lamplighter's own ``Actions.xml`` actions and
    return whatever it returned.

    Always ``waitUntilDone=True``, and that is load-bearing twice
    over. Lamplighter's two answering actions (``validate_config``,
    ``explain_zone``) only deliver their return value to a caller that
    waited — with ``waitUntilDone=False`` the value is simply dropped.
    And Indigo's own ``InvalidAction`` guard against a mistyped action
    id only fires when waiting (confirmed live on jarvis, 2026-08-24,
    Indigo 2025.2; with ``waitUntilDone=False`` a bad action id returns
    ``None`` silently). Making this configurable would quietly remove
    both, so it is not.

    The ``isEnabled()`` check is a BETTER-ERROR layer, not a safety
    net: ``executeAction`` on a disabled plugin already raises
    ``PluginDisabled``, but that names neither Lamplighter nor the
    remedy.

    KNOWN GAP, deliberately not closed here: ``isEnabled()`` reflects
    process state, not Lamplighter's own readiness. If its startup
    failed, ``self.engine`` stays ``None`` while Indigo still reports
    the plugin enabled — and ``_action_zone`` returns ``False`` for a
    ``None`` engine, so ``reset_override`` / ``lock_zone`` /
    ``set_zone_enabled`` no-op silently. ``explain_zone`` is NOT
    affected: it answers ``{"ok": false, "message": ...}`` for a
    ``None`` engine, which this module surfaces as a failed call.
    """
    plugin = _require_lamplighter_plugin(
        indigo_module, f"Action {action_id!r}"
    )

    indigo_props = indigo_module.Dict(props or {})
    try:
        return plugin.executeAction(
            action_id, props=indigo_props, waitUntilDone=True
        )
    except Exception as exc:
        if side_effect_free:
            # validate_config and explain_zone decide nothing and write
            # nothing by construction, so "may have partially completed"
            # would be false here. Still a RuntimeError rather than a
            # ValueError: nothing is wrong with the caller's arguments,
            # so this belongs in mcp_handler's back-off bucket and not
            # in its self-correct-and-retry one.
            raise RuntimeError(
                f"Lamplighter action {action_id!r} failed: "
                f"{type(exc).__name__}: {exc}. This action reads only "
                "(it decides nothing and writes nothing), so nothing was "
                "changed and retrying is safe."
            ) from exc
        # executeAction WAS called -- reset_override, lock_zone,
        # set_zone_enabled and reconcile_now all change live state, so
        # the action may have partially or fully completed before this
        # error. RuntimeError (not ValueError/TypeError) routes this to
        # mcp_handler's back-off bucket rather than its
        # self-correct-and-retry one: retrying blindly is the wrong
        # response to a fault after a write.
        raise RuntimeError(
            f"Lamplighter action {action_id!r} WAS DISPATCHED to "
            "executeAction and may have partially or fully completed "
            f"before this error: {type(exc).__name__}: {exc}. Do NOT "
            "blindly retry -- read the zone back with "
            "lamplighter_get_zone and check Lamplighter's own event log "
            "first."
        ) from exc


def _require_verdict(raw, action_id, what):
    """Coerce an action's return value into a dict carrying ``ok``.

    A ``None``/malformed answer is a FAILED call, never a quiet
    fallback: both callers below gate real consequences on ``ok``, and
    treating "no answer" as "not ok" would report an author's file as
    broken when it was never actually checked.
    """
    payload = _json_safe(raw) if raw is not None else None
    if not isinstance(payload, dict) or "ok" not in payload:
        raise ValueError(
            f"Lamplighter's {action_id} action returned no usable answer "
            f"({payload!r}), so {what}. This usually means Lamplighter "
            "raised inside the action -- check its event log."
        )
    return payload


# -- handlers --------------------------------------------------------------


def _list_zones_handler(args, indigo_module):
    """Config zones joined to their live devices, both directions.

    Never returns an empty-but-ok list for a missing or unreadable
    config (``_read_config`` raises first) or for an unreadable device
    list (``_sweep_devices`` raises).
    """
    _reject_unknown_args(args, ())
    config, path, _raw, _stat_result = _read_config(indigo_module)
    sweep = _sweep_devices(indigo_module)

    zones = []
    configured = set()
    skipped_zones = 0
    for z in config.get("zones", []):
        if (not isinstance(z, dict) or not isinstance(z.get("name"), str)
                or not z.get("name")):
            skipped_zones += 1
            continue
        name = z["name"]
        configured.add(name)
        dev = sweep["zone_devices"].get(name)
        zones.append({
            "name": name,
            # Absent means the schema default (true). Reported as the
            # raw value rather than resolved, because resolving
            # defaults here would be a second opinion on the loader.
            "enabled": z.get("enabled"),
            "lights": z.get("lights") or [],
            "presence_devices": z.get("presence_devices") or [],
            "hold_seconds": z.get("hold_seconds"),
            "lux": z.get("lux"),
            "override": z.get("override"),
            "periods": [
                p.get("name") for p in (z.get("periods") or [])
                if isinstance(p, dict)
            ],
            "device": (
                _serialize_zone_device(dev, _ZONE_SLIM_STATES)
                if dev is not None else None
            ),
        })

    orphans = [
        {
            "id": _json_safe(getattr(dev, "id", None)),
            "name": _json_safe(getattr(dev, "name", None)),
            "zone_name": zone_name,
        }
        for zone_name, dev in sweep["zone_devices"].items()
        if zone_name not in configured
    ] + [
        {
            "id": _json_safe(getattr(dev, "id", None)),
            "name": _json_safe(getattr(dev, "name", None)),
            "zone_name": "",
        }
        for dev in sweep["unnamed"]
    ]

    controller, controller_skipped = _controller_payload(sweep)

    out = {
        "zones": zones,
        "total_count": len(zones),
        "zones_without_device": [
            z["name"] for z in zones if z["device"] is None
        ],
        "orphan_zone_devices": orphans,
        "controller": controller,
        "config_path": path,
    }
    if skipped_zones:
        out["skipped_zones"] = skipped_zones
    if controller_skipped:
        out["skipped_controller"] = controller_skipped
    if sweep["duplicates"]:
        out["duplicate_zone_devices"] = sweep["duplicates"]
    if sweep["attr_read_errors"]:
        out["attr_read_errors"] = sweep["attr_read_errors"]
    return out


def _get_zone_handler(args, indigo_module):
    """One zone: its whole config block, its device's every state, and
    the live ``explain`` line."""
    _reject_unknown_args(args, ("zone",))
    zone_name = _require_str(args, "zone")
    config, path, _raw, _stat_result = _read_config(indigo_module)
    zone_obj = _zone_lookup(config, zone_name)
    sweep = _sweep_devices(indigo_module)
    dev = sweep["zone_devices"].get(zone_name)

    device = _serialize_zone_device(dev) if dev is not None else None
    out = {
        "zone": zone_name,
        "config": zone_obj,
        "device": device,
        "explain": device["states"].get("explain") if device else None,
        "config_path": path,
    }
    if device is None:
        out["skipped_device"] = (
            f"no {ZONE_DEVICE_TYPE} device carries zone_name "
            f"{zone_name!r}, so this zone's live state and explain line "
            "are unavailable -- the zone is configured but Lamplighter "
            "has not created (or cannot see) its device. This is NOT a "
            "zone with nothing to report."
        )
    if sweep["duplicates"].get(zone_name):
        out["duplicate_zone_devices"] = {
            zone_name: sweep["duplicates"][zone_name]
        }
    if sweep["unnamed"]:
        # "This zone has no device" and "there IS a zone device but its
        # zone_name could not be read" are different problems with
        # different fixes -- set the prop, versus create the device --
        # and skipped_device alone cannot tell them apart. One of these
        # may well BE this zone's device.
        out["unnamed_zone_devices"] = [
            {
                "id": _json_safe(getattr(dev, "id", None)),
                "name": _json_safe(getattr(dev, "name", None)),
            }
            for dev in sweep["unnamed"]
        ]
    if sweep["attr_read_errors"]:
        out["attr_read_errors"] = sweep["attr_read_errors"]
    return out


def _reload_snapshot(indigo_module, zone_name):
    """One observation of the live values a reload shows up in.

    Always returns a dict, and ``observed`` says whether that dict can
    be trusted at all. A FAILED observation is never evidence: it can
    neither confirm a reload nor deny one, and comparing it as though
    its missing values were real is how "the IOM hiccuped" turns into
    "the plugin ignored your write". Three ways it fails:

    - the device list would not iterate;
    - ANY single device would not read (``attr_read_errors``), because
      the one that failed may be the very controller or zone device
      this comparison rests on;
    - there is no single ``lamplighter_controller``, so
      ``config_loaded_at`` -- the only strong signal that survives an
      edit which changes nothing else -- cannot be read at all.

    ``reason`` carries the swallowed error text, so a caller can be
    told WHY the check did not happen rather than just that it did not.
    """
    try:
        sweep = _sweep_devices(indigo_module)
    except Exception as exc:
        return {
            "observed": False,
            "reason": f"the Indigo device list could not be read ({exc})",
            "attr_read_errors": 0,
        }
    attr_read_errors = sweep["attr_read_errors"]
    if attr_read_errors:
        return {
            "observed": False,
            "reason": (
                f"{attr_read_errors} device(s) could not be read, so this "
                "observation is incomplete and cannot be compared"
            ),
            "attr_read_errors": attr_read_errors,
        }
    controller, skipped = _controller_payload(sweep)
    if controller is None:
        return {"observed": False, "reason": skipped,
                "attr_read_errors": attr_read_errors}
    dev = sweep["zone_devices"].get(zone_name)
    return {
        "observed": True,
        "reason": None,
        "attr_read_errors": attr_read_errors,
        "config_loaded_at": controller.get("config_loaded_at"),
        "config_zone_count": controller.get("config_zone_count"),
        "zones": controller.get("zones"),
        "config_status": controller.get("config_status"),
        "zone_device_present": dev is not None,
        "explain": _json_safe(_states_of(dev).get("explain")) if dev else None,
    }


def _reload_evidence(before, after):
    """``(text, strength)`` where strength is "strong", "weak" or None.

    STRONG is a change only a config LOAD can produce:

    - ``config_loaded_at`` moving. Lamplighter stamps it on a
      successful load and leaves it alone for an edit it refused, so it
      is the one signal that separates "reloaded" from "looked and
      said no".
    - the configured-zone count moving (``config_zone_count``, or the
      longer-standing ``zones``, so the check still works on a
      Lamplighter that has not shipped the former yet). Only the loader
      writes either.
    - the zone's device appearing, which only ``_create_missing_devices``
      after a reload can do.

    WEAK is a change a reload would explain but does not require, and
    which something else produces just as readily:

    - ``explain`` is rewritten on every re-plan, and a re-plan happens
      on any input edge. Somebody walking past a presence sensor while
      this call was in flight produces exactly this signal.
    - ``config_status`` moves when a load is ATTEMPTED -- including one
      that was attempted and refused, which is the opposite of what
      the caller wants to hear.

    Weak evidence may never be reported as ``reloaded: true``. It is
    still worth returning: "something moved, but not the thing that
    would settle it" is more useful than silence.

    ``config_loaded_at`` is compared for CHANGE, never for ordering. It
    is a LOCAL timestamp, so it legitimately goes backwards for an hour
    every autumn; an ``after > before`` test would call that a
    non-reload. "Different, and non-empty" is the claim the data
    actually supports, so it is the claim made.
    """
    if not before.get("zone_device_present") and after.get("zone_device_present"):
        return ("the zone's lamplighter_zone device appeared, which only "
                "happens when the plugin has reloaded the file"), "strong"

    loaded_before = before.get("config_loaded_at")
    loaded_after = after.get("config_loaded_at")
    if loaded_after and loaded_after != loaded_before:
        return ("the controller's config_loaded_at moved from "
                f"{loaded_before!r} to {loaded_after!r}, which Lamplighter "
                "only stamps on a successful load"), "strong"

    for key in ("config_zone_count", "zones"):
        if before.get(key) != after.get(key):
            return (f"the controller's {key} changed from "
                    f"{before.get(key)} to {after.get(key)}"), "strong"

    if before.get("config_status") != after.get("config_status"):
        return ("the controller's config_status changed from "
                f"{before.get('config_status')!r} to "
                f"{after.get('config_status')!r} -- a load was ATTEMPTED, "
                "but that alone does not say it succeeded"), "weak"

    if before.get("explain") != after.get("explain"):
        return ("the zone device's explain line changed -- consistent with "
                "a reload, but any input edge (someone walking past a "
                "presence sensor) produces the same signal"), "weak"

    return None, None


def _wait_for_reload(indigo_module, zone_name, before):
    """Watch for evidence Lamplighter picked the new file up.

    Returns as soon as STRONG evidence appears: once the question is
    answered there is nothing to gain by sitting out the rest of the
    cap, and a caller is waiting on this. Weak evidence is recorded but
    does NOT end the wait -- a sensor tripping in the first half second
    must not stop us seeing the real thing three seconds later.

    With no usable baseline there is nothing to compare against, so the
    poll does not run at all rather than spending ten seconds proving
    nothing.
    """
    out = {
        "reloaded": False,
        "reload_evidence": None,
        "reload_evidence_strength": None,
        "config_status": None,
        "attr_read_errors": before.get("attr_read_errors", 0),
        "skipped": None,
        "observed_after": False,
        "unobserved_reason": None,
    }
    if not before.get("observed"):
        out["skipped"] = (
            "no baseline observation could be taken before the write, so "
            "there is nothing to compare against: "
            f"{before.get('reason')}"
        )
        return out

    steps = max(1, int(round(_RELOAD_POLL_SECONDS / _RELOAD_POLL_INTERVAL)))
    for _ in range(steps):
        _sleep(_RELOAD_POLL_INTERVAL)
        after = _reload_snapshot(indigo_module, zone_name)
        out["attr_read_errors"] = max(
            out["attr_read_errors"], after.get("attr_read_errors", 0)
        )
        if not after.get("observed"):
            out["unobserved_reason"] = after.get("reason")
            continue
        out["observed_after"] = True
        out["config_status"] = after.get("config_status")
        evidence, strength = _reload_evidence(before, after)
        if strength == "strong":
            out["reloaded"] = True
            out["reload_evidence"] = evidence
            out["reload_evidence_strength"] = "strong"
            return out
        if strength == "weak" and out["reload_evidence"] is None:
            out["reload_evidence"] = evidence
            out["reload_evidence_strength"] = "weak"
    return out


def _update_zone_handler(args, indigo_module):
    """Patch one zone's config block, guarded end to end.

    Order matters and is deliberate:

    1. cheap argument validation, before ``indigo_module`` is touched
       at all, so a bad argument never costs a file read;
    2. read the config and apply the merge patch **to a candidate
       document that is not written anywhere yet**;
    3. hand the whole candidate to Lamplighter's ``validate_config``
       action -- the one and only validator. A plugin that is not
       enabled cannot validate, so this step also refuses the write:
       a config this module could not get checked is not a config it
       should put on disk;
    4. recheck the file has not moved since it was read (narrows, but
       cannot eliminate, a concurrent-write race);
    5. back up, write to a temp file, ``os.replace``;
    6. poll for evidence of the hot reload.

    Steps 1-3 leave the file byte-identical. Only step 5 writes.
    """
    _reject_unknown_args(args, ("zone", "patch"))
    zone_name = _require_str(args, "zone")
    patch = args.get("patch")
    if not isinstance(patch, dict):
        raise ValueError(
            "patch must be a JSON object (a merge patch over the zone's "
            f"config block); got {patch!r} "
            f"({type(patch).__name__})"
        )
    if not patch:
        raise ValueError(
            "patch is empty, so this call would rewrite the config file "
            "without changing anything; nothing was written. Pass the "
            "keys you want changed, or null to remove one."
        )

    config, path, raw_bytes, stat_before = _read_config(indigo_module)

    warnings = []
    index = _zone_index(config, zone_name)
    created = index is None
    if created:
        # Taken VERBATIM, not merge-patched onto an empty object. RFC
        # 7386 reads null as "remove this key", which is right for an
        # edit and wrong for a create: Lamplighter's schema requires
        # `lux` to be PRESENT and allows it to be null ("this zone has
        # no daylight gate" is a stated decision, not an omission), so
        # merge-patching a new zone would silently drop exactly the
        # key the author explicitly set and produce a validation
        # failure they did not cause. In a complete zone object a null
        # is a value.
        new_zone = copy.deepcopy(patch)
        patched_name = new_zone.get("name")
        if patched_name is None:
            new_zone["name"] = zone_name
        elif patched_name != zone_name:
            raise ValueError(
                f"there is no Lamplighter zone named {zone_name!r}, so "
                "this call would create one -- but the patch names it "
                f"{patched_name!r}. Nothing was written. Known zones: "
                f"{sorted(_zone_names(config))}."
            )
        config["zones"].append(new_zone)
        final_name = zone_name
    else:
        updated = _merge_patch(config["zones"][index], patch)
        final_name = updated.get("name", zone_name)
        if final_name != zone_name:
            warnings.append(
                f"the patch renames zone {zone_name!r} to {final_name!r}. "
                "Lamplighter keys a zone's persisted state (presence "
                "last-seen, override, dark verdict) by name, so the "
                "rename resets it and the old zone device stops matching "
                "any zone."
            )

    try:
        document_json = json.dumps(config)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"the patched configuration is not JSON-serialisable ({exc}); "
            f"nothing was written to {path!r}."
        ) from exc

    verdict = _require_verdict(
        _execute_lamplighter_action(
            indigo_module, "validate_config",
            {"config_json": document_json}, side_effect_free=True,
        ),
        "validate_config",
        f"the proposed configuration could NOT be checked and nothing "
        f"was written to {path!r}",
    )
    if verdict.get("ok") is not True:
        # Same defect as explain's, fixed in the same way: a verdict
        # with no message must not render as the word "None", which
        # reads as a refusal reason rather than as its absence.
        refusal = verdict.get("message")
        if not isinstance(refusal, str) or not refusal:
            refusal = "Lamplighter returned ok:false with no message"
        raise ValueError(
            "Lamplighter refused the proposed configuration at "
            f"{verdict.get('path') or '(the top level)'}: {refusal}. "
            f"Nothing was written to {path!r}; the file on disk is "
            "unchanged."
        )

    stat_recheck = _stat(path)
    if (stat_recheck.st_mtime_ns, stat_recheck.st_size) != (
        stat_before.st_mtime_ns, stat_before.st_size,
    ):
        raise ValueError(
            f"Lamplighter config file at {path!r} changed on disk since "
            "it was read for this call -- most likely somebody edited it "
            "while this call was in flight. Refusing to write since the "
            "file no longer matches what was read; nothing was written. "
            "This narrows but does not eliminate the concurrent-write "
            "race (a save landing after this recheck is still possible) "
            "-- re-read and retry."
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = f"{path}.pre-lamplighter_update_zone-{timestamp}"
    try:
        with open(backup_path, "wb") as fh:
            fh.write(raw_bytes)
    except OSError as exc:
        raise ValueError(
            f"could not write the Lamplighter config backup to "
            f"{backup_path!r} ({exc}); nothing was written to {path!r}."
        ) from exc

    before = _reload_snapshot(indigo_module, final_name)

    tmp_path = f"{path}.tmp-{timestamp}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_path, path)
    except OSError as exc:
        raise ValueError(
            f"could not write the updated Lamplighter config to "
            f"{path!r} ({exc}); a backup was made at {backup_path!r} "
            "but the live config was not modified."
        ) from exc

    # EVERYTHING from here on runs after os.replace has already
    # swapped the file in. A raise past this point is not a failed
    # write -- the write succeeded -- so it must not reach the caller
    # looking like one, or the obvious response (retry) re-applies a
    # patch that already landed. Wrapped whole, with both paths named
    # in the text. `except Exception`, never BaseException: a
    # KeyboardInterrupt/SystemExit is Indigo shutting the host down and
    # must keep its own type.
    try:
        watch = _wait_for_reload(indigo_module, final_name, before)

        out = {
            "status": "ok",
            "zone": final_name,
            "created": created,
            "written": path,
            "backup": backup_path,
            "reloaded": watch["reloaded"],
            "reload_evidence": watch["reload_evidence"],
            "reload_evidence_strength": watch["reload_evidence_strength"],
            "config_status": watch["config_status"],
            "validated_zones": verdict.get("zones"),
            "validated_enabled": verdict.get("enabled"),
        }
        if watch["attr_read_errors"]:
            out["attr_read_errors"] = watch["attr_read_errors"]

        if watch["skipped"]:
            # Nothing was looked at, so `reloaded: false` is UNCLAIMED
            # rather than a finding. Deliberately no reassuring note
            # here: "nothing observable changed" would be a statement
            # about an observation that never happened.
            out["reload_check_skipped"] = watch["skipped"]
        elif not watch["observed_after"]:
            out["reload_note"] = (
                "the write landed and Lamplighter validated it, but the "
                "reload could NOT BE OBSERVED at any point in the "
                f"{_RELOAD_POLL_SECONDS:g}s window: "
                f"{watch['unobserved_reason']}. `reloaded: false` here "
                "means \"not looked at\", not \"did not happen\". Read "
                "the zone back with lamplighter_get_zone."
            )
        elif not watch["reloaded"]:
            weak = watch["reload_evidence"]
            out["reload_note"] = (
                "the write landed and Lamplighter validated it, but "
                "nothing conclusive changed within "
                f"{_RELOAD_POLL_SECONDS:g}s"
                + (f" (only weak evidence: {weak})" if weak else "")
                + ". That is NOT evidence the plugin ignored it: an edit "
                "which changes nothing the controller publishes produces "
                "no signal, and Lamplighter re-stats the file every 5s "
                "regardless. Read the zone back with "
                "lamplighter_get_zone to confirm."
            )

        if warnings:
            out["warnings"] = warnings
        return out
    except Exception as exc:
        raise RuntimeError(
            f"the Lamplighter config file WAS written to {path!r} (the "
            f"previous contents are backed up at {backup_path!r}) and "
            "Lamplighter will pick it up within about 5 seconds -- but "
            "this call then failed while confirming the reload: "
            f"{type(exc).__name__}: {exc}. The edit is NOT lost and must "
            "NOT be blindly re-applied. Read the zone back with "
            "lamplighter_get_zone; to undo, copy the backup over the "
            "written path."
        ) from exc


def _reset_override_handler(args, indigo_module):
    _reject_unknown_args(args, ("zone",))
    zone_name = args.get("zone")

    if zone_name is None or zone_name == ALL_ZONES:
        _execute_lamplighter_action(
            indigo_module, "reset_override", {"zone_name": ALL_ZONES}
        )
        return {"status": "ok", "zone": None, "scope": "all zones"}

    if not isinstance(zone_name, str) or not zone_name:
        raise ValueError(
            f"zone must be a non-empty string when provided; got {zone_name!r}"
        )

    config, _path, _raw, _stat_result = _read_config(indigo_module)
    _zone_lookup(config, zone_name)  # raises naming the zone if unknown

    _execute_lamplighter_action(
        indigo_module, "reset_override", {"zone_name": zone_name}
    )
    return {"status": "ok", "zone": zone_name, "scope": "one zone"}


def _lock_zone_handler(args, indigo_module):
    _reject_unknown_args(args, ("zone",))
    zone_name = _require_str(args, "zone")

    config, _path, _raw, _stat_result = _read_config(indigo_module)
    zone_obj = _zone_lookup(config, zone_name)

    # Lamplighter logs a warning and does nothing when a zone's
    # override block says enabled: false -- a silent no-op to any
    # caller that is not reading the event log. It is readable from the
    # config we already hold, so it becomes a failed call instead.
    override = zone_obj.get("override")
    if isinstance(override, dict) and override.get("enabled") is False:
        raise ValueError(
            f"Lamplighter zone {zone_name!r} has override.enabled set to "
            "false, so it never takes overrides and lock_zone would "
            "silently do nothing; the zone was NOT locked. Set "
            "override.enabled to true (lamplighter_update_zone) first if "
            "this zone should be lockable."
        )

    _execute_lamplighter_action(
        indigo_module, "lock_zone", {"zone_name": zone_name}
    )
    return {"status": "ok", "zone": zone_name}


def _controller_enabled_now(indigo_module):
    """``(enabled, reason_unavailable)`` -- the controller's live on/off.

    ``None`` for the flag whenever the read could not be trusted: a
    sweep that raised, a sweep with ``attr_read_errors`` (the device
    that failed may be the controller), no single controller, or a
    non-boolean onState. A failed read must never come back as
    ``False`` -- that would report "the enable did not take" for what
    is actually "we could not look", which is the same confident wrong
    answer the reload check exists to avoid.
    """
    try:
        sweep = _sweep_devices(indigo_module)
    except Exception as exc:
        return None, f"the Indigo device list could not be re-read ({exc})"
    if sweep["attr_read_errors"]:
        return None, (
            f"{sweep['attr_read_errors']} device(s) could not be read "
            "during the check, so the controller's state is not "
            "trustworthy"
        )
    controller, skipped = _controller_payload(sweep)
    if controller is None:
        return None, skipped
    value = controller.get("enabled")
    if not isinstance(value, bool):
        return None, (
            "the controller device reported a non-boolean on/off state "
            f"({value!r})"
        )
    return value, None


def _confirm_controller_state(indigo_module, wanted):
    """Read the controller's on/off back, returning as soon as it says
    ``wanted``.

    A single immediate re-read would be a new false negative rather
    than a check. ``indigo.device.turnOn/turnOff`` hands the command to
    the OWNING plugin in another process, and the state it publishes
    back lands a moment later -- so a one-shot read would report
    ``applied: false`` on a perfectly good write simply because the
    round trip had not finished, which is exactly the class of
    confident wrong answer this check was added to remove. Hence a
    bounded poll. The cap is short (``_ENABLE_CONFIRM_SECONDS``)
    because the only thing being waited on is one local state publish,
    not a config reload.

    Compares against ``wanted`` rather than against the previous value
    on purpose: switching an already-off controller off changes
    nothing and is still correctly applied.
    """
    steps = max(
        1, int(round(_ENABLE_CONFIRM_SECONDS / _ENABLE_CONFIRM_INTERVAL))
    )
    value, unavailable = _controller_enabled_now(indigo_module)
    for _ in range(steps):
        if value == wanted:
            return value, None
        _sleep(_ENABLE_CONFIRM_INTERVAL)
        value, unavailable = _controller_enabled_now(indigo_module)
    return value, unavailable


def _set_plugin_enabled(indigo_module, enabled):
    """The plugin-wide enable: the lamplighter_controller relay's own
    on/off (its Devices.xml says so). Lamplighter's Actions.xml has no
    global-enable action, so this is one of the few places a device
    write is the right mechanism rather than a second-best one.
    """
    sweep = _sweep_devices(indigo_module)
    controller, skipped = _controller_payload(sweep)
    if controller is None:
        raise ValueError(
            f"the Lamplighter plugin-wide enable could not be set: "
            f"{skipped}. Nothing was performed. Pass `zone` to "
            "enable/disable one zone instead."
        )
    controller_id = controller["id"]
    if not isinstance(controller_id, int) or isinstance(controller_id, bool):
        raise ValueError(
            "the lamplighter_controller device reported a non-numeric id "
            f"({controller_id!r}); refusing to switch it. Nothing was "
            "performed."
        )

    # The same gate every action path uses, and it is NOT redundant
    # here just because this is a device write. The controller device
    # outlives the plugin: with Lamplighter uninstalled or disabled its
    # device is still sitting in the Indigo database, so turnOn/turnOff
    # succeeds at the Indigo level, changes no automation whatsoever,
    # and -- without this -- the tool reports ok. That is the whole
    # failure mode this family exists to prevent, reached by the one
    # path that had no gate on it.
    _require_lamplighter_plugin(indigo_module, "The plugin-wide enable")

    enabled_before = controller.get("enabled")
    try:
        if enabled:
            indigo_module.device.turnOn(controller_id)
        else:
            indigo_module.device.turnOff(controller_id)
    except Exception as exc:
        raise RuntimeError(
            f"the Lamplighter controller device ({controller_id}) WAS "
            f"DISPATCHED a turn{'On' if enabled else 'Off'} command and "
            "may have partially or fully taken effect before this error: "
            f"{type(exc).__name__}: {exc}. Do NOT blindly retry -- read "
            "the controller back with lamplighter_list_zones and check "
            "Lamplighter's own event log first."
        ) from exc

    enabled_after, unavailable = _confirm_controller_state(
        indigo_module, enabled
    )

    if enabled_after is None:
        status, applied = "unconfirmed", None
        note = (
            "the command was sent, but the controller's on/off could not "
            f"be read back ({unavailable}), so whether it took effect is "
            "UNKNOWN -- neither confirmed nor denied. Read it back with "
            "lamplighter_list_zones."
        )
    elif enabled_after == enabled:
        status, applied, note = "ok", True, None
    else:
        status, applied = "not_applied", False
        note = (
            "the command was sent but the controller device still reports "
            f"enabled={enabled_after} after "
            f"{_ENABLE_CONFIRM_SECONDS:g}s, so the plugin-wide enable did "
            "NOT take effect. A half-started Lamplighter still reports "
            "isEnabled() true while its device callbacks do nothing -- "
            "check its event log."
        )

    out = {
        "status": status,
        "applied": applied,
        "zone": None,
        "scope": "plugin",
        "enabled": enabled,
        "enabled_before": (
            enabled_before if isinstance(enabled_before, bool) else None
        ),
        "enabled_after": enabled_after,
        "controller_id": controller_id,
    }
    if note:
        out["note"] = note
    return out


def _set_enabled_handler(args, indigo_module):
    _reject_unknown_args(args, ("zone", "enabled"))
    if "enabled" not in args or not isinstance(args["enabled"], bool):
        raise ValueError(
            f"enabled must be true or false; got {args.get('enabled')!r}"
        )
    enabled = args["enabled"]
    zone_name = args.get("zone")

    if zone_name is None:
        return _set_plugin_enabled(indigo_module, enabled)

    if not isinstance(zone_name, str) or not zone_name:
        raise ValueError(
            f"zone must be a non-empty string when provided; got {zone_name!r}"
        )

    config, _path, _raw, _stat_result = _read_config(indigo_module)
    _zone_lookup(config, zone_name)  # raises naming the zone if unknown

    # Lamplighter reads this prop as a STRING and treats
    # on/true/1/yes as enabled -- a JSON boolean crossing the bridge
    # would stringify to "True", which happens to match, but only by
    # accident of Python's repr. Sent as the literal the picker uses.
    _execute_lamplighter_action(
        indigo_module, "set_zone_enabled",
        {"zone_name": zone_name, "enabled": "on" if enabled else "off"},
    )
    return {
        "status": "ok",
        "zone": zone_name,
        "scope": "zone",
        "enabled": enabled,
    }


def _reconcile_now_handler(args, indigo_module):
    _reject_unknown_args(args, ())
    _execute_lamplighter_action(indigo_module, "reconcile_now")
    return {"status": "ok"}


def _explain_handler(args, indigo_module):
    """Lamplighter's own explanation for one zone, verbatim.

    Deliberately does NOT pre-validate the zone against the config
    file, unlike its write siblings: ``explain_zone`` RETURNS
    ``{"ok": false, "message": ...}`` for a name it does not know
    (naming the zones it does), so the plugin's own answer is both
    better and more current than a config read -- the engine's zone
    set is what the running plugin actually has, which after an
    invalid edit is not what the file says.
    """
    _reject_unknown_args(args, ("zone", "at"))
    zone_name = _require_str(args, "zone")

    props = {"zone_name": zone_name}
    at = args.get("at")
    if at is not None:
        if not isinstance(at, str) or not at.strip():
            raise ValueError(
                "at must be a non-empty string, a local time written as "
                f"YYYY-MM-DDTHH:MM; got {at!r}. Omit it entirely to "
                "explain the zone as it is right now."
            )
        at = at.strip()
        try:
            datetime.fromisoformat(at)
        except ValueError as exc:
            raise ValueError(
                f"at {at!r} is not a time this tool understands; write it "
                "as YYYY-MM-DDTHH:MM in local time, or omit it for now."
            ) from exc
        props["at"] = at

    payload = _require_verdict(
        _execute_lamplighter_action(
            indigo_module, "explain_zone", props, side_effect_free=True
        ),
        "explain_zone",
        f"zone {zone_name!r} could not be explained",
    )
    if payload.get("ok") is not True:
        # A refusal with no message must not render as the word "None",
        # which reads like a reason and is not one.
        message = payload.get("message")
        if not isinstance(message, str) or not message:
            message = "Lamplighter returned ok:false with no message"
        raise ValueError(
            f"Lamplighter could not explain zone {zone_name!r}: {message}"
        )
    return payload


# -- registration ----------------------------------------------------------


def register(handler, *, indigo_module, **_):
    """Register the eight ``lamplighter_*`` tools onto the given
    MCPHandler.

    Deliberately no zone DELETE and no whole-file replace: both are
    cheap to ask for and expensive to get wrong, and neither has a
    reversible failure mode the way a merge patch over one zone does.
    """
    handler.register_tool(
        name="lamplighter_list_zones",
        description=(
            "List every Lamplighter lighting zone: its configuration "
            "(lights, presence devices, hold_seconds, lux gate, "
            "override timing, period names, enabled) joined to its live "
            "lamplighter_zone device (id, state, explain, "
            "presence_active, period, override device/expiry, daily "
            "counters), plus the lamplighter_controller device (the "
            "plugin-wide enable, zone counts and config_status). A zone "
            "configured with no device shows `device: null` and is "
            "listed in `zones_without_device`; a lamplighter_zone "
            "device whose zone_name matches no configured zone is "
            "listed in `orphan_zone_devices` — both are real problems "
            "worth reporting, not empty results. Config fields absent "
            "from the file are returned as null rather than resolved to "
            "their schema default, so `enabled: null` means "
            "\"unstated, so true\". Raises if the config file is "
            "missing or unreadable, or if the Indigo device list cannot "
            "be read — never returns an empty zone list for a broken "
            "read."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=lambda **args: _list_zones_handler(args, indigo_module),
    )
    handler.register_tool(
        name="lamplighter_get_zone",
        description=(
            "One Lamplighter zone in full: its entire configuration "
            "block exactly as it sits in lamplighter.json, every state "
            "its lamplighter_zone device publishes (state, "
            "presence_active/last_seen, lux, dark, period, override "
            "device/expiry, desired_summary, counters, and the "
            "persisted record), and its live `explain` line. `zone` is "
            "the zone NAME from lamplighter_list_zones — Lamplighter "
            "has no numeric zone ids. For a dry run at another time, or "
            "for the plugin's freshly computed reasoning rather than "
            "the last line it published, use lamplighter_explain."
        ),
        input_schema={
            "type": "object",
            "required": ["zone"],
            "properties": {
                "zone": {"type": "string",
                         "description": "zone name, exactly as configured"},
            },
        },
        handler=lambda **args: _get_zone_handler(args, indigo_module),
    )
    handler.register_tool(
        name="lamplighter_update_zone",
        description=(
            "Change one Lamplighter zone's configuration by JSON merge "
            "patch (RFC 7386) over its config block. A key set to null "
            "is REMOVED; nested objects merge; arrays and scalars "
            "REPLACE wholesale, so patching `lights` or `periods` "
            "replaces the whole array rather than adding to it. If "
            "`zone` names no existing zone, `patch` must be a complete "
            "zone object and a new zone is created — there the object "
            "is taken verbatim, so a null is a stated VALUE (`lux`: "
            "null means \"this zone has no daylight gate\") rather than "
            "a deletion. Before anything is "
            "written the whole proposed document is sent to "
            "Lamplighter's own validator; if it refuses, this call "
            "fails with the JSON-pointer path and message it gave and "
            "the file on disk is untouched. Also refuses (writing "
            "nothing) if the Lamplighter plugin is not enabled — a "
            "config that cannot be validated must not be installed — "
            "or if the file changed on disk since it was read. On "
            "success the previous file is backed up (path returned as "
            "`backup`) and the new one written; Lamplighter hot-reloads "
            "it within about 5 seconds with overrides and presence "
            "carried across, so NO restart is needed and none is "
            "performed. `reloaded` is true ONLY on strong evidence the "
            "reload happened (the controller's config_loaded_at moving, "
            "its zone count changing, or a new zone's device "
            "appearing); a change that a reload would explain but does "
            "not require — an explain line, a config_status — is "
            "reported as `reload_evidence` with "
            "`reload_evidence_strength: \"weak\"` and leaves `reloaded` "
            "false. False therefore never means \"the plugin ignored "
            "it\": read `reload_note` (nothing conclusive changed) or "
            "`reload_check_skipped` (the check could not be run at all, "
            "so nothing is claimed either way) and confirm with "
            "lamplighter_get_zone if it matters."
        ),
        input_schema={
            "type": "object",
            "required": ["zone", "patch"],
            "properties": {
                "zone": {"type": "string",
                         "description": "zone name to patch, or to create"},
                "patch": {
                    "type": "object",
                    "description": (
                        "JSON merge patch over the zone's config block; "
                        "null removes a key"
                    ),
                },
            },
        },
        handler=lambda **args: _update_zone_handler(args, indigo_module),
    )
    handler.register_tool(
        name="lamplighter_reset_override",
        description=(
            "Release a Lamplighter manual override (and any lock left "
            "by lamplighter_lock_zone) so the zone resumes automatic "
            "control. Omit `zone` to release every zone; pass a zone "
            "name to release just that one — it must be a configured "
            "zone name from lamplighter_list_zones, since Lamplighter's "
            "own action silently no-ops on a name it does not know. "
            "Releasing a zone with no override in place is harmless."
        ),
        input_schema={
            "type": "object",
            "properties": {"zone": {"type": "string"}},
        },
        handler=lambda **args: _reset_override_handler(args, indigo_module),
    )
    handler.register_tool(
        name="lamplighter_lock_zone",
        description=(
            "Hold a Lamplighter zone at whatever its lights are showing "
            "right now, exactly as if somebody had moved a dimmer, for "
            "the active period's override duration. Release it with "
            "lamplighter_reset_override. `zone` must be a configured "
            "zone name. Fails (having done nothing) if the zone's "
            "override.enabled is false, because Lamplighter would "
            "silently decline to lock a zone that never takes "
            "overrides."
        ),
        input_schema={
            "type": "object",
            "required": ["zone"],
            "properties": {"zone": {"type": "string"}},
        },
        handler=lambda **args: _lock_zone_handler(args, indigo_module),
    )
    handler.register_tool(
        name="lamplighter_set_enabled",
        description=(
            "Enable or disable Lamplighter automation. With `zone`, "
            "switches that one zone via the plugin's own "
            "set_zone_enabled action; the zone keeps its persisted "
            "state and its device, it simply stops planning and "
            "writing. Without `zone`, switches the plugin-wide enable "
            "by turning the lamplighter_controller device on or off "
            "— \"all automation off\" — which leaves every zone's own "
            "enable untouched underneath it. A `zone` given must be a "
            "configured zone name; Lamplighter's action silently "
            "no-ops on an unknown one. Note this is a RUNTIME switch: "
            "it does not edit `enabled` in lamplighter.json, so the "
            "next config reload restores whatever the file says (use "
            "lamplighter_update_zone for a durable change). The "
            "plugin-wide form reads the controller back afterwards and "
            "reports `enabled_after` plus `applied`: true when the "
            "device confirms the requested state, false with `status: "
            "not_applied` when it does not (a half-started Lamplighter "
            "accepts the command and changes nothing), or null with "
            "`status: unconfirmed` when the device could not be "
            "re-read — which is unknown, not failed."
        ),
        input_schema={
            "type": "object",
            "required": ["enabled"],
            "properties": {
                "zone": {
                    "type": "string",
                    "description": (
                        "zone name; omit for the plugin-wide enable"
                    ),
                },
                "enabled": {"type": "boolean"},
            },
        },
        handler=lambda **args: _set_enabled_handler(args, indigo_module),
    )
    handler.register_tool(
        name="lamplighter_reconcile_now",
        description=(
            "Ask Lamplighter to re-plan every zone and re-command any "
            "light that is off its desired level, on the next worker "
            "pass. Nothing is forced on: a zone that should be dark "
            "stays dark, and an overridden zone keeps its override. "
            "Useful after devices have been off the mesh, or after a "
            "config edit, to stop waiting for the reconcile tick."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=lambda **args: _reconcile_now_handler(args, indigo_module),
    )
    handler.register_tool(
        name="lamplighter_explain",
        description=(
            "Why one Lamplighter zone is doing what it is doing, "
            "computed fresh by the plugin rather than read from a "
            "device state. Returns `explain` (one line naming the "
            "period, the state and the inputs behind it) and `desired` "
            "— a list of {device, name, level} for every light the "
            "zone would command. With `at` (local time, "
            "YYYY-MM-DDTHH:MM) it is a DRY RUN at that instant instead: "
            "which period covers it, what state the machine would be "
            "in, and what each light would be told, resolved from the "
            "inputs the zone holds now. A dry run decides nothing and "
            "writes nothing. This is the tool that replaces reading the "
            "plugin's mind from the event log."
        ),
        input_schema={
            "type": "object",
            "required": ["zone"],
            "properties": {
                "zone": {"type": "string"},
                "at": {
                    "type": "string",
                    "description": (
                        "optional local time, YYYY-MM-DDTHH:MM; omit for now"
                    ),
                },
            },
        },
        handler=lambda **args: _explain_handler(args, indigo_module),
    )
