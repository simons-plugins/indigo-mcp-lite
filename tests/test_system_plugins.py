"""TDD tests for list_plugins / get_plugin_by_id / get_plugin_status.

``list_plugins`` scans the filesystem rather than calling
``indigo.server.getPluginList()`` (the SDK call returns ``[]`` on
Indigo 2025.x — see system.py docstring). Each bundle's
``CFBundleIdentifier`` is read from its Info.plist, then resolved
via ``indigo.server.getPlugin(plugin_id)`` for the live status
flags. ``get_plugin_by_id`` and ``get_plugin_status`` use
``getPlugin`` directly.
"""
import os
import plistlib
from unittest.mock import MagicMock

import pytest


def _fake_plugin(plugin_id, *, version="1.0.0", display="Plugin",
                 enabled=True, running=True, installed=True,
                 folder_path="/tmp/plugin"):
    p = MagicMock()
    p.pluginId = plugin_id
    p.pluginVersion = version
    p.pluginDisplayName = display
    p.pluginFolderPath = folder_path
    p.isEnabled = MagicMock(return_value=enabled)
    p.isRunning = MagicMock(return_value=running)
    p.isInstalled = MagicMock(return_value=installed)
    return p


def _make_bundle(plugins_dir, plugin_id, *, name=None):
    """Create a fake ``*.indigoPlugin`` bundle with a valid Info.plist
    in ``plugins_dir`` (e.g. tmp_path / "Plugins"). Returns the bundle
    path so callers can inspect it."""
    bundle_name = name or plugin_id.replace(".", "_") + ".indigoPlugin"
    bundle = plugins_dir / bundle_name
    contents = bundle / "Contents"
    contents.mkdir(parents=True, exist_ok=True)
    plist_path = contents / "Info.plist"
    with open(plist_path, "wb") as fh:
        plistlib.dump({"CFBundleIdentifier": plugin_id}, fh)
    return bundle


# ----- list_plugins ------------------------------------------------------


def test_list_plugins_scans_filesystem_and_resolves_each(mock_indigo, tmp_path):
    from tools.system import _list_plugins_handler

    plugins_dir = tmp_path / "Plugins"
    plugins_dir.mkdir()
    _make_bundle(plugins_dir, "com.foo.alpha")
    _make_bundle(plugins_dir, "com.bar.beta")

    plugins = {
        "com.foo.alpha": _fake_plugin("com.foo.alpha", version="1.2",
                                       display="Alpha", running=True),
        "com.bar.beta": _fake_plugin("com.bar.beta", version="0.9",
                                      display="Beta", running=False),
    }
    mock_indigo.server.getInstallFolderPath.return_value = str(tmp_path)
    mock_indigo.server.getPlugin.side_effect = lambda pid: plugins[pid]

    result = _list_plugins_handler({}, mock_indigo)

    assert result["total_count"] == 2
    by_id = {r["plugin_id"]: r for r in result["results"]}
    assert by_id["com.foo.alpha"]["display_name"] == "Alpha"
    assert by_id["com.foo.alpha"]["version"] == "1.2"
    assert by_id["com.bar.beta"]["running"] is False


def test_list_plugins_includes_disabled_subdir(mock_indigo, tmp_path):
    """Both Plugins/ and Plugins (Disabled)/ should be scanned —
    the live ``isEnabled()`` flag tells callers which is which."""
    from tools.system import _list_plugins_handler

    enabled_dir = tmp_path / "Plugins"
    disabled_dir = tmp_path / "Plugins (Disabled)"
    enabled_dir.mkdir()
    disabled_dir.mkdir()
    _make_bundle(enabled_dir, "com.foo.alpha")
    _make_bundle(disabled_dir, "com.bar.disabled")

    plugins = {
        "com.foo.alpha": _fake_plugin("com.foo.alpha", enabled=True),
        "com.bar.disabled": _fake_plugin("com.bar.disabled", enabled=False),
    }
    mock_indigo.server.getInstallFolderPath.return_value = str(tmp_path)
    mock_indigo.server.getPlugin.side_effect = lambda pid: plugins[pid]

    result = _list_plugins_handler({}, mock_indigo)

    by_id = {r["plugin_id"]: r for r in result["results"]}
    assert "com.foo.alpha" in by_id
    assert "com.bar.disabled" in by_id
    assert by_id["com.bar.disabled"]["enabled"] is False


def test_list_plugins_empty_when_no_bundles(mock_indigo, tmp_path):
    from tools.system import _list_plugins_handler

    (tmp_path / "Plugins").mkdir()
    mock_indigo.server.getInstallFolderPath.return_value = str(tmp_path)

    result = _list_plugins_handler({}, mock_indigo)

    assert result["results"] == []
    assert result["total_count"] == 0


