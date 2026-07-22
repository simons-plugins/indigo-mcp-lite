"""Lookup tools — read-only primitives over Indigo's object model.

Each tool wraps a single Indigo iter / lookup; no business logic.
``_normalize_pagination`` and ``_serialize_device`` are deliberately
top-level so the next list/get tools (list_variables,
list_action_groups, get_devices_by_type, get_devices_by_state, etc.)
share the same wire shape.
"""

from tools.state_filter import matches as _state_matches


_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500


def _normalize_pagination(args):
    """Coerce + clamp limit/offset for any list-style tool.

    limit: defaults to 50, capped at 500, floored at 1.
    offset: defaults to 0, floored at 0.
    Reused by list_variables, list_action_groups, get_devices_by_type,
    get_devices_by_state — all subsequent list-paginated tools.
    """
    limit = args.get("limit", _DEFAULT_LIMIT)
    limit = max(1, min(_MAX_LIMIT, int(limit)))
    offset = max(0, int(args.get("offset", 0)))
    return limit, offset


def _serialize_device(d):
    """Stable dict shape for a single Indigo device.

    Used by list_devices, get_device_by_id, get_devices_by_type,
    get_devices_by_state. Centralised so the wire shape never drifts
    between tools.
    """
    return {
        "id": d.id,
        "name": d.name,
        "type": getattr(d, "deviceTypeId", ""),
        "model": getattr(d, "model", ""),
        "address": getattr(d, "address", ""),
        "description": getattr(d, "description", ""),
        "folder_id": getattr(d, "folderId", 0),
        "plugin_id": getattr(d, "pluginId", ""),
        "on_state": getattr(d, "onState", None),
        "brightness": getattr(d, "brightness", None),
    }


def _serialize_variable(v):
    """Stable dict shape for an Indigo variable.

    Used by list_variables and get_variable_by_id.
    """
    return {
        "id": v.id,
        "name": v.name,
        "value": getattr(v, "value", ""),
        "folder_id": getattr(v, "folderId", 0),
        "description": getattr(v, "description", ""),
    }


def _serialize_action_group(a):
    """Stable dict shape for an Indigo action group.

    Used by list_action_groups and get_action_group_by_id.
    """
    return {
        "id": a.id,
        "name": a.name,
        "description": getattr(a, "description", ""),
        "folder_id": getattr(a, "folderId", 0),
    }


def register(handler, *, indigo_module):
    """Register every lookup tool onto the given MCPHandler.

    ``indigo_module`` is injected (not imported here) so tests can
    pass a MagicMock without monkey-patching ``sys.modules`` from
    inside this file.
    """
    handler.register_tool(
        name="list_devices",
        description="List Indigo devices with optional pagination and filters.",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "offset": {"type": "integer", "minimum": 0},
            },
        },
        handler=lambda **args: _list_devices_handler(args, indigo_module),
    )
    handler.register_tool(
        name="list_variables",
        description="List Indigo variables with optional pagination.",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "offset": {"type": "integer", "minimum": 0},
            },
        },
        handler=lambda **args: _list_variables_handler(args, indigo_module),
    )
    handler.register_tool(
        name="list_action_groups",
        description="List Indigo action groups with optional pagination.",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "offset": {"type": "integer", "minimum": 0},
            },
        },
        handler=lambda **args: _list_action_groups_handler(args, indigo_module),
    )
    handler.register_tool(
        name="list_variable_folders",
        description="List Indigo variable folders (id + name).",
        input_schema={"type": "object", "properties": {}},
        handler=lambda **args: _list_variable_folders_handler(args, indigo_module),
    )
    handler.register_tool(
        name="list_device_folders",
        description="List Indigo device folders (id + name).",
        input_schema={"type": "object", "properties": {}},
        handler=lambda **args: _list_device_folders_handler(args, indigo_module),
    )
    handler.register_tool(
        name="get_device_by_id",
        description="Return a single Indigo device by id.",
        input_schema={
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "integer"}},
        },
        handler=lambda **args: _get_device_handler(args, indigo_module),
    )
    handler.register_tool(
        name="get_variable_by_id",
        description="Return a single Indigo variable by id.",
        input_schema={
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "integer"}},
        },
        handler=lambda **args: _get_variable_handler(args, indigo_module),
    )
    handler.register_tool(
        name="get_action_group_by_id",
        description="Return a single Indigo action group by id.",
        input_schema={
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "integer"}},
        },
        handler=lambda **args: _get_action_group_handler(args, indigo_module),
    )
    handler.register_tool(
        name="get_devices_by_type",
        description="List Indigo devices filtered by deviceTypeId.",
        input_schema={
            "type": "object",
            "required": ["device_type"],
            "properties": {
                "device_type": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "offset": {"type": "integer", "minimum": 0},
            },
        },
        handler=lambda **args: _get_devices_by_type_handler(args, indigo_module),
    )
    handler.register_tool(
        name="get_devices_by_state",
        description=(
            "List Indigo devices matching a state spec. "
            "Spec is a JSON object whose keys are state names; values are "
            "either literals (==) or string-prefixed comparators "
            "(>=, <=, >, <), e.g. {\"onState\": true, \"batteryLevel\": \"<20\"}."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "state_spec": {"type": "object", "additionalProperties": True},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "offset": {"type": "integer", "minimum": 0},
            },
        },
        handler=lambda **args: _get_devices_by_state_handler(args, indigo_module),
    )


