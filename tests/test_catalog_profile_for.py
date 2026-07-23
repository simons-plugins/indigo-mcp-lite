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


def test_registry_and_lookups_survive_broken_snapshot(mock_indigo):
    """A corrupt/missing catalog_snapshot.py must NOT take down the
    tool registry: every tool module imports through catalog.py at
    registration time, so the import failure has to degrade to an
    empty catalog (profile_for → None, meta → {}) with all tools
    still registered.

    Simulated by purging the modules and re-importing with
    ``sys.modules["catalog_snapshot"] = None`` — Python raises
    ImportError for a None entry, exactly like a broken file at
    plugin startup.
    """
    import sys

    affected = [
        n for n in sys.modules
        if n in ("catalog", "catalog_snapshot", "tool_registry", "tools")
        or n.startswith("tools.")
    ]
    saved = {n: sys.modules.pop(n) for n in affected}
    sys.modules["catalog_snapshot"] = None  # import now raises ImportError
    try:
        import catalog
        import tool_registry

        dev = _fake_device("com.test.plugin", "myType")
        assert catalog.profile_for(dev) is None
        assert catalog.snapshot_meta() == {}

        handler = MagicMock()
        tool_registry.register_all(handler, indigo_module=mock_indigo)
        names = [
            (call.kwargs.get("name") or (call.args[0] if call.args else None))
            for call in handler.register_tool.call_args_list
        ]
        assert "get_device_by_id" in names
        assert "device_set_rgb_color" in names
        assert "list_uncataloged_devices" in names
    finally:
        # Drop the broken/fresh modules and restore the healthy
        # originals so later tests keep their module identity.
        for name in [
            n for n in sys.modules
            if n in ("catalog", "catalog_snapshot", "tool_registry", "tools")
            or n.startswith("tools.")
        ]:
            del sys.modules[name]
        sys.modules.update(saved)


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
