"""Control tools — write operations on Indigo entities.

One module for every Phase 4 control tool. Each tool is a thin
wrapper over a single ``indigo.<namespace>.<method>`` call: argument
coercion via the helpers in ``tools.value_helpers`` /
``tools.color_palette``, integer-id validation reused from
``tools.lookup``, and a ``{"status": "ok"}`` shape on success.

All tools register with ``handler.register_tool`` using the
``lambda **args: _handler(args, indigo_module)`` shape — never
``lambda args:``. ``mcp_handler.dispatch_tool`` calls the lambda as
``handler(**tool_args)`` and the ``args`` form raises
``TypeError: <lambda>() got an unexpected keyword argument …`` on
every real wire call. Phase 3 caught this; preserve it here.
"""

from color_palette import lookup_named_color
from tools.lookup import _require_int_id
from tools.value_helpers import (
    byte_to_percent,
    clamp_percent,
    normalize_brightness,
    parse_hex_color,
)


def _require_numeric(args, key):
    """Pull a numeric value out of args under ``key`` or raise ValueError.

    Bools are rejected (same reason as ``_require_int_id`` — they
    silently coerce). Strings are rejected even if they parse,
    because at the JSON-RPC layer the schema declared ``"number"``
    and a stringly-typed value implies a client bug worth surfacing.
    """
    raw = args.get(key)
    if raw is None or isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{key} must be a number")
    return raw


def _optional_delay_duration(args):
    """Extract optional ``delay``/``duration`` seconds as kwargs.

    Only keys the caller actually supplied are forwarded, so the
    Indigo command sees its own defaults otherwise. Bools rejected,
    negatives rejected — a negative delay is always a caller bug.
    """
    out = {}
    for key in ("delay", "duration"):
        raw = args.get(key)
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise ValueError(f"{key} must be a non-negative integer (seconds)")
        out[key] = raw
    return out


def _turn_on_handler(args, indigo_module):
    """Turn a device on by id, optionally delayed / for a duration."""
    indigo_module.device.turnOn(
        _require_int_id(args, "device_id"), **_optional_delay_duration(args)
    )
    return {"status": "ok"}


def _turn_off_handler(args, indigo_module):
    """Turn a device off by id, optionally delayed / for a duration."""
    indigo_module.device.turnOff(
        _require_int_id(args, "device_id"), **_optional_delay_duration(args)
    )
    return {"status": "ok"}


def _toggle_handler(args, indigo_module):
    """Toggle a device on<->off, optionally delayed / for a duration."""
    indigo_module.device.toggle(
        _require_int_id(args, "device_id"), **_optional_delay_duration(args)
    )
    return {"status": "ok"}


def _enable_handler(args, indigo_module):
    """Enable or disable a device's communication."""
    enabled = args.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    indigo_module.device.enable(
        _require_int_id(args, "device_id"), value=enabled
    )
    return {"status": "ok"}


def _status_request_handler(args, indigo_module):
    """Ask the hardware to refresh its status in Indigo."""
    indigo_module.device.statusRequest(
        _require_int_id(args, "device_id"), suppressLogging=True
    )
    return {"status": "ok"}


def _lock_handler(args, indigo_module):
    """Lock a lock-subtype relay device."""
    indigo_module.device.lock(
        _require_int_id(args, "device_id"), **_optional_delay_duration(args)
    )
    return {"status": "ok"}


def _unlock_handler(args, indigo_module):
    """Unlock a lock-subtype relay device."""
    indigo_module.device.unlock(
        _require_int_id(args, "device_id"), **_optional_delay_duration(args)
    )
    return {"status": "ok"}


def _relative_brightness_kwargs(args):
    """Extract optional ``by`` (1-100) and ``delay`` for brighten/dim."""
    out = {}
    raw_by = args.get("by")
    if raw_by is not None:
        if isinstance(raw_by, bool) or not isinstance(raw_by, int) \
                or not 1 <= raw_by <= 100:
            raise ValueError("by must be an integer 1-100")
        out["by"] = raw_by
    delay = _optional_delay_duration(args)
    if "duration" in delay:
        raise ValueError("brighten/dim take delay only, not duration")
    out.update(delay)
    return out


def _brighten_handler(args, indigo_module):
    """Brighten a dimmer relative to its current level."""
    indigo_module.dimmer.brighten(
        _require_int_id(args, "device_id"), **_relative_brightness_kwargs(args)
    )
    return {"status": "ok"}


