"""TDD tests for thermostat_set_hvac_mode.

Accepts a friendly-string mode (off, heat, cool, auto, …); maps it
to the matching ``indigo.kHvacMode.X`` enum constant and calls
``indigo.thermostat.setHvacMode(id, value=...)``.
"""
import pytest


def test_set_hvac_mode_heat(mock_indigo):
    from tools.control import _set_hvac_mode_handler

    result = _set_hvac_mode_handler(
        {"device_id": 11, "mode": "heat"}, mock_indigo
    )
    mock_indigo.thermostat.setHvacMode.assert_called_once_with(
        11, value=mock_indigo.kHvacMode.Heat
    )
    assert result["status"] == "ok"


def test_set_hvac_mode_off(mock_indigo):
    from tools.control import _set_hvac_mode_handler

    _set_hvac_mode_handler({"device_id": 11, "mode": "off"}, mock_indigo)
    mock_indigo.thermostat.setHvacMode.assert_called_once_with(
        11, value=mock_indigo.kHvacMode.Off
    )


def test_set_hvac_mode_auto_maps_to_HeatCool(mock_indigo):
    from tools.control import _set_hvac_mode_handler

    # "auto" is the user-facing name; the SDK enum is HeatCool.
    _set_hvac_mode_handler({"device_id": 11, "mode": "auto"}, mock_indigo)
    mock_indigo.thermostat.setHvacMode.assert_called_once_with(
        11, value=mock_indigo.kHvacMode.HeatCool
    )


def test_set_hvac_mode_case_insensitive(mock_indigo):
    from tools.control import _set_hvac_mode_handler

    _set_hvac_mode_handler({"device_id": 11, "mode": "COOL"}, mock_indigo)
    mock_indigo.thermostat.setHvacMode.assert_called_once_with(
        11, value=mock_indigo.kHvacMode.Cool
    )


def test_set_hvac_mode_program_heat_cool(mock_indigo):
    from tools.control import _set_hvac_mode_handler

    _set_hvac_mode_handler(
        {"device_id": 11, "mode": "programheatcool"}, mock_indigo
    )
    mock_indigo.thermostat.setHvacMode.assert_called_once_with(
        11, value=mock_indigo.kHvacMode.ProgramHeatCool
    )


def test_set_hvac_mode_unknown_raises(mock_indigo):
    from tools.control import _set_hvac_mode_handler

    with pytest.raises(ValueError, match="mode"):
        _set_hvac_mode_handler(
            {"device_id": 11, "mode": "boost"}, mock_indigo
        )
    mock_indigo.thermostat.setHvacMode.assert_not_called()


def test_set_hvac_mode_missing_mode_raises(mock_indigo):
    from tools.control import _set_hvac_mode_handler

    with pytest.raises(ValueError, match="mode"):
        _set_hvac_mode_handler({"device_id": 11}, mock_indigo)
    mock_indigo.thermostat.setHvacMode.assert_not_called()