def _list_devices_handler(args, indigo_module):
    """Return a paginated, serialized snapshot of ``indigo.devices``."""
    limit, offset = _normalize_pagination(args)
    all_devices = list(indigo_module.devices)
    total = len(all_devices)
    page = all_devices[offset:offset + limit]
    return {
        "results": [_serialize_device(d) for d in page],
        "total_count": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
    }


def _list_variables_handler(args, indigo_module):
    """Return a paginated, serialized snapshot of ``indigo.variables``."""
    limit, offset = _normalize_pagination(args)
    all_variables = list(indigo_module.variables)
    total = len(all_variables)
    page = all_variables[offset:offset + limit]
    return {
        "results": [_serialize_variable(v) for v in page],
        "total_count": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
    }


def _list_action_groups_handler(args, indigo_module):
    """Return a paginated, serialized snapshot of ``indigo.actionGroups``."""
    limit, offset = _normalize_pagination(args)
    all_groups = list(indigo_module.actionGroups)
    total = len(all_groups)
    page = all_groups[offset:offset + limit]
    return {
        "results": [_serialize_action_group(a) for a in page],
        "total_count": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
    }


def _serialize_folder(f):
    """Stable dict shape for a variable or device folder.

    Folders are minimal in the IOM (id + name); kept in a single
    helper because both folder list tools serialise identically.
    """
    return {"id": f.id, "name": f.name}


def _list_folders(folder_iterable):
    """Materialise + serialise a folder iterable.

    No pagination — folder counts are small (tens, not hundreds),
    so the full list always fits in one response.
    """
    folders = list(folder_iterable)
    return {
        "results": [_serialize_folder(f) for f in folders],
        "total_count": len(folders),
    }


def _list_variable_folders_handler(_args, indigo_module):
    """Return all variable folders as ``{results, total_count}``."""
    return _list_folders(indigo_module.variables.folders)


def _list_device_folders_handler(_args, indigo_module):
    """Return all device folders as ``{results, total_count}``."""
    return _list_folders(indigo_module.devices.folders)