def _dim_handler(args, indigo_module):
    """Dim a dimmer relative to its current level."""
    indigo_module.dimmer.dim(
        _require_int_id(args, "device_id"), **_relative_brightness_kwargs(args)
    )
    return {"status": "ok"}


def _ping_handler(args, indigo_module):
    """Ping a Z-Wave/Insteon module and report round-trip time."""
    result = indigo_module.device.ping(
        _require_int_id(args, "device_id"), suppressLogging=True
    )
    return {
        "success": bool(result["Success"]),
        "time_ms": result["TimeDelta"] if result["Success"] else None,
    }


def _beep_handler(args, indigo_module):
    """Ask the device to emit an audible beep (hardware permitting)."""
    indigo_module.device.beep(_require_int_id(args, "device_id"))
    return {"status": "ok"}


def _reset_energy_handler(args, indigo_module):
    """Reset a device's accumulated-energy counter."""
    indigo_module.device.resetEnergyAccumTotal(
        _require_int_id(args, "device_id")
    )
    return {"status": "ok"}


def _set_brightness_handler(args, indigo_module):
    """Set a dimmer's brightness 0-100 (accepts 0-1 floats too)."""
    device_id = _require_int_id(args, "device_id")
    brightness = normalize_brightness(_require_numeric(args, "brightness"))
    indigo_module.dimmer.setBrightness(device_id, value=brightness)
    return {"status": "ok"}


def _require_rgb_channels(args, *, percent):
    """Pull and normalise red/green/blue channels from args.

    Both RGB tools share required-key validation and channel
    normalisation. ``percent=True`` means inputs are 0-100 (clamp +
    round); ``percent=False`` means inputs are 0-255 bytes that need
    converting to Indigo's 0-100 wire unit. Either way the return is
    the (red, green, blue) percent tuple ready for ``setColorLevels``.
    """
    out = []
    for channel in ("red", "green", "blue"):
        raw = _require_numeric(args, channel)
        out.append(clamp_percent(raw) if percent else byte_to_percent(raw))
    return tuple(out)


def _set_rgb_color_handler(args, indigo_module):
    """Set RGB colour from 0-255 byte channels."""
    device_id = _require_int_id(args, "device_id")
    r, g, b = _require_rgb_channels(args, percent=False)
    indigo_module.dimmer.setColorLevels(
        device_id, redLevel=r, greenLevel=g, blueLevel=b
    )
    return {"status": "ok"}


def _set_rgb_percent_handler(args, indigo_module):
    """Set RGB colour from 0-100 percent channels."""
    device_id = _require_int_id(args, "device_id")
    r, g, b = _require_rgb_channels(args, percent=True)
    indigo_module.dimmer.setColorLevels(
        device_id, redLevel=r, greenLevel=g, blueLevel=b
    )
    return {"status": "ok"}


def _require_str(args, key, *, allow_empty=False):
    """Pull a string out of args under ``key`` or raise.

    Bools are rejected to match ``_require_int_id`` (they coerce
    silently in some contexts and a stringly-typed bool is almost
    always a caller bug). ``allow_empty`` defaults to False — most
    callers (named colour, hex colour, variable name) want a
    non-empty string. ``variable_update``/``variable_create`` set
    ``allow_empty=True`` so callers can clear a variable's value.
    """
    raw = args.get(key)
    if isinstance(raw, bool) or not isinstance(raw, str):
        raise ValueError(f"{key} must be a string")
    if not allow_empty and not raw:
        raise ValueError(f"{key} must be a non-empty string")
    return raw


def _set_color_from_bytes(indigo_module, device_id, rgb_bytes):
    """Convert a (r, g, b) byte tuple to percent and call setColorLevels.

    Used by both hex and named-colour tools so the byte→percent
    conversion lives in exactly one place.
    """
    r, g, b = (byte_to_percent(c) for c in rgb_bytes)
    indigo_module.dimmer.setColorLevels(
        device_id, redLevel=r, greenLevel=g, blueLevel=b
    )


def _set_hex_color_handler(args, indigo_module):
    """Set RGB colour from a hex string (#RRGGBB / RRGGBB / #RGB)."""
    device_id = _require_int_id(args, "device_id")
    rgb_bytes = parse_hex_color(_require_str(args, "color"))
    _set_color_from_bytes(indigo_module, device_id, rgb_bytes)
    return {"status": "ok"}


