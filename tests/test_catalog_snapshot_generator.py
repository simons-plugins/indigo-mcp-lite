"""Tests for scripts/generate_catalog_snapshot.py.

Golden-fixture based: a tiny synthetic catalog checkout is built in
tmp_path, and the generator must produce deterministic, importable
output from it. Determinism matters because the catalog-refresh CI
workflow decides "did anything change?" via git diff of the emitted
file — a nondeterministic generator would open a refresh PR on every
dispatch.
"""

import json
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def _write_catalog(root, files):
    """Create catalog/by-class/*.json files under ``root``."""
    by_class = root / "catalog" / "by-class"
    by_class.mkdir(parents=True)
    for name, payload in files.items():
        (by_class / name).write_text(json.dumps(payload))
    return root


def _profile(plugin_id, type_id, caps, **extra):
    profile = {
        "pluginId": plugin_id,
        "pluginName": "Test Plugin",
        "deviceTypeId": type_id,
        "capabilities": caps,
        "states": {"onOffState": {"type": "boolean"}},
        "metadata": {"contributedBy": "tester", "discoveredAt": "2026-01-01"},
    }
    profile.update(extra)
    return profile


@pytest.fixture
def small_catalog(tmp_path):
    return _write_catalog(tmp_path, {
        "relay.json": {
            "baseClass": "indigo.RelayDevice",
            "classCapabilities": ["supportsOnState"],
            "profiles": [
                _profile(
                    "com.test.b", "typeB",
                    {"supportsOnState": True, "supportsRGB": False},
                    protocol="mqtt", model="Model B",
                ),
                _profile("com.test.a", "typeA", {"supportsOnState": True}),
            ],
        },
        "dimmer.json": {
            "baseClass": "indigo.DimmerDevice",
            "classCapabilities": ["supportsRGB"],
            "profiles": [
                _profile(
                    "com.test.a", "typeDim",
                    {"supportsRGB": True},
                    subModel="Sub", displayStateId="brightnessLevel",
                ),
            ],
        },
    })


def test_build_profiles_keys_and_fields(small_catalog):
    from generate_catalog_snapshot import build_profiles

    profiles = build_profiles(small_catalog)
    assert set(profiles) == {
        ("com.test.a", "typeA"),
        ("com.test.b", "typeB"),
        ("com.test.a", "typeDim"),
    }
    b = profiles[("com.test.b", "typeB")]
    assert b["base_class"] == "indigo.RelayDevice"
    assert b["capabilities"] == {
        "supportsOnState": True, "supportsRGB": False,
    }
    assert b["protocol"] == "mqtt"
    assert b["model"] == "Model B"
    dim = profiles[("com.test.a", "typeDim")]
    assert dim["sub_model"] == "Sub"
    assert dim["display_state_id"] == "brightnessLevel"
    # Empty/absent optional fields are omitted, not vendored as None.
    assert "protocol" not in profiles[("com.test.a", "typeA")]
    # states are deliberately NOT vendored — the live device is fresher.
    assert all("states" not in p for p in profiles.values())


def test_render_is_deterministic(small_catalog):
    from generate_catalog_snapshot import build_profiles, render

    first = render(build_profiles(small_catalog), "abc123", "2026-01-01")
    second = render(build_profiles(small_catalog), "abc123", "2026-01-01")
    assert first == second


def test_rendered_module_is_importable(small_catalog, tmp_path):
    from generate_catalog_snapshot import build_profiles, render

    source = render(build_profiles(small_catalog), "abc123", "2026-01-01")
    namespace = {}
    exec(compile(source, "catalog_snapshot.py", "exec"), namespace)
    assert namespace["SNAPSHOT_META"] == {
        "catalog_commit": "abc123",
        "catalog_date": "2026-01-01",
        "profile_count": 3,
    }
    assert ("com.test.a", "typeA") in namespace["PROFILES"]


def test_duplicate_key_raises(tmp_path):
    from generate_catalog_snapshot import build_profiles

    _write_catalog(tmp_path, {
        "relay.json": {
            "baseClass": "indigo.RelayDevice",
            "classCapabilities": [],
            "profiles": [_profile("com.test.a", "typeA", {})],
        },
        "sensor.json": {
            "baseClass": "indigo.SensorDevice",
            "classCapabilities": [],
            "profiles": [_profile("com.test.a", "typeA", {})],
        },
    })
    with pytest.raises(ValueError, match="duplicate catalog key"):
        build_profiles(tmp_path)


def test_missing_catalog_dir_raises(tmp_path):
    from generate_catalog_snapshot import build_profiles

    with pytest.raises(ValueError, match="by-class"):
        build_profiles(tmp_path / "nowhere")


def test_main_writes_output_with_explicit_meta(small_catalog, tmp_path):
    from generate_catalog_snapshot import main

    out = tmp_path / "out.py"
    main([
        str(small_catalog), "--commit", "deadbeef",
        "--date", "2026-02-02", "--output", str(out),
    ])
    text = out.read_text()
    assert "'deadbeef'" in text
    assert "'2026-02-02'" in text
    assert "AUTO-GENERATED" in text
