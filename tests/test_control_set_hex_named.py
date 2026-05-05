"""TDD tests for device_set_hex_color and device_set_named_color.

Both decode their string input to (r, g, b) bytes, then convert to
0-100 percent for ``setColorLevels`` — same call shape as
``device_set_rgb_color``.
"""
import pytest


# ----- device_set_hex_color ----------------------------------------------


def test_set_hex_color_full_form_orange(mock_indigo):
    from tools.control import _set_hex_color_handler

    result = _set_hex_color_handler(
        {"device_id": 7, "color": "#FF8000"}, mock_indigo
    )
    # 255 → 100, 128 → 50, 0 → 0
    mock_indigo.dimmer.setColorLevels.assert_called_once_with(
        7, redLevel=100, greenLevel=50, blueLevel=0
    )
    assert result["status"] == "ok"


def test_set_hex_color_short_form(mock_indigo):
    from tools.control import _set_hex_color_handler

    _set_hex_color_handler({"device_id": 7, "color": "#F00"}, mock_indigo)
    mock_indigo.dimmer.setColorLevels.assert_called_once_with(
        7, redLevel=100, greenLevel=0, blueLevel=0
    )


def test_set_hex_color_no_hash(mock_indigo):
    from tools.control import _set_hex_color_handler

    _set_hex_color_handler({"device_id": 7, "color": "00FF00"}, mock_indigo)
    mock_indigo.dimmer.setColorLevels.assert_called_once_with(
        7, redLevel=0, greenLevel=100, blueLevel=0
    )


def test_set_hex_color_invalid_raises(mock_indigo):
    from tools.control import _set_hex_color_handler

    with pytest.raises(ValueError, match="hex"):
        _set_hex_color_handler(
            {"device_id": 7, "color": "not-a-color"}, mock_indigo
        )
    mock_indigo.dimmer.setColorLevels.assert_not_called()


def test_set_hex_color_missing_color_raises(mock_indigo):
    from tools.control import _set_hex_color_handler

    with pytest.raises(ValueError, match="color"):
        _set_hex_color_handler({"device_id": 7}, mock_indigo)
    mock_indigo.dimmer.setColorLevels.assert_not_called()


def test_set_hex_color_missing_device_id_raises(mock_indigo):
    from tools.control import _set_hex_color_handler

    with pytest.raises(ValueError, match="device_id"):
        _set_hex_color_handler({"color": "#FF0000"}, mock_indigo)
    mock_indigo.dimmer.setColorLevels.assert_not_called()


# ----- device_set_named_color --------------------------------------------


def test_set_named_color_red(mock_indigo):
    from tools.control import _set_named_color_handler

    _set_named_color_handler(
        {"device_id": 7, "name": "red"}, mock_indigo
    )
    mock_indigo.dimmer.setColorLevels.assert_called_once_with(
        7, redLevel=100, greenLevel=0, blueLevel=0
    )


def test_set_named_color_case_and_space_insensitive(mock_indigo):
    from tools.control import _set_named_color_handler

    _set_named_color_handler(
        {"device_id": 7, "name": "Alice Blue"}, mock_indigo
    )
    # aliceblue is (240, 248, 255). 240/255*100 ≈ 94, 248/255 ≈ 97, 255 → 100
    mock_indigo.dimmer.setColorLevels.assert_called_once_with(
        7, redLevel=94, greenLevel=97, blueLevel=100
    )


def test_set_named_color_grey_resolves_to_gray(mock_indigo):
    from tools.control import _set_named_color_handler

    _set_named_color_handler(
        {"device_id": 7, "name": "grey"}, mock_indigo
    )
    # gray is (128, 128, 128) → 50, 50, 50
    mock_indigo.dimmer.setColorLevels.assert_called_once_with(
        7, redLevel=50, greenLevel=50, blueLevel=50
    )


def test_set_named_color_unknown_raises(mock_indigo):
    from tools.control import _set_named_color_handler

    with pytest.raises(ValueError, match="unknown color"):
        _set_named_color_handler(
            {"device_id": 7, "name": "british racing green"}, mock_indigo
        )
    mock_indigo.dimmer.setColorLevels.assert_not_called()


def test_set_named_color_missing_name_raises(mock_indigo):
    from tools.control import _set_named_color_handler

    with pytest.raises(ValueError, match="name"):
        _set_named_color_handler({"device_id": 7}, mock_indigo)
    mock_indigo.dimmer.setColorLevels.assert_not_called()
