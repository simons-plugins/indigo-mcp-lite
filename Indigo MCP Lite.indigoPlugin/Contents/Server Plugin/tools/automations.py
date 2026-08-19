"""Trigger & schedule tools — Indigo's automations, visible and controllable.

Before this module the MCP surface only served devices, variables,
and action groups — an agent couldn't see what automations exist,
let alone arm or fire them. Lookup mirrors ``tools/lookup.py``'s
envelope; control follows ``tools/control.py``'s thin-wrapper shape.

``indigo.trigger.execute`` honours conditions by default;
``ignore_conditions`` is surfaced but discouraged in the tool
description, matching the IOM's own guidance.
"""

from tools.control import _optional_delay_duration
from tools.lookup import (
    _lookup_or_raise,
    _normalize_pagination,
    _reject_unknown_args,
    _require_int_id,
)


def _serialize_automation(obj):
    """Stable dict shape shared by triggers and schedules.

    ``type`` is the Python class name (e.g. DeviceStateChangeTrigger)
    — the only reliable discriminator the IOM exposes.
    """
    out = {
        "id": obj.id,
        "name": obj.name,
        "type": type(obj).__name__,
        "description": getattr(obj, "description", ""),
        "enabled": getattr(obj, "enabled", None),
        "folder_id": getattr(obj, "folderId", 0),
    }
    next_execution = getattr(obj, "nextExecution", None)
    if next_execution is not None and hasattr(next_execution, "isoformat"):
        out["next_execution"] = next_execution.isoformat()
    return out


def _paginate(collection, args):
    limit, offset = _normalize_pagination(args)
    items = list(collection)
    total = len(items)
    page = items[offset:offset + limit]
    return {
        "results": [_serialize_automation(x) for x in page],
        "total_count": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
    }


def _optional_bool(args, key, default):
    raw = args.get(key, default)
    if not isinstance(raw, bool):
        raise ValueError(f"{key} must be a boolean")
    return raw


def register(handler, *, indigo_module, **_):
    """Register the trigger + schedule tools onto the given MCPHandler."""
    pagination_schema = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "offset": {"type": "integer", "minimum": 0},
        },
    }
    id_schema = {
        "type": "object",
        "required": ["id"],
        "properties": {"id": {"type": "integer"}},
    }
    enable_schema = {
        "type": "object",
        "required": ["id", "enabled"],
        "properties": {
            "id": {"type": "integer"},
            "enabled": {"type": "boolean"},
            "delay": {
                "type": "integer", "minimum": 0,
                "description": "Seconds to wait before applying.",
            },
            "duration": {
                "type": "integer", "minimum": 0,
                "description": (
                    "Seconds until Indigo fires the COMPLEMENTARY "
                    "action — the opposite of `enabled`, regardless of "
                    "the entity's prior state — e.g. disable with "
                    "duration=1209600 disarms for two weeks, then "
                    "Indigo re-enables it."
                ),
            },
        },
    }

    handler.register_tool(
        name="list_triggers",
        description=(
            "List Indigo triggers (id, name, type, enabled) with "
            "pagination. Triggers are event-driven automations. "
            "Metadata only — for what a trigger watches and does, "
            "call get_automation_contents(entity_type='trigger')."
        ),
        input_schema=pagination_schema,
        handler=lambda **args: _paginate(indigo_module.triggers, args),
    )
    handler.register_tool(
        name="get_trigger_by_id",
        description=(
            "Return a single Indigo trigger by id — METADATA only "
            "(name, folder, enabled, type). It does NOT return what "
            "the trigger watches or the actions it runs; "
            "get_automation_contents(entity_type='trigger') does."
        ),
        input_schema=id_schema,
        handler=lambda **args: _get_trigger_handler(args, indigo_module),
    )
    handler.register_tool(
        name="trigger_enable",
        description=(
            "Enable or disable an Indigo trigger by id. Optional "
            "duration fires the OPPOSITE command after N seconds — "
            "e.g. disable with duration auto re-enables (holiday "
            "mode)."
        ),
        input_schema=enable_schema,
        handler=lambda **args: _trigger_enable_handler(args, indigo_module),
    )
    handler.register_tool(
        name="trigger_execute",
        description=(
            "Execute a trigger's actions now. Conditions are honoured "
            "by default; set ignore_conditions ONLY for an explicit "
            "user-requested override."
        ),
        input_schema={
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "integer"},
                "ignore_conditions": {"type": "boolean", "default": False},
            },
        },
        handler=lambda **args: _trigger_execute_handler(args, indigo_module),
    )
    handler.register_tool(
        name="list_schedules",
        description=(
            "List Indigo schedules (id, name, enabled, next_execution) "
            "with pagination. Schedules are time-driven automations. "
            "next_execution is the next TIMESTAMP, not the firing "
            "rule — it cannot tell an absolute 06:00 schedule from "
            "one tracking sunrise. For the rule, call "
            "get_automation_contents(entity_type='schedule')."
        ),
        input_schema=pagination_schema,
        handler=lambda **args: _paginate(indigo_module.schedules, args),
    )
    handler.register_tool(
        name="get_schedule_by_id",
        description=(
            "Return a single Indigo schedule by id — METADATA only, "
            "plus next_execution. next_execution is the next "
            "TIMESTAMP, not the rule: it cannot show that a schedule "
            "named for 19:00 is actually set to 20:00. For WHEN it "
            "fires (absolute / sunrise / sunset + offset / countdown, "
            "and the day rule) and WHAT it runs, call "
            "get_automation_contents(entity_type='schedule')."
        ),
        input_schema=id_schema,
        handler=lambda **args: _get_schedule_handler(args, indigo_module),
    )
    handler.register_tool(
        name="schedule_enable",
        description=(
            "Enable or disable an Indigo schedule by id. Optional "
            "duration fires the OPPOSITE command after N seconds — "
            "e.g. disable with duration for a self-rearming holiday "
            "mode."
        ),
        input_schema=enable_schema,
        handler=lambda **args: _schedule_enable_handler(args, indigo_module),
    )
    handler.register_tool(
        name="schedule_execute",
        description=(
            "Execute a schedule's actions now (out of its normal "
            "timing). Conditions honoured by default."
        ),
        input_schema={
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "integer"},
                "ignore_conditions": {"type": "boolean", "default": False},
            },
        },
        handler=lambda **args: _schedule_execute_handler(args, indigo_module),
    )


