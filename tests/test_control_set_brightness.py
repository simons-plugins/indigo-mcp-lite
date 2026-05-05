"""TDD tests for device_set_brightness.

Tool args: ``{"device_id": int, "brightness": int|float}``. Brightness
goes through ``normalize_brightness`` so callers can pass 0-100, 0-1
floats, or out-of-range values that get clamped.
"""
import pytest


def test_set_brightness_calls_indigo_with_normalized_value(mock_indigo):
    from tools.control import _set_brightness_handler

    result = _set_brightness_handler({"device_id": 7, "brightness": 75}, mock_indigo)
    mock_indigo.dimmer.setBrightness.assert_called_once_with(7, value=75)
    assert result["status"] == "ok"


def test_set_brightness_scales_0_to_1_float(mock_indigo):
    from tools.control import _set_brightness_handler

    _set_brightness_handler({"device_id": 7, "brightness": 0.5}, mock_indigo)
    mock_indigo.dimmer.setBrightness.assert_called_once_with(7, value=50)


def test_set_brightness_clamps_out_of_range(mock_indigo):
    from tools.control import _set_brightness_handler

    _set_brightness_handler({"device_id": 7, "brightness": 150}, mock_indigo)
    mock_indigo.dimmer.setBrightness.assert_called_once_with(7, value=100)


def test_set_brightness_missing_device_id_raises(mock_indigo):
    from tools.control import _set_brightness_handler

    with pytest.raises(ValueError, match="device_id"):
        _set_brightness_handler({"brightness": 50}, mock_indigo)
    mock_indigo.dimmer.setBrightness.assert_not_called()


def test_set_brightness_missing_brightness_raises(mock_indigo):
    from tools.control import _set_brightness_handler

    with pytest.raises(ValueError, match="brightness"):
        _set_brightness_handler({"device_id": 7}, mock_indigo)
    mock_indigo.dimmer.setBrightness.assert_not_called()


def test_set_brightness_non_numeric_brightness_raises(mock_indigo):
    from tools.control import _set_brightness_handler

    with pytest.raises(ValueError, match="brightness"):
        _set_brightness_handler({"device_id": 7, "brightness": "fifty"}, mock_indigo)
    mock_indigo.dimmer.setBrightness.assert_not_called()
