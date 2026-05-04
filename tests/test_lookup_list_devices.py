"""TDD tests for tools.lookup._list_devices_handler.

Establishes the test fixture pattern (`_fake_device`) and pagination
expectations (default 50, max 500, offset). Sibling list tools
(list_variables, list_action_groups, get_devices_by_type, etc.)
inherit this style.
"""

from unittest.mock import MagicMock

# Module under test imported at function level so the conftest sys.path
# insert is in effect first.


def _fake_device(id_, name, type_id="dimmer", on=False, brightness=0, folder_id=0):
    d = MagicMock()
    d.id = id_
    d.name = name
    d.deviceTypeId = type_id
    d.onState = on
    d.brightness = brightness
    d.folderId = folder_id
    d.description = ""
    d.model = ""
    d.address = ""
    d.pluginId = ""
    return d


def test_list_devices_returns_all_devices_under_limit(mock_indigo):
    from tools.lookup import _list_devices_handler

    mock_indigo.devices = [_fake_device(1, "A"), _fake_device(2, "B")]
    result = _list_devices_handler({}, mock_indigo)
    assert result["total_count"] == 2
    assert len(result["results"]) == 2
    assert result["has_more"] is False


def test_list_devices_paginates(mock_indigo):
    from tools.lookup import _list_devices_handler

    mock_indigo.devices = [_fake_device(i, f"D{i}") for i in range(120)]
    result = _list_devices_handler({"limit": 50, "offset": 0}, mock_indigo)
    assert result["total_count"] == 120
    assert len(result["results"]) == 50
    assert result["has_more"] is True
    assert result["offset"] == 0


def test_list_devices_offset_works(mock_indigo):
    from tools.lookup import _list_devices_handler

    mock_indigo.devices = [_fake_device(i, f"D{i}") for i in range(10)]
    result = _list_devices_handler({"limit": 5, "offset": 5}, mock_indigo)
    assert result["total_count"] == 10
    assert len(result["results"]) == 5
    assert result["results"][0]["id"] == 5
    assert result["has_more"] is False


def test_list_devices_default_limit_is_50(mock_indigo):
    from tools.lookup import _list_devices_handler

    mock_indigo.devices = [_fake_device(i, f"D{i}") for i in range(80)]
    result = _list_devices_handler({}, mock_indigo)
    assert len(result["results"]) == 50


def test_list_devices_caps_limit_at_500(mock_indigo):
    from tools.lookup import _list_devices_handler

    mock_indigo.devices = [_fake_device(i, f"D{i}") for i in range(1000)]
    result = _list_devices_handler({"limit": 9999}, mock_indigo)
    assert len(result["results"]) == 500


def test_list_devices_serializes_expected_fields(mock_indigo):
    from tools.lookup import _list_devices_handler

    dev = _fake_device(42, "Bedside Lamp", type_id="dimmer", brightness=80, folder_id=3)
    dev.description = "Above the nightstand"
    dev.model = "LIFX A19"
    dev.address = "192.168.1.50"
    dev.pluginId = "com.lifx.indigoplugin"
    mock_indigo.devices = [dev]

    result = _list_devices_handler({}, mock_indigo)
    [serialized] = result["results"]
    assert serialized["id"] == 42
    assert serialized["name"] == "Bedside Lamp"
    assert serialized["type"] == "dimmer"
    assert serialized["brightness"] == 80
    assert serialized["folder_id"] == 3
    assert serialized["description"] == "Above the nightstand"
    assert serialized["model"] == "LIFX A19"
    assert serialized["address"] == "192.168.1.50"
    assert serialized["plugin_id"] == "com.lifx.indigoplugin"
