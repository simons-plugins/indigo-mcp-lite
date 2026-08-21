"""Auto Lights config tools (issue #66) — scoped writes to a sibling
plugin's zone settings, plus the two zone-lifecycle actions that were
otherwise only reachable by hand-wrapping an Indigo action group.

Auto Lights (``com.vtmikel.autolights``) is a fork we own
(``indigo-auto-lights``). Two very different mechanisms back its
tools, and getting this wrong produces exactly the bug this issue
exists to prevent — a call that reports success while nothing about
the running plugin actually changed:

- ``auto_lights_set_level`` is a genuine **file write**.
  ``device_period_map`` lives only in
  ``Preferences/com.vtmikel.autolights/config/auto_lights_conf.json``,
  read by the plugin at startup and cached — a write with no restart
  changes nothing observable, so a restart is mandatory and a failed
  restart must fail the whole call (see ``_set_level_handler``).
- ``auto_lights_set_zone_enabled`` and ``auto_lights_reset_locks`` are
  **not** config-file fields at all. A zone's enabled flag is the live
  ``onState`` of its own Indigo device
  (``auto_lights_agent.enable_zone``/``disable_zone`` call
  ``indigo.device.turnOn/turnOff``), and lock state is in-memory
  (``Zone.locked``). Writing either into the JSON would be silently
  ignored forever — the exact "honest-looking answer produced when the
  code could not do its job" ADR-0002 names. These two instead call
  Auto Lights' own registered ``Actions.xml`` actions directly via
  ``indigo.server.getPlugin(...).executeAction(...)``, the same
  cross-plugin mechanism ``restart_plugin`` uses for ``.restart()``.
  Confirmed against the live ``Actions.xml``: ``enable_zone`` /
  ``disable_zone`` / ``reset_all_locks`` / ``reset_zone_locks`` take a
  ``zone_list`` prop equal to the zone's **name** (there is no numeric
  zone id in the config), and none of them raise on an unmatched zone
  name — they just silently do nothing. So both callers validate the
  zone against the config file themselves before calling, which is
  the only thing standing between "no such zone" and a quiet no-op.

Deliberately no ``jsonschema`` dependency, matching this plugin's
stdlib-only design (see workspace ADR-0003) and Auto Lights' own
config schema not being enforced at runtime either. Instead of a full
schema validate, this module validates exactly what ``set_level``
touches — the level's type/range and that the zone/period/device
triple actually exists in the config — which is sufficient because a
single ``device_period_map`` cell cannot structurally violate any
other part of the document.
"""

import json
import os
from datetime import datetime, timezone

from tools.lookup import _reject_unknown_args, _require_int_id

AUTOLIGHTS_PLUGIN_ID = "com.vtmikel.autolights"

_CONFIG_SUBPATH = os.path.join(
    "Preferences", "com.vtmikel.autolights", "config", "auto_lights_conf.json"
)


# -- path / read -------------------------------------------------------


