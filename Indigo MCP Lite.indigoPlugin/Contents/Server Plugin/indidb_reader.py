"""Read-only parser of the Indigo database (.indiDb) XML.

The IOM exposes *that* triggers/schedules/action groups exist but not
*what they do* — action steps and condition trees live only in the
database file. This module streams that file (~9MB on jarvis) with
``xml.etree.ElementTree.iterparse`` and decodes the three automation
element types into plain dicts:

- ``TDTrigger``  → schedules (time/date triggers, incl. firing rule)
- ``Trigger``    → event triggers
- ``ActionGroup``→ action groups

Schedules and triggers embed their steps in an *anonymous* nested
``ActionGroup`` element (no ID/Name children), so top-level detection
tracks element depth rather than trusting the tag alone.

Code → label maps were derived empirically from the live jarvis DB
(issue #38 recon) plus the runtime enums dumped via IndigoPluginHost3
(``kDeviceAction``, ``kUniversalAction``, ``kThermostatAction``).
Unknown codes are preserved numerically — never guessed.

The parse is cached keyed on ``(path, mtime_ns, size)`` — the path
component is load-bearing: it invalidates the cache when the server
switches databases, so a database swap is picked up without a plugin
restart. Degraded parses never fail silently: automations skipped for
a missing/unparseable ID and corrupted integer fields are counted
into ``skipped_automations`` (surfaced by the tools) and logged. A
mid-write parse failure (Indigo rewrites the file periodically)
raises a friendly ValueError with a retry hint rather than crashing
the tool call.
"""

import logging
import os
import xml.etree.ElementTree as ET

# Truncation bound for embedded script sources — long enough to audit,
# short enough that one automation can't balloon a tool response.
_SCRIPT_TRUNCATE_AT = 2000

_RETRY_HINT = (
    "the Indigo server may be mid-write; retry in a few seconds"
)

# Action ``Class`` codes (issue #38 census — 8 codes cover the DB).
_CLASS_PLUGIN = 999
_CLASS_DEVICE = 1
_CLASS_DEVICE_UNIVERSAL = 9
_CLASS_EXEC_GROUP = 100
_CLASS_VARIABLE = 201
_CLASS_SCRIPT = 101
_CLASS_HVAC = 3
_CLASS_EMPTY = 0

# indigo.kDeviceAction (dumped from the 2025.2 runtime).
_DEVICE_ACTION_LABELS = {
    0: "all_off", 1: "all_lights_on", 2: "all_lights_off",
    4: "turn_on", 5: "turn_off", 6: "toggle", 7: "set_brightness",
    8: "brighten_by", 9: "dim_by", 10: "set_color_levels",
    11: "request_status", 28: "lock", 29: "unlock",
    30: "open", 31: "close",
}

# indigo.kUniversalAction (Class 9 steps use these codes).
_UNIVERSAL_ACTION_LABELS = {
    11: "request_status", 30: "beep",
    100: "energy_update", 101: "energy_reset",
}

# indigo.kThermostatAction (Class 3 / HVACAction).
_HVAC_ACTION_LABELS = {
    0: "set_heat_setpoint", 1: "set_cool_setpoint",
    2: "increase_heat_setpoint", 3: "increase_cool_setpoint",
    4: "decrease_heat_setpoint", 5: "decrease_cool_setpoint",
    7: "request_status_all", 8: "request_mode",
    9: "request_equipment_state", 10: "request_temperatures",
    11: "request_humidities", 12: "request_deadbands",
    13: "request_setpoints", 100: "set_hvac_mode",
    101: "set_fan_mode",
}

# VarAction 0 observed setting string values ("true", "1"). Code 1
# appears with VarValue "1" but its semantics are unconfirmed — it
# stays numeric rather than guessed.
_VAR_ACTION_LABELS = {0: "set_value"}

# ScriptType 0 observed carrying Python source in the live DB.
_SCRIPT_TYPE_LABELS = {0: "python"}

# Condition DevComp codes — best-effort labels inferred from live-DB
# samples (boolean states with empty values for 0/1; numeric
# comparisons for 2-5; list states for 8/9). Raw code always ships
# alongside; treat labels as advisory.
_COMPARATOR_LABELS = {
    0: "is_true", 1: "is_false", 2: "equal", 3: "not_equal",
    4: "greater_than", 5: "less_than", 8: "contains", 9: "not_contains",
}

