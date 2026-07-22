"""Tests for the synonym expansion and XKCD colour additions."""

from unittest.mock import MagicMock

from synonyms import expand
from color_palette import NAMED_COLORS, lookup_named_color
from xkcd_palette import XKCD_COLORS


# ----- synonyms.expand ---------------------------------------------------


def test_expand_room_synonyms_bidirectional():
    assert "lounge" in expand("Living Room Lamp")
    assert "living room" in expand("Lounge Lamp")


def test_expand_multiword_phrase_match():
    out = expand("Washing Machine Plug")
    assert "washer" in out
    # "plug" is in the socket group — its siblings should appear too
    assert "socket" in out and "outlet" in out


def test_expand_does_not_echo_source_words():
    out = expand("Lounge Lamp").split()
    assert "lounge" not in out
    assert "lamp" not in out


def test_expand_no_match_returns_empty():
    assert expand("Zorbulator Mk7") == ""


def test_expand_case_insensitive_and_deterministic():
    assert expand("LOUNGE tv") == expand("lounge TV")


def test_expand_uk_terms():
    assert "radiator" in expand("Hall TRV")
    assert "vacuum" in expand("Hoover Socket")


# ----- indexer integration ----------------------------------------------


def test_indexer_aliases_include_synonyms(mock_indigo):
    from indexer import Indexer

    dev = MagicMock()
    dev.id = 1
    dev.name = "Telly Socket"
    dev.description = ""
    dev.deviceTypeId = "relay"
    dev.folderId = 0
    dev.model = ""
    dev.address = ""
    mock_indigo.devices.__iter__.side_effect = lambda: iter([dev])
    mock_indigo.variables.__iter__.side_effect = lambda: iter([])
    mock_indigo.actionGroups.__iter__.side_effect = lambda: iter([])
    mock_indigo.devices.folders.getName.return_value = ""

    idx = Indexer(indigo_module=mock_indigo, logger=MagicMock())
    idx.build()
    rows = idx.connection.execute(
        "SELECT entity_id FROM entities WHERE entities MATCH 'aliases:television'"
    ).fetchall()
    assert rows == [(1,)]


# ----- XKCD colours ------------------------------------------------------


def test_xkcd_table_size_and_shape():
    assert len(XKCD_COLORS) > 900
    for value in (XKCD_COLORS["burntorange"], XKCD_COLORS["duckeggblue"]):
        assert len(value) == 3
        assert all(isinstance(c, int) and 0 <= c <= 255 for c in value)


def test_lookup_falls_back_to_xkcd():
    assert lookup_named_color("burnt orange") == XKCD_COLORS["burntorange"]
    assert lookup_named_color("Duck Egg Blue") == XKCD_COLORS["duckeggblue"]


def test_lookup_css_wins_on_collision():
    # "teal" exists in both tables; CSS's value must win.
    assert lookup_named_color("teal") == NAMED_COLORS["teal"]


def test_lookup_gray_spelling_reaches_xkcd_grey():
    # XKCD keys use British "grey"; a US-spelled query still resolves.
    assert lookup_named_color("steel gray") == XKCD_COLORS["steelgrey"]


def test_lookup_unknown_still_raises():
    import pytest

    with pytest.raises(ValueError, match="unknown color"):
        lookup_named_color("definitely not a colour")
