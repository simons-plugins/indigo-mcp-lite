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


def _turn_on_handler(args, indigo_module):
    """Turn a device on by id."""
    indigo_module.device.turnOn(_require_int_id(args, "device_id"))
    return {"status": "ok"}


def _turn_off_handler(args, indigo_module):
    """Turn a device off by id."""
    indigo_module.device.turnOff(_require_int_id(args, "device_id"))
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


def _require_str(args, key):
    """Pull a non-empty string out of args under ``key`` or raise."""
    raw = args.get(key)
    if not isinstance(raw, str) or not raw:
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
    raw = args.get("mode")
    if not isinstance(raw, str):
        raise ValueError("mode must be a string")
    key = raw.lower().replace("_", "").replace(" ", "")
    if key not in _HVAC_MODE_NAMES:
        valid = ", ".join(sorted(_HVAC_MODE_NAMES))
        raise ValueError(f"mode {raw!r} not recognised; valid: {valid}")
    enum_value = getattr(indigo_module.kHvacMode, _HVAC_MODE_NAMES[key])
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

_HVAC_MODE_SCHEMA = {
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
        description="Turn a device on by id.",
        input_schema=_DEVICE_ID_SCHEMA,
        handler=lambda **args: _turn_on_handler(args, indigo_module),
    )
    handler.register_tool(
        name="device_turn_off",
        description="Turn a device off by id.",
        input_schema=_DEVICE_ID_SCHEMA,
        handler=lambda **args: _turn_off_handler(args, indigo_module),
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
        input_schema=_HVAC_MODE_SCHEMA,
        handler=lambda **args: _set_hvac_mode_handler(args, indigo_module),
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
