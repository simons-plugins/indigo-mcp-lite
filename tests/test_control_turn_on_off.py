"""TDD tests for device_turn_on / device_turn_off control tools.

Both tools accept ``{"device_id": int}``. Validation is shared with
the lookup tools (``_require_int_id`` in ``tools.lookup``), so the
non-int / missing-arg paths exercise that helper indirectly.
"""
import pytest


def test_turn_on_calls_indigo_device_turn_on(mock_indigo):
    from tools.control import _turn_on_handler

    result = _turn_on_handler({"device_id": 42}, mock_indigo)
    mock_indigo.device.turnOn.assert_called_once_with(42)
    assert result["status"] == "ok"


def test_turn_off_calls_indigo_device_turn_off(mock_indigo):
    from tools.control import _turn_off_handler

    result = _turn_off_handler({"device_id": 42}, mock_indigo)
    mock_indigo.device.turnOff.assert_called_once_with(42)
    assert result["status"] == "ok"


def test_turn_on_missing_device_id_raises(mock_indigo):
    from tools.control import _turn_on_handler

    with pytest.raises(ValueError, match="device_id"):
        _turn_on_handler({}, mock_indigo)
    mock_indigo.device.turnOn.assert_not_called()


def test_turn_on_non_int_device_id_raises(mock_indigo):
    from tools.control import _turn_on_handler

    with pytest.raises(ValueError, match="device_id"):
        _turn_on_handler({"device_id": "fortytwo"}, mock_indigo)
    mock_indigo.device.turnOn.assert_not_called()


def test_turn_off_missing_device_id_raises(mock_indigo):
    from tools.control import _turn_off_handler

    with pytest.raises(ValueError, match="device_id"):
        _turn_off_handler({}, mock_indigo)
    mock_indigo.device.turnOff.assert_not_called()


def test_turn_on_rejects_bool_device_id(mock_indigo):
    from tools.control import _turn_on_handler

    # isinstance(True, int) is True in Python — would silently turn on
    # device id 1. The validator must reject this.
    with pytest.raises(ValueError, match="device_id"):
        _turn_on_handler({"device_id": True}, mock_indigo)
    mock_indigo.device.turnOn.assert_not_called()
