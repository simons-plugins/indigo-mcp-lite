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
