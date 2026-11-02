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


# ---------------------------------------------------------------------
# Built-in MCP gate (workspace ADR-0009). On Indigo 2026.1+ the server's
# own MCP at /v3/api/mcp covers lookup and control; lite registers only
# its extension tools unless the Legacy tools pref is on.
# ---------------------------------------------------------------------

import pytest

# The tools lite keeps on a 2026.1+ server. Literal on purpose: a tool
# that silently joins or leaves this set should fail a test, not just
# shift a count.
EXTENSION_TOOLS = frozenset({
    "get_automation_contents", "find_automation_references",
    "list_automation_scripts", "query_sql_logger", "list_sql_logger_columns",
    "list_plugin_actions", "plugin_execute_action",
    "lamplighter_list_zones", "lamplighter_get_zone", "lamplighter_update_zone",
    "lamplighter_reset_override", "lamplighter_lock_zone",
    "lamplighter_set_enabled", "lamplighter_reconcile_now",
    "lamplighter_explain",
    "auto_lights_list_zones", "auto_lights_set_level",
    "auto_lights_set_zone_enabled", "auto_lights_reset_locks",
    "list_zwave_devices", "get_zwave_device_details",
    "trigger_enable", "schedule_enable", "device_enable", "variable_delete",
    "variable_move_to_folder", "device_status_request", "device_ping",
    "device_beep", "device_reset_energy_accum", "get_dependencies",
    "get_device_group", "list_uncataloged_devices",
})


def _registered(handler):
    return [
        (call.kwargs.get("name") or (call.args[0] if call.args else None))
        for call in handler.register_tool.call_args_list
    ]


def _register(mock_indigo, **kwargs):
    from tool_registry import register_all

    handler = MagicMock()
    result = register_all(handler, indigo_module=mock_indigo, **kwargs)
    return _registered(handler), result


def test_extension_set_is_33_tools():
    assert len(EXTENSION_TOOLS) == 33


def test_2026_1_registers_only_the_extension_tools(mock_indigo):
    from tool_registry import BUILTIN_COVERED

    mock_indigo.server.version = "2026.1.0"
    names, result = _register(mock_indigo, indexer=MagicMock())

    assert set(names) == EXTENSION_TOOLS
    assert len(names) == len(set(names)) == 33
    assert not set(names) & BUILTIN_COVERED
    # find_devices is skipped even though an indexer was supplied.
    assert "find_devices" not in names
    assert result == {"slim": True, "registered": 33, "skipped": 53}


def test_2025_2_registers_everything(mock_indigo):
    mock_indigo.server.version = "2025.2.5"
    names, result = _register(mock_indigo, indexer=MagicMock())

    assert "list_devices" in names
    assert "find_devices" in names
    assert EXTENSION_TOOLS <= set(names)
    assert result["slim"] is False
    assert result["skipped"] == 0
    assert result["registered"] == len(names) == 86


def test_2026_1_with_legacy_tools_registers_everything(mock_indigo):
    from tool_registry import BUILTIN_COVERED

    mock_indigo.server.version = "2026.1.0"
    names, result = _register(mock_indigo, indexer=MagicMock(),
                              legacy_tools=True)

    assert BUILTIN_COVERED <= set(names)
    assert EXTENSION_TOOLS <= set(names)
    assert len(names) == 86
    assert result["slim"] is False


@pytest.mark.parametrize("version", ["MISSING", "MAGICMOCK", "garbage", "", 2026.1])
def test_unknown_server_version_falls_back_to_everything(mock_indigo, version):
    # Registering too much is recoverable; registering too little hides
    # tools a 2025.x user depends on. Anything not clearly 2026.1+ is
    # treated as "no built-in MCP".
    if version == "MISSING":
        del mock_indigo.server
    elif version != "MAGICMOCK":
        mock_indigo.server.version = version
    names, result = _register(mock_indigo, indexer=MagicMock())

    assert "list_devices" in names
    assert "find_devices" in names
    assert len(names) == 86
    assert result["slim"] is False


def test_builtin_covered_is_exactly_53_real_tool_names(mock_indigo):
    # Guards against a renamed tool silently escaping the gate: every
    # name in the set must be one a full registration actually produces.
    from tool_registry import BUILTIN_COVERED

    mock_indigo.server.version = "2025.2.5"
    names, _ = _register(mock_indigo, indexer=MagicMock())

    assert len(BUILTIN_COVERED) == 53
    assert BUILTIN_COVERED <= set(names)
    # ...and together with the extension set it is the whole surface.
    assert BUILTIN_COVERED | EXTENSION_TOOLS == set(names)
    assert not BUILTIN_COVERED & EXTENSION_TOOLS


@pytest.mark.parametrize("version,expected", [
    ("2025.2.5", False),
    ("2026.0.9", False),
    ("2026.1.0", True),
    ("2026.1", True),
    ("2026.12.3", True),
    ("2027.0.0", True),
    ("2026", False),
])
def test_server_has_builtin_mcp_boundaries(mock_indigo, version, expected):
    from tool_registry import server_has_builtin_mcp

    mock_indigo.server.version = version
    assert server_has_builtin_mcp(mock_indigo) is expected


def test_server_has_builtin_mcp_logs_why_it_said_no(mock_indigo):
    from tool_registry import server_has_builtin_mcp

    logger = MagicMock()
    mock_indigo.server.version = "garbage"
    assert server_has_builtin_mcp(mock_indigo, logger) is False
    assert "garbage" in logger.debug.call_args.args[0]


def test_gate_proxy_forwards_everything_but_covered_tools():
    # The proxy only filters register_tool; register_resource (no tool
    # module calls it today) and any other handler attribute must reach
    # the real handler untouched.
    from tool_registry import BUILTIN_COVERED, _GatedRegistrar

    real = MagicMock()
    proxy = _GatedRegistrar(real, BUILTIN_COVERED)
    proxy.register_resource(uri="indigo://x", name="x", description="d",
                            handler=lambda: "")
    proxy.register_tool(name="list_devices", description="", input_schema={},
                        handler=lambda: None)
    proxy.register_tool(name="device_ping", description="", input_schema={},
                        handler=lambda: None)

    real.register_resource.assert_called_once()
    assert _registered(real) == ["device_ping"]
    assert (proxy.registered, proxy.skipped) == (1, 1)
    assert proxy.instructions is real.instructions
