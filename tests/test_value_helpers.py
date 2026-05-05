"""Tests for brightness / RGB / hex normalization helpers."""
import pytest

from tools.value_helpers import (
    normalize_brightness,
    normalize_rgb_byte,
    normalize_rgb_percent,
    parse_hex_color,
)


def test_normalize_brightness_0_to_100_passthrough():
    assert normalize_brightness(50) == 50
    assert normalize_brightness(100) == 100
    assert normalize_brightness(0) == 0


def test_normalize_brightness_0_to_1_scales_to_100():
    assert normalize_brightness(0.5) == 50
    assert normalize_brightness(1.0) == 100
    # 0.0 stays 0 — ambiguous (could mean 0% or 0.0% of max) but
    # consistent with the integer 0 case so callers don't see a jump.
    assert normalize_brightness(0.0) == 0


def test_normalize_brightness_clamps_out_of_range():
    assert normalize_brightness(150) == 100
    assert normalize_brightness(-5) == 0


def test_normalize_brightness_rounds_floats_above_1():
    # 50.4 → 50, 50.5 → 50 (banker's rounding) or 51 — accept either.
    result = normalize_brightness(50.6)
    assert result == 51


def test_normalize_rgb_byte_clamps_to_0_255():
    assert normalize_rgb_byte(128) == 128
    assert normalize_rgb_byte(300) == 255
    assert normalize_rgb_byte(-10) == 0


def test_normalize_rgb_byte_rounds_floats():
    assert normalize_rgb_byte(127.6) == 128


def test_normalize_rgb_percent_scales_to_0_255():
    assert normalize_rgb_percent(0) == 0
    assert normalize_rgb_percent(50) == 128  # 50 * 255 / 100 = 127.5 → 128
    assert normalize_rgb_percent(100) == 255


def test_normalize_rgb_percent_clamps():
    assert normalize_rgb_percent(150) == 255
    assert normalize_rgb_percent(-5) == 0


def test_parse_hex_color_full_form():
    assert parse_hex_color("#FF8000") == (255, 128, 0)


def test_parse_hex_color_full_form_lowercase():
    assert parse_hex_color("#ff8000") == (255, 128, 0)


def test_parse_hex_color_short_form():
    # 3-digit hex doubles each digit: F80 → FF8800
    assert parse_hex_color("#F80") == (255, 136, 0)


def test_parse_hex_color_no_hash():
    assert parse_hex_color("FF8000") == (255, 128, 0)


def test_parse_hex_color_invalid_raises():
    with pytest.raises(ValueError):
        parse_hex_color("not-a-color")


def test_parse_hex_color_wrong_length_raises():
    with pytest.raises(ValueError):
        parse_hex_color("#FF80")


def test_parse_hex_color_invalid_hex_chars_raises():
    with pytest.raises(ValueError):
        parse_hex_color("#GGGGGG")