# ConditionList Logic codes (per recon: 1=all, 0=any).
_LOGIC_LABELS = {0: "any", 1: "all"}

# Condition ``Type`` is a global condition-kind enum, not a per-family
# one: a live-DB census found each code with exactly one child set
# (0 bare, 1/2 bare, 3 variable, 5 time/date, 7 device state, 100
# nested list). Codes 1 and 2 are the "is it dark/light outside"
# conditions — they carry no fields at all, so the mapping was
# confirmed by name agreement across the whole DB (19/19 Type 1 named
# dark/night/evening, 7/7 Type 2 named daytime/daylight). Raw code
# always ships alongside.
_SUN_STATE_LABELS = {1: "dark", 2: "daylight"}

# TDTrigger ``TimeType`` — indigo.kTimeType, cross-checked against the
# live IOM's schedule.timeType for the same records.
_TIME_TYPE_LABELS = {
    0: "absolute", 1: "sunrise", 2: "sunset", 3: "countdown",
}

# TDTrigger ``DateType``. Only the two codes seen live are labelled:
# 0 pairs with RepeatInterval (every N days), 1 with DayFlag (specific
# weekdays). indigo.kDateType names two more; they stay numeric rather
# than guessed.
_DATE_TYPE_LABELS = {0: "days_interval", 1: "days_of_week"}

# DayFlag bitmask, bit 0 = Sunday. Verified against nextExecution on
# three schedules (Fri-only=32, Mon-only=2, Sat+Sun=65).
_WEEKDAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")

_SECONDS_PER_DAY = 86400


class _AnomalyCounter:
    """Counts + logs degraded-parse events (skipped automations,
    corrupted integer fields) so they surface in tool responses
    instead of silently producing false-negative results."""

    def __init__(self, logger=None):
        self.count = 0
        self._logger = logger

    def note(self, message):
        self.count += 1
        if self._logger is not None:
            self._logger.warning("indiDb parse anomaly: %s", message)


def _text(el, tag):
    """Child element text or None (missing child → None, not '')."""
    child = el.find(tag)
    return None if child is None else (child.text or "")


def _int(el, tag, anomalies=None):
    """Child element text as int, or None if missing/unparseable.

    A *missing* child is normal (optional field) and never counted;
    *unparseable* text is a corruption signal and is noted on the
    anomaly counter when one is supplied — a corrupted DeviceID must
    not silently read as "no device reference".
    """
    raw = _text(el, tag)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        if anomalies is not None:
            anomalies.note(f"unparseable integer in <{tag}>: {raw!r}")
        return None


def _bool(el, tag):
    raw = _text(el, tag)
    if raw is None:
        return None
    return raw.strip().lower() == "true"


def _typed_value(el):
    """Decode one Indigo typed-XML element into a plain JSON value.

    Every value in the DB carries a ``type`` attribute; containers
    (``dict``/``vector``) recurse. An unparseable number degrades to
    its raw text rather than vanishing — the same "preserve, never
    guess" rule the code maps follow.
    """
    kind = el.get("type")
    if kind == "dict":
        return {child.tag: _typed_value(child) for child in el}
    if kind == "vector":
        return [_typed_value(child) for child in el]
    raw = el.text or ""
    if kind == "bool":
        return raw.strip().lower() == "true"
    if kind in ("integer", "real"):
        try:
            return int(raw) if kind == "integer" else float(raw)
        except ValueError:
            return raw
    return raw


def _plugin_action_props(action_el, plugin_id):
    """A plugin step's own parameters, from ``MetaProps[PluginID]``.

    MetaProps is keyed by plugin id and a live census found every
    class-999 step with props holding exactly one key — its own
    PluginID — so the step's parameters are always at that key. The
    dict passes through verbatim: no per-plugin knowledge, and the
    step's target device is sometimes only in here (zigbee2mqtt's
    ``dimmer_device_id``) rather than in the sibling ``<DeviceID>``.
    """
    if not plugin_id:
        return None
    meta_el = action_el.find("MetaProps")
    if meta_el is None:
        return None
    for child in meta_el:
        if child.tag == plugin_id:
            return _typed_value(child)
    return None


