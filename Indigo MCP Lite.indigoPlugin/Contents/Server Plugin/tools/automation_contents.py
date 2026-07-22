"""Automation-contents tools — what schedules/triggers/action groups DO.

The IOM answers "what automations exist" (``tools/automations.py``)
but not "what do they do". These tools read the Indigo database file
via ``indidb_reader.IndiDbReader`` and expose the decoded action
steps, condition trees, and embedded scripts:

- ``get_automation_contents``      — one automation, fully decoded
- ``find_automation_references``   — reverse index: which automations
  touch a given device/variable, role-tagged (acts_on / condition /
  watches)
- ``list_automation_scripts``      — every embedded script in the DB
  with its owner (audit surface)

The reader is created at registration but does no I/O until the first
tool call; parse results are cached on (path, mtime, size) inside the
reader. All failure modes (path unavailable, unreadable file,
mid-write parse error) surface as friendly ValueError → isError tool
results, and degraded parses report a ``skipped_automations`` count
rather than silently thinning results.
"""

import logging

from tools.lookup import _lookup_or_raise, _reject_unknown_args, _require_int_id

_ENTITY_TYPES = ("action_group", "schedule", "trigger")

# entity_type -> (IOM collection attr for name resolution, reader kind)
_KIND_LABELS = {
    "schedule": "schedule",
    "trigger": "trigger",
    "action_group": "action group",
}


