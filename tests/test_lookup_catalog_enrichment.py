"""Tests for catalog enrichment of the device-detail serializer.

get_device_by_id gains a ``capabilities`` block when the device's
(pluginId, deviceTypeId) matches a vendored catalog profile; the key
must be entirely absent (not null/empty) when there is no profile, so
callers can distinguish "no colour support" from "no data".
"""

from unittest.mock import MagicMock


def _fake_device(plugin_id="", type_id="dimmer"):
    d = MagicMock()
    d.id = 42
    d.name = "Test Device"
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
    ("com.test.plugin", "myDimmer"): {
        "base_class": "indigo.DimmerDevice",
        "capabilities": {"supportsRGB": True, "supportsWhite": False},
    },
}


def test_detail_includes_capabilities_when_profile_exists(
        mock_indigo, monkeypatch):
    import catalog_snapshot
    from tools.lookup import _serialize_device_detail

    monkeypatch.setattr(catalog_snapshot, "PROFILES", _TEST_PROFILES)
    out = _serialize_device_detail(_fake_device("com.test.plugin", "myDimmer"))
    assert out["capabilities"] == {
        "supportsRGB": True, "supportsWhite": False,
    }


def test_detail_capabilities_is_a_copy(mock_indigo, monkeypatch):
    import catalog_snapshot
    from tools.lookup import _serialize_device_detail

    monkeypatch.setattr(catalog_snapshot, "PROFILES", _TEST_PROFILES)
    out = _serialize_device_detail(_fake_device("com.test.plugin", "myDimmer"))
    out["capabilities"]["supportsRGB"] = False
    # The vendored table must not be mutated through the response.
    assert _TEST_PROFILES[("com.test.plugin", "myDimmer")][
        "capabilities"]["supportsRGB"] is True


def test_detail_omits_capabilities_when_no_profile(mock_indigo, monkeypatch):
    import catalog_snapshot
    from tools.lookup import _serialize_device_detail

    monkeypatch.setattr(catalog_snapshot, "PROFILES", _TEST_PROFILES)
    out = _serialize_device_detail(_fake_device("com.unknown.plugin", "other"))
    assert "capabilities" not in out


def test_detail_omits_capabilities_for_builtin_device(
        mock_indigo, monkeypatch):
    import catalog_snapshot
    from tools.lookup import _serialize_device_detail

    monkeypatch.setattr(catalog_snapshot, "PROFILES", _TEST_PROFILES)
    # Built-in/interface devices have no pluginId.
    out = _serialize_device_detail(_fake_device("", "zwRelayType"))
    assert "capabilities" not in out


def test_get_device_by_id_carries_capabilities_end_to_end(
        mock_indigo, monkeypatch):
    import catalog_snapshot
    from tools.lookup import _get_device_handler

    monkeypatch.setattr(catalog_snapshot, "PROFILES", _TEST_PROFILES)
    dev = _fake_device("com.test.plugin", "myDimmer")
    mock_indigo.devices.__getitem__.side_effect = {42: dev}.__getitem__
    result = _get_device_handler({"id": 42}, mock_indigo)
    assert result["capabilities"]["supportsRGB"] is True
