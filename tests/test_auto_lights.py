"""TDD tests for the auto_lights_* tools (issue #66).

Covers the degradation paths named in the issue and the workspace
testing convention: a restart/action that raises must fail the whole
call rather than report success; an mtime that moves between read
and write must refuse (and leave the file untouched); ``level=True``
must never be stored as ``1``; unknown zone/period/device must raise
naming which one; and a missing/corrupt config must raise rather than
return an empty-but-ok result.
"""
import json
import os
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------

def _sample_config():
    return {
        "plugin_config": {"default_lock_duration": 3600},
        "zones": [
            {
                "name": "Kitchen",
                "lighting_period_ids": [1, 2],
                "device_settings": {
                    "on_lights_dev_ids": [100, 101],
                    "off_lights_dev_ids": [102],
                    "luminance_dev_ids": [200],
                    "presence_dev_ids": [300],
                },
                "minimum_luminance_settings": {"minimum_luminance": 30},
                "behavior_settings": {"lock_duration": -1},
                "advanced_settings": {"exclude_from_lock_dev_ids": []},
                "device_period_map": {"1": {"100": True}},
                "global_behavior_variables_map": {},
            },
            {
                "name": "Hallway",
                "lighting_period_ids": [1],
                "device_settings": {
                    "on_lights_dev_ids": [400],
                    "off_lights_dev_ids": [],
                    "luminance_dev_ids": [],
                    "presence_dev_ids": [],
                },
            },
        ],
        "lighting_periods": [
            {
                "id": 1, "name": "Evening", "mode": "On and Off",
                "from_time_hour": 18, "from_time_minute": 0,
                "to_time_hour": 23, "to_time_minute": 0,
                "lock_duration": -1, "limit_brightness": -1,
            },
            {
                "id": 2, "name": "Night", "mode": "Off Only",
                "from_time_hour": 23, "from_time_minute": 0,
                "to_time_hour": 6, "to_time_minute": 0,
                "lock_duration": -1, "limit_brightness": -1,
            },
        ],
    }


def _write_config(mock_indigo, tmp_path, config):
    """Write ``config`` at the real Preferences path under ``tmp_path``
    and point ``getInstallFolderPath`` at it. Returns the config path."""
    config_dir = tmp_path / "Preferences" / "com.vtmikel.autolights" / "config"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "auto_lights_conf.json"
    config_path.write_text(json.dumps(config, indent=2))
    mock_indigo.server.getInstallFolderPath.return_value = str(tmp_path)
    return config_path


def _fake_plugin():
    p = MagicMock()
    p.restart = MagicMock()
    p.executeAction = MagicMock()
    return p


# ---------------------------------------------------------------------
# auto_lights_list_zones
# ---------------------------------------------------------------------

def test_list_zones_happy_path(mock_indigo, tmp_path):
    from tools.auto_lights import _list_zones_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    result = _list_zones_handler({}, mock_indigo)

    assert result["total_count"] == 2
    by_name = {z["name"]: z for z in result["zones"]}
    assert by_name["Kitchen"]["device_settings"]["on_lights_dev_ids"] == [100, 101]
    assert [p["id"] for p in by_name["Kitchen"]["periods"]] == [1, 2]
    assert by_name["Kitchen"]["device_period_map"] == {"1": {"100": True}}
    assert len(result["lighting_periods"]) == 2


def test_list_zones_rejects_unknown_args(mock_indigo, tmp_path):
    from tools.auto_lights import _list_zones_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    with pytest.raises(ValueError, match="unknown argument"):
        _list_zones_handler({"bogus": 1}, mock_indigo)


def test_list_zones_missing_config_raises_not_empty_result(mock_indigo, tmp_path):
    """Missing config -> ValueError, never {"zones": [], "total_count": 0}."""
    from tools.auto_lights import _list_zones_handler

    mock_indigo.server.getInstallFolderPath.return_value = str(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        _list_zones_handler({}, mock_indigo)


def test_list_zones_corrupt_json_raises(mock_indigo, tmp_path):
    from tools.auto_lights import _list_zones_handler

    config_dir = tmp_path / "Preferences" / "com.vtmikel.autolights" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "auto_lights_conf.json").write_text("{not valid json")
    mock_indigo.server.getInstallFolderPath.return_value = str(tmp_path)

    with pytest.raises(ValueError, match="not valid JSON"):
        _list_zones_handler({}, mock_indigo)


def test_list_zones_malformed_shape_raises(mock_indigo, tmp_path):
    """Parses fine as JSON but doesn't look like an Auto Lights config."""
    from tools.auto_lights import _list_zones_handler

    _write_config(mock_indigo, tmp_path, {"not": "an auto lights config"})
    with pytest.raises(ValueError, match="does not look like"):
        _list_zones_handler({}, mock_indigo)