def register(handler, *, indigo_module, indidb_reader=None, logger=None, **_):
    """Register the automation-contents tools onto the given MCPHandler.

    ``indidb_reader`` is injectable for tests; by default an
    ``IndiDbReader`` is constructed here — object construction is
    eager but cheap, no I/O happens until the first tool call touches
    the DB file. ``logger`` is the plugin's logger threaded through
    ``tool_registry.register_all``; degraded reader paths (skips,
    resolve failures, parse retries) log through it.
    """
    if indidb_reader is None:
        from indidb_reader import IndiDbReader
        indidb_reader = IndiDbReader(
            indigo_module=indigo_module,
            logger=logger or logging.getLogger("Plugin"),
        )

    handler.register_tool(
        name="get_automation_contents",
        description=(
            "Return WHAT an automation does: decoded action steps "
            "(device/variable/plugin/script/HVAC actions, in order) "
            "and its condition tree, read from the Indigo database "
            "file. entity_type is schedule, trigger, or action_group. "
            "Execute-action-group steps are expanded one level. "
            "Complements get_trigger_by_id / get_schedule_by_id / "
            "get_action_group_by_id, which only return metadata."
        ),
        input_schema={
            "type": "object",
            "required": ["entity_type", "id"],
            "properties": {
                "entity_type": {
                    "type": "string",
                    "enum": sorted(_ENTITY_TYPES),
                },
                "id": {"type": "integer"},
            },
        },
        handler=lambda **args: _get_contents_handler(args, indidb_reader),
    )
    handler.register_tool(
        name="find_automation_references",
        description=(
            "Reverse index over all schedules/triggers/action groups: "
            "which automations reference a device or variable, and in "
            "what role — acts_on (in an action step), condition (in "
            "the condition tree), watches (a trigger fires on it). "
            "acts_on covers DIRECT references only: a schedule that "
            "runs an action group which acts on the device is "
            "reported via the action group, not the schedule — "
            "follow execute-action-group results (via "
            "get_automation_contents) to trace indirect chains. "
            "Pass exactly one of device_id or variable_id. Use before "
            "renaming/removing anything, or to answer 'what turns "
            "this on?'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "device_id": {"type": "integer"},
                "variable_id": {"type": "integer"},
            },
        },
        handler=lambda **args: _find_references_handler(args, indidb_reader),
    )
    handler.register_tool(
        name="list_automation_scripts",
        description=(
            "List every embedded script across all schedules, "
            "triggers, and action groups in the Indigo database, with "
            "the owning automation's type/id/name. Sources longer "
            "than ~2000 chars are truncated (truncated: true). The "
            "audit surface for 'what Python is hiding in my "
            "automations?'."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=lambda **args: _list_scripts_handler(args, indidb_reader),
    )


# ---------------------------------------------------------------------
# Name resolution / serialization
# ---------------------------------------------------------------------

def _annotate_step(step, reader, expand_group_ids=True, seen=()):
    """Copy a step dict, resolving referenced ids to live IOM names.

    ``expand_group_ids`` controls the one-level expansion of
    execute-action-group steps; ``seen`` carries automation ids
    already on the expansion path so a group that executes itself
    (directly or via the requested entity) doesn't recurse.
    """
    out = dict(step)
    device_id = out.get("device_id")
    if device_id is not None:
        out["device_name"] = reader.resolve_name("devices", device_id)
    variable_id = out.get("variable_id")
    if variable_id is not None:
        out["variable_name"] = reader.resolve_name("variables", variable_id)
    group_id = out.get("action_group_id")
    if group_id is not None:
        out["action_group_name"] = reader.resolve_name(
            "actionGroups", group_id
        )
        if expand_group_ids and group_id not in seen:
            nested = reader.automations()["action_group"].get(group_id)
            if nested is not None:
                out["action_group"] = {
                    "id": nested["id"],
                    "name": nested["name"],
                    "steps": [
                        _annotate_step(s, reader, expand_group_ids=False)
                        for s in nested["steps"]
                    ],
                }
    return out


def _annotate_conditions(node, reader):
    """Copy a condition tree, resolving device/variable ids to names."""
    if node is None:
        return None
    out = dict(node)
    if "conditions" in out:
        out["conditions"] = [
            _annotate_conditions(child, reader)
            for child in out["conditions"]
        ]
        return out
    device_id = out.get("device_id")
    if device_id is not None:
        out["device_name"] = reader.resolve_name("devices", device_id)
    variable_id = out.get("variable_id")
    if variable_id is not None:
        out["variable_name"] = reader.resolve_name("variables", variable_id)
    return out


def _get_contents_handler(args, reader):
    _reject_unknown_args(args, ("entity_type", "id"))
    entity_type = args.get("entity_type")
    if entity_type not in _ENTITY_TYPES:
        raise ValueError(
            f"entity_type must be one of {sorted(_ENTITY_TYPES)}, "
            f"got {entity_type!r}"
        )
    entity_id = _require_int_id(args)
    data = reader.automations()
    record = _lookup_or_raise(
        data[entity_type], entity_id, _KIND_LABELS[entity_type],
    )
    seen = {entity_id} if entity_type == "action_group" else set()
    out = {
        "entity_type": entity_type,
        "id": record["id"],
        "name": record["name"],
        "folder_id": record.get("folder_id"),
        "steps": [
            _annotate_step(s, reader, seen=seen) for s in record["steps"]
        ],
        "conditions": _annotate_conditions(record.get("conditions"), reader),
    }
    if "enabled" in record:
        out["enabled"] = record["enabled"]
    if "watch" in record:
        out["watch"] = _annotate_watch(record["watch"], reader)
    return _with_skip_count(out, data)


def _with_skip_count(out, data):
    """Attach the reader's degraded-parse counter when nonzero, so a
    partially parsed database is visible in every tool response
    instead of silently thinning results."""
    skipped = data.get("skipped_automations", 0)
    if skipped:
        out["skipped_automations"] = skipped
    return out


def _annotate_watch(watch, reader):
    out = dict(watch)
    if out.get("device_id") is not None:
        out["device_name"] = reader.resolve_name("devices", out["device_id"])
    if out.get("variable_id") is not None:
        out["variable_name"] = reader.resolve_name(
            "variables", out["variable_id"]
        )
    return out


# ---------------------------------------------------------------------
# Reverse index
# ---------------------------------------------------------------------

def _step_references(step, key, entity_id):
    """True if a step acts on the given device/variable id."""
    if step.get(key) == entity_id:
        return True
    nested = step.get("action_group")
    if nested:  # never populated in raw reader records, but cheap guard
        return any(_step_references(s, key, entity_id)
                   for s in nested.get("steps", ()))
    return False


def _condition_references(node, key, entity_id):
    if node is None:
        return False
    children = node.get("conditions")
    if children is not None:
        return any(_condition_references(c, key, entity_id)
                   for c in children)
    return node.get(key) == entity_id


def _find_references_handler(args, reader):
    _reject_unknown_args(args, ("device_id", "variable_id"))
    supplied = [k for k in ("device_id", "variable_id") if k in args]
    if len(supplied) != 1:
        raise ValueError(
            "pass exactly one of device_id or variable_id"
        )
    key = supplied[0]
    entity_id = _require_int_id(args, key)
    data = reader.automations()
    references = []
    for kind in ("schedule", "trigger", "action_group"):
        for record in data[kind].values():
            roles = []
            if any(_step_references(s, key, entity_id)
                   for s in record["steps"]):
                roles.append("acts_on")
            if _condition_references(record.get("conditions"),
                                     key, entity_id):
                roles.append("condition")
            if kind == "trigger" and \
                    record.get("watch", {}).get(key) == entity_id:
                roles.append("watches")
            if roles:
                references.append({
                    "automation_type": kind,
                    "id": record["id"],
                    "name": record["name"],
                    "roles": roles,
                })
    collection = "devices" if key == "device_id" else "variables"
    return _with_skip_count({
        key: entity_id,
        "name": reader.resolve_name(collection, entity_id),
        "references": references,
        "total_count": len(references),
    }, data)


# ---------------------------------------------------------------------
# Script audit
# ---------------------------------------------------------------------

def _list_scripts_handler(args, reader):
    _reject_unknown_args(args, ())
    data = reader.automations()
    results = []
    for kind in ("schedule", "trigger", "action_group"):
        for record in data[kind].values():
            for index, step in enumerate(record["steps"]):
                if step.get("type") != "embedded_script":
                    continue
                results.append({
                    "owner_type": kind,
                    "owner_id": record["id"],
                    "owner_name": record["name"],
                    "step_index": index,
                    "script_type": step.get("script_type"),
                    "script_type_label": step.get("script_type_label"),
                    "source": step.get("source"),
                    "truncated": step.get("truncated", False),
                })
    return _with_skip_count(
        {"results": results, "total_count": len(results)}, data
    )
