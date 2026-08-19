"""Tests for tools/automation_contents — decoded automation contents,
reverse references, and the embedded-script audit.

Reuses the .indiDb fixture from test_indidb_reader so the tool layer
is exercised against exactly the shapes the reader produces.
"""

import json

import pytest

from indidb_reader import IndiDbReader
from tools.automation_contents import (
    _find_references_handler,
    _get_contents_handler,
    _list_scripts_handler,
)

from test_indidb_reader import FIXTURE, SKIP_FIXTURE
from test_indidb_reader_semantics import FIXTURE as SEMANTICS_FIXTURE


class _Named:
    def __init__(self, name):
        self.name = name


def _wire_names(mock_indigo):
    devices = {
        111: _Named("Kitchen Light"),
        333: _Named("Hall Thermostat"),
        444: _Named("Sonos Kitchen"),
    }
    variables = {555: _Named("holiday_mode")}
    groups = {100: _Named("Morning.Scene"), 200: _Named("Chained")}
    mock_indigo.devices.__getitem__.side_effect = lambda i: devices[i]
    mock_indigo.variables.__getitem__.side_effect = lambda i: variables[i]
    mock_indigo.actionGroups.__getitem__.side_effect = lambda i: groups[i]


@pytest.fixture
def reader(tmp_path, mock_indigo):
    path = tmp_path / "Test House.indiDb"
    path.write_text(FIXTURE, encoding="utf-8")
    mock_indigo.server.getDbFilePath.return_value = str(path)
    _wire_names(mock_indigo)
    return IndiDbReader(indigo_module=mock_indigo)


@pytest.fixture
def semantics_reader(tmp_path, mock_indigo):
    """Reader over the fixture carrying sun conditions, plugin props,
    and schedule timing — the three shapes ``get_automation_contents``
    must pass through to callers."""
    path = tmp_path / "Semantics.indiDb"
    path.write_text(SEMANTICS_FIXTURE, encoding="utf-8")
    mock_indigo.server.getDbFilePath.return_value = str(path)
    mock_indigo.devices.__getitem__.side_effect = KeyError
    mock_indigo.actionGroups.__getitem__.side_effect = KeyError
    return IndiDbReader(indigo_module=mock_indigo)


# ---------------------------------------------------------------------
# get_automation_contents
# ---------------------------------------------------------------------

def test_get_contents_action_group_with_names(reader):
    out = _get_contents_handler(
        {"entity_type": "action_group", "id": 100}, reader
    )
    assert out["name"] == "Morning.Scene"
    assert out["entity_type"] == "action_group"
    first = out["steps"][0]
    assert first["device_id"] == 111
    assert first["device_name"] == "Kitchen Light"
    var_step = out["steps"][2]
    assert var_step["variable_name"] == "holiday_mode"


def test_get_contents_expands_group_one_level(reader):
    out = _get_contents_handler(
        {"entity_type": "action_group", "id": 100}, reader
    )
    exec_step = out["steps"][-1]
    assert exec_step["type"] == "execute_action_group"
    assert exec_step["action_group_name"] == "Chained"
    nested = exec_step["action_group"]
    assert nested["id"] == 200 and nested["name"] == "Chained"
    assert [s["type"] for s in nested["steps"]] == [
        "execute_action_group", "embedded_script",
    ]


def test_get_contents_cycle_guard_stops_at_one_level(reader):
    # 100 -> 200 -> 100 is a cycle; the nested summary of 200 must
    # name its 100-step but NOT expand it further.
    out = _get_contents_handler(
        {"entity_type": "action_group", "id": 100}, reader
    )
    nested_steps = out["steps"][-1]["action_group"]["steps"]
    back_ref = nested_steps[0]
    assert back_ref["action_group_id"] == 100
    assert back_ref["action_group_name"] == "Morning.Scene"
    assert "action_group" not in back_ref  # no second-level expansion


def test_get_contents_self_executing_group_not_expanded(reader):
    # Group 200 executes 100 which executes 200 — requesting 200,
    # the expansion of 100 must not re-expand 200 (seen guard).
    out = _get_contents_handler(
        {"entity_type": "action_group", "id": 200}, reader
    )
    nested = out["steps"][0]["action_group"]
    assert nested["id"] == 100
    loop_step = nested["steps"][-1]
    assert loop_step["action_group_id"] == 200
    assert "action_group" not in loop_step


def test_get_contents_schedule_conditions_annotated(reader):
    out = _get_contents_handler({"entity_type": "schedule", "id": 300}, reader)
    cond = out["conditions"]
    assert cond["logic"] == "all"
    leaf = cond["conditions"][0]
    assert leaf["device_name"] == "Kitchen Light"
    var_leaf = cond["conditions"][1]["conditions"][0]
    assert var_leaf["variable_name"] == "holiday_mode"


def test_get_contents_trigger_watch_annotated(reader):
    out = _get_contents_handler({"entity_type": "trigger", "id": 400}, reader)
    assert out["enabled"] is False
    assert out["watch"]["device_id"] == 111
    assert out["watch"]["device_name"] == "Kitchen Light"
    assert out["conditions"] is None