def test_list_zones_install_path_unavailable_raises(mock_indigo):
    from tools.auto_lights import _list_zones_handler

    mock_indigo.server.getInstallFolderPath.side_effect = Exception("boom")
    with pytest.raises(ValueError, match="install folder"):
        _list_zones_handler({}, mock_indigo)


# ---------------------------------------------------------------------
# auto_lights_set_level — validation ordering / cheap-before-expensive
# ---------------------------------------------------------------------

def test_set_level_invalid_level_never_touches_indigo(mock_indigo):
    """Cheap argument validation runs before any Indigo/file I/O — the
    workspace convention of pinning that the cheap check runs before
    the expensive one. Hand it a dependency that raises if touched."""
    from tools.auto_lights import _set_level_handler

    mock_indigo.server.getInstallFolderPath.side_effect = AssertionError(
        "must not be called for a bad level"
    )
    with pytest.raises(ValueError, match="level"):
        _set_level_handler(
            {"zone": "Kitchen", "period": 1, "device": 100, "level": None},
            mock_indigo,
        )
    mock_indigo.server.getInstallFolderPath.assert_not_called()


@pytest.mark.parametrize("bad_level", [0, 101, "30", None])
def test_set_level_rejects_bad_levels_with_distinct_messages(mock_indigo, bad_level):
    from tools.auto_lights import _set_level_handler

    with pytest.raises(ValueError) as excinfo:
        _set_level_handler(
            {"zone": "Kitchen", "period": 1, "device": 100, "level": bad_level},
            mock_indigo,
        )
    message = str(excinfo.value)
    if bad_level in (0, 101):
        assert "between 1 and 100" in message
    elif bad_level is None:
        assert "required" in message
    else:
        assert "int 1-100" in message and "str" in message


def test_set_level_rejects_unknown_args(mock_indigo, tmp_path):
    from tools.auto_lights import _set_level_handler

    with pytest.raises(ValueError, match="unknown argument"):
        _set_level_handler(
            {"zone": "Kitchen", "period": 1, "device": 100, "level": 50,
             "bogus": 1},
            mock_indigo,
        )


# ---------------------------------------------------------------------
# auto_lights_set_level — happy path, backup, restart
# ---------------------------------------------------------------------

def test_set_level_writes_backs_up_and_restarts(mock_indigo, tmp_path):
    from tools.auto_lights import _set_level_handler

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    plugin = _fake_plugin()
    mock_indigo.server.getPlugin.return_value = plugin

    result = _set_level_handler(
        {"zone": "Kitchen", "period": 1, "device": 101, "level": 42},
        mock_indigo,
    )

    assert result["status"] == "ok"
    assert result["level"] == 42
    assert result["previous_level"] is None
    plugin.restart.assert_called_once_with(waitUntilDone=True)

    written = json.loads(config_path.read_text())
    kitchen = next(z for z in written["zones"] if z["name"] == "Kitchen")
    assert kitchen["device_period_map"]["1"]["101"] == 42

    backup_path = result["backup_path"]
    assert os.path.isfile(backup_path)
    backed_up = json.loads(open(backup_path).read())
    # Backup is the PRE-write content — device 101 wasn't set yet.
    assert "101" not in backed_up["zones"][0]["device_period_map"].get("1", {})


def test_set_level_true_is_not_stored_as_1(mock_indigo, tmp_path):
    """bool is a subclass of int — true must be rejected before any
    int range check or it reads as brightness 1."""
    from tools.auto_lights import _set_level_handler

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _set_level_handler(
        {"zone": "Kitchen", "period": 2, "device": 100, "level": True},
        mock_indigo,
    )
    assert result["level"] is True

    raw_text = config_path.read_text()
    written = json.loads(raw_text)
    kitchen = next(z for z in written["zones"] if z["name"] == "Kitchen")
    assert kitchen["device_period_map"]["2"]["100"] is True
    # Byte-level: JSON must say true, never 1.
    assert '"100": true' in raw_text
    assert '"100": 1' not in raw_text


def test_set_level_false_is_stored_as_false(mock_indigo, tmp_path):
    from tools.auto_lights import _set_level_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _set_level_handler(
        {"zone": "Kitchen", "period": 1, "device": 102, "level": False},
        mock_indigo,
    )
    assert result["level"] is False


# ---------------------------------------------------------------------
# auto_lights_set_level — restart failure must fail the whole call
# ---------------------------------------------------------------------

