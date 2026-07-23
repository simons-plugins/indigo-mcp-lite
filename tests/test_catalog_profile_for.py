"""Tests for catalog.profile_for — the snapshot lookup helper."""

from unittest.mock import MagicMock


def _fake_device(plugin_id, type_id):
    d = MagicMock()
    d.pluginId = plugin_id
    d.deviceTypeId = type_id
    return d


_TEST_PROFILES = {
    ("com.test.plugin", "myType"): {
        "base_class": "indigo.DimmerDevice",
        "capabilities": {"supportsRGB": True, "supportsWhite": False},
    },
}


def test_profile_for_hit(monkeypatch):
    import catalog_snapshot
    from catalog import profile_for

    monkeypatch.setattr(catalog_snapshot, "PROFILES", _TEST_PROFILES)
    profile = profile_for(_fake_device("com.test.plugin", "myType"))
    assert profile is not None
    assert profile["capabilities"]["supportsRGB"] is True


def test_profile_for_miss_returns_none(monkeypatch):
    import catalog_snapshot
    from catalog import profile_for

    monkeypatch.setattr(catalog_snapshot, "PROFILES", _TEST_PROFILES)
    assert profile_for(_fake_device("com.other.plugin", "myType")) is None
    assert profile_for(_fake_device("com.test.plugin", "otherType")) is None


def test_profile_for_empty_plugin_id_returns_none(monkeypatch):
    import catalog_snapshot
    from catalog import profile_for

    monkeypatch.setattr(catalog_snapshot, "PROFILES", _TEST_PROFILES)
    assert profile_for(_fake_device("", "myType")) is None
    assert profile_for(_fake_device("com.test.plugin", "")) is None


def test_profile_for_non_string_ids_return_none():
    from catalog import profile_for

    # A bare MagicMock's attributes are MagicMocks, not strings — the
    # helper must miss cleanly rather than raise or false-match.
    assert profile_for(MagicMock()) is None


def test_vendored_snapshot_is_consistent():
    """The real generated snapshot loads and its meta matches."""
    from catalog import snapshot_meta
    from catalog_snapshot import PROFILES

    meta = snapshot_meta()
    assert meta["profile_count"] == len(PROFILES)
    assert len(PROFILES) > 0
    for key, profile in PROFILES.items():
        assert isinstance(key, tuple) and len(key) == 2
        assert all(isinstance(part, str) and part for part in key)
        assert isinstance(profile["capabilities"], dict)
        assert profile["base_class"]