def _coerce_int(raw):
    """Return ``raw`` as an int if it is integer-valued, else None.

    Accepts ints and integral floats (JSON Schema treats ``5.0`` as a
    valid ``"integer"``, and JSON clients routinely emit floats), but
    rejects bools, fractional floats, and strings.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    return None


def _require_int_id(args, key="id"):
    """Pull an integer id out of args under ``key`` or raise ValueError.

    Booleans are rejected even though ``isinstance(True, int)`` is
    True — the schema says integer, and a bool id is almost always a
    caller bug rather than a meaningful query. Integral floats coerce
    (see ``_coerce_int``). ``key`` defaults to ``"id"`` for the lookup
    tools but Phase 4 control tools pass ``"device_id"``,
    ``"variable_id"``, etc., so the same validator applies everywhere
    without each tool reimplementing it.
    """
    value = _coerce_int(args.get(key))
    if value is None:
        raise ValueError(f"{key} must be an integer")
    return value


def _reject_unknown_args(args, allowed):
    """Raise if ``args`` contains keys outside ``allowed``.

    MCPHandler does no schema validation and every handler is
    ``lambda **args``, so a misspelled optional key (``delay_seconds``
    for ``delay``) would otherwise be silently dropped and the command
    would fire immediately — the caller's real intent lost with a
    misleading ok. Naming the valid keys lets an LLM self-correct.
    """
    unknown = set(args) - set(allowed)
    if unknown:
        raise ValueError(
            f"unknown argument(s) {sorted(unknown)}; "
            f"valid: {sorted(allowed)}"
        )


def _lookup_or_raise(collection, entity_id, entity_label):
    """Subscript ``collection[entity_id]`` and translate Indigo's
    KeyError / IndexError / ValueError into a friendly ValueError."""
    try:
        return collection[entity_id]
    except (KeyError, IndexError, ValueError):
        raise ValueError(f"no {entity_label} with id {entity_id}")


# Attributes worth surfacing on a single-device lookup beyond the slim
# list shape. Curated rather than a blind dict(dev) dump: these are the
# fields an assistant actually reasons about, and several are subclass-
# specific so getattr-with-None keeps one serializer for all types.
_DETAIL_ATTRS = (
    ("battery_level", "batteryLevel"),
    ("enabled", "enabled"),
    ("error_state", "errorState"),
    ("sensor_value", "sensorValue"),
    ("display_state_ui", "displayStateValUi"),
    ("hvac_mode", "hvacMode"),
    ("fan_mode", "fanMode"),
    ("heat_setpoint", "heatSetpoint"),
    ("cool_setpoint", "coolSetpoint"),
    ("speed_index", "speedIndex"),
    ("speed_level", "speedLevel"),
    ("active_zone", "activeZone"),
    ("zone_count", "zoneCount"),
    ("zone_names", "zoneNames"),
    ("energy_cur_level", "energyCurLevel"),
    ("energy_accum_total", "energyAccumTotal"),
    ("last_changed", "lastChanged"),
    ("last_successful_comm", "lastSuccessfulComm"),
)


def _serialize_device_detail(d):
    """Full single-device shape: slim fields + states + subclass attrs.

    Only used by get_device_by_id — the list tools keep the slim shape
    so a 500-row page doesn't balloon the prompt. Values go through
    the same JSON-safe coercion as the Z-Wave props (enums and
    datetimes degrade to strings, indigo.Dict/List to dict/list).
    """
    from tools.zwave import _json_safe

    out = _serialize_device(d)
    out["protocol"] = str(getattr(d, "protocol", "")) or None
    attr_read_errors = 0
    for key, attr in _DETAIL_ATTRS:
        try:
            value = getattr(d, attr, None)
        except Exception:
            # Count rather than fully swallow: a systemically broken
            # IOM must not read as "this device has no attributes".
            attr_read_errors += 1
            value = None
        if value is not None:
            if hasattr(value, "isoformat"):
                value = value.isoformat()  # match the zwave serializer
            out[key] = _json_safe(value)
    if attr_read_errors:
        out["attr_read_errors"] = attr_read_errors
    states = getattr(d, "states", None)
    if states is not None and hasattr(states, "items"):
        # Dot-suffixed sub-states (enum `state.option` booleans, `.ui`
        # display strings) duplicate their base state — drop them only
        # when the base key actually exists, so a genuine state id that
        # happens to contain a dot survives.
        keys = {str(k) for k in states}
        out["states"] = {
            str(k): _json_safe(v)
            for k, v in states.items()
            if "." not in str(k) or str(k).split(".", 1)[0] not in keys
        }
    return out


def _get_device_handler(args, indigo_module):
    """Return a single fully-serialised device by id (incl. states)."""
    device_id = _require_int_id(args)
    dev = _lookup_or_raise(indigo_module.devices, device_id, "device")
    return _serialize_device_detail(dev)


def _get_variable_handler(args, indigo_module):
    """Return a single serialised variable by id."""
    variable_id = _require_int_id(args)
    var = _lookup_or_raise(indigo_module.variables, variable_id, "variable")
    return _serialize_variable(var)


def _get_action_group_handler(args, indigo_module):
    """Return a single serialised action group by id."""
    group_id = _require_int_id(args)
    group = _lookup_or_raise(indigo_module.actionGroups, group_id, "action group")
    return _serialize_action_group(group)


def _paginate_devices(matched, limit, offset):
    """Bundle a filtered device list into the standard list envelope.

    Shared between get_devices_by_type and get_devices_by_state — both
    filter then paginate, so the wire shape and the slicing live here
    once.
    """
    total = len(matched)
    page = matched[offset:offset + limit]
    return {
        "results": [_serialize_device(d) for d in page],
        "total_count": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
    }


def _get_devices_by_type_handler(args, indigo_module):
    """Return paginated devices whose deviceTypeId matches ``device_type``."""
    device_type = args.get("device_type")
    if not isinstance(device_type, str):
        raise ValueError("device_type must be a string")
    limit, offset = _normalize_pagination(args)
    matched = [
        d for d in indigo_module.devices
        if getattr(d, "deviceTypeId", "") == device_type
    ]
    return _paginate_devices(matched, limit, offset)


def _get_devices_by_state_handler(args, indigo_module):
    """Return paginated devices matching a state spec.

    See ``tools.state_filter`` for the spec format. An empty (or
    missing) spec matches every device — symmetric with list_devices.
    """
    spec = args.get("state_spec", {})
    if not isinstance(spec, dict):
        raise ValueError("state_spec must be an object")
    limit, offset = _normalize_pagination(args)
    matched = [d for d in indigo_module.devices if _state_matches(d, spec)]
    return _paginate_devices(matched, limit, offset)
