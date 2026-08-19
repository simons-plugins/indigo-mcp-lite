"""Automation-contents tools — what schedules/triggers/action groups DO.

The IOM answers "what automations exist" (``tools/automations.py``)
but not "what do they do". These tools read the Indigo database file
via ``indidb_reader.IndiDbReader`` and expose the decoded action
steps, condition trees, and embedded scripts:

- ``get_automation_contents``      — one automation, fully decoded
- ``find_automation_references``   — reverse index: which automations
  touch a given device/variable, role-tagged (acts_on /
  acts_on_via_props / condition / watches)
- ``list_automation_scripts``      — every embedded script in the DB
  with its owner (audit surface)

For schedules the decoded record also carries a ``schedule`` block —
the firing *rule* (absolute/sunrise/sunset/countdown + day rule), not
just the next timestamp the IOM reports.

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

# Why an acts_on_via_props pass did not run. Present in the response
# only when it was skipped, so a zero-reference answer can never be
# mistaken for a checked-and-clear one.
_PROPS_INFERENCE_NOTES = {
    "absent": (
        "skipped: no live object with this id, so a matching prop "
        "value could equally be an ordinary parameter (a level, a "
        "delay). Declared references above are unaffected."
    ),
    "unavailable": (
        "NOT CHECKED: the live-object lookup failed, so plugin action "
        "parameters were never searched and this result may be "
        "incomplete. Retry, or read `props` on plugin steps via "
        "get_automation_contents before concluding nothing uses this."
    ),
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
            "Plugin-action steps carry their parameters in `props` — "
            "and note a plugin step's target device is sometimes only "
            "in there (e.g. `dimmer_device_id`), so a missing "
            "device_id does NOT mean the step is device-less. For a "
            "schedule, `schedule` gives WHEN it fires (absolute time / "
            "sunrise / sunset + offset / countdown interval, plus the "
            "day rule) — the rule itself, which the single "
            "next_execution timestamp on get_schedule_by_id cannot "
            "convey. Complements get_trigger_by_id / "
            "get_schedule_by_id / get_action_group_by_id, which only "
            "return metadata."
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
            "which automations reference a device or variable. ONE "
            "call is the whole answer — `references` lists EVERY "
            "automation that touches it and `roles` says how, so do "
            "not filter to a single role or call another tool to "
            "complete the picture. Roles: acts_on (a declared action "
            "step), acts_on_via_props (a plugin action step naming it "
            "only inside its own parameters — `device-id`, "
            "`dimmer_device_id`, a comma-separated list — with "
            "`matched_props` naming the parameters that matched, so "
            "the inference is auditable), condition (in the condition "
            "tree), watches (a trigger fires on it). Both acts_on "
            "roles cover DIRECT references only: a schedule that runs "
            "an action group which acts on the device is reported via "
            "the action group, not the schedule — follow "
            "execute-action-group results (via get_automation_contents) "
            "to trace indirect chains. Indigo's own dependency check "
            "(get_dependencies) does NOT see the props case, so it "
            "can report zero dependents for a device that several "
            "action groups genuinely drive; this tool does see them. "
            "If a `props_inference` field is present, that pass did "
            "NOT run — a zero result is then not a clear answer, and "
            "the field says what to do about it. Pass exactly one of "
            "device_id or variable_id. Use before renaming/removing "
            "anything, or to answer 'what turns this on?'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "device_id": {"type": "integer"},
                "variable_id": {"type": "integer"},
            },
        },
        handler=lambda **args: _find_references_handler(
            args, indidb_reader, indigo_module
        ),
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
    if "schedule" in record:
        out["schedule"] = record["schedule"]
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


def _prop_value_matches(value, entity_id):
    """True if one decoded prop value names ``entity_id``.

    Props are plugin-defined, so an id arrives as an integer, as a
    bare string, or as one member of a comma-separated list
    (``sensorDevices`` style). Booleans are excluded even though
    ``isinstance(True, int)`` is True, and floats are never treated as
    ids — a ``real`` prop is a level or a setpoint, not a reference.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value == entity_id
    if isinstance(value, str):
        target = str(entity_id)
        return any(part.strip() == target for part in value.split(","))
    return False


