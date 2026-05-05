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
