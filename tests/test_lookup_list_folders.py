"""TDD tests for tools.lookup folder listers.

Covers _list_variable_folders_handler + _list_device_folders_handler
together because they're symmetric — same shape, different source.
No pagination (folders are few).
"""

from unittest.mock import MagicMock


def _fake_folder(id_, name):
    f = MagicMock()
    f.id = id_
    f.name = name
    return f


# ----- variable folders ---------------------------------------------------


def test_list_variable_folders_empty(mock_indigo):
    from tools.lookup import _list_variable_folders_handler

    mock_indigo.variables.folders = []
    result = _list_variable_folders_handler({}, mock_indigo)
    assert result == {"results": [], "total_count": 0}


def test_list_variable_folders_returns_in_order(mock_indigo):
    from tools.lookup import _list_variable_folders_handler

    mock_indigo.variables.folders = [
        _fake_folder(1, "Doors"),
        _fake_folder(2, "Sensors"),
        _fake_folder(3, "Modes"),
    ]
    result = _list_variable_folders_handler({}, mock_indigo)
    assert result["total_count"] == 3
    assert [f["name"] for f in result["results"]] == ["Doors", "Sensors", "Modes"]


def test_list_variable_folders_serializes_id_and_name(mock_indigo):
    from tools.lookup import _list_variable_folders_handler

    mock_indigo.variables.folders = [_fake_folder(99, "Misc")]
    [serialized] = _list_variable_folders_handler({}, mock_indigo)["results"]
    assert serialized == {"id": 99, "name": "Misc"}


# ----- device folders -----------------------------------------------------


def test_list_device_folders_empty(mock_indigo):
    from tools.lookup import _list_device_folders_handler

    mock_indigo.devices.folders = []
    result = _list_device_folders_handler({}, mock_indigo)
    assert result == {"results": [], "total_count": 0}


def test_list_device_folders_returns_in_order(mock_indigo):
    from tools.lookup import _list_device_folders_handler

    mock_indigo.devices.folders = [
        _fake_folder(10, "Lights"),
        _fake_folder(11, "Heating"),
    ]
    result = _list_device_folders_handler({}, mock_indigo)
    assert result["total_count"] == 2
    assert [f["name"] for f in result["results"]] == ["Lights", "Heating"]


def test_list_device_folders_serializes_id_and_name(mock_indigo):
    from tools.lookup import _list_device_folders_handler

    mock_indigo.devices.folders = [_fake_folder(7, "Garage")]
    [serialized] = _list_device_folders_handler({}, mock_indigo)["results"]
    assert serialized == {"id": 7, "name": "Garage"}