def _get_trigger_handler(args, indigo_module):
    trigger = _lookup_or_raise(
        indigo_module.triggers, _require_int_id(args), "trigger"
    )
    return _serialize_automation(trigger)


def _get_schedule_handler(args, indigo_module):
    schedule = _lookup_or_raise(
        indigo_module.schedules, _require_int_id(args), "schedule"
    )
    return _serialize_automation(schedule)


def _trigger_enable_handler(args, indigo_module):
    _reject_unknown_args(args, ("id", "enabled", "delay", "duration"))
    entity_id = _require_int_id(args)
    _lookup_or_raise(indigo_module.triggers, entity_id, "trigger")
    indigo_module.trigger.enable(
        entity_id,
        value=_optional_bool(args, "enabled", None),
        **_optional_delay_duration(args),
    )
    return {"status": "ok"}


def _schedule_enable_handler(args, indigo_module):
    _reject_unknown_args(args, ("id", "enabled", "delay", "duration"))
    entity_id = _require_int_id(args)
    _lookup_or_raise(indigo_module.schedules, entity_id, "schedule")
    indigo_module.schedule.enable(
        entity_id,
        value=_optional_bool(args, "enabled", None),
        **_optional_delay_duration(args),
    )
    return {"status": "ok"}


def _trigger_execute_handler(args, indigo_module):
    _reject_unknown_args(args, ("id", "ignore_conditions"))
    _lookup_or_raise(indigo_module.triggers, _require_int_id(args), "trigger")
    indigo_module.trigger.execute(
        _require_int_id(args),
        ignoreConditions=_optional_bool(args, "ignore_conditions", False),
    )
    return {"status": "ok"}


def _schedule_execute_handler(args, indigo_module):
    _reject_unknown_args(args, ("id", "ignore_conditions"))
    _lookup_or_raise(indigo_module.schedules, _require_int_id(args), "schedule")
    indigo_module.schedule.execute(
        _require_int_id(args),
        ignoreConditions=_optional_bool(args, "ignore_conditions", False),
    )
    return {"status": "ok"}
