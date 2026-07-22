"""Z-Wave lookup tools — read-only details for Z-Wave devices.

Z-Wave devices are native Indigo devices (``dev.protocol ==
indigo.kProtocol.ZWave``), not plugin devices, so the interesting
metadata lives on base-class properties (``batteryLevel``,
``address`` = node id, ``version`` = firmware) plus whatever the
Z-Wave interface stores in ``globalProps`` under its own plugin id
(listening/battery-powered flags, endpoint info, manufacturer ids).
Nothing here writes to the network.
"""

from tools.lookup import (
    _lookup_or_raise,
    _normalize_pagination,
    _require_int_id,
    _serialize_device,
)


def _is_zwave(d, indigo_module):
    return getattr(d, "protocol", None) == indigo_module.kProtocol.ZWave


def _json_safe(value, _depth=0):
    """Recursively convert indigo.Dict / indigo.List containers into
    plain JSON-serialisable dicts/lists. Unknown leaf types (enums,
    datetimes) degrade to ``str`` rather than raising, since
    globalProps content is owned by the Z-Wave interface and can
    change shape between Indigo releases."""
    if _depth > 6:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "items"):
        return {str(k): _json_safe(v, _depth + 1) for k, v in value.items()}
    if hasattr(value, "__iter__"):
        return [_json_safe(v, _depth + 1) for v in value]
    return str(value)


def _serialize_zwave_device(d):
    """Standard device shape plus the Z-Wave-relevant base-class props."""
    out = _serialize_device(d)
    out["battery_level"] = getattr(d, "batteryLevel", None)
    out["firmware_version"] = _json_safe(getattr(d, "version", None))
    out["enabled"] = getattr(d, "enabled", None)
    out["error_state"] = getattr(d, "errorState", "")
    last = getattr(d, "lastChanged", None)
    out["last_changed"] = last.isoformat() if hasattr(last, "isoformat") else None
    return out


def _zwave_global_props(d):
    """Extract the Z-Wave interface's sub-dict(s) from globalProps.

    Matched by plugin-id substring rather than a hardcoded id so a
    rename of the interface's internal id between Indigo releases
    degrades to an empty dict, not a wrong-key miss."""
    global_props = getattr(d, "globalProps", None)
    if global_props is None or not hasattr(global_props, "items"):
        return {}
    return {
        str(key): _json_safe(sub)
        for key, sub in global_props.items()
        if "zwave" in str(key).lower()
    }


def register(handler, *, indigo_module, **_):
    """Register the Z-Wave lookup tools onto the given MCPHandler."""
    handler.register_tool(
        name="list_zwave_devices",
        description=(
            "List Z-Wave devices (protocol == ZWave) with node address, "
            "battery level, firmware version, and error state. "
            "Use get_zwave_device_details for one device's full metadata."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "offset": {"type": "integer", "minimum": 0},
            },
        },
        handler=lambda **args: _list_zwave_devices_handler(args, indigo_module),
    )
    handler.register_tool(
        name="get_zwave_device_details",
        description=(
            "Return full Z-Wave metadata for one device: node address, "
            "battery level, firmware version, error state, and the raw "
            "Z-Wave interface properties (listening/battery flags, "
            "manufacturer info) from globalProps."
        ),
        input_schema={
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "integer"}},
        },
        handler=lambda **args: _get_zwave_details_handler(args, indigo_module),
    )


def _list_zwave_devices_handler(args, indigo_module):
    """Return the standard paginated envelope over Z-Wave devices only."""
    limit, offset = _normalize_pagination(args)
    matched = [d for d in indigo_module.devices if _is_zwave(d, indigo_module)]
    total = len(matched)
    page = matched[offset:offset + limit]
    return {
        "results": [_serialize_zwave_device(d) for d in page],
        "total_count": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
    }


def _get_zwave_details_handler(args, indigo_module):
    """Return one Z-Wave device's summary plus its raw interface props."""
    device_id = _require_int_id(args)
    dev = _lookup_or_raise(indigo_module.devices, device_id, "device")
    if not _is_zwave(dev, indigo_module):
        raise ValueError(
            f"device {device_id} is not a Z-Wave device "
            f"(protocol={getattr(dev, 'protocol', None)})"
        )
    result = _serialize_zwave_device(dev)
    result["zwave_props"] = _zwave_global_props(dev)
    return result