def test_set_level_restart_failure_fails_the_call(mock_indigo, tmp_path):
    """A restart that raises must make the whole call fail, never
    report success — even though the file write already landed."""
    from tools.auto_lights import _set_level_handler

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    plugin = _fake_plugin()
    plugin.restart.side_effect = Exception("plugin host unreachable")
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError, match="restart"):
        _set_level_handler(
            {"zone": "Kitchen", "period": 1, "device": 101, "level": 77},
            mock_indigo,
        )

    # The write itself happened (that part is real and documented);
    # the failure is that the call did NOT report success.
    written = json.loads(config_path.read_text())
    kitchen = next(z for z in written["zones"] if z["name"] == "Kitchen")
    assert kitchen["device_period_map"]["1"]["101"] == 77


# ---------------------------------------------------------------------
# auto_lights_set_level — mtime-moved refusal
# ---------------------------------------------------------------------

def test_set_level_refuses_when_mtime_moved_between_read_and_write(
    mock_indigo, tmp_path, monkeypatch
):
    """Simulate a save (e.g. by the web editor) landing between our
    read and our pre-write recheck. Must refuse AND leave the file
    byte-identical."""
    import tools.auto_lights as auto_lights_module

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    original_bytes = config_path.read_bytes()
    real_stat = os.stat(str(config_path))

    class _FakeStat:
        def __init__(self, mtime_ns, size):
            self.st_mtime_ns = mtime_ns
            self.st_size = size

    changed_stat = _FakeStat(real_stat.st_mtime_ns + 1_000_000_000, real_stat.st_size)

    calls = {"n": 0}

    def fake_stat(path):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_stat
        return changed_stat

    monkeypatch.setattr(auto_lights_module, "_stat", fake_stat)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    with pytest.raises(ValueError, match="changed on disk"):
        auto_lights_module._set_level_handler(
            {"zone": "Kitchen", "period": 1, "device": 101, "level": 55},
            mock_indigo,
        )

    assert calls["n"] == 2
    assert config_path.read_bytes() == original_bytes
    mock_indigo.server.getPlugin.assert_not_called()


# ---------------------------------------------------------------------
# auto_lights_set_level — unknown zone / period / device
# ---------------------------------------------------------------------

def test_set_level_unknown_zone_names_it(mock_indigo, tmp_path):
    from tools.auto_lights import _set_level_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    with pytest.raises(ValueError, match="no Auto Lights zone named 'Attic'"):
        _set_level_handler(
            {"zone": "Attic", "period": 1, "device": 100, "level": 50},
            mock_indigo,
        )


def test_set_level_unknown_period_names_it(mock_indigo, tmp_path):
    from tools.auto_lights import _set_level_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    with pytest.raises(ValueError, match="period 99 is not linked to zone 'Kitchen'"):
        _set_level_handler(
            {"zone": "Kitchen", "period": 99, "device": 100, "level": 50},
            mock_indigo,
        )


def test_set_level_unknown_device_names_it(mock_indigo, tmp_path):
    from tools.auto_lights import _set_level_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    with pytest.raises(ValueError, match="device 999 is not one of zone 'Kitchen'"):
        _set_level_handler(
            {"zone": "Kitchen", "period": 1, "device": 999, "level": 50},
            mock_indigo,
        )


def test_set_level_device_valid_for_other_zone_still_rejected(mock_indigo, tmp_path):
    """Device 400 belongs to Hallway, not Kitchen — must not leak across
    zones."""
    from tools.auto_lights import _set_level_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    with pytest.raises(ValueError, match="device 400 is not one of zone 'Kitchen'"):
        _set_level_handler(
            {"zone": "Kitchen", "period": 1, "device": 400, "level": 50},
            mock_indigo,
        )


def test_set_level_no_write_on_unknown_zone(mock_indigo, tmp_path):
    """Isolation: a failed validation must not touch the file at all."""
    from tools.auto_lights import _set_level_handler

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    original_bytes = config_path.read_bytes()

    with pytest.raises(ValueError):
        _set_level_handler(
            {"zone": "Attic", "period": 1, "device": 100, "level": 50},
            mock_indigo,
        )

    assert config_path.read_bytes() == original_bytes
    mock_indigo.server.getPlugin.assert_not_called()


# ---------------------------------------------------------------------
# auto_lights_set_zone_enabled
# ---------------------------------------------------------------------

def test_set_zone_enabled_true_calls_enable_zone(mock_indigo, tmp_path):
    from tools.auto_lights import _set_zone_enabled_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    plugin = _fake_plugin()
    mock_indigo.server.getPlugin.return_value = plugin

    result = _set_zone_enabled_handler(
        {"zone": "Kitchen", "enabled": True}, mock_indigo
    )
    assert result == {"status": "ok", "zone": "Kitchen", "enabled": True}
    plugin.executeAction.assert_called_once_with(
        "enable_zone", props={"zone_list": "Kitchen"}, waitUntilDone=True
    )