def test_get_contents_unresolvable_id_keeps_id_only(reader):
    out = _get_contents_handler({"entity_type": "trigger", "id": 400}, reader)
    plugin_step = out["steps"][1]
    assert plugin_step["device_id"] == 444
    assert plugin_step["device_name"] == "Sonos Kitchen"
    # Now break resolution — ids must survive with name None.
    reader._indigo.devices.__getitem__.side_effect = KeyError
    out = _get_contents_handler({"entity_type": "trigger", "id": 400}, reader)
    assert out["steps"][1]["device_id"] == 444
    assert out["steps"][1]["device_name"] is None


def test_get_contents_rejects_bad_entity_type(reader):
    with pytest.raises(ValueError, match="entity_type must be one of"):
        _get_contents_handler({"entity_type": "device", "id": 1}, reader)


def test_get_contents_missing_id_friendly(reader):
    with pytest.raises(ValueError, match="no schedule with id 999"):
        _get_contents_handler({"entity_type": "schedule", "id": 999}, reader)


def test_get_contents_rejects_unknown_args(reader):
    with pytest.raises(ValueError, match="unknown argument"):
        _get_contents_handler(
            {"entity_type": "schedule", "id": 300, "depth": 2}, reader
        )


def test_handlers_surface_db_unavailable_as_value_error(mock_indigo):
    mock_indigo.server.getDbFilePath.side_effect = RuntimeError("down")
    reader = IndiDbReader(indigo_module=mock_indigo)
    with pytest.raises(ValueError, match="database path unavailable"):
        _get_contents_handler({"entity_type": "trigger", "id": 1}, reader)
    with pytest.raises(ValueError, match="database path unavailable"):
        _find_references_handler({"device_id": 1}, reader)
    with pytest.raises(ValueError, match="database path unavailable"):
        _list_scripts_handler({}, reader)


# ---------------------------------------------------------------------
# find_automation_references
# ---------------------------------------------------------------------

def test_find_references_device_roles(reader):
    out = _find_references_handler({"device_id": 111}, reader)
    assert out["device_id"] == 111
    assert out["name"] == "Kitchen Light"
    by_id = {(r["automation_type"], r["id"]): r["roles"]
             for r in out["references"]}
    assert by_id[("schedule", 300)] == ["acts_on", "condition"]
    assert by_id[("trigger", 400)] == ["watches"]
    assert by_id[("action_group", 100)] == ["acts_on"]
    assert out["total_count"] == 3
    # Clean parse -> no skipped_automations noise in the response.
    assert "skipped_automations" not in out


def test_find_references_acts_on_via_plugin_step_device(reader):
    # Device 444 appears ONLY as the Class-999 plugin step's DeviceID
    # in trigger 400 — plugin actions must count as acts_on.
    out = _find_references_handler({"device_id": 444}, reader)
    assert out["total_count"] == 1
    ref = out["references"][0]
    assert (ref["automation_type"], ref["id"]) == ("trigger", 400)
    assert ref["roles"] == ["acts_on"]


def test_find_references_variable_roles(reader):
    out = _find_references_handler({"variable_id": 555}, reader)
    by_id = {(r["automation_type"], r["id"]): r["roles"]
             for r in out["references"]}
    assert by_id[("schedule", 300)] == ["condition"]
    assert by_id[("trigger", 401)] == ["watches"]
    assert by_id[("action_group", 100)] == ["acts_on"]
    assert out["total_count"] == 3


def test_find_references_no_hits_empty(reader):
    out = _find_references_handler({"device_id": 987654}, reader)
    assert out["references"] == []
    assert out["total_count"] == 0


def test_find_references_requires_exactly_one_id(reader):
    with pytest.raises(ValueError, match="exactly one"):
        _find_references_handler({}, reader)
    with pytest.raises(ValueError, match="exactly one"):
        _find_references_handler(
            {"device_id": 1, "variable_id": 2}, reader
        )


def test_find_references_rejects_unknown_args(reader):
    with pytest.raises(ValueError, match="unknown argument"):
        _find_references_handler({"device_id": 1, "role": "acts_on"}, reader)


# ---------------------------------------------------------------------
# list_automation_scripts
# ---------------------------------------------------------------------

def test_list_scripts_finds_all_with_owners(reader):
    out = _list_scripts_handler({}, reader)
    assert out["total_count"] == 2
    owners = {(s["owner_type"], s["owner_id"], s["owner_name"])
              for s in out["results"]}
    assert owners == {
        ("action_group", 100, "Morning.Scene"),
        ("action_group", 200, "Chained"),
    }
    by_owner = {s["owner_id"]: s for s in out["results"]}
    assert by_owner[100]["truncated"] is True
    assert len(by_owner[100]["source"]) == 2000
    assert by_owner[200]["source"] == 'print("hi")'
    assert by_owner[200]["truncated"] is False
    assert by_owner[200]["script_type_label"] == "python"
    assert by_owner[200]["step_index"] == 1