def _set_named_color_handler(args, indigo_module):
    """Set RGB colour from a CSS named colour (red, aliceblue, …)."""
    device_id = _require_int_id(args, "device_id")
    rgb_bytes = lookup_named_color(_require_str(args, "name"))
    _set_color_from_bytes(indigo_module, device_id, rgb_bytes)
    return {"status": "ok"}


_FAN_MODE_NAMES = {
    "auto": "Auto",
    "alwayson": "AlwaysOn",
}


_HVAC_MODE_NAMES = {
    "off": "Off",
    "heat": "Heat",
    "cool": "Cool",
    "auto": "HeatCool",          # friendly alias
    "heatcool": "HeatCool",
    "programheat": "ProgramHeat",
    "programcool": "ProgramCool",
    "programheatcool": "ProgramHeatCool",
}


def _resolve_mode(args, name_map, *, mode_label):
    """Resolve a friendly-string mode to a (raw, attr-name) pair.

    Lower-cases input, strips spaces/underscores, looks up in
    ``name_map``. Shared between hvac/fan mode tools so the
    case-/space-insensitive matching lives once.
    """
    raw = args.get("mode")
    if not isinstance(raw, str):
        raise ValueError("mode must be a string")
    key = raw.lower().replace("_", "").replace(" ", "")
    if key not in name_map:
        valid = ", ".join(sorted(name_map))
        raise ValueError(
            f"mode {raw!r} not recognised for {mode_label}; valid: {valid}"
        )
    return name_map[key]


def _set_fan_mode_handler(args, indigo_module):
    """Set a thermostat's fan mode (auto / alwayson)."""
    device_id = _require_int_id(args, "device_id")
    attr = _resolve_mode(args, _FAN_MODE_NAMES, mode_label="fan")
    enum_value = getattr(indigo_module.kFanMode, attr)
    indigo_module.thermostat.setFanMode(device_id, value=enum_value)
    return {"status": "ok"}


def _set_hvac_mode_handler(args, indigo_module):
    """Set a thermostat's HVAC mode by friendly-string name.

    The SDK takes ``indigo.kHvacMode.X`` enum constants — we accept
    case-insensitive friendly names (off / heat / cool / auto /
    heatcool / programheat / programcool / programheatcool) and
    resolve to the right attribute via ``getattr``. ``auto`` is an
    alias for ``HeatCool`` since "auto" is what most thermostats
    label the dual-mode setting.
    """
    device_id = _require_int_id(args, "device_id")
    attr = _resolve_mode(args, _HVAC_MODE_NAMES, mode_label="hvac")
    enum_value = getattr(indigo_module.kHvacMode, attr)
    indigo_module.thermostat.setHvacMode(device_id, value=enum_value)
    return {"status": "ok"}


def _set_heat_setpoint_handler(args, indigo_module):
    """Set a thermostat's heating setpoint to ``temperature``."""
    device_id = _require_int_id(args, "device_id")
    temperature = _require_numeric(args, "temperature")
    indigo_module.thermostat.setHeatSetpoint(device_id, value=temperature)
    return {"status": "ok"}


def _set_cool_setpoint_handler(args, indigo_module):
    """Set a thermostat's cooling setpoint to ``temperature``."""
    device_id = _require_int_id(args, "device_id")
    temperature = _require_numeric(args, "temperature")
    indigo_module.thermostat.setCoolSetpoint(device_id, value=temperature)
    return {"status": "ok"}


def _variable_create_handler(args, indigo_module):
    """Create a new Indigo variable and return its serialised shape.

    Both ``name`` and ``value`` are required; ``value`` may be empty
    (Indigo treats variable values as strings on the wire and an
    empty string is a valid initial state). ``folder`` is optional
    and only forwarded when present so we don't override Indigo's
    default ("(no folder)") with an explicit zero/None.
    """
    name = _require_str(args, "name")
    value = _require_str(args, "value", allow_empty=True)
    kwargs = {"value": value}
    if "folder" in args:
        kwargs["folder"] = _require_int_id(args, "folder")
    var = indigo_module.variable.create(name, **kwargs)
    return {
        "status": "ok",
        "id": getattr(var, "id", None),
        "name": getattr(var, "name", name),
        "value": getattr(var, "value", value),
        "folder_id": getattr(var, "folderId", 0),
    }


