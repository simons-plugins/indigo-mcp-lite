"""Dependency + device-group introspection tools.

``get_dependencies`` answers "what references this?" before a delete
or disable — the IOM exposes it on all five entity namespaces.
``get_device_group`` resolves multi-endpoint modules (common on
Z-Wave) to their group members and root device, since properties
like ``batteryLevel`` live only on the root.
"""

from tools.lookup import (
    _lookup_or_raise,
    _reject_unknown_args,
    _require_int_id,
    _serialize_device,
)
from tools.zwave import _json_safe

# entity_type value -> (command namespace attr, collection attr)
_NAMESPACES = {
    "device": ("device", "devices"),
    "variable": ("variable", "variables"),
    "trigger": ("trigger", "triggers"),
    "schedule": ("schedule", "schedules"),
    "action_group": ("actionGroup", "actionGroups"),
}


def register(handler, *, indigo_module, **_):
    """Register the introspection tools onto the given MCPHandler."""
    handler.register_tool(
        name="get_dependencies",
        description=(
            "List everything that references an entity (triggers, "
            "schedules, action groups, control pages, devices, "
            "variables). Check this BEFORE variable_delete or "
            "device_enable(false) — deleting something with dependents "
            "breaks the automations that use it."
        ),
        input_schema={
            "type": "object",
            "required": ["entity_type", "id"],
            "properties": {
                "entity_type": {
                    "type": "string",
                    "enum": sorted(_NAMESPACES),
                },
                "id": {"type": "integer"},
            },
        },
        handler=lambda **args: _dependencies_handler(args, indigo_module),
    )
    handler.register_tool(
        name="get_device_group",
        description=(
            "Resolve a grouped (multi-endpoint) device to its group: "
            "member device ids plus the serialised root device. Some "
            "properties (battery_level especially) exist only on the "
            "root — use this when a child endpoint shows null battery."
        ),
        input_schema={
            "type": "object",
            "required": ["device_id"],
            "properties": {"device_id": {"type": "integer"}},
        },
        handler=lambda **args: _device_group_handler(args, indigo_module),
    )


def _dependencies_handler(args, indigo_module):
    _reject_unknown_args(args, ("entity_type", "id"))
    entity_type = args.get("entity_type")
    if entity_type not in _NAMESPACES:
        raise ValueError(
            f"entity_type must be one of {sorted(_NAMESPACES)}, "
            f"got {entity_type!r}"
        )
    entity_id = _require_int_id(args)
    command_ns, collection = _NAMESPACES[entity_type]
    _lookup_or_raise(
        getattr(indigo_module, collection), entity_id, entity_type
    )
    raw = getattr(indigo_module, command_ns).getDependencies(entity_id)
    dependencies = _json_safe(raw)
    if not isinstance(dependencies, dict):
        dependencies = {"raw": dependencies}
    total = sum(
        len(v) for v in dependencies.values() if isinstance(v, list)
    )
    return {
        "entity_type": entity_type,
        "id": entity_id,
        "dependencies": dependencies,
        "total_dependents": total,
    }


def _device_group_handler(args, indigo_module):
    _reject_unknown_args(args, ("device_id",))
    device_id = _require_int_id(args, "device_id")
    _lookup_or_raise(indigo_module.devices, device_id, "device")
    group = _json_safe(indigo_module.device.getGroupList(device_id))
    if not isinstance(group, list):
        group = []
    group_ids = [g for g in group if isinstance(g, int)]
    result = {"device_id": device_id, "group_ids": group_ids}
    if group_ids:
        try:
            root = indigo_module.devices[group_ids[0]]
            root_out = _serialize_device(root)
            root_out["battery_level"] = getattr(root, "batteryLevel", None)
            result["root_device"] = root_out
        except (KeyError, IndexError, ValueError):
            pass  # group root vanished between calls; ids still useful
    return result
