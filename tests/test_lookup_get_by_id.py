"""TDD tests for the get_*_by_id family.

Covers _get_device_handler / _get_variable_handler /
_get_action_group_handler. Each takes ``{"id": int}`` and returns
the same per-entity shape as the matching list tool. Missing,
non-int, or unknown ids raise ValueError — the MCPHandler maps that
to a tool-result error (isError: true).
"""

from unittest.mock import MagicMock

import pytest


# ----- fixture helpers ---------------------------------------------------


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


def _fake_variable(id_, name, value=""):
    v = MagicMock()
    v.id = id_
    v.name = name
    v.value = value
    v.folderId = 0
    v.description = ""
    return v


def _fake_action_group(id_, name):
    a = MagicMock()
    a.id = id_
    a.name = name
    a.folderId = 0
    a.description = ""
    return a


def _attach_store(collection, store):
    """Wire a MagicMock collection so ``collection[id]`` reads from
    ``store`` and raises KeyError when the id is unknown."""
    collection.__getitem__.side_effect = lambda i: store[i]


# ----- get_device_by_id --------------------------------------------------


def test_get_device_by_id_happy_path(mock_indigo):
    from tools.lookup import _get_device_handler

    _attach_store(mock_indigo.devices, {42: _fake_device(42, "X")})
    result = _get_device_handler({"id": 42}, mock_indigo)
    assert result["id"] == 42
    assert result["name"] == "X"
    assert result["type"] == "dimmer"


def test_get_device_by_id_missing_raises(mock_indigo):
    from tools.lookup import _get_device_handler

    _attach_store(mock_indigo.devices, {1: _fake_device(1, "A")})
    with pytest.raises(ValueError, match="999"):
        _get_device_handler({"id": 999}, mock_indigo)


def test_get_device_by_id_non_int_raises(mock_indigo):
    from tools.lookup import _get_device_handler

    with pytest.raises(ValueError, match="integer"):
        _get_device_handler({"id": "fortytwo"}, mock_indigo)


def test_get_device_by_id_missing_arg_raises(mock_indigo):
    from tools.lookup import _get_device_handler

    with pytest.raises(ValueError, match="integer"):
        _get_device_handler({}, mock_indigo)


# ----- get_variable_by_id ------------------------------------------------


def test_get_variable_by_id_happy_path(mock_indigo):
    from tools.lookup import _get_variable_handler

    _attach_store(mock_indigo.variables, {3: _fake_variable(3, "doorOpen", value="true")})
    result = _get_variable_handler({"id": 3}, mock_indigo)
    assert result["id"] == 3
    assert result["name"] == "doorOpen"
    assert result["value"] == "true"


def test_get_variable_by_id_missing_raises(mock_indigo):
    from tools.lookup import _get_variable_handler

    _attach_store(mock_indigo.variables, {1: _fake_variable(1, "A")})
    with pytest.raises(ValueError, match="999"):
        _get_variable_handler({"id": 999}, mock_indigo)


def test_get_variable_by_id_non_int_raises(mock_indigo):
    from tools.lookup import _get_variable_handler

    with pytest.raises(ValueError, match="integer"):
        _get_variable_handler({"id": "three"}, mock_indigo)


def test_get_variable_by_id_missing_arg_raises(mock_indigo):
    from tools.lookup import _get_variable_handler

    with pytest.raises(ValueError, match="integer"):
        _get_variable_handler({}, mock_indigo)


# ----- get_action_group_by_id --------------------------------------------


def test_get_action_group_by_id_happy_path(mock_indigo):
    from tools.lookup import _get_action_group_handler

    _attach_store(mock_indigo.actionGroups, {12: _fake_action_group(12, "Goodnight")})
    result = _get_action_group_handler({"id": 12}, mock_indigo)
    assert result["id"] == 12
    assert result["name"] == "Goodnight"


def test_get_action_group_by_id_missing_raises(mock_indigo):
    from tools.lookup import _get_action_group_handler

    _attach_store(mock_indigo.actionGroups, {1: _fake_action_group(1, "A")})
    with pytest.raises(ValueError, match="999"):
        _get_action_group_handler({"id": 999}, mock_indigo)


def test_get_action_group_by_id_non_int_raises(mock_indigo):
    from tools.lookup import _get_action_group_handler

    with pytest.raises(ValueError, match="integer"):
        _get_action_group_handler({"id": "twelve"}, mock_indigo)


def test_get_action_group_by_id_missing_arg_raises(mock_indigo):
    from tools.lookup import _get_action_group_handler

    with pytest.raises(ValueError, match="integer"):
        _get_action_group_handler({}, mock_indigo)
