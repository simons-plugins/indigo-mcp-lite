"""Tests for the trigger & schedule tools (control-wave PR #29)."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tools.automations import (
    _get_schedule_handler,
    _get_trigger_handler,
    _paginate,
    _schedule_enable_handler,
    _schedule_execute_handler,
    _serialize_automation,
    _trigger_enable_handler,
    _trigger_execute_handler,
)


class _Trigger(SimpleNamespace):
    pass


def _fake_trigger(id_, name, enabled=True):
    return _Trigger(id=id_, name=name, description="", enabled=enabled,
                    folderId=0)


def _fake_schedule(id_, name):
    s = _Trigger(id=id_, name=name, description="", enabled=True, folderId=0)
    s.nextExecution = datetime(2026, 7, 23, 7, 0, 0)
    return s


def test_serialize_trigger_shape():
    out = _serialize_automation(_fake_trigger(1, "Door opened"))
    assert out == {
        "id": 1, "name": "Door opened", "type": "_Trigger",
        "description": "", "enabled": True, "folder_id": 0,
    }


def test_serialize_schedule_includes_next_execution():
    out = _serialize_automation(_fake_schedule(2, "Evening lights"))
    assert out["next_execution"] == "2026-07-23T07:00:00"


def test_paginate_envelope():
    items = [_fake_trigger(i, f"T{i}") for i in range(5)]
    out = _paginate(items, {"limit": 2, "offset": 4})
    assert out["total_count"] == 5
    assert [r["id"] for r in out["results"]] == [4]
    assert out["has_more"] is False


def test_get_by_id_and_missing(mock_indigo):
    store = {7: _fake_trigger(7, "X")}
    mock_indigo.triggers.__getitem__.side_effect = lambda i: store[i]
    assert _get_trigger_handler({"id": 7}, mock_indigo)["name"] == "X"
    with pytest.raises(ValueError, match="999"):
        _get_trigger_handler({"id": 999}, mock_indigo)


def test_enable_handlers(mock_indigo):
    _trigger_enable_handler({"id": 5, "enabled": False}, mock_indigo)
    mock_indigo.trigger.enable.assert_called_once_with(5, value=False)
    _schedule_enable_handler({"id": 6, "enabled": True}, mock_indigo)
    mock_indigo.schedule.enable.assert_called_once_with(6, value=True)


def test_enable_requires_boolean(mock_indigo):
    with pytest.raises(ValueError, match="boolean"):
        _trigger_enable_handler({"id": 5, "enabled": "on"}, mock_indigo)


def test_execute_defaults_honour_conditions(mock_indigo):
    _trigger_execute_handler({"id": 5}, mock_indigo)
    mock_indigo.trigger.execute.assert_called_once_with(
        5, ignoreConditions=False)
    _schedule_execute_handler({"id": 6, "ignore_conditions": True}, mock_indigo)
    mock_indigo.schedule.execute.assert_called_once_with(
        6, ignoreConditions=True)


def test_schedule_get(mock_indigo):
    store = {2: _fake_schedule(2, "Evening")}
    mock_indigo.schedules.__getitem__.side_effect = lambda i: store[i]
    out = _get_schedule_handler({"id": 2}, mock_indigo)
    assert out["next_execution"].startswith("2026-07-23")


def test_register_all_includes_automation_tools(mock_indigo):
    from tool_registry import register_all

    handler = MagicMock()
    register_all(handler, indigo_module=mock_indigo)
    names = {
        (call.kwargs.get("name") or (call.args[0] if call.args else None))
        for call in handler.register_tool.call_args_list
    }
    assert {
        "list_triggers", "get_trigger_by_id", "trigger_enable",
        "trigger_execute", "list_schedules", "get_schedule_by_id",
        "schedule_enable", "schedule_execute",
    } <= names


def test_paginate_defaults_and_overflow():
    items = [_fake_trigger(i, f"T{i}") for i in range(3)]
    out = _paginate(items, {})
    assert (out["limit"], out["offset"], out["total_count"]) == (50, 0, 3)
    out = _paginate(items, {"offset": 10})
    assert out["results"] == [] and out["has_more"] is False


def test_enable_missing_enabled_rejected(mock_indigo):
    mock_indigo.triggers.__getitem__.side_effect = lambda i: _fake_trigger(5, "X")
    with pytest.raises(ValueError, match="enabled must be a boolean"):
        _trigger_enable_handler({"id": 5}, mock_indigo)


def test_mutating_tools_reject_unknown_id(mock_indigo):
    def _missing(i):
        raise KeyError(i)
    mock_indigo.triggers.__getitem__.side_effect = _missing
    mock_indigo.schedules.__getitem__.side_effect = _missing
    with pytest.raises(ValueError, match="no trigger with id 999"):
        _trigger_execute_handler({"id": 999}, mock_indigo)
    with pytest.raises(ValueError, match="no schedule with id 999"):
        _schedule_enable_handler({"id": 999, "enabled": True}, mock_indigo)
    mock_indigo.trigger.execute.assert_not_called()
    mock_indigo.schedule.enable.assert_not_called()


def test_unknown_arg_rejected(mock_indigo):
    with pytest.raises(ValueError, match="unknown argument"):
        _trigger_execute_handler({"id": 5, "force": True}, mock_indigo)
