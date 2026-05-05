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

from tools.lookup import _require_int_id
from tools.value_helpers import normalize_brightness


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
