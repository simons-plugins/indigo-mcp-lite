"""Integration test: tools dispatched through the real MCPHandler.

Catches signature drift between tool registrations and the JSON-RPC
dispatcher: mcp_handler calls ``handler(**tool_args)``, so registrations
must use ``lambda **args:`` form (or a function that accepts kwargs),
not ``lambda args:``. Every list_devices unit test passes with the
broken form because they bypass the lambda — this test goes through
the wire path that production traffic uses.
"""

import json


def test_tools_call_list_devices_dispatches_through_mcp_handler(mock_indigo):
    from mcp_handler import MCPHandler
    from tool_registry import register_all

    handler = MCPHandler(server_name="test", server_version="0")
    mock_indigo.devices = []
    register_all(handler, indigo_module=mock_indigo)

    response = handler.handle_request(
        http_method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "list_devices", "arguments": {"limit": 10}},
        }),
    )

    assert response["status"] == 200, response
    body = json.loads(response["content"])
    assert "result" in body, f"expected result, got: {body}"
    result = body["result"]
    assert result.get("isError") is not True, f"tool returned error: {result}"

    inner = json.loads(result["content"][0]["text"])
    assert inner["total_count"] == 0
    assert inner["results"] == []


def test_tools_list_through_mcp_handler_includes_lookup_tools(mock_indigo):
    from mcp_handler import MCPHandler
    from tool_registry import register_all

    handler = MCPHandler(server_name="test", server_version="0")
    register_all(handler, indigo_module=mock_indigo)

    response = handler.handle_request(
        http_method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
    )

    assert response["status"] == 200, response
    body = json.loads(response["content"])
    names = {t["name"] for t in body["result"]["tools"]}
    assert "list_devices" in names
    assert "get_devices_by_state" in names
    assert "get_device_by_id" in names
    # Phase 4 control tools — at least one from each tool family so a
    # broken `lambda args:` registration in any of them surfaces here
    # rather than only on a real Indigo wire call.
    assert "device_turn_on" in names
    assert "device_set_brightness" in names
    assert "device_set_named_color" in names
    assert "thermostat_set_hvac_mode" in names
    assert "variable_update" in names
    assert "action_execute_group" in names


def test_tools_call_device_turn_on_dispatches_through_mcp_handler(mock_indigo):
    """End-to-end exercise of a Phase 4 control tool through the real
    JSON-RPC layer.

    Phase 3 caught a latent ``lambda args:`` bug pre-merge by running
    one tool through this path; the same regression-prevention pattern
    applies here. ``device_turn_on`` is small enough that the assertion
    can check the SDK call directly — if the registration uses the
    wrong lambda shape, ``mcp_handler.dispatch_tool`` raises and the
    ``isError`` check below trips.
    """
    from mcp_handler import MCPHandler
    from tool_registry import register_all

    handler = MCPHandler(server_name="test", server_version="0")
    register_all(handler, indigo_module=mock_indigo)

    response = handler.handle_request(
        http_method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "device_turn_on",
                "arguments": {"device_id": 42},
            },
        }),
    )

    assert response["status"] == 200, response
    body = json.loads(response["content"])
    result = body["result"]
    assert result.get("isError") is not True, f"tool returned error: {result}"
    mock_indigo.device.turnOn.assert_called_once_with(42)


def test_tools_call_device_set_brightness_dispatches_through_mcp_handler(mock_indigo):
    """Second control-tool integration test with kwargs in the
    arguments — exercises ``brightness`` flowing through
    ``dispatch_tool``'s ``handler(**tool_args)`` and the
    ``lambda **args:`` registration.
    """
    from mcp_handler import MCPHandler
    from tool_registry import register_all

    handler = MCPHandler(server_name="test", server_version="0")
    register_all(handler, indigo_module=mock_indigo)

    response = handler.handle_request(
        http_method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "device_set_brightness",
                "arguments": {"device_id": 42, "brightness": 75},
            },
        }),
    )

    assert response["status"] == 200, response
    body = json.loads(response["content"])
    result = body["result"]
    assert result.get("isError") is not True, f"tool returned error: {result}"
    mock_indigo.dimmer.setBrightness.assert_called_once_with(42, value=75)