def _iter_prop_matches(value, entity_id, path=""):
    """Yield the prop paths under ``value`` that name ``entity_id``.

    Paths are dotted, with ``[i]`` for vector members, so a match
    inside a nested dict stays auditable — the caller can see exactly
    which parameter produced the inference. A non-container value at
    the root (empty ``path``) never matches: only a named parameter
    counts as a reference.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _iter_prop_matches(child, entity_id, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_prop_matches(
                child, entity_id, f"{path}[{index}]"
            )
    elif path and _prop_value_matches(value, entity_id):
        yield path


def _step_prop_references(step, entity_id):
    """Prop paths in a step (and any nested group) naming entity_id."""
    matches = list(_iter_prop_matches(step.get("props"), entity_id))
    nested = step.get("action_group")
    if nested:  # never populated in raw reader records, but cheap guard
        for child in nested.get("steps", ()):
            matches.extend(_step_prop_references(child, entity_id))
    return matches


def _entity_presence(indigo_module, collection_attr, entity_id):
    """Whether ``entity_id`` names a live object: present / absent /
    unavailable.

    ``IndiDbReader.resolve_name`` deliberately collapses both failures
    into ``None`` — right for a display label, wrong here. Props
    matching is gated on this answer, so "there is no such device" and
    "the lookup itself failed" must not look alike: the first is a
    real answer, the second means the props pass never ran and the
    result may be incomplete.
    """
    collection = getattr(indigo_module, collection_attr, None)
    if collection is None:
        return "unavailable"
    try:
        collection[entity_id]
    except (KeyError, IndexError, ValueError, TypeError):
        return "absent"
    except Exception:
        return "unavailable"
    return "present"


def _reference_entry(record, kind, key, entity_id, match_props):
    """One ``references`` entry for ``record``, or None if it doesn't
    reference the entity at all."""
    roles = []
    matched_props = set()
    if any(_step_references(s, key, entity_id) for s in record["steps"]):
        roles.append("acts_on")
    if match_props:
        for step in record["steps"]:
            matched_props.update(_step_prop_references(step, entity_id))
    if matched_props:
        roles.append("acts_on_via_props")
    if _condition_references(record.get("conditions"), key, entity_id):
        roles.append("condition")
    if kind == "trigger" and record.get("watch", {}).get(key) == entity_id:
        roles.append("watches")
    if not roles:
        return None
    entry = {
        "automation_type": kind,
        "id": record["id"],
        "name": record["name"],
        "roles": roles,
    }
    if matched_props:
        entry["matched_props"] = sorted(matched_props)
    return entry


def _condition_references(node, key, entity_id):
    if node is None:
        return False
    children = node.get("conditions")
    if children is not None:
        return any(_condition_references(c, key, entity_id)
                   for c in children)
    return node.get(key) == entity_id


def _find_references_handler(args, reader, indigo_module):
    _reject_unknown_args(args, ("device_id", "variable_id"))
    supplied = [k for k in ("device_id", "variable_id") if k in args]
    if len(supplied) != 1:
        raise ValueError(
            "pass exactly one of device_id or variable_id"
        )
    key = supplied[0]
    entity_id = _require_int_id(args, key)
    collection = "devices" if key == "device_id" else "variables"
    # Props matching is INFERRED from raw parameter values rather than
    # read from a declared field, so it is gated on the id naming a
    # live object of the requested kind. Indigo ids are globally
    # unique across every object type, so a prop value equal to a real
    # device id IS that device whatever the key is called — which is
    # what makes value-matching safe without a per-plugin allowlist.
    # An id that names nothing has no such guarantee: it could collide
    # with an ordinary numeric parameter (a level, a delay), and
    # inventing references for it is worse than reporting none.
    presence = _entity_presence(indigo_module, collection, entity_id)
    data = reader.automations()
    references = []
    for kind in ("schedule", "trigger", "action_group"):
        for record in data[kind].values():
            entry = _reference_entry(
                record, kind, key, entity_id, presence == "present",
            )
            if entry is not None:
                references.append(entry)
    out = {
        key: entity_id,
        "name": reader.resolve_name(collection, entity_id),
        "references": references,
        "total_count": len(references),
    }
    # Never let a skipped inference pass read as a confident zero.
    if presence != "present":
        out["props_inference"] = _PROPS_INFERENCE_NOTES[presence]
    return _with_skip_count(out, data)


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
