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
    # Phase 5 system tools — same regression-prevention coverage.
    assert "query_event_log" in names
    assert "list_plugins" in names
    assert "get_plugin_by_id" in names
    assert "get_plugin_status" in names
    assert "restart_plugin" in names


def test_find_devices_dispatches_through_mcp_handler(mock_indigo):
    """End-to-end: find_devices routed through real MCPHandler with
    a real Indexer. Confirms the indexer kwarg threading works and
    the lambda **args: registration shape survives the JSON-RPC
    wire path.
    """
    import json
    from unittest.mock import MagicMock
    from indexer import Indexer
    from mcp_handler import MCPHandler
    from tool_registry import register_all

    class _AttrList(list):
        pass

    dev = MagicMock()
    dev.id = 7; dev.name = "Kitchen Dimmer"
    dev.deviceTypeId = "dimmer"; dev.folderId = 10
    dev.description = ""; dev.model = ""; dev.address = ""

    devs = _AttrList([dev])
    devs.folders = MagicMock(getName=MagicMock(return_value="Kitchen"))
    mock_indigo.devices = devs
    vars_ = _AttrList(); vars_.folders = MagicMock(getName=MagicMock(return_value=""))
    mock_indigo.variables = vars_
    mock_indigo.actionGroups = _AttrList()

    indexer = Indexer(indigo_module=mock_indigo)
    indexer.build()

    handler = MCPHandler(server_name="test", server_version="0")
    register_all(handler, indigo_module=mock_indigo, indexer=indexer)

    response = handler.handle_request(
        http_method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "find_devices",
                "arguments": {"query": "kitchen"},
            },
        }),
    )

    assert response["status"] == 200, response
    body = json.loads(response["content"])
    result = body["result"]
    assert result.get("isError") is not True, f"tool returned error: {result}"
    inner = json.loads(result["content"][0]["text"])
    assert inner["total_count"] >= 1
    assert any(r["id"] == 7 for r in inner["results"])


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


def test_tools_call_query_event_log_dispatches_through_mcp_handler(mock_indigo):
    """Phase 5 integration coverage: query_event_log routed through
    real ``MCPHandler.handle_request``. Confirms the system tool
    family registers correctly and survives the dispatcher's
    ``handler(**tool_args)`` shape.
    """
    from mcp_handler import MCPHandler
    from tool_registry import register_all

    mock_indigo.server.getEventLogList.return_value = [
        {"TimeStamp": "2026-05-05 10:00:00.000",
         "TypeStr": "Server", "Message": "Started"},
    ]

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
                "name": "query_event_log",
                "arguments": {"limit": 5},
            },
        }),
    )

    assert response["status"] == 200, response
    body = json.loads(response["content"])
    result = body["result"]
    assert result.get("isError") is not True, f"tool returned error: {result}"
    inner = json.loads(result["content"][0]["text"])
    assert inner["total_count"] == 1
    assert inner["results"][0]["message"] == "Started"


def test_tools_call_restart_plugin_self_guard_returns_clean_error(mock_indigo):
    """The self-restart guard must surface a clean tool error, NOT a
    transport-level failure. ``isError`` should be True with an
    explanatory message; the dispatcher should not have called
    getPlugin at all.
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
                "name": "restart_plugin",
                "arguments": {"plugin_id": "com.simons-plugins.indigo-mcp-lite"},
            },
        }),
    )

    assert response["status"] == 200, response
    body = json.loads(response["content"])
    result = body["result"]
    assert result.get("isError") is True, f"expected error, got: {result}"
    assert "self" in result["content"][0]["text"].lower()
    mock_indigo.server.getPlugin.assert_not_called()


def _wire_call(mock_indigo, name, arguments):
    from mcp_handler import MCPHandler
    from tool_registry import register_all

    handler = MCPHandler(server_name="test", server_version="0")
    register_all(handler, indigo_module=mock_indigo)
    response = handler.handle_request(
        http_method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }),
    )
    assert response["status"] == 200, response
    return json.loads(response["content"])["result"]


def test_wire_dispatch_control_wave_toggle(mock_indigo):
    result = _wire_call(mock_indigo, "device_toggle", {"device_id": 5})
    assert result.get("isError") is not True, result
    mock_indigo.device.toggle.assert_called_once_with(5)


def test_wire_dispatch_automations_list(mock_indigo):
    mock_indigo.triggers = []
    result = _wire_call(mock_indigo, "list_triggers", {})
    assert result.get("isError") is not True, result
    assert json.loads(result["content"][0]["text"])["total_count"] == 0


def test_wire_dispatch_irrigation_stop(mock_indigo):
    result = _wire_call(mock_indigo, "sprinkler_stop", {"device_id": 3})
    assert result.get("isError") is not True, result
    mock_indigo.sprinkler.stop.assert_called_once_with(3)


def test_wire_dispatch_list_uncataloged_devices(mock_indigo):
    """Catalog-enrichment wave: registration + ``lambda **args:``
    shape for the gap-report tool survive the real JSON-RPC path."""
    mock_indigo.devices = []
    result = _wire_call(mock_indigo, "list_uncataloged_devices", {})
    assert result.get("isError") is not True, result
    inner = json.loads(result["content"][0]["text"])
    assert inner["total_count"] == 0
    assert inner["results"] == []
    assert "catalog_snapshot" in inner


def test_wire_dispatch_capability_refusal_is_friendly_tool_error(
        mock_indigo, monkeypatch):
    """A catalog capability refusal must arrive on the wire as a clean
    isError tool result (friendly ValueError text naming the failing
    flag and what the device does support), never a transport-level
    failure — and the SDK call must not have happened."""
    from unittest.mock import MagicMock

    import catalog_snapshot

    monkeypatch.setattr(catalog_snapshot, "PROFILES", {
        ("com.test.plugin", "ledStrip"): {
            "base_class": "indigo.DimmerDevice",
            "capabilities": {"supportsRGB": False, "supportsOnState": True},
        },
    })
    dev = MagicMock()
    dev.pluginId = "com.test.plugin"
    dev.deviceTypeId = "ledStrip"
    mock_indigo.devices.__getitem__.side_effect = {7: dev}.__getitem__

    result = _wire_call(
        mock_indigo, "device_set_rgb_color",
        {"device_id": 7, "red": 255, "green": 0, "blue": 0},
    )
    assert result.get("isError") is True, result
    text = result["content"][0]["text"]
    assert "supportsRGB" in text
    assert "supportsOnState" in text  # names what the device DOES support
    mock_indigo.dimmer.setColorLevels.assert_not_called()