def _parse_action(action_el, anomalies):
    """Decode one ``Action`` element into a step dict, or None to skip.

    Unknown Class codes are preserved (``type: "unknown"``) rather
    than dropped — an automation summary that silently loses steps
    would misrepresent what the automation does.
    """
    cls = _int(action_el, "Class", anomalies)
    if cls == _CLASS_EMPTY:
        return None
    if cls == _CLASS_PLUGIN:
        step = {
            "type": "plugin_action",
            "class": cls,
            "label": _text(action_el, "TypeLabelPlugin"),
            "plugin_id": _text(action_el, "PluginID"),
            "plugin_type_id": _text(action_el, "TypeIdPlugin"),
        }
        device_id = _int(action_el, "DeviceID", anomalies)
        if device_id is not None:
            step["device_id"] = device_id
        props = _plugin_action_props(action_el, step["plugin_id"])
        if props is not None:
            step["props"] = props
        return step
    if cls in (_CLASS_DEVICE, _CLASS_DEVICE_UNIVERSAL):
        code = _int(action_el, "DeviceAction", anomalies)
        labels = (_DEVICE_ACTION_LABELS if cls == _CLASS_DEVICE
                  else _UNIVERSAL_ACTION_LABELS)
        return {
            "type": "device_action",
            "class": cls,
            "device_id": _int(action_el, "DeviceID", anomalies),
            "action_code": code,
            "action_label": labels.get(code),
            "action_value": _int(action_el, "DeviceActionValue", anomalies),
        }
    if cls == _CLASS_EXEC_GROUP:
        return {
            "type": "execute_action_group",
            "class": cls,
            "action_group_id": _int(action_el, "ActionGroupID", anomalies),
        }
    if cls == _CLASS_VARIABLE:
        code = _int(action_el, "VarAction", anomalies)
        return {
            "type": "variable_action",
            "class": cls,
            "variable_id": _int(action_el, "VarID", anomalies),
            "action_code": code,
            "action_label": _VAR_ACTION_LABELS.get(code),
            "value": _text(action_el, "VarValue"),
        }
    if cls == _CLASS_SCRIPT:
        source = _text(action_el, "ScriptSource") or ""
        truncated = len(source) > _SCRIPT_TRUNCATE_AT
        script_type = _int(action_el, "ScriptType", anomalies)
        return {
            "type": "embedded_script",
            "class": cls,
            "script_type": script_type,
            "script_type_label": _SCRIPT_TYPE_LABELS.get(script_type),
            "source": source[:_SCRIPT_TRUNCATE_AT],
            "truncated": truncated,
        }
    if cls == _CLASS_HVAC:
        code = _int(action_el, "HVACAction", anomalies)
        raw_value = _text(action_el, "HVACActionValue")
        try:
            value = float(raw_value) if raw_value not in (None, "") else None
        except ValueError:
            value = raw_value
        return {
            "type": "hvac_action",
            "class": cls,
            "device_id": _int(action_el, "DeviceID", anomalies),
            "action_code": code,
            "action_label": _HVAC_ACTION_LABELS.get(code),
            "action_value": value,
        }
    return {"type": "unknown", "class": cls}


def _parse_steps(owner_el, anomalies):
    """Ordered step list from ``<owner> > ActionGroup > ActionSteps``.

    Top-level ActionGroup elements hold ActionSteps directly;
    triggers/schedules nest theirs inside an anonymous ActionGroup.
    """
    steps_el = owner_el.find("ActionSteps")
    if steps_el is None:
        nested = owner_el.find("ActionGroup")
        if nested is not None:
            steps_el = nested.find("ActionSteps")
    if steps_el is None:
        return []
    steps = []
    for action_el in steps_el.findall("Action"):
        step = _parse_action(action_el, anomalies)
        if step is not None:
            steps.append(step)
    return steps