def test_set_zone_enabled_false_calls_disable_zone(mock_indigo, tmp_path):
    from tools.auto_lights import _set_zone_enabled_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    plugin = _fake_plugin()
    mock_indigo.server.getPlugin.return_value = plugin

    _set_zone_enabled_handler({"zone": "Kitchen", "enabled": False}, mock_indigo)
    plugin.executeAction.assert_called_once_with(
        "disable_zone", props={"zone_list": "Kitchen"}, waitUntilDone=True
    )


def test_set_zone_enabled_rejects_int_for_enabled(mock_indigo, tmp_path):
    """enabled=1 must not silently pass as True — require an actual bool."""
    from tools.auto_lights import _set_zone_enabled_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    with pytest.raises(ValueError, match="enabled must be true or false"):
        _set_zone_enabled_handler({"zone": "Kitchen", "enabled": 1}, mock_indigo)
    mock_indigo.server.getPlugin.assert_not_called()


def test_set_zone_enabled_unknown_zone_names_it_and_skips_action(mock_indigo, tmp_path):
    from tools.auto_lights import _set_zone_enabled_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    with pytest.raises(ValueError, match="no Auto Lights zone named 'Attic'"):
        _set_zone_enabled_handler({"zone": "Attic", "enabled": True}, mock_indigo)
    mock_indigo.server.getPlugin.assert_not_called()


def test_set_zone_enabled_action_failure_fails_the_call(mock_indigo, tmp_path):
    from tools.auto_lights import _set_zone_enabled_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    plugin = _fake_plugin()
    plugin.executeAction.side_effect = Exception("not running")
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError, match="enable_zone"):
        _set_zone_enabled_handler({"zone": "Kitchen", "enabled": True}, mock_indigo)


# ---------------------------------------------------------------------
# auto_lights_reset_locks
# ---------------------------------------------------------------------

def test_reset_locks_no_zone_resets_all(mock_indigo, tmp_path):
    from tools.auto_lights import _reset_locks_handler

    plugin = _fake_plugin()
    mock_indigo.server.getPlugin.return_value = plugin

    result = _reset_locks_handler({}, mock_indigo)
    assert result == {"status": "ok", "zone": None}
    plugin.executeAction.assert_called_once_with(
        "reset_all_locks", props={}, waitUntilDone=True
    )


def test_reset_locks_with_zone_resets_that_zone_only(mock_indigo, tmp_path):
    from tools.auto_lights import _reset_locks_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    plugin = _fake_plugin()
    mock_indigo.server.getPlugin.return_value = plugin

    result = _reset_locks_handler({"zone": "Hallway"}, mock_indigo)
    assert result == {"status": "ok", "zone": "Hallway"}
    plugin.executeAction.assert_called_once_with(
        "reset_zone_locks", props={"zone_list": "Hallway"}, waitUntilDone=True
    )


def test_reset_locks_unknown_zone_names_it_and_skips_action(mock_indigo, tmp_path):
    from tools.auto_lights import _reset_locks_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    with pytest.raises(ValueError, match="no Auto Lights zone named 'Attic'"):
        _reset_locks_handler({"zone": "Attic"}, mock_indigo)
    mock_indigo.server.getPlugin.assert_not_called()


def test_reset_locks_action_failure_fails_the_call(mock_indigo, tmp_path):
    from tools.auto_lights import _reset_locks_handler

    plugin = _fake_plugin()
    plugin.executeAction.side_effect = Exception("plugin not installed")
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError, match="reset_all_locks"):
        _reset_locks_handler({}, mock_indigo)


def test_reset_locks_rejects_unknown_args(mock_indigo):
    from tools.auto_lights import _reset_locks_handler

    with pytest.raises(ValueError, match="unknown argument"):
        _reset_locks_handler({"zones": ["a"]}, mock_indigo)


# ---------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------

def test_register_wires_all_four_tools(mock_indigo):
    from tools.auto_lights import register

    handler = MagicMock()
    register(handler, indigo_module=mock_indigo)

    names = [call.kwargs["name"] for call in handler.register_tool.call_args_list]
    assert names == [
        "auto_lights_list_zones",
        "auto_lights_set_level",
        "auto_lights_set_zone_enabled",
        "auto_lights_reset_locks",
    ]


def test_register_all_includes_auto_lights_tools(mock_indigo):
    from tool_registry import register_all

    handler = MagicMock()
    register_all(handler, indigo_module=mock_indigo)

    names = [
        (call.kwargs.get("name") or (call.args[0] if call.args else None))
        for call in handler.register_tool.call_args_list
    ]
    assert "auto_lights_list_zones" in names
    assert "auto_lights_set_level" in names
    assert "auto_lights_set_zone_enabled" in names
    assert "auto_lights_reset_locks" in names
