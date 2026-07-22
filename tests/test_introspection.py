"""Tests for get_dependencies / get_device_group and rich device detail."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tools.introspection import _dependencies_handler, _device_group_handler
from tools.lookup import _serialize_device_detail


def test_dependencies_happy_path(mock_indigo):
    mock_indigo.variables.__getitem__.side_effect = lambda i: {9: object()}[i]
    mock_indigo.variable.getDependencies.return_value = {
        "triggers": [{"ID": 1, "Name": "T"}],
        "controlPages": [],
        "actionGroups": [{"ID": 2, "Name": "A"}],
    }
    out = _dependencies_handler({"entity_type": "variable", "id": 9},
                                mock_indigo)
    assert out["total_dependents"] == 2
    assert out["dependencies"]["triggers"][0]["Name"] == "T"


def test_dependencies_unknown_entity_type(mock_indigo):
    with pytest.raises(ValueError, match="entity_type"):
        _dependencies_handler({"entity_type": "banana", "id": 9}, mock_indigo)


def test_dependencies_unknown_id(mock_indigo):
    def _missing(i):
        raise KeyError(i)
    mock_indigo.triggers.__getitem__.side_effect = _missing
    with pytest.raises(ValueError, match="no trigger with id 404"):
        _dependencies_handler({"entity_type": "trigger", "id": 404},
                              mock_indigo)


def test_device_group_with_root(mock_indigo):
    root = SimpleNamespace(id=1, name="Root", deviceTypeId="zwRelayType",
                           model="", address="29", description="",
                           folderId=0, pluginId="", onState=None,
                           brightness=None, batteryLevel=88)
    mock_indigo.devices.__getitem__.side_effect = lambda i: {1: root, 2: root}[i]
    mock_indigo.device.getGroupList.return_value = [1, 2]
    out = _device_group_handler({"device_id": 2}, mock_indigo)
    assert out["group_ids"] == [1, 2]
    assert out["root_device"]["battery_level"] == 88


def test_device_detail_serializer_subclass_fields():
    dev = SimpleNamespace(
        id=5, name="Thermostat", deviceTypeId="thermostat", model="",
        address="", description="", folderId=0, pluginId="",
        onState=None, brightness=None, protocol="ZWave",
        batteryLevel=None, enabled=True, errorState="",
        sensorValue=None, displayStateValUi="21.5 °C",
        hvacMode="Heat", fanMode=None, heatSetpoint=21.0,
        coolSetpoint=None, speedIndex=None, speedLevel=None,
        activeZone=None, zoneCount=None, zoneNames=None,
        energyCurLevel=None, energyAccumTotal=None,
        lastChanged=datetime(2026, 7, 22, 9, 0),
        lastSuccessfulComm=None,
        states={"hvacOperationModeIsHeat": True, "temperatureInput1": 21.4,
                "hvacOperationMode": "heat", "hvacOperationMode.heat": True,
                "com.vendor.dotted": 1},
    )
    out = _serialize_device_detail(dev)
    assert out["hvac_mode"] == "Heat"
    assert out["heat_setpoint"] == 21.0
    assert out["display_state_ui"] == "21.5 °C"
    assert out["enabled"] is True
    assert "cool_setpoint" not in out  # None attrs omitted
    assert out["states"]["temperatureInput1"] == 21.4
    assert "hvacOperationMode.heat" not in out["states"]  # enum dup dropped
    assert out["states"]["com.vendor.dotted"] == 1  # no base key -> kept
    assert "2026-07-22" in out["last_changed"]


def test_register_all_includes_introspection(mock_indigo):
    from tool_registry import register_all

    handler = MagicMock()
    register_all(handler, indigo_module=mock_indigo)
    names = {
        (call.kwargs.get("name") or (call.args[0] if call.args else None))
        for call in handler.register_tool.call_args_list
    }
    assert {"get_dependencies", "get_device_group"} <= names


def test_dependencies_all_five_namespaces(mock_indigo):
    for entity_type, (ns, coll) in (
        ("device", ("device", "devices")),
        ("variable", ("variable", "variables")),
        ("trigger", ("trigger", "triggers")),
        ("schedule", ("schedule", "schedules")),
        ("action_group", ("actionGroup", "actionGroups")),
    ):
        getattr(mock_indigo, coll).__getitem__.side_effect = lambda i: object()
        getattr(mock_indigo, ns).getDependencies.return_value = {"triggers": []}
        out = _dependencies_handler({"entity_type": entity_type, "id": 1},
                                    mock_indigo)
        getattr(mock_indigo, ns).getDependencies.assert_called_once_with(1)
        assert out["total_dependents"] == 0


def test_dependencies_unrecognised_shape_reports_unknown(mock_indigo):
    mock_indigo.variables.__getitem__.side_effect = lambda i: object()
    mock_indigo.variable.getDependencies.return_value = ["weird"]
    out = _dependencies_handler({"entity_type": "variable", "id": 1},
                                mock_indigo)
    assert out["total_dependents"] is None
    assert out["dependencies"] == {"raw": ["weird"]}


def test_device_group_non_list_and_mixed_returns(mock_indigo):
    fake = SimpleNamespace(id=1, name="R", deviceTypeId="", model="",
                           address="", description="", folderId=0,
                           pluginId="", onState=None, brightness=None,
                           batteryLevel=None)
    mock_indigo.devices.__getitem__.side_effect = lambda i: fake
    mock_indigo.device.getGroupList.return_value = None
    assert _device_group_handler({"device_id": 1}, mock_indigo)["group_ids"] == []
    mock_indigo.device.getGroupList.return_value = [1, "x", 2.5]
    out = _device_group_handler({"device_id": 1}, mock_indigo)
    assert out["group_ids"] == [1]


def test_detail_attr_read_errors_counted():
    class _Broken:
        id = 1
        name = "B"
        deviceTypeId = ""
        model = ""
        address = ""
        description = ""
        folderId = 0
        pluginId = ""
        onState = None
        brightness = None
        protocol = "ZWave"
        states = {}

        @property
        def batteryLevel(self):
            raise RuntimeError("server link down")

        @property
        def sensorValue(self):
            raise RuntimeError("server link down")

    out = _serialize_device_detail(_Broken())
    assert out["attr_read_errors"] == 2
    assert "battery_level" not in out


def test_get_device_by_id_returns_detail_shape(mock_indigo):
    from tools.lookup import _get_device_handler

    dev = SimpleNamespace(
        id=42, name="X", deviceTypeId="sensor", model="", address="",
        description="", folderId=0, pluginId="", onState=None,
        brightness=None, protocol="ZWave", batteryLevel=55,
        states={"sensorValue": 21.4},
    )
    mock_indigo.devices.__getitem__.side_effect = lambda i: {42: dev}[i]
    out = _get_device_handler({"id": 42}, mock_indigo)
    assert out["battery_level"] == 55
    assert out["states"] == {"sensorValue": 21.4}


def test_wire_dispatch_introspection(mock_indigo):
    import json as _json

    from mcp_handler import MCPHandler
    from tool_registry import register_all

    handler = MCPHandler(server_name="test", server_version="0")
    register_all(handler, indigo_module=mock_indigo)
    mock_indigo.variables.__getitem__.side_effect = lambda i: object()
    mock_indigo.variable.getDependencies.return_value = {"triggers": []}
    response = handler.handle_request(
        http_method="POST",
        headers={"Content-Type": "application/json"},
        body=_json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "get_dependencies",
                       "arguments": {"entity_type": "variable", "id": 7}},
        }),
    )
    result = _json.loads(response["content"])["result"]
    assert result.get("isError") is not True, result
