"""Tests for how a fresh MCP client learns what this server can do.

A remote model never reads the README or CLAUDE.md — it sees the
``initialize`` response and the tool descriptions, nothing else. Two
mechanisms carry that:

- the MCP ``instructions`` string (InitializeResult), read once up
  front, which is the only place routing rules between tool families
  can live;
- reverse pointers on the metadata tools, so an agent that starts at
  ``get_schedule_by_id`` learns that a richer tool exists rather than
  stopping at a plausible-looking ``next_execution``.

Both are description-only, so they have no unit test of their own
unless one is written; these are that test.
"""

import json

from unittest.mock import MagicMock

from mcp_handler import MCPHandler


def _initialize(handler):
    response = handler.handle_request(
        http_method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "clientInfo": {"name": "test-client", "version": "1"},
            },
        }),
    )
    assert response["status"] == 200, response
    return json.loads(response["content"])["result"]


def test_instructions_absent_when_not_supplied():
    # Optional per spec: omitted entirely rather than sent empty, so a
    # client can tell "not provided" from "nothing to say".
    assert "instructions" not in _initialize(MCPHandler())


def test_instructions_returned_when_supplied():
    handler = MCPHandler(instructions="Read me first.")
    assert _initialize(handler)["instructions"] == "Read me first."


def test_instructions_do_not_disturb_the_rest_of_initialize():
    result = _initialize(MCPHandler(instructions="x"))
    assert result["protocolVersion"] == "2025-11-25"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert "serverInfo" in result


def test_plugin_ships_instructions_naming_every_tool_family():
    # The plugin's own text, not a fixture — if it stops orienting a
    # client, this fails.
    import importlib.util
    import pathlib
    import sys

    path = (pathlib.Path(__file__).parent.parent
            / "Indigo MCP Lite.indigoPlugin" / "Contents"
            / "Server Plugin" / "plugin.py")
    sys.modules.setdefault("indigo", MagicMock())
    spec = importlib.util.spec_from_file_location("_lite_plugin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    text = module.SERVER_INSTRUCTIONS
    # Names the tool that answers "what does this do / when".
    assert "get_automation_contents" in text
    # Names the three incomplete-looking answers it exists to correct.
    assert "next_execution" in text
    assert "get_dependencies" in text
    assert "props" in text
    # ...and routes the props case to the tool that actually resolves
    # it, rather than leaving the client to read raw props itself.
    assert "acts_on_via_props" in text


def _descriptions(mock_indigo):
    from tool_registry import register_all

    handler = MagicMock()
    register_all(handler, indigo_module=mock_indigo)
    out = {}
    for call in handler.register_tool.call_args_list:
        name = call.kwargs.get("name") or (call.args[0] if call.args else None)
        out[name] = call.kwargs.get("description", "")
    return out


def test_metadata_tools_point_at_the_tool_that_answers_the_question(
        mock_indigo):
    # The pointer that matters runs metadata -> contents: an agent
    # asking "when does this run?" starts at the metadata tool and
    # would otherwise stop at a timestamp.
    descriptions = _descriptions(mock_indigo)
    for name in (
        "get_schedule_by_id", "get_trigger_by_id",
        "get_action_group_by_id", "list_schedules", "list_triggers",
        "list_action_groups",
    ):
        assert "get_automation_contents" in descriptions[name], name


def test_get_dependencies_declares_its_own_blind_spot(mock_indigo):
    # It wraps Indigo's dependency check, which misses devices named
    # only inside a plugin action's props — a zero-dependents answer
    # is not proof that nothing uses the device.
    description = _descriptions(mock_indigo)["get_dependencies"]
    assert "INCOMPLETE" in description
    assert "find_automation_references" in description


def test_schedule_lookups_warn_that_next_execution_is_not_the_rule(
        mock_indigo):
    descriptions = _descriptions(mock_indigo)
    for name in ("get_schedule_by_id", "list_schedules"):
        assert "TIMESTAMP" in descriptions[name], name
