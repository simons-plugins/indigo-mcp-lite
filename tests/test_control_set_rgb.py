"""TDD tests for device_set_rgb_color (bytes) and
device_set_rgb_percent (percent).

Both call ``indigo.dimmer.setColorLevels`` which expects 0-100
percent levels — the byte tool converts 0-255 → 0-100, the percent
tool clamps and passes through.
"""
import pytest


# ----- device_set_rgb_color (input 0-255 bytes) --------------------------


def test_set_rgb_color_converts_bytes_to_percent(mock_indigo):
    from tools.control import _set_rgb_color_handler

    result = _set_rgb_color_handler(
        {"device_id": 7, "red": 255, "green": 128, "blue": 0},
        mock_indigo,
    )
    # 255 → 100, 128 → 50 (round 50.196 ≈ 50), 0 → 0
    mock_indigo.dimmer.setColorLevels.assert_called_once_with(
        7, redLevel=100, greenLevel=50, blueLevel=0
    )
    assert result["status"] == "ok"


def test_set_rgb_color_clamps_out_of_range_bytes(mock_indigo):
    from tools.control import _set_rgb_color_handler

    _set_rgb_color_handler(
        {"device_id": 7, "red": 300, "green": -10, "blue": 128},
        mock_indigo,
    )
    mock_indigo.dimmer.setColorLevels.assert_called_once_with(
        7, redLevel=100, greenLevel=0, blueLevel=50
    )


def test_set_rgb_color_missing_channel_raises(mock_indigo):
    from tools.control import _set_rgb_color_handler

    with pytest.raises(ValueError, match="green"):
        _set_rgb_color_handler({"device_id": 7, "red": 255, "blue": 0}, mock_indigo)
    mock_indigo.dimmer.setColorLevels.assert_not_called()


def test_set_rgb_color_missing_device_id_raises(mock_indigo):
    from tools.control import _set_rgb_color_handler

    with pytest.raises(ValueError, match="device_id"):
        _set_rgb_color_handler({"red": 1, "green": 2, "blue": 3}, mock_indigo)
    mock_indigo.dimmer.setColorLevels.assert_not_called()


# ----- device_set_rgb_percent (input 0-100 percent) ----------------------


def test_set_rgb_percent_passes_through(mock_indigo):
    from tools.control import _set_rgb_percent_handler

    result = _set_rgb_percent_handler(
        {"device_id": 7, "red": 100, "green": 50, "blue": 0},
        mock_indigo,
    )
    mock_indigo.dimmer.setColorLevels.assert_called_once_with(
        7, redLevel=100, greenLevel=50, blueLevel=0
    )
    assert result["status"] == "ok"


def test_set_rgb_percent_clamps_out_of_range(mock_indigo):
    from tools.control import _set_rgb_percent_handler

    _set_rgb_percent_handler(
        {"device_id": 7, "red": 150, "green": -5, "blue": 50},
        mock_indigo,
    )
    mock_indigo.dimmer.setColorLevels.assert_called_once_with(
        7, redLevel=100, greenLevel=0, blueLevel=50
    )


def test_set_rgb_percent_missing_channel_raises(mock_indigo):
    from tools.control import _set_rgb_percent_handler

    with pytest.raises(ValueError, match="blue"):
        _set_rgb_percent_handler({"device_id": 7, "red": 50, "green": 50}, mock_indigo)
    mock_indigo.dimmer.setColorLevels.assert_not_called()