def _parse_condition_leaf(cond_el, anomalies):
    """One leaf ``Condition`` (no nested ConditionList) → dict or None.

    Leaf shapes observed live: device-state comparison (DevID...),
    variable comparison (VarID...), time/date window
    (StartTimeDate...). Type 0 leaves carry nothing — they're the
    "no condition" placeholder and collapse to None.
    """
    type_code = _int(cond_el, "Type", anomalies)
    if _text(cond_el, "DevID") is not None:
        comp = _int(cond_el, "DevComp", anomalies)
        leaf = {
            "type": "device_state",
            "type_code": type_code,
            "device_id": _int(cond_el, "DevID", anomalies),
            "state": _text(cond_el, "DevState"),
            "comparator_code": comp,
            "comparator": _COMPARATOR_LABELS.get(comp),
            "value": _text(cond_el, "DevValue"),
        }
        value2 = _text(cond_el, "DevValue2")
        if value2:
            leaf["value2"] = value2
        return leaf
    if _text(cond_el, "VarID") is not None:
        # VarState carries the comparator code for variable leaves
        # (live-DB observation: 0/1 with empty values on booleans,
        # matching the DevComp is_true/is_false family).
        comp = _int(cond_el, "VarState", anomalies)
        leaf = {
            "type": "variable",
            "type_code": type_code,
            "variable_id": _int(cond_el, "VarID", anomalies),
            "comparator_code": comp,
            "comparator": _COMPARATOR_LABELS.get(comp),
            "value": _text(cond_el, "VarValue"),
        }
        variable_id2 = _int(cond_el, "VarID2", anomalies)
        if variable_id2:  # 0 = unused second operand
            leaf["variable_id2"] = variable_id2
        value2 = _text(cond_el, "VarValue2")
        if value2:
            leaf["value2"] = value2
        compare_to_value = _bool(cond_el, "CompareVarToValue")
        if compare_to_value is not None:
            leaf["compare_var_to_value"] = compare_to_value
        return leaf
    if _text(cond_el, "StartTimeDate") is not None or \
            _text(cond_el, "EndTimeDate") is not None:
        return {
            "type": "time_date",
            "type_code": type_code,
            "start": _text(cond_el, "StartTimeDate"),
            "end": _text(cond_el, "EndTimeDate"),
            "operator_code": _int(cond_el, "TimeDateCompareOperator",
                                  anomalies),
        }
    if type_code in _SUN_STATE_LABELS:
        return {
            "type": "sun",
            "type_code": type_code,
            "state": _SUN_STATE_LABELS[type_code],
        }
    if not type_code:
        return None  # Type 0 placeholder — no condition
    return {"type": "unknown", "type_code": type_code}


def _parse_condition_list(list_el, anomalies):
    """``ConditionList`` element → {logic, logic_code, conditions}."""
    logic_code = _int(list_el, "Logic", anomalies)
    conditions = []
    conditions_el = list_el.find("Conditions")
    if conditions_el is not None:
        for child in conditions_el.findall("Condition"):
            parsed = _parse_condition(child, anomalies)
            if parsed is not None:
                conditions.append(parsed)
    return {
        "logic": _LOGIC_LABELS.get(logic_code),
        "logic_code": logic_code,
        "conditions": conditions,
    }


def _parse_condition(cond_el, anomalies):
    """``Condition`` element (wrapper or leaf) → nested dict or None."""
    nested = cond_el.find("ConditionList")
    if nested is not None:
        return _parse_condition_list(nested, anomalies)
    return _parse_condition_leaf(cond_el, anomalies)


def _parse_trigger_watch(trigger_el, anomalies):
    """What a Trigger's own definition watches, from top-level fields.

    Observed field families (issue #38 census): DeviceID +
    DeviceStateSelector/DeviceStateChange[/DeviceStateValue] for
    device-state triggers; VarID + VarChange[/VarValue] for variable
    triggers; PluginID/TypeIdPlugin/TypeLabelPlugin for plugin-event
    triggers. All read defensively — absent families are omitted.
    """
    watch = {}
    device_id = _int(trigger_el, "DeviceID", anomalies)
    if device_id is not None:
        watch["device_id"] = device_id
        selector = _text(trigger_el, "DeviceStateSelector")
        if selector is not None:
            watch["state_selector"] = selector
        change = _int(trigger_el, "DeviceStateChange", anomalies)
        if change is not None:
            watch["state_change_code"] = change
        state_value = _text(trigger_el, "DeviceStateValue")
        if state_value is not None:
            watch["state_value"] = state_value
    variable_id = _int(trigger_el, "VarID", anomalies)
    if variable_id is not None:
        watch["variable_id"] = variable_id
        change = _int(trigger_el, "VarChange", anomalies)
        if change is not None:
            watch["change_code"] = change
        value = _text(trigger_el, "VarValue")
        if value is not None:
            watch["value"] = value
    plugin_id = _text(trigger_el, "PluginID")
    if plugin_id:
        watch["plugin_id"] = plugin_id
        watch["event_type_id"] = _text(trigger_el, "TypeIdPlugin")
        watch["event_label"] = _text(trigger_el, "TypeLabelPlugin")
    return watch


