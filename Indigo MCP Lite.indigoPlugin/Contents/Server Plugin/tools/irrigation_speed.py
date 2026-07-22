"""Sprinkler + speed-control tools, plus variable delete/move.

Control wave 3 from the 2026-07 gap audit — the last uncontrollable
device categories. Thin wrappers over ``indigo.sprinkler.*`` and
``indigo.speedcontrol.*`` in the ``tools/control.py`` shape.
"""

from tools.lookup import _require_int_id


def _optional_positive_int(args, key):
    raw = args.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError(f"{key} must be a positive integer")
    return raw


def register(handler, *, indigo_module, **_):
    """Register sprinkler, speed-control, and variable-management tools."""
    device_id_schema = {
        "type": "object",
        "required": ["device_id"],
        "properties": {"device_id": {"type": "integer"}},
    }

    # ----- sprinklers ----------------------------------------------------
    handler.register_tool(
        name="sprinkler_run_zone",
        description=(
            "Turn on ONE sprinkler zone (1-based index); any active zone "
            "stops first. Runs until sprinkler_stop or another zone starts."
        ),
        input_schema={
            "type": "object",
            "required": ["device_id", "zone"],
            "properties": {
                "device_id": {"type": "integer"},
                "zone": {"type": "integer", "minimum": 1},
            },
        },
        handler=lambda **args: _run_zone_handler(args, indigo_module),
    )
    handler.register_tool(
        name="sprinkler_run_schedule",
        description=(
            "Run a full watering schedule: a list of per-zone durations "
            "in MINUTES, one entry per zone in zone order (0 = skip). "
            "Zones run sequentially."
        ),
        input_schema={
            "type": "object",
            "required": ["device_id", "durations"],
            "properties": {
                "device_id": {"type": "integer"},
                "durations": {
                    "type": "array",
                    "items": {"type": "number", "minimum": 0},
                    "minItems": 1,
                },
            },
        },
        handler=lambda **args: _run_schedule_handler(args, indigo_module),
    )
    handler.register_tool(
        name="sprinkler_stop",
        description="Stop all watering on a sprinkler device (clears any paused schedule).",
        input_schema=device_id_schema,
        handler=lambda **args: _sprinkler_simple(args, indigo_module, "stop"),
    )
    handler.register_tool(
        name="sprinkler_pause",
        description="Pause the currently running sprinkler schedule (resume continues where it left off).",
        input_schema=device_id_schema,
        handler=lambda **args: _sprinkler_simple(args, indigo_module, "pause"),
    )
    handler.register_tool(
        name="sprinkler_resume",
        description="Resume a paused sprinkler schedule.",
        input_schema=device_id_schema,
        handler=lambda **args: _sprinkler_simple(args, indigo_module, "resume"),
    )
    handler.register_tool(
        name="sprinkler_next_zone",
        description="Skip to the next zone in the running schedule.",
        input_schema=device_id_schema,
        handler=lambda **args: _sprinkler_simple(args, indigo_module, "nextZone"),
    )
    handler.register_tool(
        name="sprinkler_previous_zone",
        description="Go back to the previous zone in the running schedule.",
        input_schema=device_id_schema,
        handler=lambda **args: _sprinkler_simple(
            args, indigo_module, "previousZone"
        ),
    )

    # ----- speed control (fans) ------------------------------------------
    handler.register_tool(
        name="speedcontrol_set_index",
        description=(
            "Set a speed-control device (e.g. ceiling fan) to a discrete "
            "speed index: 0=off, then 1..N per the device's speed count."
        ),
        input_schema={
            "type": "object",
            "required": ["device_id", "index"],
            "properties": {
                "device_id": {"type": "integer"},
                "index": {"type": "integer", "minimum": 0},
            },
        },
        handler=lambda **args: _set_speed_index_handler(args, indigo_module),
    )
    handler.register_tool(
        name="speedcontrol_set_level",
        description="Set a speed-control device to a 0-100 percent speed level.",
        input_schema={
            "type": "object",
            "required": ["device_id", "level"],
            "properties": {
                "device_id": {"type": "integer"},
                "level": {"type": "integer", "minimum": 0, "maximum": 100},
            },
        },
        handler=lambda **args: _set_speed_level_handler(args, indigo_module),
    )
    handler.register_tool(
        name="speedcontrol_increase",
        description="Increase a speed-control device's speed index (optional `by`).",
        input_schema={
            "type": "object",
            "required": ["device_id"],
            "properties": {
                "device_id": {"type": "integer"},
                "by": {"type": "integer", "minimum": 1},
            },
        },
        handler=lambda **args: _speed_step_handler(
            args, indigo_module, "increaseSpeedIndex"
        ),
    )
    handler.register_tool(
        name="speedcontrol_decrease",
        description="Decrease a speed-control device's speed index (optional `by`).",
        input_schema={
            "type": "object",
            "required": ["device_id"],
            "properties": {
                "device_id": {"type": "integer"},
                "by": {"type": "integer", "minimum": 1},
            },
        },
        handler=lambda **args: _speed_step_handler(
            args, indigo_module, "decreaseSpeedIndex"
        ),
    )

    # ----- variables ------------------------------------------------------
    handler.register_tool(
        name="variable_delete",
        description=(
            "Delete an Indigo variable by id. PERMANENT — check "
            "dependencies (triggers/control pages referencing it) with "
            "the user before deleting anything you didn't create."
        ),
        input_schema={
            "type": "object",
            "required": ["variable_id"],
            "properties": {"variable_id": {"type": "integer"}},
        },
        handler=lambda **args: _variable_delete_handler(args, indigo_module),
    )
    handler.register_tool(
        name="variable_move_to_folder",
        description="Move a variable into a folder (see list_variable_folders).",
        input_schema={
            "type": "object",
            "required": ["variable_id", "folder_id"],
            "properties": {
                "variable_id": {"type": "integer"},
                "folder_id": {"type": "integer"},
            },
        },
        handler=lambda **args: _variable_move_handler(args, indigo_module),
    )


