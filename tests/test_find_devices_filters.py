"""TDD tests for find_devices filters: room / type / entity_type."""
from unittest.mock import MagicMock

import pytest


class _AttrList(list):
    pass


def _device(id_, name, type_id="dimmer", folder_id=0):
    d = MagicMock()
    d.id = id_; d.name = name
    d.deviceTypeId = type_id; d.folderId = folder_id
    d.description = ""; d.model = ""; d.address = ""
    return d


def _variable(id_, name, folder_id=0):
    v = MagicMock()
    v.id = id_; v.name = name; v.value = ""; v.folderId = folder_id
    return v


def _action(id_, name):
    a = MagicMock()
    a.id = id_; a.name = name; a.folderId = 0; a.description = ""
    return a


def _make_indexer(mock_indigo, *, devs=(), variables=(), actions=(),
                   dev_folders=None, var_folders=None):
    from indexer import Indexer

    d = _AttrList(devs)
    d.folders = MagicMock()
    d.folders.getName.side_effect = lambda fid: (dev_folders or {}).get(fid, "")
    mock_indigo.devices = d

    v = _AttrList(variables)
    v.folders = MagicMock()
    v.folders.getName.side_effect = lambda fid: (var_folders or {}).get(fid, "")
    mock_indigo.variables = v

    mock_indigo.actionGroups = _AttrList(actions)

    idx = Indexer(indigo_module=mock_indigo)
    idx.build()
    return idx


# ----- room filter -------------------------------------------------------


def test_room_filter_narrows_to_named_folder(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(
        mock_indigo,
        devs=[
            _device(1, "Lamp", folder_id=10),
            _device(2, "Lamp", folder_id=20),
        ],
        dev_folders={10: "Kitchen", 20: "Bedroom"},
    )

    result = _find_devices_handler(
        {"query": "lamp", "room": "Kitchen"}, indexer=idx
    )
    ids = [r["id"] for r in result["results"]]
    assert ids == [1]


def test_room_filter_is_case_insensitive(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(
        mock_indigo,
        devs=[_device(1, "Lamp", folder_id=10)],
        dev_folders={10: "Kitchen"},
    )

    result = _find_devices_handler(
        {"query": "lamp", "room": "kitchen"}, indexer=idx
    )
    assert result["total_count"] == 1


# ----- type filter -------------------------------------------------------


def test_type_filter_narrows_by_device_type(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(
        mock_indigo,
        devs=[
            _device(1, "Kitchen", type_id="dimmer"),
            _device(2, "Kitchen", type_id="relay"),
        ],
    )

    result = _find_devices_handler(
        {"query": "kitchen", "type": "dimmer"}, indexer=idx
    )
    ids = [r["id"] for r in result["results"]]
    assert ids == [1]


# ----- entity_type filter ------------------------------------------------


def test_entity_type_filter_excludes_other_types(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(
        mock_indigo,
        devs=[_device(1, "Kitchen Dimmer")],
        variables=[_variable(2, "kitchenMode")],
        actions=[_action(3, "Kitchen Goodnight")],
    )

    result = _find_devices_handler(
        {"query": "kitchen", "entity_type": "device"}, indexer=idx
    )
    types = {r["entity_type"] for r in result["results"]}
    assert types == {"device"}


def test_entity_type_filter_accepts_list(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(
        mock_indigo,
        devs=[_device(1, "Kitchen Dimmer")],
        variables=[_variable(2, "kitchenMode")],
        actions=[_action(3, "Kitchen Goodnight")],
    )

    result = _find_devices_handler(
        {"query": "kitchen", "entity_type": ["device", "action"]}, indexer=idx
    )
    types = {r["entity_type"] for r in result["results"]}
    assert types == {"device", "action"}


def test_entity_type_filter_invalid_raises(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(mock_indigo, devs=[_device(1, "x")])
    with pytest.raises(ValueError, match="entity_type"):
        _find_devices_handler(
            {"query": "x", "entity_type": "trigger"}, indexer=idx
        )


# ----- combinations ------------------------------------------------------


def test_room_plus_type_plus_query_combines_correctly(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(
        mock_indigo,
        devs=[
            _device(1, "Counter Light", type_id="dimmer", folder_id=10),
            _device(2, "Counter Light", type_id="relay", folder_id=10),
            _device(3, "Bedside", type_id="dimmer", folder_id=20),
        ],
        dev_folders={10: "Kitchen", 20: "Bedroom"},
    )

    result = _find_devices_handler(
        {"query": "light", "room": "Kitchen", "type": "dimmer"}, indexer=idx
    )
    ids = [r["id"] for r in result["results"]]
    assert ids == [1]
