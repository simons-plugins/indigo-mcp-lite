"""TDD tests for thermostat_set_fan_mode.

Two valid modes (Auto, AlwaysOn) per ``indigo.kFanMode``. Friendly
strings ``auto`` and ``alwayson`` / ``always_on`` / ``always on``
all accepted; everything else raises.
"""
import pytest


def test_set_fan_mode_auto(mock_indigo):
    from tools.control import _set_fan_mode_handler

    result = _set_fan_mode_handler(
        {"device_id": 11, "mode": "auto"}, mock_indigo
    )
    mock_indigo.thermostat.setFanMode.assert_called_once_with(
        11, value=mock_indigo.kFanMode.Auto
    )
    assert result["status"] == "ok"


def test_set_fan_mode_alwayson(mock_indigo):
    from tools.control import _set_fan_mode_handler

    _set_fan_mode_handler({"device_id": 11, "mode": "alwayson"}, mock_indigo)
    mock_indigo.thermostat.setFanMode.assert_called_once_with(
        11, value=mock_indigo.kFanMode.AlwaysOn
    )


def test_set_fan_mode_always_on_with_underscore(mock_indigo):
    from tools.control import _set_fan_mode_handler

    _set_fan_mode_handler(
        {"device_id": 11, "mode": "always_on"}, mock_indigo
    )
    mock_indigo.thermostat.setFanMode.assert_called_once_with(
        11, value=mock_indigo.kFanMode.AlwaysOn
    )


def test_set_fan_mode_always_on_with_space(mock_indigo):
    from tools.control import _set_fan_mode_handler

    _set_fan_mode_handler(
        {"device_id": 11, "mode": "always on"}, mock_indigo
    )
    mock_indigo.thermostat.setFanMode.assert_called_once_with(
        11, value=mock_indigo.kFanMode.AlwaysOn
    )


def test_set_fan_mode_unknown_raises(mock_indigo):
    from tools.control import _set_fan_mode_handler

    with pytest.raises(ValueError, match="mode"):
        _set_fan_mode_handler(
            {"device_id": 11, "mode": "boost"}, mock_indigo
        )
    mock_indigo.thermostat.setFanMode.assert_not_called()


def test_set_fan_mode_missing_mode_raises(mock_indigo):
    from tools.control import _set_fan_mode_handler

    with pytest.raises(ValueError, match="mode"):
        _set_fan_mode_handler({"device_id": 11}, mock_indigo)
    mock_indigo.thermostat.setFanMode.assert_not_called()