def _variable_update_handler(args, indigo_module):
    """Update an existing Indigo variable's value to ``value``.

    Empty string is permitted — it's the canonical way to "clear" a
    variable in Indigo's model.
    """
    variable_id = _require_int_id(args, "variable_id")
    value = _require_str(args, "value", allow_empty=True)
    indigo_module.variable.updateValue(variable_id, value=value)
    return {"status": "ok"}


def _action_execute_group_handler(args, indigo_module):
    """Execute an Indigo action group by id."""
    action_group_id = _require_int_id(args, "action_group_id")
    indigo_module.actionGroup.execute(action_group_id)
    return {"status": "ok"}


def _set_white_levels_handler(args, indigo_module):
    """Set warm/cool white levels and/or colour temperature.

    All three keys (``white``, ``white2``, ``temperature``) are
    optional individually but at least one must be present —
    otherwise the SDK call would have nothing to set and the tool
    would silently no-op. White levels go through ``clamp_percent``
    (0-100); ``temperature`` is passed through as Kelvin since
    devices vary in supported range (typically 2000-6500).
    """
    device_id = _require_int_id(args, "device_id")
    kwargs = {}
    if "white" in args:
        kwargs["whiteLevel"] = clamp_percent(_require_numeric(args, "white"))
    if "white2" in args:
        kwargs["whiteLevel2"] = clamp_percent(_require_numeric(args, "white2"))
    if "temperature" in args:
        kwargs["whiteTemperature"] = int(_require_numeric(args, "temperature"))
    if not kwargs:
        raise ValueError(
            "must supply at least one of: white, white2, temperature"
        )
    indigo_module.dimmer.setColorLevels(device_id, **kwargs)
    return {"status": "ok"}


_DEVICE_ID_SCHEMA = {
    "type": "object",
    "required": ["device_id"],
    "properties": {"device_id": {"type": "integer"}},
}

_DELAY_DURATION_PROPS = {
    "delay": {
        "type": "integer", "minimum": 0,
        "description": "Seconds to wait before executing.",
    },
    "duration": {
        "type": "integer", "minimum": 0,
        "description": "Seconds until the device reverts to its prior state.",
    },
}

_DEVICE_ID_DELAY_DURATION_SCHEMA = {
    "type": "object",
    "required": ["device_id"],
    "properties": {"device_id": {"type": "integer"}, **_DELAY_DURATION_PROPS},
}

_RELATIVE_BRIGHTNESS_SCHEMA = {
    "type": "object",
    "required": ["device_id"],
    "properties": {
        "device_id": {"type": "integer"},
        "by": {
            "type": "integer", "minimum": 1, "maximum": 100,
            "description": "Relative brightness change (default: Indigo's step).",
        },
        "delay": _DELAY_DURATION_PROPS["delay"],
    },
}

_BRIGHTNESS_SCHEMA = {
    "type": "object",
    "required": ["device_id", "brightness"],
    "properties": {
        "device_id": {"type": "integer"},
        "brightness": {"type": "number"},
    },
}

_RGB_SCHEMA = {
    "type": "object",
    "required": ["device_id", "red", "green", "blue"],
    "properties": {
        "device_id": {"type": "integer"},
        "red": {"type": "number"},
        "green": {"type": "number"},
        "blue": {"type": "number"},
    },
}

_HEX_COLOR_SCHEMA = {
    "type": "object",
    "required": ["device_id", "color"],
    "properties": {
        "device_id": {"type": "integer"},
        "color": {"type": "string"},
    },
}

_NAMED_COLOR_SCHEMA = {
    "type": "object",
    "required": ["device_id", "name"],
    "properties": {
        "device_id": {"type": "integer"},
        "name": {"type": "string"},
    },
}

_MODE_SCHEMA = {
    "type": "object",
    "required": ["device_id", "mode"],
    "properties": {
        "device_id": {"type": "integer"},
        "mode": {"type": "string"},
    },
}

_TEMPERATURE_SCHEMA = {
    "type": "object",
    "required": ["device_id", "temperature"],
    "properties": {
        "device_id": {"type": "integer"},
        "temperature": {"type": "number"},
    },
}

_ACTION_GROUP_ID_SCHEMA = {
    "type": "object",
    "required": ["action_group_id"],
    "properties": {"action_group_id": {"type": "integer"}},
}

