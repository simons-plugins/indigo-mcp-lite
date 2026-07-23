"""Tests for list_uncataloged_devices — the catalog gap report.

Plugin-owned devices (non-empty pluginId) with no vendored catalog
profile, in the standard paginated list envelope. Built-in and
interface devices are excluded: the catalog only profiles plugin
device types.
"""

from unittest.mock import MagicMock

import pytest


def _fake_device(id_, name, plugin_id, type_id):
    d = MagicMock()
    d.id = id_
    d.name = name
    d.deviceTypeId = type_id
    d.onState = False
    d.brightness = 0
    d.folderId = 0
    d.description = ""
    d.model = ""
    d.address = ""
    d.pluginId = plugin_id
    return d


_TEST_PROFILES = {
    ("com.test.plugin", "cataloged"): {
        "base_class": "indigo.RelayDevice",
        "capabilities": {"supportsOnState": True},
    },
}


def _populate(mock_indigo, monkeypatch):
    import catalog_snapshot

    monkeypatch.setattr(catalog_snapshot, "PROFILES", _TEST_PROFILES)
    mock_indigo.devices = [
        _fake_device(1, "Cataloged", "com.test.plugin", "cataloged"),
        _fake_device(2, "Uncataloged A", "com.test.plugin", "mystery"),
        _fake_device(3, "Built-in Z-Wave", "", "zwRelayType"),
        _fake_device(4, "Uncataloged B", "com.other.plugin", "widget"),
    ]


def test_lists_only_plugin_devices_without_profile(mock_indigo, monkeypatch):
    from tools.lookup import _list_uncataloged_devices_handler

    _populate(mock_indigo, monkeypatch)
    result = _list_uncataloged_devices_handler({}, mock_indigo)
    assert result["total_count"] == 2
    assert [r["id"] for r in result["results"]] == [2, 4]
    # Standard list envelope.
    assert result["offset"] == 0
    assert result["has_more"] is False


def test_pagination(mock_indigo, monkeypatch):
    from tools.lookup import _list_uncataloged_devices_handler

    _populate(mock_indigo, monkeypatch)
    result = _list_uncataloged_devices_handler(
        {"limit": 1, "offset": 0}, mock_indigo
    )
    assert result["total_count"] == 2
    assert len(result["results"]) == 1
    assert result["has_more"] is True
    second = _list_uncataloged_devices_handler(
        {"limit": 1, "offset": 1}, mock_indigo
    )
    assert [r["id"] for r in second["results"]] == [4]
    assert second["has_more"] is False


def test_empty_when_everything_cataloged(mock_indigo, monkeypatch):
    import catalog_snapshot
    from tools.lookup import _list_uncataloged_devices_handler

    monkeypatch.setattr(catalog_snapshot, "PROFILES", _TEST_PROFILES)
    mock_indigo.devices = [
        _fake_device(1, "Cataloged", "com.test.plugin", "cataloged"),
        _fake_device(3, "Built-in", "", "zwRelayType"),
    ]
    result = _list_uncataloged_devices_handler({}, mock_indigo)
    assert result["total_count"] == 0
    assert result["results"] == []


def test_unknown_args_rejected(mock_indigo, monkeypatch):
    from tools.lookup import _list_uncataloged_devices_handler

    _populate(mock_indigo, monkeypatch)
    with pytest.raises(ValueError, match="unknown argument"):
        _list_uncataloged_devices_handler({"plugin": "x"}, mock_indigo)
