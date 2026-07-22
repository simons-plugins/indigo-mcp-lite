"""Tests for sprinkler / speed-control / variable-management tools (wave 3)."""

from unittest.mock import MagicMock

import pytest

from tools.irrigation_speed import (
    _run_schedule_handler,
    _run_zone_handler,
    _set_speed_index_handler,
    _set_speed_level_handler,
    _speed_step_handler,
    _sprinkler_simple,
    _variable_delete_handler,
    _variable_move_handler,
)


def test_run_zone(mock_indigo):
    _run_zone_handler({"device_id": 1, "zone": 3}, mock_indigo)
    mock_indigo.sprinkler.setActiveZone.assert_called_once_with(1, index=3)


def test_run_zone_rejects_zero_and_bool(mock_indigo):
    with pytest.raises(ValueError, match="zone"):
        _run_zone_handler({"device_id": 1, "zone": 0}, mock_indigo)
    with pytest.raises(ValueError, match="zone"):
        _run_zone_handler({"device_id": 1, "zone": True}, mock_indigo)


def test_run_schedule(mock_indigo):
    _run_schedule_handler(
        {"device_id": 1, "durations": [10, 15, 0, 8]}, mock_indigo)
    mock_indigo.sprinkler.run.assert_called_once_with(
        1, schedule=[10, 15, 0, 8])


def test_run_schedule_rejects_bad_lists(mock_indigo):
    for bad in ([], [5, -1], [5, "x"], [True], "10,20"):
        with pytest.raises(ValueError, match="durations"):
            _run_schedule_handler({"device_id": 1, "durations": bad},
                                  mock_indigo)


def test_sprinkler_simple_methods(mock_indigo):
    for method in ("stop", "pause", "resume", "nextZone", "previousZone"):
        _sprinkler_simple({"device_id": 2}, mock_indigo, method)
        getattr(mock_indigo.sprinkler, method).assert_called_once_with(2)


def test_speed_index_and_level(mock_indigo):
    _set_speed_index_handler({"device_id": 3, "index": 2}, mock_indigo)
    mock_indigo.speedcontrol.setSpeedIndex.assert_called_once_with(3, value=2)
    _set_speed_level_handler({"device_id": 3, "level": 75}, mock_indigo)
    mock_indigo.speedcontrol.setSpeedLevel.assert_called_once_with(3, value=75)


def test_speed_level_range(mock_indigo):
    with pytest.raises(ValueError, match="0-100"):
        _set_speed_level_handler({"device_id": 3, "level": 101}, mock_indigo)


def test_speed_step_with_and_without_by(mock_indigo):
    _speed_step_handler({"device_id": 3}, mock_indigo, "increaseSpeedIndex")
    mock_indigo.speedcontrol.increaseSpeedIndex.assert_called_once_with(3)
    _speed_step_handler({"device_id": 3, "by": 2}, mock_indigo,
                        "decreaseSpeedIndex")
    mock_indigo.speedcontrol.decreaseSpeedIndex.assert_called_once_with(
        3, by=2)


def test_variable_delete_and_move(mock_indigo):
    _variable_delete_handler({"variable_id": 9}, mock_indigo)
    mock_indigo.variable.delete.assert_called_once_with(9)
    _variable_move_handler({"variable_id": 9, "folder_id": 4}, mock_indigo)
    mock_indigo.variable.moveToFolder.assert_called_once_with(9, value=4)


def test_register_all_includes_wave3_tools(mock_indigo):
    from tool_registry import register_all

    handler = MagicMock()
    register_all(handler, indigo_module=mock_indigo)
    names = {
        (call.kwargs.get("name") or (call.args[0] if call.args else None))
        for call in handler.register_tool.call_args_list
    }
    assert {
        "sprinkler_run_zone", "sprinkler_run_schedule", "sprinkler_stop",
        "sprinkler_pause", "sprinkler_resume", "sprinkler_next_zone",
        "sprinkler_previous_zone", "speedcontrol_set_index",
        "speedcontrol_set_level", "speedcontrol_increase",
        "speedcontrol_decrease", "variable_delete", "variable_move_to_folder",
    } <= names


def test_unknown_device_id_friendly_error(mock_indigo):
    def _missing(i):
        raise KeyError(i)
    mock_indigo.devices.__getitem__.side_effect = _missing
    with pytest.raises(ValueError, match="no device with id 404"):
        _sprinkler_simple({"device_id": 404}, mock_indigo, "stop")
    mock_indigo.sprinkler.stop.assert_not_called()


def test_variable_delete_unknown_id(mock_indigo):
    def _missing(i):
        raise KeyError(i)
    mock_indigo.variables.__getitem__.side_effect = _missing
    with pytest.raises(ValueError, match="no variable with id 404"):
        _variable_delete_handler({"variable_id": 404}, mock_indigo)
    mock_indigo.variable.delete.assert_not_called()


def test_unknown_arg_and_float_coercion(mock_indigo):
    with pytest.raises(ValueError, match="unknown argument"):
        _run_zone_handler({"device_id": 1, "zone_index": 3}, mock_indigo)
    _run_zone_handler({"device_id": 1, "zone": 3.0}, mock_indigo)
    mock_indigo.sprinkler.setActiveZone.assert_called_once_with(1, index=3)