def _config_path(indigo_module):
    """Resolve the Auto Lights config path from the live install
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


def _read_config(indigo_module):
    """Read + parse the Auto Lights config.

    Returns ``(config, path, raw_bytes, stat_result)``. Every way this
    can fail short of a genuine parse raises a friendly ``ValueError``
    naming the likely cause — missing install path, missing file,
    unreadable file, invalid JSON, or a file that parses but isn't
    shaped like an Auto Lights config — rather than degrading to an
    empty-but-successful result (workspace convention, ADR-0002).
    """
    path = _config_path(indigo_module)
    try:
        stat_result = _stat(path)
    except FileNotFoundError as exc:
        raise ValueError(
            f"Auto Lights config file not found at {path!r}; is the "
            f"Auto Lights plugin ({AUTOLIGHTS_PLUGIN_ID}) installed "
            "and has it run at least once?"
        ) from exc
    except OSError as exc:
        raise ValueError(
            f"Auto Lights config file is not readable ({exc}); check "
            f"permissions on {path!r}"
        ) from exc

    try:
        with open(path, "rb") as fh:
            raw_bytes = fh.read()
    except OSError as exc:
        raise ValueError(f"Auto Lights config file could not be read ({exc})") from exc

    try:
        config = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Auto Lights config file at {path!r} is not valid JSON "
            f"({exc}); it may be mid-write or hand-edited incorrectly. "
            "Retry, or inspect the file directly before trying again."
        ) from exc

    if (
        not isinstance(config, dict)
        or not isinstance(config.get("zones"), list)
        or not isinstance(config.get("lighting_periods"), list)
    ):
        raise ValueError(
            f"{path!r} does not look like an Auto Lights config file "
            "(missing or malformed 'zones'/'lighting_periods'); "
            "refusing to treat it as one."
        )

    return config, path, raw_bytes, stat_result


# -- validation ----------------------------------------------------------


def _require_str(args, key):
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _validate_level(raw):
    """1-100 int, or bool. ``bool`` MUST be checked before the int
    range check — ``bool`` is a subclass of ``int`` in Python, so
    ``level=True`` would otherwise read as brightness 1. This has
    bitten the sibling repo twice."""
    if raw is None:
        raise ValueError("level is required: an int 1-100, true, or false")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        if 1 <= raw <= 100:
            return raw
        raise ValueError(
            f"level must be between 1 and 100 (or true/false); got {raw}"
        )
    raise ValueError(
        f"level must be an int 1-100, true, or false; got {raw!r} "
        f"({type(raw).__name__})"
    )


def _zone_lookup(config, zone_name):
    for z in config.get("zones", []):
        if isinstance(z, dict) and z.get("name") == zone_name:
            return z
    known = sorted(
        z.get("name") for z in config.get("zones", [])
        if isinstance(z, dict) and z.get("name")
    )
    raise ValueError(f"no Auto Lights zone named {zone_name!r}; known zones: {known}")


def _period_lookup(config, zone_obj, period_id):
    zone_period_ids = zone_obj.get("lighting_period_ids") or []
    if period_id not in zone_period_ids:
        raise ValueError(
            f"period {period_id} is not linked to zone "
            f"{zone_obj.get('name')!r}; linked periods: "
            f"{sorted(zone_period_ids)}"
        )
    for p in config.get("lighting_periods", []):
        if isinstance(p, dict) and p.get("id") == period_id:
            return p
    raise ValueError(
        f"period {period_id} is linked to zone {zone_obj.get('name')!r} "
        "but does not exist in lighting_periods; the config may be corrupt"
    )


def _known_zone_device_ids(zone_obj):
    settings = zone_obj.get("device_settings") or {}
    return set(settings.get("on_lights_dev_ids") or []) | set(
        settings.get("off_lights_dev_ids") or []
    )


def _validate_device(zone_obj, device_id):
    known = _known_zone_device_ids(zone_obj)
    if device_id not in known:
        raise ValueError(
            f"device {device_id} is not one of zone "
            f"{zone_obj.get('name')!r} on/off lights; known devices: "
            f"{sorted(known)}"
        )


# -- cross-plugin actions -------------------------------------------------


def _execute_autolights_action(indigo_module, action_id, props=None):
    """Invoke one of Auto Lights' own ``Actions.xml`` actions via
    ``executeAction`` — the documented cross-plugin call, same family
    as ``restart_plugin``'s ``.restart()``. Any failure (plugin not
    installed, not running, action itself raising) is mapped to a
    friendly ``ValueError`` so the call fails rather than reporting a
    success the plugin never actually performed."""
    try:
        plugin = indigo_module.server.getPlugin(AUTOLIGHTS_PLUGIN_ID)
        plugin.executeAction(action_id, props=props or {}, waitUntilDone=True)
    except Exception as exc:
        raise ValueError(
            f"Auto Lights action {action_id!r} failed ({exc}); is the "
            f"Auto Lights plugin ({AUTOLIGHTS_PLUGIN_ID}) installed and "
            "running?"
        ) from exc


# -- handlers --------------------------------------------------------------


def _serialize_period(p):
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "mode": p.get("mode"),
        "from_time_hour": p.get("from_time_hour"),
        "from_time_minute": p.get("from_time_minute"),
        "to_time_hour": p.get("to_time_hour"),
        "to_time_minute": p.get("to_time_minute"),
        "lock_duration": p.get("lock_duration"),
        "limit_brightness": p.get("limit_brightness"),
    }


def _list_zones_handler(args, indigo_module):
    """Read-only: what the config file (and so the plugin, once
    restarted) currently believes. Never returns an empty-but-ok
    ``{"zones": [], "total_count": 0}`` for a missing/corrupt config —
    ``_read_config`` raises first."""
    _reject_unknown_args(args, ())
    config, path, _raw, _stat_result = _read_config(indigo_module)

    periods_by_id = {
        p.get("id"): p for p in config.get("lighting_periods", [])
        if isinstance(p, dict)
    }
    zones = []
    for z in config.get("zones", []):
        if not isinstance(z, dict):
            continue
        period_ids = z.get("lighting_period_ids") or []
        zones.append({
            "name": z.get("name"),
            "periods": [
                _serialize_period(periods_by_id[pid])
                for pid in period_ids if pid in periods_by_id
            ],
            "device_settings": z.get("device_settings") or {},
            "minimum_luminance_settings": z.get("minimum_luminance_settings") or {},
            "behavior_settings": z.get("behavior_settings") or {},
            "advanced_settings": z.get("advanced_settings") or {},
            "device_period_map": z.get("device_period_map") or {},
            "global_behavior_variables_map": z.get("global_behavior_variables_map") or {},
        })

    return {
        "zones": zones,
        "lighting_periods": [
            _serialize_period(p) for p in config.get("lighting_periods", [])
            if isinstance(p, dict)
        ],
        "total_count": len(zones),
        "config_path": path,
    }


def _set_level_handler(args, indigo_module):
    """Write one ``device_period_map`` cell, guarded end to end.

    Order matters and is deliberate: cheap argument validation (zone/
    period/device/level shape) happens before ``indigo_module`` is
    touched at all, so a bad argument never costs a file read. Only
    once the value is known-good do we read the config, validate the
    zone/period/device actually exist, recheck the file hasn't moved
    since we read it, back it up, write it, and restart the plugin —
    a failed restart fails the whole call even though the file write
    already landed, because the running plugin has not picked it up.
    """
    _reject_unknown_args(args, ("zone", "period", "device", "level"))
    zone_name = _require_str(args, "zone")
    period_id = _require_int_id(args, "period")
    device_id = _require_int_id(args, "device")
    level = _validate_level(args.get("level"))

    config, path, raw_bytes, stat_before = _read_config(indigo_module)
    zone_obj = _zone_lookup(config, zone_name)
    _period_lookup(config, zone_obj, period_id)
    _validate_device(zone_obj, device_id)

    device_period_map = zone_obj.setdefault("device_period_map", {})
    period_bucket = device_period_map.setdefault(str(period_id), {})
    previous_level = period_bucket.get(str(device_id))
    period_bucket[str(device_id)] = level

    stat_recheck = _stat(path)
    if (stat_recheck.st_mtime_ns, stat_recheck.st_size) != (
        stat_before.st_mtime_ns, stat_before.st_size,
    ):
        raise ValueError(
            f"Auto Lights config file at {path!r} changed on disk since "
            "it was read for this call — most likely the web editor "
            "saved while this call was in flight. Refusing to write and "
            "risk clobbering that change; nothing was written. Re-read "
            "and retry."
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = f"{path}.pre-auto_lights_set_level-{timestamp}"
    with open(backup_path, "wb") as fh:
        fh.write(raw_bytes)

    tmp_path = f"{path}.tmp-{timestamp}"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")
    os.replace(tmp_path, path)

    try:
        plugin = indigo_module.server.getPlugin(AUTOLIGHTS_PLUGIN_ID)
        plugin.restart(waitUntilDone=True)
    except Exception as exc:
        raise ValueError(
            f"Auto Lights config was written and backed up (backup: "
            f"{backup_path!r}) but restarting the Auto Lights plugin "
            f"failed ({exc}); it has NOT picked up this change yet. "
            "Restart it manually (Indigo > Plugins) or retry this call."
        ) from exc

    return {
        "status": "ok",
        "zone": zone_name,
        "period": period_id,
        "device": device_id,
        "level": level,
        "previous_level": previous_level,
        "config_path": path,
        "backup_path": backup_path,
    }


def _set_zone_enabled_handler(args, indigo_module):
    _reject_unknown_args(args, ("zone", "enabled"))
    zone_name = _require_str(args, "zone")
    if "enabled" not in args or not isinstance(args["enabled"], bool):
        raise ValueError(f"enabled must be true or false; got {args.get('enabled')!r}")
    enabled = args["enabled"]

    config, _path, _raw, _stat_result = _read_config(indigo_module)
    _zone_lookup(config, zone_name)  # raises naming the zone if unknown

    action_id = "enable_zone" if enabled else "disable_zone"
    _execute_autolights_action(indigo_module, action_id, props={"zone_list": zone_name})

    return {"status": "ok", "zone": zone_name, "enabled": enabled}


def _reset_locks_handler(args, indigo_module):
    _reject_unknown_args(args, ("zone",))
    zone_name = args.get("zone")

    if zone_name is None:
        _execute_autolights_action(indigo_module, "reset_all_locks")
        return {"status": "ok", "zone": None}

    if not isinstance(zone_name, str) or not zone_name:
        raise ValueError(f"zone must be a non-empty string when provided; got {zone_name!r}")

    config, _path, _raw, _stat_result = _read_config(indigo_module)
    _zone_lookup(config, zone_name)  # raises naming the zone if unknown

    _execute_autolights_action(
        indigo_module, "reset_zone_locks", props={"zone_list": zone_name}
    )
    return {"status": "ok", "zone": zone_name}


# -- registration ----------------------------------------------------------


def register(handler, *, indigo_module, **_):
    """Register the four ``auto_lights_*`` tools onto the given
    MCPHandler. v1 surface only (issue #66) — no creating/deleting
    zones or periods, no editing period times or device lists, no
    luminance setters. Those are where a wrong answer is expensive and
    the Auto Lights web editor is right there."""
    handler.register_tool(
        name="auto_lights_list_zones",
        description=(
            "Read the Auto Lights config: every zone with its linked "
            "lighting periods, on/off/luminance/presence device ids, "
            "luminance and lock/behavior settings, and the "
            "device_period_map (per-device brightness overrides per "
            "period). This is what the plugin believes as of its last "
            "restart, not necessarily what's live right now (e.g. "
            "a zone's actual on/off/lock state is separate runtime "
            "state, not a config field). Raises if the config file is "
            "missing or unreadable — never returns an empty result for "
            "a broken read."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=lambda **args: _list_zones_handler(args, indigo_module),
    )
    handler.register_tool(
        name="auto_lights_set_level",
        description=(
            "Set one Auto Lights device_period_map cell: how `device` "
            "behaves in `zone` during lighting `period`. `level` is an "
            "int 1-100 (brightness override), true (include the device "
            "at its normal calculated brightness), or false (exclude it "
            "from this period). zone/period/device must already exist "
            "in the config — call auto_lights_list_zones first to find "
            "the right ids/names. Writes the config file, backs it up "
            "first (path returned as backup_path), and restarts the "
            "Auto Lights plugin so the change takes effect immediately "
            "— the plugin only reads this file at startup, so a write "
            "with no restart changes nothing observable. If the restart "
            "fails this call fails too, even though the file was "
            "written; the change is not yet live. Refuses to write (and "
            "changes nothing) if the config file was modified on disk "
            "since it was read for this call, e.g. by the web editor."
        ),
        input_schema={
            "type": "object",
            "required": ["zone", "period", "device", "level"],
            "properties": {
                "zone": {"type": "string"},
                "period": {"type": "integer",
                           "description": "lighting period id"},
                "device": {"type": "integer",
                           "description": "Indigo device id"},
                "level": {
                    "description": "1-100 brightness, or true/false to "
                                    "include/exclude",
                },
            },
        },
        handler=lambda **args: _set_level_handler(args, indigo_module),
    )
    handler.register_tool(
        name="auto_lights_set_zone_enabled",
        description=(
            "Enable or disable an Auto Lights zone by name (its "
            "on/off automation, not the config file — this calls the "
            "plugin's own enable_zone/disable_zone action directly). "
            "zone must be an existing zone name from "
            "auto_lights_list_zones; Auto Lights' own action silently "
            "no-ops on an unknown zone name, so this validates first "
            "and raises rather than risk that."
        ),
        input_schema={
            "type": "object",
            "required": ["zone", "enabled"],
            "properties": {
                "zone": {"type": "string"},
                "enabled": {"type": "boolean"},
            },
        },
        handler=lambda **args: _set_zone_enabled_handler(args, indigo_module),
    )
    handler.register_tool(
        name="auto_lights_reset_locks",
        description=(
            "Reset Auto Lights zone lock(s) via the plugin's own "
            "reset_all_locks / reset_zone_locks actions. Omit `zone` "
            "to reset every zone's lock; pass a zone name to reset "
            "just that one — it must be an existing zone name from "
            "auto_lights_list_zones, since Auto Lights' own action "
            "silently no-ops on an unmatched name."
        ),
        input_schema={
            "type": "object",
            "properties": {"zone": {"type": "string"}},
        },
        handler=lambda **args: _reset_locks_handler(args, indigo_module),
    )
