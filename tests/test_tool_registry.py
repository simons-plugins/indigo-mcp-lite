"""Tests for the top-level tool registry."""

from unittest.mock import MagicMock


def test_register_all_registers_list_devices(mock_indigo):
    from tool_registry import register_all

    handler = MagicMock()
    register_all(handler, indigo_module=mock_indigo)

    # register_all should have called register_tool at least once with
    # name="list_devices" — args may be positional or keyword.
    names = [
        (call.kwargs.get("name") or (call.args[0] if call.args else None))
        for call in handler.register_tool.call_args_list
    ]
    assert "list_devices" in names
