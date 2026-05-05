"""TDD tests for the basic find_devices FTS5 MATCH path."""
from unittest.mock import MagicMock


class _AttrList(list):
    pass


def _device(id_, name, type_id="dimmer", folder_id=0, description=""):
    d = MagicMock()
    d.id = id_; d.name = name
    d.deviceTypeId = type_id; d.folderId = folder_id
    d.description = description; d.model = ""; d.address = ""
    return d


def _make_indexer(mock_indigo, devs, folder_names=None,
                   variables=None, actions=None):
    from indexer import Indexer

    d = _AttrList(devs)
    d.folders = MagicMock()
    d.folders.getName.side_effect = lambda fid: (folder_names or {}).get(fid, "")
    mock_indigo.devices = d

    v = _AttrList(variables or [])
    v.folders = MagicMock(getName=MagicMock(return_value=""))
    mock_indigo.variables = v

    mock_indigo.actionGroups = _AttrList(actions or [])

    idx = Indexer(indigo_module=mock_indigo)
    idx.build()
    return idx


def test_find_devices_matches_by_name(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(
        mock_indigo,
        [_device(1, "Kitchen Dimmer", folder_id=10)],
        folder_names={10: "Kitchen"},
    )

    result = _find_devices_handler({"query": "kitchen"}, indexer=idx)
    assert result["total_count"] >= 1
    assert any(r["id"] == 1 for r in result["results"])


def test_find_devices_alias_expansion_lets_light_find_dimmer(mock_indigo):
    """``light`` is in the dimmer alias list — query should surface a
    dimmer even though no name actually contains 'light'."""
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(
        mock_indigo,
        [_device(1, "Kitchen Dimmer", type_id="dimmer")],
    )

    result = _find_devices_handler({"query": "light"}, indexer=idx)
    assert any(r["id"] == 1 for r in result["results"])


def test_find_devices_returns_score_field(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(
        mock_indigo,
        [_device(1, "Kitchen Dimmer")],
    )

    result = _find_devices_handler({"query": "kitchen"}, indexer=idx)
    assert "score" in result["results"][0]


def test_find_devices_name_match_outranks_alias_only_match(mock_indigo):
    """A device whose actual name contains the query word should
    rank above one matched only via the alias column. bm25 weights
    name=3.0 vs aliases=2.0 to make this happen."""
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(
        mock_indigo,
        [
            _device(1, "Generic Dimmer", type_id="dimmer"),  # 'light' via alias
            _device(2, "Kitchen Light", type_id="dimmer"),    # 'light' via name
        ],
    )

    result = _find_devices_handler({"query": "light"}, indexer=idx)
    # bm25 score is more-negative for better matches in SQLite FTS5,
    # so the lowest score is the best match. Sort ascending and check
    # the name-match comes first.
    ids_in_rank_order = [r["id"] for r in result["results"]]
    assert ids_in_rank_order[0] == 2


def test_find_devices_no_match_returns_empty(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(
        mock_indigo,
        [_device(1, "Kitchen Dimmer")],
    )

    result = _find_devices_handler({"query": "xyzzynothing"}, indexer=idx)
    assert result["total_count"] == 0
    assert result["results"] == []


def test_find_devices_returns_entity_type_in_results(mock_indigo):
    """Results should include entity_type so callers can route
    devices vs variables vs actions in the same response."""
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(
        mock_indigo,
        [_device(1, "Kitchen Dimmer")],
    )

    result = _find_devices_handler({"query": "kitchen"}, indexer=idx)
    assert all("entity_type" in r for r in result["results"])
    assert result["results"][0]["entity_type"] == "device"
