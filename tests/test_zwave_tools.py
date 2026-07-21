"""Tests for the Z-Wave lookup tools.

Covers _list_zwave_devices_handler / _get_zwave_details_handler and
the _json_safe / _zwave_global_props helpers. Z-Wave-ness is
``dev.protocol == indigo.kProtocol.ZWave``; the fake devices point
protocol at the mock module's enum object so equality holds the
same way it does against the live enum.
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest


# ----- fixture helpers ---------------------------------------------------


def _fake_zwave_device(mock_indigo, id_, name, battery=None, global_props=None):
    d = MagicMock()
    d.id = id_
    d.name = name
    d.deviceTypeId = "zwOnOffSensorType"
    d.onState = False
    d.brightness = None
    d.folderId = 0
    d.description = ""
    d.model = "Fibaro Sensor"
    d.address = "23"
    d.pluginId = ""
    d.protocol = mock_indigo.kProtocol.ZWave
    d.batteryLevel = battery
    d.version = "3.2"
    d.enabled = True
    d.errorState = ""
    d.lastChanged = datetime(2026, 7, 21, 12, 0, 0)
    d.globalProps = global_props if global_props is not None else {}
    return d


def _fake_plugin_device(mock_indigo, id_, name):
    d = _fake_zwave_device(mock_indigo, id_, name)
    d.protocol = mock_indigo.kProtocol.Plugin
    return d


def _attach_store(collection, store):
    collection.__getitem__.side_effect = lambda i: store[i]


# ----- list_zwave_devices ------------------------------------------------


def test_list_zwave_devices_filters_protocol(mock_indigo):
    from tools.zwave import _list_zwave_devices_handler

    devices = [
        _fake_zwave_device(mock_indigo, 1, "Sensor A", battery=80),
        _fake_plugin_device(mock_indigo, 2, "Plugin Thing"),
        _fake_zwave_device(mock_indigo, 3, "Sensor B"),
    ]
    mock_indigo.devices.__iter__.side_effect = lambda: iter(devices)

    result = _list_zwave_devices_handler({}, mock_indigo)
    assert result["total_count"] == 2
    assert [r["id"] for r in result["results"]] == [1, 3]


def test_list_zwave_devices_shape(mock_indigo):
    from tools.zwave import _list_zwave_devices_handler

    devices = [_fake_zwave_device(mock_indigo, 1, "Sensor A", battery=80)]
    mock_indigo.devices.__iter__.side_effect = lambda: iter(devices)

    row = _list_zwave_devices_handler({}, mock_indigo)["results"][0]
    assert row["battery_level"] == 80
    assert row["address"] == "23"
    assert row["firmware_version"] == "3.2"
    assert row["enabled"] is True
    assert row["error_state"] == ""
    assert row["last_changed"] == "2026-07-21T12:00:00"


def test_list_zwave_devices_battery_none_passthrough(mock_indigo):
    from tools.zwave import _list_zwave_devices_handler

    devices = [_fake_zwave_device(mock_indigo, 1, "Mains Switch", battery=None)]
    mock_indigo.devices.__iter__.side_effect = lambda: iter(devices)

    row = _list_zwave_devices_handler({}, mock_indigo)["results"][0]
    assert row["battery_level"] is None


def test_list_zwave_devices_pagination(mock_indigo):
    from tools.zwave import _list_zwave_devices_handler

    devices = [
        _fake_zwave_device(mock_indigo, i, f"S{i}") for i in range(1, 6)
    ]
    mock_indigo.devices.__iter__.side_effect = lambda: iter(devices)

    result = _list_zwave_devices_handler({"limit": 2, "offset": 2}, mock_indigo)
    assert [r["id"] for r in result["results"]] == [3, 4]
    assert result["total_count"] == 5
    assert result["has_more"] is True


# ----- get_zwave_device_details ------------------------------------------


def test_get_zwave_details_happy_path(mock_indigo):
    from tools.zwave import _get_zwave_details_handler

    props = {
        "com.perceptiveautomation.indigoplugin.zwave": {
            "zwNodeId": 23,
            "listening": False,
        },
        "com.other.plugin": {"ignored": True},
    }
    dev = _fake_zwave_device(mock_indigo, 42, "Sensor", battery=55,
                             global_props=props)
    _attach_store(mock_indigo.devices, {42: dev})

    result = _get_zwave_details_handler({"id": 42}, mock_indigo)
    assert result["battery_level"] == 55
    assert result["zwave_props"] == {
        "com.perceptiveautomation.indigoplugin.zwave": {
            "zwNodeId": 23,
            "listening": False,
        }
    }


def test_get_zwave_details_non_zwave_raises(mock_indigo):
    from tools.zwave import _get_zwave_details_handler

    _attach_store(mock_indigo.devices,
                  {7: _fake_plugin_device(mock_indigo, 7, "Plugin Thing")})
    with pytest.raises(ValueError, match="not a Z-Wave device"):
        _get_zwave_details_handler({"id": 7}, mock_indigo)


def test_get_zwave_details_missing_raises(mock_indigo):
    from tools.zwave import _get_zwave_details_handler

    _attach_store(mock_indigo.devices, {})
    with pytest.raises(ValueError, match="999"):
        _get_zwave_details_handler({"id": 999}, mock_indigo)


def test_get_zwave_details_non_int_raises(mock_indigo):
    from tools.zwave import _get_zwave_details_handler

    with pytest.raises(ValueError, match="integer"):
        _get_zwave_details_handler({"id": "42"}, mock_indigo)


def test_get_zwave_details_no_global_props(mock_indigo):
    from tools.zwave import _get_zwave_details_handler

    dev = _fake_zwave_device(mock_indigo, 1, "Sensor")
    dev.globalProps = None
    _attach_store(mock_indigo.devices, {1: dev})

    result = _get_zwave_details_handler({"id": 1}, mock_indigo)
    assert result["zwave_props"] == {}


# ----- _json_safe ---------------------------------------------------------


def test_json_safe_passthrough_primitives():
    from tools.zwave import _json_safe

    assert _json_safe(None) is None
    assert _json_safe(True) is True
    assert _json_safe(5) == 5
    assert _json_safe("x") == "x"


def test_json_safe_nested_containers():
    from tools.zwave import _json_safe

    class DictLike:
        def __init__(self, d):
            self._d = d

        def items(self):
            return self._d.items()

    value = DictLike({"a": [1, DictLike({"b": "c"})], 2: 3.5})
    assert _json_safe(value) == {"a": [1, {"b": "c"}], "2": 3.5}


def test_json_safe_unknown_leaf_becomes_str():
    from tools.zwave import _json_safe

    class Enum:
        def __str__(self):
            return "ZWave"

    assert _json_safe(Enum()) == "ZWave"


# ----- registration -------------------------------------------------------


def test_register_all_registers_zwave_tools(mock_indigo):
    from tool_registry import register_all

    handler = MagicMock()
    register_all(handler, indigo_module=mock_indigo)
    names = [
        (call.kwargs.get("name") or (call.args[0] if call.args else None))
        for call in handler.register_tool.call_args_list
    ]
    assert "list_zwave_devices" in names
    assert "get_zwave_device_details" in names