def _clock(seconds):
    """Seconds past midnight → ``HH:MM:SS``, or None if out of range.

    Sun-relative schedules park a 4294967295 sentinel in ``Time``;
    anything outside a single day is that sentinel or corruption and
    must not be formatted into a plausible-looking clock time.
    """
    if seconds is None or not 0 <= seconds < _SECONDS_PER_DAY:
        return None
    return (f"{seconds // 3600:02d}:"
            f"{seconds % 3600 // 60:02d}:"
            f"{seconds % 60:02d}")


def _parse_schedule_timing(el, anomalies):
    """When a schedule fires — the rule, not just the next timestamp.

    ``list_schedules``/``get_schedule_by_id`` report ``nextExecution``,
    a single timestamp that cannot distinguish an absolute 06:00
    schedule from one tracking sunrise. The rule lives in four
    TDTrigger fields: TimeType (absolute/sunrise/sunset/countdown),
    Time (seconds past midnight, absolute only), SunDelta (offset from
    sunrise/sunset), Countdown (repeat interval), plus the day rule in
    DateType + DayFlag/RepeatInterval.
    """
    time_type = _int(el, "TimeType", anomalies)
    timing = {
        "time_type": _TIME_TYPE_LABELS.get(time_type),
        "time_type_code": time_type,
    }
    if time_type == 0:
        seconds = _int(el, "Time", anomalies)
        clock = _clock(seconds)
        if clock is not None:
            timing["time"] = clock
            timing["time_seconds"] = seconds
    elif time_type in (1, 2):
        timing["sun_offset_seconds"] = _int(el, "SunDelta", anomalies) or 0
    elif time_type == 3:
        interval = _int(el, "Countdown", anomalies)
        if interval is not None:
            timing["interval_seconds"] = interval
    randomize = _int(el, "RandomizeAmount", anomalies)
    if randomize:
        timing["randomize_seconds"] = randomize
    date_type = _int(el, "DateType", anomalies)
    timing["date_type"] = _DATE_TYPE_LABELS.get(date_type)
    timing["date_type_code"] = date_type
    if date_type == 1:
        flags = _int(el, "DayFlag", anomalies)
        if flags is not None:
            timing["days_of_week"] = [
                name for bit, name in enumerate(_WEEKDAY_NAMES)
                if flags >> bit & 1
            ]
    elif date_type == 0:
        days = _int(el, "RepeatInterval", anomalies)
        if days is not None:
            timing["repeat_interval_days"] = days
    return timing


def _parse_automation(tag, el, anomalies):
    """One top-level TDTrigger/Trigger/ActionGroup element → record.

    Returns None (and counts the skip) when the element has no
    parseable integer ID — anonymous embedded ActionGroups never
    reach here (depth tracking), so a missing ID at top level is
    genuine schema drift/corruption worth surfacing.
    """
    # ID corruption folds into the skip note below rather than
    # double-counting via _int's own anomaly path.
    entity_id = _int(el, "ID")
    if entity_id is None:
        anomalies.note(
            f"skipping <{tag}> {(_text(el, 'Name') or '(unnamed)')!r} "
            "with missing/unparseable ID"
        )
        return None
    record = {
        "id": entity_id,
        "name": _text(el, "Name") or "",
        "folder_id": _int(el, "FolderID", anomalies),
        "steps": _parse_steps(el, anomalies),
    }
    enabled = _bool(el, "Enabled")
    if enabled is not None:
        record["enabled"] = enabled
    condition_el = el.find("Condition")
    if condition_el is not None:
        record["conditions"] = _parse_condition(condition_el, anomalies)
    else:
        record["conditions"] = None
    if tag == "Trigger":
        record["watch"] = _parse_trigger_watch(el, anomalies)
    elif tag == "TDTrigger":
        record["schedule"] = _parse_schedule_timing(el, anomalies)
    return record