def _run_zone_handler(args, indigo_module):
    device_id = _require_int_id(args, "device_id")
    zone = args.get("zone")
    if isinstance(zone, bool) or not isinstance(zone, int) or zone < 1:
        raise ValueError("zone must be a 1-based integer zone index")
    indigo_module.sprinkler.setActiveZone(device_id, index=zone)
    return {"status": "ok"}


def _run_schedule_handler(args, indigo_module):
    device_id = _require_int_id(args, "device_id")
    durations = args.get("durations")
    if (
        not isinstance(durations, list)
        or not durations
        or not all(
            isinstance(d, (int, float)) and not isinstance(d, bool) and d >= 0
            for d in durations
        )
    ):
        raise ValueError(
            "durations must be a non-empty list of non-negative minutes, "
            "one entry per zone"
        )
    indigo_module.sprinkler.run(device_id, schedule=list(durations))
    return {"status": "ok"}


def _sprinkler_simple(args, indigo_module, method):
    device_id = _require_int_id(args, "device_id")
    getattr(indigo_module.sprinkler, method)(device_id)
    return {"status": "ok"}


def _set_speed_index_handler(args, indigo_module):
    device_id = _require_int_id(args, "device_id")
    index = args.get("index")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("index must be a non-negative integer")
    indigo_module.speedcontrol.setSpeedIndex(device_id, value=index)
    return {"status": "ok"}


def _set_speed_level_handler(args, indigo_module):
    device_id = _require_int_id(args, "device_id")
    level = args.get("level")
    if isinstance(level, bool) or not isinstance(level, int) \
            or not 0 <= level <= 100:
        raise ValueError("level must be an integer 0-100")
    indigo_module.speedcontrol.setSpeedLevel(device_id, value=level)
    return {"status": "ok"}


def _speed_step_handler(args, indigo_module, method):
    device_id = _require_int_id(args, "device_id")
    by = _optional_positive_int(args, "by")
    kwargs = {"by": by} if by is not None else {}
    getattr(indigo_module.speedcontrol, method)(device_id, **kwargs)
    return {"status": "ok"}


def _variable_delete_handler(args, indigo_module):
    indigo_module.variable.delete(_require_int_id(args, "variable_id"))
    return {"status": "ok"}


def _variable_move_handler(args, indigo_module):
    indigo_module.variable.moveToFolder(
        _require_int_id(args, "variable_id"),
        value=_require_int_id(args, "folder_id"),
    )
    return {"status": "ok"}
