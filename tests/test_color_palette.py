"""Tests for the 140-entry CSS named-color palette."""
import pytest

from color_palette import NAMED_COLORS, lookup_named_color


def test_palette_has_140_entries():
    assert len(NAMED_COLORS) == 140


def test_lookup_red_is_255_0_0():
    assert lookup_named_color("red") == (255, 0, 0)


def test_lookup_is_case_insensitive():
    assert lookup_named_color("RED") == (255, 0, 0)
    assert lookup_named_color("Red") == (255, 0, 0)


def test_lookup_alice_blue_handles_spaces():
    assert lookup_named_color("alice blue") == (240, 248, 255)
    assert lookup_named_color("aliceblue") == (240, 248, 255)


def test_lookup_unknown_raises_valueerror():
    with pytest.raises(ValueError, match="unknown color"):
        lookup_named_color("glorble fizz")


@pytest.mark.parametrize("name,expected", [
    ("white", (255, 255, 255)),
    ("black", (0, 0, 0)),
    ("lime", (0, 255, 0)),
    ("blue", (0, 0, 255)),
    ("yellow", (255, 255, 0)),
    ("cyan", (0, 255, 255)),
    ("magenta", (255, 0, 255)),
    ("dodgerblue", (30, 144, 255)),
    ("yellowgreen", (154, 205, 50)),
])
def test_spot_check_colors(name, expected):
    assert lookup_named_color(name) == expected


def test_aqua_and_cyan_are_aliases_of_same_rgb():
    # CSS aliases — both are (0, 255, 255).
    assert lookup_named_color("aqua") == lookup_named_color("cyan")


def test_fuchsia_and_magenta_are_aliases_of_same_rgb():
    # CSS aliases — both are (255, 0, 255).
    assert lookup_named_color("fuchsia") == lookup_named_color("magenta")


def test_grey_and_gray_both_resolve():
    # CSS includes both spellings of gray/grey for several colors.
    assert lookup_named_color("gray") == (128, 128, 128)
    assert lookup_named_color("grey") == (128, 128, 128)
