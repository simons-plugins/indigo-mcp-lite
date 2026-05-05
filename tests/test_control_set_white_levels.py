"""TDD tests for device_set_white_levels.

Args: ``{"device_id": int, "white": 0-100, "white2": 0-100 (optional),
"temperature": int (optional Kelvin)}``. Single tool that maps to
``setColorLevels(whiteLevel=, whiteLevel2=, whiteTemperature=)``.
"""
import pytest


def test_set_white_levels_white_only(mock_indigo):
    from tools.control import _set_white_levels_handler

    result = _set_white_levels_handler(
        {"device_id": 7, "white": 80}, mock_indigo
    )
    mock_indigo.dimmer.setColorLevels.assert_called_once_with(
        7, whiteLevel=80
    )
    assert result["status"] == "ok"


def test_set_white_levels_includes_white2_when_given(mock_indigo):
    from tools.control import _set_white_levels_handler

    _set_white_levels_handler(
        {"device_id": 7, "white": 60, "white2": 40}, mock_indigo
    )
    mock_indigo.dimmer.setColorLevels.assert_called_once_with(
        7, whiteLevel=60, whiteLevel2=40
    )


def test_set_white_levels_includes_temperature_when_given(mock_indigo):
    from tools.control import _set_white_levels_handler

    _set_white_levels_handler(
        {"device_id": 7, "white": 80, "temperature": 2700}, mock_indigo
    )
    mock_indigo.dimmer.setColorLevels.assert_called_once_with(
        7, whiteLevel=80, whiteTemperature=2700
    )


def test_set_white_levels_temperature_only(mock_indigo):
    from tools.control import _set_white_levels_handler

    # Temperature without white is a valid call — the SDK supports
    # changing colour temperature without retouching the level.
    _set_white_levels_handler(
        {"device_id": 7, "temperature": 6500}, mock_indigo
    )
    mock_indigo.dimmer.setColorLevels.assert_called_once_with(
        7, whiteTemperature=6500
    )


def test_set_white_levels_clamps_white(mock_indigo):
    from tools.control import _set_white_levels_handler

    _set_white_levels_handler(
        {"device_id": 7, "white": 150}, mock_indigo
    )
    mock_indigo.dimmer.setColorLevels.assert_called_once_with(
        7, whiteLevel=100
    )


def test_set_white_levels_no_args_raises(mock_indigo):
    from tools.control import _set_white_levels_handler

    # If caller provides nothing, there's no SDK call to make — surface
    # a clear error rather than no-op.
    with pytest.raises(ValueError, match="white"):
        _set_white_levels_handler({"device_id": 7}, mock_indigo)
    mock_indigo.dimmer.setColorLevels.assert_not_called()


def test_set_white_levels_missing_device_id_raises(mock_indigo):
    from tools.control import _set_white_levels_handler

    with pytest.raises(ValueError, match="device_id"):
        _set_white_levels_handler({"white": 80}, mock_indigo)
    mock_indigo.dimmer.setColorLevels.assert_not_called()
