"""Tests for catalog-driven capability pre-checks on colour/white tools.

The catalog is advisory: a refusal happens ONLY when a vendored
profile exists and explicitly carries the relevant flag as False.
Uncataloged devices, profiles missing the flag, and unreadable
devices all proceed to the SDK call exactly as before — the tools
must never block on missing data.
"""

from unittest.mock import MagicMock

import pytest


def _fake_device(plugin_id="com.test.plugin", type_id="ledStrip"):
    d = MagicMock()
    d.pluginId = plugin_id
    d.deviceTypeId = type_id
    return d


_NO_COLOR_PROFILES = {
    ("com.test.plugin", "ledStrip"): {
        "base_class": "indigo.DimmerDevice",
        "capabilities": {
            "supportsOnState": True,
            "supportsRGB": False,
            "supportsWhite": False,
            "supportsTwoWhiteLevels": False,
            "supportsWhiteTemperature": False,
        },
    },
}

_FULL_COLOR_PROFILES = {
    ("com.test.plugin", "ledStrip"): {
        "base_class": "indigo.DimmerDevice",
        "capabilities": {
            "supportsRGB": True,
            "supportsWhite": True,
            "supportsTwoWhiteLevels": True,
            "supportsWhiteTemperature": True,
        },
    },
}


def _wire(mock_indigo, monkeypatch, profiles):
    import catalog_snapshot

    monkeypatch.setattr(catalog_snapshot, "PROFILES", profiles)
    dev = _fake_device()
    mock_indigo.devices.__getitem__.side_effect = {7: dev}.__getitem__
    return dev


# ----- refusals when the catalog says no ---------------------------------


def test_set_rgb_color_refused_without_rgb_support(mock_indigo, monkeypatch):
    from tools.control import _set_rgb_color_handler

    _wire(mock_indigo, monkeypatch, _NO_COLOR_PROFILES)
    with pytest.raises(ValueError, match="supportsRGB"):
        _set_rgb_color_handler(
            {"device_id": 7, "red": 255, "green": 0, "blue": 0}, mock_indigo
        )
    mock_indigo.dimmer.setColorLevels.assert_not_called()


def test_set_rgb_percent_refused_without_rgb_support(mock_indigo, monkeypatch):
    from tools.control import _set_rgb_percent_handler

    _wire(mock_indigo, monkeypatch, _NO_COLOR_PROFILES)
    with pytest.raises(ValueError, match="supportsRGB"):
        _set_rgb_percent_handler(
            {"device_id": 7, "red": 100, "green": 0, "blue": 0}, mock_indigo
        )
    mock_indigo.dimmer.setColorLevels.assert_not_called()


def test_set_hex_color_refused_without_rgb_support(mock_indigo, monkeypatch):
    from tools.control import _set_hex_color_handler

    _wire(mock_indigo, monkeypatch, _NO_COLOR_PROFILES)
    with pytest.raises(ValueError, match="supportsRGB"):
        _set_hex_color_handler({"device_id": 7, "color": "#ff0000"}, mock_indigo)
    mock_indigo.dimmer.setColorLevels.assert_not_called()


def test_set_named_color_refused_without_rgb_support(mock_indigo, monkeypatch):
    from tools.control import _set_named_color_handler

    _wire(mock_indigo, monkeypatch, _NO_COLOR_PROFILES)
    with pytest.raises(ValueError, match="supportsRGB"):
        _set_named_color_handler({"device_id": 7, "name": "red"}, mock_indigo)
    mock_indigo.dimmer.setColorLevels.assert_not_called()


def test_refusal_names_what_the_device_does_support(mock_indigo, monkeypatch):
    from tools.control import _set_rgb_color_handler

    _wire(mock_indigo, monkeypatch, _NO_COLOR_PROFILES)
    with pytest.raises(ValueError, match="supportsOnState"):
        _set_rgb_color_handler(
            {"device_id": 7, "red": 255, "green": 0, "blue": 0}, mock_indigo
        )