class IndiDbReader:
    """Streaming, cached, read-only view of the Indigo database file.

    ``indigo_module`` is injected (never imported) so tests drive the
    reader with a mock; the DB path comes from
    ``indigo.server.getDbFilePath()`` at each refresh so a database
    switch on the server is picked up without a plugin restart.
    """

    def __init__(self, *, indigo_module, logger=None):
        self._indigo = indigo_module
        self._logger = logger or logging.getLogger("Plugin")
        self._cache_key = None   # (path, mtime_ns, size)
        self._cache = None

    # -- public API ----------------------------------------------------

    def automations(self):
        """Parsed automations: {"schedule"|"trigger"|"action_group":
        {id: record}, "skipped_automations": n}. Cached until the DB
        file's path/mtime/size change. Raises a friendly ValueError
        when the path is unavailable, the file is unreadable, or the
        file can't be parsed (mid-write)."""
        path = self._db_path()
        try:
            stat = os.stat(path)
        except OSError as exc:
            raise self._friendly_os_error(exc)
        cache_key = (path, stat.st_mtime_ns, stat.st_size)
        if self._cache is not None and cache_key == self._cache_key:
            return self._cache
        parsed = self._parse(path)
        self._cache_key = cache_key
        self._cache = parsed
        return parsed

    def resolve_name(self, collection_attr, entity_id):
        """Best-effort name lookup on the live IOM (``devices``,
        ``variables``, ``actionGroups``, ...). Unresolvable → None —
        callers keep the raw id either way. Lookup-style errors mean
        "deleted since the DB was written" and stay quiet; anything
        else is logged before degrading."""
        collection = getattr(self._indigo, collection_attr, None)
        if collection is None or entity_id is None:
            return None
        try:
            obj = collection[entity_id]
        except (KeyError, IndexError, ValueError, TypeError):
            return None
        except Exception as exc:
            self._logger.warning(
                "indiDb name lookup failed for %s[%s]: %s",
                collection_attr, entity_id, exc,
            )
            return None
        name = getattr(obj, "name", None)
        return name if isinstance(name, str) else None

    # -- internals -----------------------------------------------------

    def _db_path(self):
        try:
            path = self._indigo.server.getDbFilePath()
        except Exception as exc:
            raise ValueError(
                f"Indigo database path unavailable ({exc}); "
                "is the Indigo server running?"
            )
        if not isinstance(path, str) or not path:
            raise ValueError(
                "Indigo database path unavailable "
                "(server returned no path)"
            )
        return path

    def _friendly_os_error(self, exc):
        """Classify an OSError from stat/iterparse into a friendly
        ValueError: missing-file/permission problems point at the
        path, anything else (EIO etc.) gets the mid-write retry
        hint."""
        if isinstance(exc, (FileNotFoundError, PermissionError)):
            message = (
                f"Indigo database file is not readable ({exc}); "
                "check the database path/permissions"
            )
        else:
            message = (
                f"Indigo database file could not be read ({exc}); "
                f"{_RETRY_HINT}"
            )
        self._logger.warning(message)
        return ValueError(message)

    def _parse(self, path):
        """Stream-parse the file. Depth tracking distinguishes real
        top-level automations from the anonymous ActionGroup nested
        inside every trigger/schedule; non-automation elements are
        cleared as soon as they close to keep memory flat on a ~9MB
        file full of device definitions we don't want."""
        tag_to_kind = {
            "TDTrigger": "schedule",
            "Trigger": "trigger",
            "ActionGroup": "action_group",
        }
        anomalies = _AnomalyCounter(self._logger)
        result = {"schedule": {}, "trigger": {}, "action_group": {}}
        depth = 0
        try:
            for event, el in ET.iterparse(path, events=("start", "end")):
                if event == "start":
                    if el.tag in tag_to_kind:
                        depth += 1
                    continue
                if el.tag in tag_to_kind:
                    depth -= 1
                    if depth == 0:
                        record = _parse_automation(el.tag, el, anomalies)
                        if record is not None:
                            result[tag_to_kind[el.tag]][record["id"]] = record
                        el.clear()
                elif depth == 0:
                    el.clear()
        except ET.ParseError as exc:
            message = (
                f"Indigo database file could not be parsed ({exc}); "
                f"{_RETRY_HINT}"
            )
            self._logger.warning(message)
            raise ValueError(message)
        except OSError as exc:
            raise self._friendly_os_error(exc)
        result["skipped_automations"] = anomalies.count
        return result
