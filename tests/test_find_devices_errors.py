"""TDD tests for find_devices error handling + FTS5 syntax fallback.

FTS5 has a strict query language: bare ``AND``, ``OR``, ``*``, etc.
at unexpected positions raise ``sqlite3.OperationalError``. Real
users type natural-language queries that often include those
tokens by accident (``AND lamp``, ``light or lamp`` in lower case
which is OK, etc.). Strategy: try the query as-is; if FTS5 rejects
it, retry as a single quoted phrase so the malformed query at
worst falls back to literal-substring search.
"""
from unittest.mock import MagicMock

import pytest


class _AttrList(list):
    pass


def _device(id_, name):
    d = MagicMock()
    d.id = id_; d.name = name
    d.deviceTypeId = "dimmer"; d.folderId = 0
    d.description = ""; d.model = ""; d.address = ""
    return d


def _make_indexer(mock_indigo, devs):
    from indexer import Indexer

    d = _AttrList(devs)
    d.folders = MagicMock(getName=MagicMock(return_value=""))
    mock_indigo.devices = d
    v = _AttrList(); v.folders = MagicMock(getName=MagicMock(return_value=""))
    mock_indigo.variables = v
    mock_indigo.actionGroups = _AttrList()
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()
    return idx


def test_empty_query_raises(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(mock_indigo, [_device(1, "Lamp")])
    with pytest.raises(ValueError, match="query required"):
        _find_devices_handler({"query": ""}, indexer=idx)


def test_whitespace_only_query_raises(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(mock_indigo, [_device(1, "Lamp")])
    with pytest.raises(ValueError, match="query required"):
        _find_devices_handler({"query": "   "}, indexer=idx)


def test_missing_query_raises(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(mock_indigo, [_device(1, "Lamp")])
    with pytest.raises(ValueError, match="query required"):
        _find_devices_handler({}, indexer=idx)


def test_non_string_query_raises(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(mock_indigo, [_device(1, "Lamp")])
    with pytest.raises(ValueError, match="query required"):
        _find_devices_handler({"query": 42}, indexer=idx)


def test_malformed_fts5_query_falls_back_to_quoted_phrase(mock_indigo):
    """Bare ``AND`` at the start is invalid FTS5; the tool should
    retry the whole query as a quoted phrase."""
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(mock_indigo, [_device(1, "AND lamp")])

    # Without fallback this would raise sqlite3.OperationalError.
    # With fallback, the literal phrase "AND lamp" matches the
    # device whose name is exactly "AND lamp".
    result = _find_devices_handler({"query": "AND lamp"}, indexer=idx)
    assert result["total_count"] >= 1


def test_query_with_unmatched_paren_falls_back(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(mock_indigo, [_device(1, "Kitchen Lamp")])

    # Unmatched parenthesis is also invalid FTS5 syntax.
    result = _find_devices_handler({"query": "kitchen)"}, indexer=idx)
    # Either returns the device matching 'kitchen' via the quoted-
    # phrase fallback, or returns 0 matches because no device name
    # contains 'kitchen)' literally — both are clean outcomes vs
    # the original OperationalError.
    assert "results" in result


def test_well_formed_query_with_special_chars_does_not_double_escape(mock_indigo):
    """A query that's already a quoted phrase should not get double-
    quoted on retry — that would never match anything."""
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(mock_indigo, [_device(1, "Kitchen Lamp")])

    result = _find_devices_handler({"query": '"kitchen lamp"'}, indexer=idx)
    assert result["total_count"] >= 1
