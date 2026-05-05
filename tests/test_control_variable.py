"""TDD tests for variable_create and variable_update.

variable_create: ``{"name": str, "value": str, "folder": int|None}``
variable_update: ``{"variable_id": int, "value": str}``

Indigo variable values are always strings on the wire; numeric and
boolean callers must serialise themselves before calling.
"""
from unittest.mock import MagicMock

import pytest


def _fake_var(id_, name, value="", folder_id=0):
    """Build a stand-in for an Indigo variable.

    MagicMock's ``name`` kwarg sets the *mock's* name (used in repr),
    not a ``.name`` attribute — assigning afterwards is the canonical
    workaround so ``var.name`` returns the actual string.
    """
    v = MagicMock()
    v.id = id_
    v.name = name
    v.value = value
    v.folderId = folder_id
    return v


def test_variable_create_calls_indigo(mock_indigo):
    from tools.control import _variable_create_handler

    mock_indigo.variable.create.return_value = _fake_var(42, "newVar", "hello")

    result = _variable_create_handler(
        {"name": "newVar", "value": "hello"}, mock_indigo
    )
    mock_indigo.variable.create.assert_called_once_with(
        "newVar", value="hello"
    )
    assert result["status"] == "ok"
    assert result["id"] == 42
    assert result["name"] == "newVar"


def test_variable_create_includes_folder_when_given(mock_indigo):
    from tools.control import _variable_create_handler

    mock_indigo.variable.create.return_value = _fake_var(42, "newVar", "hello", 5)

    _variable_create_handler(
        {"name": "newVar", "value": "hello", "folder": 5}, mock_indigo
    )
    mock_indigo.variable.create.assert_called_once_with(
        "newVar", value="hello", folder=5
    )


def test_variable_create_missing_name_raises(mock_indigo):
    from tools.control import _variable_create_handler

    with pytest.raises(ValueError, match="name"):
        _variable_create_handler({"value": "x"}, mock_indigo)
    mock_indigo.variable.create.assert_not_called()


def test_variable_create_missing_value_raises(mock_indigo):
    from tools.control import _variable_create_handler

    with pytest.raises(ValueError, match="value"):
        _variable_create_handler({"name": "newVar"}, mock_indigo)
    mock_indigo.variable.create.assert_not_called()


def test_variable_create_non_string_value_raises(mock_indigo):
    from tools.control import _variable_create_handler

    # Indigo variable values are strings on the wire — refuse to
    # silently coerce ints / bools, even though .create might accept
    # them (let the caller convert explicitly).
    with pytest.raises(ValueError, match="value"):
        _variable_create_handler(
            {"name": "newVar", "value": 42}, mock_indigo
        )
    mock_indigo.variable.create.assert_not_called()


def test_variable_update_calls_indigo(mock_indigo):
    from tools.control import _variable_update_handler

    result = _variable_update_handler(
        {"variable_id": 42, "value": "new"}, mock_indigo
    )
    mock_indigo.variable.updateValue.assert_called_once_with(42, value="new")
    assert result["status"] == "ok"


def test_variable_update_missing_value_raises(mock_indigo):
    from tools.control import _variable_update_handler

    with pytest.raises(ValueError, match="value"):
        _variable_update_handler({"variable_id": 42}, mock_indigo)
    mock_indigo.variable.updateValue.assert_not_called()


def test_variable_update_missing_variable_id_raises(mock_indigo):
    from tools.control import _variable_update_handler

    with pytest.raises(ValueError, match="variable_id"):
        _variable_update_handler({"value": "x"}, mock_indigo)
    mock_indigo.variable.updateValue.assert_not_called()


def test_variable_update_non_string_value_raises(mock_indigo):
    from tools.control import _variable_update_handler

    with pytest.raises(ValueError, match="value"):
        _variable_update_handler(
            {"variable_id": 42, "value": True}, mock_indigo
        )
    mock_indigo.variable.updateValue.assert_not_called()
