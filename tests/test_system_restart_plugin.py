"""TDD tests for restart_plugin.

Wraps ``plugin.restart(waitUntilDone=True)`` on the plugin object
returned by ``indigo.server.getPlugin``. CRITICAL self-restart
guard: refuse to restart ``com.simons-plugins.indigo-mcp-lite``,
since the in-flight MCP request would die mid-response and the
caller would see a transport error rather than a clean tool result.
"""
from unittest.mock import MagicMock

import pytest


def _fake_plugin(plugin_id):
    p = MagicMock()
    p.pluginId = plugin_id
    p.restart = MagicMock()
    return p


def test_restart_plugin_calls_restart(mock_indigo):
    from tools.system import _restart_plugin_handler

    plugin = _fake_plugin("com.foo.alpha")
    mock_indigo.server.getPlugin.return_value = plugin

    result = _restart_plugin_handler(
        {"plugin_id": "com.foo.alpha"}, mock_indigo
    )

    plugin.restart.assert_called_once_with(waitUntilDone=True)
    assert result["status"] == "ok"
    assert result["plugin_id"] == "com.foo.alpha"


def test_restart_plugin_refuses_self_by_id(mock_indigo):
    """Refuse to restart self — the in-flight MCP request would die
    mid-response, leaving the caller with a transport error rather
    than a clean tool result."""
    from tools.system import _restart_plugin_handler

    with pytest.raises(ValueError, match="self"):
        _restart_plugin_handler(
            {"plugin_id": "com.simons-plugins.indigo-mcp-lite"},
            mock_indigo,
        )
    mock_indigo.server.getPlugin.assert_not_called()


def test_restart_plugin_missing_plugin_id_raises(mock_indigo):
    from tools.system import _restart_plugin_handler

    with pytest.raises(ValueError, match="plugin_id"):
        _restart_plugin_handler({}, mock_indigo)


def test_restart_plugin_non_string_plugin_id_raises(mock_indigo):
    from tools.system import _restart_plugin_handler

    with pytest.raises(ValueError, match="plugin_id"):
        _restart_plugin_handler({"plugin_id": 42}, mock_indigo)


def test_restart_plugin_unknown_id_raises(mock_indigo):
    from tools.system import _restart_plugin_handler

    mock_indigo.server.getPlugin.side_effect = KeyError("nope")
    with pytest.raises(ValueError, match="com.unknown"):
        _restart_plugin_handler(
            {"plugin_id": "com.unknown"}, mock_indigo
        )


def test_restart_plugin_self_check_is_case_sensitive(mock_indigo):
    """Plugin ids in Indigo are case-sensitive bundle identifiers, so
    the guard matches exactly. A case-mangled id wouldn't be the live
    plugin anyway."""
    from tools.system import _restart_plugin_handler

    plugin = _fake_plugin("COM.SIMONS-PLUGINS.INDIGO-MCP-LITE")
    mock_indigo.server.getPlugin.return_value = plugin

    # The guard is case-sensitive — uppercase id does not match self,
    # so it falls through to the normal restart path. (In practice
    # this would fail at getPlugin since the id wouldn't exist, but
    # we want the guard itself to be exact-match.)
    _restart_plugin_handler(
        {"plugin_id": "COM.SIMONS-PLUGINS.INDIGO-MCP-LITE"},
        mock_indigo,
    )
    plugin.restart.assert_called_once()
