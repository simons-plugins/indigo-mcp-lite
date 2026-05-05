"""TDD tests for list_plugins / get_plugin_by_id / get_plugin_status.

``indigo.server.getPluginList()`` returns a list of plugin id strings;
``indigo.server.getPlugin(plugin_id)`` returns a plugin object with
attributes (``pluginId``, ``pluginVersion``, ``pluginDisplayName``,
``pluginFolderPath``) and callable status methods (``isEnabled()``,
``isRunning()``, ``isInstalled()``).
"""
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


# ----- list_plugins ------------------------------------------------------


def test_list_plugins_returns_serialised_entries(mock_indigo):
    from tools.system import _list_plugins_handler

    plugins = {
        "com.foo.alpha": _fake_plugin("com.foo.alpha", version="1.2",
                                       display="Alpha", running=True),
        "com.bar.beta": _fake_plugin("com.bar.beta", version="0.9",
                                      display="Beta", running=False),
    }
    mock_indigo.server.getPluginList.return_value = list(plugins.keys())
    mock_indigo.server.getPlugin.side_effect = lambda pid: plugins[pid]

    result = _list_plugins_handler({}, mock_indigo)

    assert result["total_count"] == 2
    by_id = {r["plugin_id"]: r for r in result["results"]}
    assert by_id["com.foo.alpha"]["display_name"] == "Alpha"
    assert by_id["com.foo.alpha"]["version"] == "1.2"
    assert by_id["com.foo.alpha"]["running"] is True
    assert by_id["com.bar.beta"]["running"] is False


def test_list_plugins_empty(mock_indigo):
    from tools.system import _list_plugins_handler

    mock_indigo.server.getPluginList.return_value = []
    result = _list_plugins_handler({}, mock_indigo)

    assert result["results"] == []
    assert result["total_count"] == 0


def test_list_plugins_skips_unresolvable(mock_indigo):
    """If getPlugin raises for a listed id, skip it gracefully — the
    plugin manifest can lag behind getPluginList by a tick during
    install/uninstall."""
    from tools.system import _list_plugins_handler

    good = _fake_plugin("com.good.one")
    mock_indigo.server.getPluginList.return_value = ["com.good.one", "com.broken.two"]

    def _get(pid):
        if pid == "com.good.one":
            return good
        raise KeyError(pid)

    mock_indigo.server.getPlugin.side_effect = _get
    result = _list_plugins_handler({}, mock_indigo)

    assert result["total_count"] == 1
    assert result["results"][0]["plugin_id"] == "com.good.one"


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
