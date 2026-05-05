"""TDD tests for find_devices pagination."""
from unittest.mock import MagicMock


class _AttrList(list):
    pass


def _device(id_, name):
    d = MagicMock()
    d.id = id_; d.name = name
    d.deviceTypeId = "dimmer"; d.folderId = 0
    d.description = ""; d.model = ""; d.address = ""
    return d


def _make_indexer(mock_indigo, n_devices):
    from indexer import Indexer

    devs = _AttrList(_device(i, f"Lamp {i}") for i in range(n_devices))
    devs.folders = MagicMock(getName=MagicMock(return_value=""))
    mock_indigo.devices = devs
    v = _AttrList(); v.folders = MagicMock(getName=MagicMock(return_value=""))
    mock_indigo.variables = v
    mock_indigo.actionGroups = _AttrList()
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()
    return idx


def test_default_limit_is_50(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(mock_indigo, 75)
    result = _find_devices_handler({"query": "lamp"}, indexer=idx)

    assert result["limit"] == 50
    assert len(result["results"]) == 50
    assert result["total_count"] == 75
    assert result["has_more"] is True
    assert result["offset"] == 0


def test_limit_caps_at_500(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(mock_indigo, 600)
    result = _find_devices_handler({"query": "lamp", "limit": 9999}, indexer=idx)

    assert result["limit"] == 500
    assert len(result["results"]) == 500
    assert result["total_count"] == 600
    assert result["has_more"] is True


def test_explicit_limit(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(mock_indigo, 30)
    result = _find_devices_handler({"query": "lamp", "limit": 10}, indexer=idx)

    assert result["limit"] == 10
    assert len(result["results"]) == 10
    assert result["total_count"] == 30
    assert result["has_more"] is True


def test_offset_skips_results(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(mock_indigo, 30)
    page1 = _find_devices_handler(
        {"query": "lamp", "limit": 10, "offset": 0}, indexer=idx
    )
    page2 = _find_devices_handler(
        {"query": "lamp", "limit": 10, "offset": 10}, indexer=idx
    )

    assert page1["offset"] == 0
    assert page2["offset"] == 10
    page1_ids = {r["id"] for r in page1["results"]}
    page2_ids = {r["id"] for r in page2["results"]}
    assert page1_ids.isdisjoint(page2_ids)


def test_has_more_false_at_end(mock_indigo):
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(mock_indigo, 30)
    last_page = _find_devices_handler(
        {"query": "lamp", "limit": 10, "offset": 25}, indexer=idx
    )

    assert last_page["total_count"] == 30
    assert len(last_page["results"]) == 5
    assert last_page["has_more"] is False


def test_total_count_reflects_full_match_set_not_page(mock_indigo):
    """total_count must be the FULL match count, not the page size —
    so callers can compute the number of pages without paging through
    everything."""
    from tools.find_devices import _find_devices_handler

    idx = _make_indexer(mock_indigo, 100)
    result = _find_devices_handler(
        {"query": "lamp", "limit": 10}, indexer=idx
    )

    assert result["total_count"] == 100
    assert len(result["results"]) == 10
