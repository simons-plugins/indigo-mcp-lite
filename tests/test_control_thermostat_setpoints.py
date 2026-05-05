"""TDD tests for thermostat_set_heat_setpoint and
thermostat_set_cool_setpoint.

Both call ``indigo.thermostat.setHeatSetpoint`` /
``setCoolSetpoint`` (capital S — plan template had it lowercase, the
SDK reference confirms capital). Temperature is passed through to
the SDK in whatever unit the thermostat is configured for; we don't
unit-convert here because Indigo handles °F/°C per device.
"""
import pytest


def test_set_heat_setpoint_calls_indigo_with_value(mock_indigo):
    from tools.control import _set_heat_setpoint_handler

    result = _set_heat_setpoint_handler(
        {"device_id": 11, "temperature": 68}, mock_indigo
    )
    mock_indigo.thermostat.setHeatSetpoint.assert_called_once_with(
        11, value=68
    )
    assert result["status"] == "ok"


def test_set_cool_setpoint_calls_indigo_with_value(mock_indigo):
    from tools.control import _set_cool_setpoint_handler

    _set_cool_setpoint_handler(
        {"device_id": 11, "temperature": 76.5}, mock_indigo
    )
    # Float passes through — thermostats happily take fractional °C.
    mock_indigo.thermostat.setCoolSetpoint.assert_called_once_with(
        11, value=76.5
    )


def test_set_heat_setpoint_missing_temperature_raises(mock_indigo):
    from tools.control import _set_heat_setpoint_handler

    with pytest.raises(ValueError, match="temperature"):
        _set_heat_setpoint_handler({"device_id": 11}, mock_indigo)
    mock_indigo.thermostat.setHeatSetpoint.assert_not_called()


def test_set_heat_setpoint_non_numeric_raises(mock_indigo):
    from tools.control import _set_heat_setpoint_handler

    with pytest.raises(ValueError, match="temperature"):
        _set_heat_setpoint_handler(
            {"device_id": 11, "temperature": "warm"}, mock_indigo
        )
    mock_indigo.thermostat.setHeatSetpoint.assert_not_called()


def test_set_heat_setpoint_missing_device_id_raises(mock_indigo):
    from tools.control import _set_heat_setpoint_handler

    with pytest.raises(ValueError, match="device_id"):
        _set_heat_setpoint_handler({"temperature": 68}, mock_indigo)
    mock_indigo.thermostat.setHeatSetpoint.assert_not_called()


def test_set_cool_setpoint_missing_temperature_raises(mock_indigo):
    from tools.control import _set_cool_setpoint_handler

    with pytest.raises(ValueError, match="temperature"):
        _set_cool_setpoint_handler({"device_id": 11}, mock_indigo)
    mock_indigo.thermostat.setCoolSetpoint.assert_not_called()