def test_list_plugins_skips_bundles_missing_info_plist(mock_indigo, tmp_path):
    from tools.system import _list_plugins_handler

    plugins_dir = tmp_path / "Plugins"
    plugins_dir.mkdir()
    # Real bundle, valid Info.plist
    _make_bundle(plugins_dir, "com.good.one")
    # Bundle with no Info.plist (corrupt install)
    bad = plugins_dir / "broken.indigoPlugin"
    (bad / "Contents").mkdir(parents=True)

    mock_indigo.server.getInstallFolderPath.return_value = str(tmp_path)
    mock_indigo.server.getPlugin.return_value = _fake_plugin("com.good.one")

    result = _list_plugins_handler({}, mock_indigo)

    assert result["total_count"] == 1
    assert result["results"][0]["plugin_id"] == "com.good.one"


def test_list_plugins_skips_bundles_whose_getPlugin_raises(mock_indigo, tmp_path):
    """Plugin in directory but getPlugin raises (rare uninstall race
    or unloaded plugin) — skip rather than fail the listing."""
    from tools.system import _list_plugins_handler

    plugins_dir = tmp_path / "Plugins"
    plugins_dir.mkdir()
    _make_bundle(plugins_dir, "com.good.one")
    _make_bundle(plugins_dir, "com.broken.two")

    mock_indigo.server.getInstallFolderPath.return_value = str(tmp_path)

    def _get(pid):
        if pid == "com.good.one":
            return _fake_plugin("com.good.one")
        raise KeyError(pid)

    mock_indigo.server.getPlugin.side_effect = _get
    result = _list_plugins_handler({}, mock_indigo)

    assert result["total_count"] == 1
    assert result["results"][0]["plugin_id"] == "com.good.one"


def test_list_plugins_returns_empty_when_install_path_unresolvable(mock_indigo):
    """If getInstallFolderPath raises (or returns empty), return an
    empty list — better than crashing the call."""
    from tools.system import _list_plugins_handler

    mock_indigo.server.getInstallFolderPath.side_effect = Exception("boom")
    result = _list_plugins_handler({}, mock_indigo)

    assert result["total_count"] == 0


# ----- get_plugin_by_id --------------------------------------------------


def test_get_plugin_by_id_happy_path(mock_indigo):
    from tools.system import _get_plugin_by_id_handler

    plugin = _fake_plugin("com.foo.alpha", version="1.2",
                           display="Alpha", folder_path="/x/Alpha.indigoPlugin")
    mock_indigo.server.getPlugin.return_value = plugin

    result = _get_plugin_by_id_handler(
        {"plugin_id": "com.foo.alpha"}, mock_indigo
    )

    mock_indigo.server.getPlugin.assert_called_once_with("com.foo.alpha")
    assert result["plugin_id"] == "com.foo.alpha"
    assert result["display_name"] == "Alpha"
    assert result["version"] == "1.2"
    assert result["folder_path"] == "/x/Alpha.indigoPlugin"
    assert result["enabled"] is True
    assert result["running"] is True
    assert result["installed"] is True


def test_get_plugin_by_id_missing_arg_raises(mock_indigo):
    from tools.system import _get_plugin_by_id_handler

    with pytest.raises(ValueError, match="plugin_id"):
        _get_plugin_by_id_handler({}, mock_indigo)


def test_get_plugin_by_id_non_string_raises(mock_indigo):
    from tools.system import _get_plugin_by_id_handler

    with pytest.raises(ValueError, match="plugin_id"):
        _get_plugin_by_id_handler({"plugin_id": 42}, mock_indigo)


def test_get_plugin_by_id_unknown_raises(mock_indigo):
    """Indigo's getPlugin raises (typically Exception/KeyError) when
    the plugin id isn't installed; surface as a clean ValueError."""
    from tools.system import _get_plugin_by_id_handler

    mock_indigo.server.getPlugin.side_effect = KeyError("not found")
    with pytest.raises(ValueError, match="com.unknown"):
        _get_plugin_by_id_handler(
            {"plugin_id": "com.unknown"}, mock_indigo
        )


# ----- get_plugin_status -------------------------------------------------


def test_get_plugin_status_returns_runtime_state(mock_indigo):
    from tools.system import _get_plugin_status_handler

    plugin = _fake_plugin("com.foo.alpha",
                           enabled=True, running=False, installed=True)
    mock_indigo.server.getPlugin.return_value = plugin

    result = _get_plugin_status_handler(
        {"plugin_id": "com.foo.alpha"}, mock_indigo
    )

    assert result["plugin_id"] == "com.foo.alpha"
    assert result["enabled"] is True
    assert result["running"] is False
    assert result["installed"] is True


def test_get_plugin_status_unknown_raises(mock_indigo):
    from tools.system import _get_plugin_status_handler

    mock_indigo.server.getPlugin.side_effect = KeyError("nope")
    with pytest.raises(ValueError, match="com.unknown"):
        _get_plugin_status_handler(
            {"plugin_id": "com.unknown"}, mock_indigo
        )