_VARIABLE_CREATE_SCHEMA = {
    "type": "object",
    "required": ["name", "value"],
    "properties": {
        "name": {"type": "string"},
        "value": {"type": "string"},
        "folder": {"type": "integer"},
    },
}

_VARIABLE_UPDATE_SCHEMA = {
    "type": "object",
    "required": ["variable_id", "value"],
    "properties": {
        "variable_id": {"type": "integer"},
        "value": {"type": "string"},
    },
}

_WHITE_LEVELS_SCHEMA = {
    "type": "object",
    "required": ["device_id"],
    "properties": {
        "device_id": {"type": "integer"},
        "white": {"type": "number"},
        "white2": {"type": "number"},
        "temperature": {"type": "number"},
    },
}


def register(handler, *, indigo_module):
    """Register every control tool onto the given MCPHandler.

    ``indigo_module`` is injected (matches the lookup tools) so tests
    can pass a MagicMock without monkey-patching ``sys.modules``.
    """
    handler.register_tool(
        name="device_turn_on",
        description=(
            "Turn a device on by id. Optional delay (seconds before) "
            "and duration (seconds until it turns back off)."
        ),
        input_schema=_DEVICE_ID_DELAY_DURATION_SCHEMA,
        handler=lambda **args: _turn_on_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_turn_off",
        description=(
            "Turn a device off by id. Optional delay (seconds before) "
            "and duration (seconds until it turns back on)."
        ),
        input_schema=_DEVICE_ID_DELAY_DURATION_SCHEMA,
        handler=lambda **args: _turn_off_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_toggle",
        description=(
            "Toggle a device between on and off. Optional delay and "
            "duration (seconds until it toggles back)."
        ),
        input_schema=_DEVICE_ID_DELAY_DURATION_SCHEMA,
        handler=lambda **args: _toggle_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_enable",
        description=(
            "Enable or disable a device's communication (take a "
            "flapping/offline device out of service, or bring it back)."
        ),
        input_schema={
            "type": "object",
            "required": ["device_id", "enabled"],
            "properties": {
                "device_id": {"type": "integer"},
                "enabled": {"type": "boolean"},
            },
        },
        handler=lambda **args: _enable_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_status_request",
        description=(
            "Ask the device hardware to refresh its status in Indigo — "
            "use when a device's shown state looks stale."
        ),
        input_schema=_DEVICE_ID_SCHEMA,
        handler=lambda **args: _status_request_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_lock",
        description=(
            "Lock a lock device. Optional delay, and duration until it "
            "auto-unlocks."
        ),
        input_schema=_DEVICE_ID_DELAY_DURATION_SCHEMA,
        handler=lambda **args: _lock_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_unlock",
        description=(
            "Unlock a lock device. Optional delay, and duration until it "
            "auto-locks."
        ),
        input_schema=_DEVICE_ID_DELAY_DURATION_SCHEMA,
        handler=lambda **args: _unlock_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_brighten",
        description=(
            "Brighten a dimmer relative to its current level "
            "(optional `by` 1-100, optional delay)."
        ),
        input_schema=_RELATIVE_BRIGHTNESS_SCHEMA,
        handler=lambda **args: _brighten_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_dim",
        description=(
            "Dim a dimmer relative to its current level "
            "(optional `by` 1-100, optional delay)."
        ),
        input_schema=_RELATIVE_BRIGHTNESS_SCHEMA,
        handler=lambda **args: _dim_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_ping",
        description=(
            "Ping a Z-Wave/Insteon module and report round-trip time in "
            "ms — checks reachability without changing state."
        ),
        input_schema=_DEVICE_ID_SCHEMA,
        handler=lambda **args: _ping_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_beep",
        description="Ask a device to emit an audible beep (hardware permitting) — useful for physically locating it.",
        input_schema=_DEVICE_ID_SCHEMA,
        handler=lambda **args: _beep_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_reset_energy_accum",
        description="Reset a device's accumulated energy (kWh) counter to zero.",
        input_schema=_DEVICE_ID_SCHEMA,
        handler=lambda **args: _reset_energy_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_set_brightness",
        description=(
            "Set a dimmer's brightness 0-100. "
            "Accepts a 0-1 float for fractional callers; out-of-range "
            "values are clamped."
        ),
        input_schema=_BRIGHTNESS_SCHEMA,
        handler=lambda **args: _set_brightness_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_set_rgb_color",
        description=(
            "Set a colour-capable dimmer's RGB colour. "
            "Channels are 0-255 byte values; out-of-range values "
            "are clamped before being converted to Indigo's 0-100 "
            "wire unit."
        ),
        input_schema=_RGB_SCHEMA,
        handler=lambda **args: _set_rgb_color_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_set_rgb_percent",
        description=(
            "Set a colour-capable dimmer's RGB colour using 0-100 "
            "percent channels (matches Indigo's native unit)."
        ),
        input_schema=_RGB_SCHEMA,
        handler=lambda **args: _set_rgb_percent_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_set_hex_color",
        description=(
            "Set a colour-capable dimmer's RGB colour from a hex "
            "string. Accepts #RRGGBB, RRGGBB, #RGB, or RGB."
        ),
        input_schema=_HEX_COLOR_SCHEMA,
        handler=lambda **args: _set_hex_color_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_set_named_color",
        description=(
            "Set a colour-capable dimmer's RGB colour from a CSS "
            "named colour (red, aliceblue, dodgerblue, …). 140 names "
            "supported; case- and space-insensitive; British "
            "*grey* spellings resolve to the *gray* equivalents."
        ),
        input_schema=_NAMED_COLOR_SCHEMA,
        handler=lambda **args: _set_named_color_handler(args, indigo_module),
    )
    handler.register_tool(
        name="thermostat_set_hvac_mode",
        description=(
            "Set a thermostat's HVAC mode. Accepts friendly names "
            "(case-insensitive): off, heat, cool, auto (= heatcool), "
            "heatcool, programheat, programcool, programheatcool."
        ),
        input_schema=_MODE_SCHEMA,
        handler=lambda **args: _set_hvac_mode_handler(args, indigo_module),
    )
    handler.register_tool(
        name="thermostat_set_fan_mode",
        description=(
            "Set a thermostat's fan mode. Accepts friendly names "
            "(case-insensitive, spaces/underscores ignored): "
            "auto, alwayson (or 'always on' / 'always_on')."
        ),
        input_schema=_MODE_SCHEMA,
        handler=lambda **args: _set_fan_mode_handler(args, indigo_module),
    )
    handler.register_tool(
        name="thermostat_set_heat_setpoint",
        description=(
            "Set a thermostat's heating setpoint. Temperature units "
            "follow the thermostat's own configuration (°F or °C); "
            "Indigo handles the conversion."
        ),
        input_schema=_TEMPERATURE_SCHEMA,
        handler=lambda **args: _set_heat_setpoint_handler(args, indigo_module),
    )
    handler.register_tool(
        name="thermostat_set_cool_setpoint",
        description=(
            "Set a thermostat's cooling setpoint. Temperature units "
            "follow the thermostat's own configuration (°F or °C); "
            "Indigo handles the conversion."
        ),
        input_schema=_TEMPERATURE_SCHEMA,
        handler=lambda **args: _set_cool_setpoint_handler(args, indigo_module),
    )
    handler.register_tool(
        name="action_execute_group",
        description="Execute an Indigo action group by id.",
        input_schema=_ACTION_GROUP_ID_SCHEMA,
        handler=lambda **args: _action_execute_group_handler(args, indigo_module),
    )
    handler.register_tool(
        name="variable_create",
        description=(
            "Create a new Indigo variable. Returns the new variable's "
            "id, name, value, and folder. Indigo variable values are "
            "strings on the wire; numeric/bool callers must serialise "
            "themselves before calling. Empty value is permitted."
        ),
        input_schema=_VARIABLE_CREATE_SCHEMA,
        handler=lambda **args: _variable_create_handler(args, indigo_module),
    )
    handler.register_tool(
        name="variable_update",
        description=(
            "Update an existing Indigo variable's value. Empty value "
            "clears the variable."
        ),
        input_schema=_VARIABLE_UPDATE_SCHEMA,
        handler=lambda **args: _variable_update_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_set_white_levels",
        description=(
            "Set warm/cool white levels and/or colour temperature on "
            "an RGBW or dual-white dimmer. All three keys (white, "
            "white2, temperature in Kelvin) are individually optional "
            "but at least one must be supplied. White levels are "
            "0-100; temperatures are typically 2000-6500K."
        ),
        input_schema=_WHITE_LEVELS_SCHEMA,
        handler=lambda **args: _set_white_levels_handler(args, indigo_module),
    )
