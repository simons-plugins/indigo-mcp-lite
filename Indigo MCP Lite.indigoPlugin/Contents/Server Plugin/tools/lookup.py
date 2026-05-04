"""Lookup tools — read-only primitives over Indigo's object model.

Each tool wraps a single Indigo iter / lookup; no business logic.
``_normalize_pagination`` and ``_serialize_device`` are deliberately
top-level so the next list/get tools (list_variables,
list_action_groups, get_devices_by_type, get_devices_by_state, etc.)
share the same wire shape.
"""


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
        handler=lambda args: _list_devices_handler(args, indigo_module),
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
