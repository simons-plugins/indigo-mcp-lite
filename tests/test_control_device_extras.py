"""Tests for control wave 1 — toggle, delay/duration, enable, status,
lock/unlock, relative brighten/dim, ping/beep/energy-reset."""

from unittest.mock import MagicMock

import pytest

from tools.control import (
    _beep_handler,
    _brighten_handler,
    _dim_handler,
    _enable_handler,
    _lock_handler,
    _ping_handler,
    _reset_energy_handler,
    _status_request_handler,
    _toggle_handler,
    _turn_on_handler,
    _unlock_handler,
)


def test_toggle_plain(mock_indigo):
    assert _toggle_handler({"device_id": 5}, mock_indigo) == {"status": "ok"}
    mock_indigo.device.toggle.assert_called_once_with(5)


def test_toggle_with_delay_duration(mock_indigo):
    _toggle_handler({"device_id": 5, "delay": 10, "duration": 300}, mock_indigo)
    mock_indigo.device.toggle.assert_called_once_with(5, delay=10, duration=300)


def test_turn_on_forwards_only_given_kwargs(mock_indigo):
    _turn_on_handler({"device_id": 5, "duration": 60}, mock_indigo)
    mock_indigo.device.turnOn.assert_called_once_with(5, duration=60)


def test_negative_delay_rejected(mock_indigo):
    with pytest.raises(ValueError, match="delay"):
        _toggle_handler({"device_id": 5, "delay": -1}, mock_indigo)
    mock_indigo.device.toggle.assert_not_called()


def test_bool_duration_rejected(mock_indigo):
    with pytest.raises(ValueError, match="duration"):
        _turn_on_handler({"device_id": 5, "duration": True}, mock_indigo)


def test_enable_and_disable(mock_indigo):
    _enable_handler({"device_id": 5, "enabled": False}, mock_indigo)
    mock_indigo.device.enable.assert_called_once_with(5, value=False)


def test_enable_requires_boolean(mock_indigo):
    with pytest.raises(ValueError, match="boolean"):
        _enable_handler({"device_id": 5, "enabled": "yes"}, mock_indigo)


def test_status_request_logs_and_notes_async(mock_indigo):
    out = _status_request_handler({"device_id": 5}, mock_indigo)
    mock_indigo.device.statusRequest.assert_called_once_with(
        5, suppressLogging=False
    )
    assert out["status"] == "requested" and "asynchronous" in out["note"]


def test_lock_unlock_with_duration(mock_indigo):
    out = _lock_handler({"device_id": 7, "duration": 30}, mock_indigo)
    mock_indigo.device.lock.assert_called_once_with(7, duration=30)
    assert out["status"] == "dispatched" and "on_state" in out["note"]
    out = _unlock_handler({"device_id": 7}, mock_indigo)
    mock_indigo.device.unlock.assert_called_once_with(7)
    assert out["status"] == "dispatched"


def test_unknown_arg_rejected_with_valid_list(mock_indigo):
    with pytest.raises(ValueError, match="delay_seconds.*valid.*delay"):
        _turn_on_handler({"device_id": 5, "delay_seconds": 10}, mock_indigo)
    mock_indigo.device.turnOn.assert_not_called()


def test_integral_floats_coerce(mock_indigo):
    _toggle_handler({"device_id": 5.0, "delay": 10.0}, mock_indigo)
    mock_indigo.device.toggle.assert_called_once_with(5, delay=10)
    with pytest.raises(ValueError, match="delay"):
        _toggle_handler({"device_id": 5, "delay": 1.5}, mock_indigo)


def test_ping_unexpected_result_raises(mock_indigo):
    mock_indigo.device.ping.return_value = None
    with pytest.raises(ValueError, match="may not support ping"):
        _ping_handler({"device_id": 9}, mock_indigo)


def test_brighten_dim_relative(mock_indigo):
    _brighten_handler({"device_id": 3, "by": 20}, mock_indigo)
    mock_indigo.dimmer.brighten.assert_called_once_with(3, by=20)
    _dim_handler({"device_id": 3, "by": 10, "delay": 4}, mock_indigo)
    mock_indigo.dimmer.dim.assert_called_once_with(3, by=10, delay=4)


def test_brighten_by_out_of_range(mock_indigo):
    with pytest.raises(ValueError, match="1-100"):
        _brighten_handler({"device_id": 3, "by": 0}, mock_indigo)
    with pytest.raises(ValueError, match="1-100"):
        _dim_handler({"device_id": 3, "by": 101}, mock_indigo)


def test_brighten_rejects_duration(mock_indigo):
    # Caught by the unknown-arg guard (duration isn't a brighten arg).
    with pytest.raises(ValueError, match="unknown argument"):
        _brighten_handler({"device_id": 3, "duration": 5}, mock_indigo)


def test_ping_success_and_failure(mock_indigo):
    mock_indigo.device.ping.return_value = {"Success": True, "TimeDelta": 142}
    assert _ping_handler({"device_id": 9}, mock_indigo) == {
        "success": True, "time_ms": 142,
    }
    mock_indigo.device.ping.return_value = {"Success": False, "TimeDelta": 0}
    assert _ping_handler({"device_id": 9}, mock_indigo) == {
        "success": False, "time_ms": None,
    }


def test_beep_and_reset_energy(mock_indigo):
    _beep_handler({"device_id": 4}, mock_indigo)
    mock_indigo.device.beep.assert_called_once_with(4)
    _reset_energy_handler({"device_id": 4}, mock_indigo)
    mock_indigo.device.resetEnergyAccumTotal.assert_called_once_with(4)


def test_register_all_includes_wave1_tools(mock_indigo):
    from tool_registry import register_all

    handler = MagicMock()
    register_all(handler, indigo_module=mock_indigo)
    names = {
        (call.kwargs.get("name") or (call.args[0] if call.args else None))
        for call in handler.register_tool.call_args_list
    }
    assert {
        "device_toggle", "device_enable", "device_status_request",
        "device_lock", "device_unlock", "device_brighten", "device_dim",
        "device_ping", "device_beep", "device_reset_energy_accum",
    } <= names