def test_list_scripts_rejects_unknown_args(reader):
    with pytest.raises(ValueError, match="unknown argument"):
        _list_scripts_handler({"limit": 5}, reader)


NO_SCRIPTS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<Database type="dict">
  <ActionGroupList type="vector">
    <ActionGroup type="dict">
      <ActionSteps type="vector">
        <Action type="dict">
          <Class type="integer">1</Class>
          <DeviceAction type="integer">4</DeviceAction>
          <DeviceActionValue type="integer">0</DeviceActionValue>
          <DeviceID type="integer">111</DeviceID>
        </Action>
      </ActionSteps>
      <ID type="integer">700</ID>
      <Name type="string">Scriptless</Name>
    </ActionGroup>
  </ActionGroupList>
</Database>
"""


def test_list_scripts_empty_when_none(tmp_path, mock_indigo):
    path = tmp_path / "noscripts.indiDb"
    path.write_text(NO_SCRIPTS_FIXTURE, encoding="utf-8")
    mock_indigo.server.getDbFilePath.return_value = str(path)
    reader = IndiDbReader(indigo_module=mock_indigo)
    out = _list_scripts_handler({}, reader)
    assert out == {"results": [], "total_count": 0}


def test_skipped_automations_surfaced_in_all_three_tools(
        tmp_path, mock_indigo):
    path = tmp_path / "skip.indiDb"
    path.write_text(SKIP_FIXTURE, encoding="utf-8")
    mock_indigo.server.getDbFilePath.return_value = str(path)
    _wire_names(mock_indigo)
    reader = IndiDbReader(indigo_module=mock_indigo)

    contents = _get_contents_handler(
        {"entity_type": "action_group", "id": 900}, reader
    )
    assert contents["skipped_automations"] == 1

    refs = _find_references_handler({"device_id": 111}, reader)
    assert refs["skipped_automations"] == 1
    assert refs["total_count"] == 1  # the good group still resolves

    scripts = _list_scripts_handler({}, reader)
    assert scripts["skipped_automations"] == 1
    assert scripts["results"] == []


# ---------------------------------------------------------------------
# Registration + wire path
# ---------------------------------------------------------------------

def test_register_all_includes_automation_contents_tools(mock_indigo):
    from unittest.mock import MagicMock
    from tool_registry import register_all

    handler = MagicMock()
    register_all(handler, indigo_module=mock_indigo)
    names = {
        (call.kwargs.get("name") or (call.args[0] if call.args else None))
        for call in handler.register_tool.call_args_list
    }
    assert {
        "get_automation_contents", "find_automation_references",
        "list_automation_scripts",
    } <= names


def test_get_automation_contents_dispatches_through_mcp_handler(
        tmp_path, mock_indigo):
    """Wire-path regression test: the lambda **args: registration must
    survive MCPHandler's ``handler(**tool_args)`` dispatch, and the
    reader created inside register() must lazily read the DB file
    supplied by indigo.server.getDbFilePath()."""
    from mcp_handler import MCPHandler
    from tool_registry import register_all

    path = tmp_path / "Wire House.indiDb"
    path.write_text(FIXTURE, encoding="utf-8")
    mock_indigo.server.getDbFilePath.return_value = str(path)
    _wire_names(mock_indigo)

    handler = MCPHandler(server_name="test", server_version="0")
    register_all(handler, indigo_module=mock_indigo)

    response = handler.handle_request(
        http_method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {
                "name": "get_automation_contents",
                "arguments": {"entity_type": "action_group", "id": 100},
            },
        }),
    )

    assert response["status"] == 200, response
    body = json.loads(response["content"])
    result = body["result"]
    assert result.get("isError") is not True, f"tool returned error: {result}"
    inner = json.loads(result["content"][0]["text"])
    assert inner["name"] == "Morning.Scene"
    assert inner["steps"][0]["action_label"] == "set_brightness"
    assert inner["steps"][-1]["action_group"]["name"] == "Chained"


def test_get_contents_reports_when_a_schedule_fires(semantics_reader):
    out = _get_contents_handler(
        {"entity_type": "schedule", "id": 601}, semantics_reader
    )
    assert out["schedule"]["time_type"] == "sunset"
    assert out["schedule"]["sun_offset_seconds"] == -1800


def test_get_contents_keeps_plugin_props_through_annotation(semantics_reader):
    out = _get_contents_handler(
        {"entity_type": "schedule", "id": 600}, semantics_reader
    )
    assert out["steps"][0]["props"]["whiteTemperature"] == "4000"


def test_get_contents_keeps_sun_conditions_through_annotation(
        semantics_reader):
    out = _get_contents_handler(
        {"entity_type": "trigger", "id": 700}, semantics_reader
    )
    assert [c.get("state") for c in out["conditions"]["conditions"]] == [
        "daylight", "dark", None,
    ]


def test_get_contents_omits_schedule_block_for_non_schedules(
        semantics_reader):
    out = _get_contents_handler(
        {"entity_type": "trigger", "id": 700}, semantics_reader
    )
    assert "schedule" not in out
