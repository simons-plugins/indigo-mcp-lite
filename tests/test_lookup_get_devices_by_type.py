"""TDD tests for tools.lookup._get_devices_by_type_handler.

Filters indigo.devices by deviceTypeId == device_type, then paginates
the matched subset using the standard envelope.
"""

from unittest.mock import MagicMock

import pytest


def _fake_device(id_, name, type_id="dimmer"):
    d = MagicMock()
    d.id = id_
    d.name = name
    d.deviceTypeId = type_id
    d.onState = False
    d.brightness = 0
    d.folderId = 0
    d.description = ""
    d.model = ""
    d.address = ""
    d.pluginId = ""
    return d


def test_get_devices_by_type_filters(mock_indigo):
    from tools.lookup import _get_devices_by_type_handler

    mock_indigo.devices = [
        _fake_device(1, "Dim1", type_id="dimmer"),
        _fake_device(2, "Relay1", type_id="relay"),
        _fake_device(3, "Dim2", type_id="dimmer"),
        _fake_device(4, "Sensor1", type_id="sensor"),
    ]
    result = _get_devices_by_type_handler({"device_type": "dimmer"}, mock_indigo)
    assert result["total_count"] == 2
    assert {d["id"] for d in result["results"]} == {1, 3}


def test_get_devices_by_type_unknown_returns_empty(mock_indigo):
    from tools.lookup import _get_devices_by_type_handler

    mock_indigo.devices = [
        _fake_device(1, "Dim1", type_id="dimmer"),
        _fake_device(2, "Relay1", type_id="relay"),
    ]
    result = _get_devices_by_type_handler({"device_type": "thermostat"}, mock_indigo)
    assert result["total_count"] == 0
    assert result["results"] == []
    assert result["has_more"] is False


def test_get_devices_by_type_paginates_filtered(mock_indigo):
    from tools.lookup import _get_devices_by_type_handler

    # 6 dimmers interleaved with 4 relays — pagination should run on
    # the filtered set, not the raw set.
    devices = []
    for i in range(6):
        devices.append(_fake_device(100 + i, f"Dim{i}", type_id="dimmer"))
    for i in range(4):
        devices.append(_fake_device(200 + i, f"Rel{i}", type_id="relay"))
    mock_indigo.devices = devices

    result = _get_devices_by_type_handler(
        {"device_type": "dimmer", "limit": 3, "offset": 0},
        mock_indigo,
    )
    assert result["total_count"] == 6
    assert len(result["results"]) == 3
    assert result["has_more"] is True
    # First page must be the first three dimmers in iteration order.
    assert [d["id"] for d in result["results"]] == [100, 101, 102]


def test_get_devices_by_type_missing_arg_raises(mock_indigo):
    from tools.lookup import _get_devices_by_type_handler

    with pytest.raises(ValueError, match="device_type"):
        _get_devices_by_type_handler({}, mock_indigo)


def test_get_devices_by_type_non_string_raises(mock_indigo):
    from tools.lookup import _get_devices_by_type_handler

    with pytest.raises(ValueError, match="device_type"):
        _get_devices_by_type_handler({"device_type": 42}, mock_indigo)