def test_set_white_refused_without_white_support(mock_indigo, monkeypatch):
    from tools.control import _set_white_levels_handler

    _wire(mock_indigo, monkeypatch, _NO_COLOR_PROFILES)
    with pytest.raises(ValueError, match="supportsWhite"):
        _set_white_levels_handler({"device_id": 7, "white": 50}, mock_indigo)
    mock_indigo.dimmer.setColorLevels.assert_not_called()


def test_set_white2_refused_without_two_white_levels(mock_indigo, monkeypatch):
    from tools.control import _set_white_levels_handler

    _wire(mock_indigo, monkeypatch, _NO_COLOR_PROFILES)
    with pytest.raises(ValueError, match="supportsTwoWhiteLevels"):
        _set_white_levels_handler({"device_id": 7, "white2": 50}, mock_indigo)
    mock_indigo.dimmer.setColorLevels.assert_not_called()


def test_set_temperature_refused_without_white_temperature(
        mock_indigo, monkeypatch):
    from tools.control import _set_white_levels_handler

    _wire(mock_indigo, monkeypatch, _NO_COLOR_PROFILES)
    with pytest.raises(ValueError, match="supportsWhiteTemperature"):
        _set_white_levels_handler(
            {"device_id": 7, "temperature": 2700}, mock_indigo
        )
    mock_indigo.dimmer.setColorLevels.assert_not_called()


# ----- pass-throughs: capability present, or no data ----------------------


def test_set_rgb_color_proceeds_when_catalog_says_yes(
        mock_indigo, monkeypatch):
    from tools.control import _set_rgb_color_handler

    _wire(mock_indigo, monkeypatch, _FULL_COLOR_PROFILES)
    result = _set_rgb_color_handler(
        {"device_id": 7, "red": 255, "green": 0, "blue": 0}, mock_indigo
    )
    assert result["status"] == "ok"
    mock_indigo.dimmer.setColorLevels.assert_called_once()


def test_set_rgb_color_proceeds_without_profile(mock_indigo, monkeypatch):
    from tools.control import _set_rgb_color_handler

    _wire(mock_indigo, monkeypatch, {})  # device is uncataloged
    result = _set_rgb_color_handler(
        {"device_id": 7, "red": 255, "green": 0, "blue": 0}, mock_indigo
    )
    assert result["status"] == "ok"
    mock_indigo.dimmer.setColorLevels.assert_called_once()


def test_set_white_proceeds_without_profile(mock_indigo, monkeypatch):
    from tools.control import _set_white_levels_handler

    _wire(mock_indigo, monkeypatch, {})
    result = _set_white_levels_handler({"device_id": 7, "white": 50}, mock_indigo)
    assert result["status"] == "ok"
    mock_indigo.dimmer.setColorLevels.assert_called_once()


def test_check_proceeds_when_flag_absent_from_profile(
        mock_indigo, monkeypatch):
    """A profile that simply doesn't mention supportsRGB must not
    refuse — only an explicit False is a refusal."""
    from tools.control import _set_rgb_color_handler

    profiles = {
        ("com.test.plugin", "ledStrip"): {
            "base_class": "indigo.DimmerDevice",
            "capabilities": {"supportsOnState": True},
        },
    }
    _wire(mock_indigo, monkeypatch, profiles)
    result = _set_rgb_color_handler(
        {"device_id": 7, "red": 255, "green": 0, "blue": 0}, mock_indigo
    )
    assert result["status"] == "ok"
    mock_indigo.dimmer.setColorLevels.assert_called_once()


def test_check_proceeds_when_device_lookup_fails(mock_indigo, monkeypatch):
    """If the pre-check can't read the device, the SDK call still
    happens — the catalog gate must never add a new failure mode."""
    import catalog_snapshot
    from tools.control import _set_rgb_color_handler

    monkeypatch.setattr(
        catalog_snapshot, "PROFILES", _NO_COLOR_PROFILES
    )
    mock_indigo.devices.__getitem__.side_effect = KeyError(7)
    result = _set_rgb_color_handler(
        {"device_id": 7, "red": 255, "green": 0, "blue": 0}, mock_indigo
    )
    assert result["status"] == "ok"
    mock_indigo.dimmer.setColorLevels.assert_called_once()
