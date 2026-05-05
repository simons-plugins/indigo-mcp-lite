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


def _turn_on_handler(args, indigo_module):
    """Turn a device on by id."""
    indigo_module.device.turnOn(_require_int_id(args, "device_id"))
    return {"status": "ok"}


def _turn_off_handler(args, indigo_module):
    """Turn a device off by id."""
    indigo_module.device.turnOff(_require_int_id(args, "device_id"))
    return {"status": "ok"}


_DEVICE_ID_SCHEMA = {
    "type": "object",
    "required": ["device_id"],
    "properties": {"device_id": {"type": "integer"}},
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
