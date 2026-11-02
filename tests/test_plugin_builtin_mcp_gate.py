"""Plugin-level behaviour of the built-in MCP gate (workspace ADR-0009).

``tool_registry`` decides which tools register; these tests pin what
``plugin.py`` does around that decision on a 2026.1 server: the FTS5
index is never built, the change subscriptions that feed it are never
made, and the client is handed the extension-layer instructions.

"Not built" / "not subscribed" are made FATAL rather than merely
unasserted: constructing the Indexer or calling subscribeToChanges
raises, so a regression fails loudly instead of passing on output.
"""

import importlib.util
import json
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

PLUGIN_PY = (pathlib.Path(__file__).parent.parent
             / "Indigo MCP Lite.indigoPlugin" / "Contents"
             / "Server Plugin" / "plugin.py")


class _FakePluginBase:
    """Just enough of indigo.PluginBase for Plugin.__init__/startup."""

    def __init__(self, plugin_id, plugin_display_name, plugin_version, plugin_prefs):
        self.pluginId = plugin_id
        self.pluginPrefs = plugin_prefs
        self.logger = MagicMock(name="logger")
        self.indigo_log_handler = MagicMock(name="indigo_log_handler")


def _fatal(what):
    def boom(*_args, **_kwargs):
        raise AssertionError(f"{what} must not run on a slim (2026.1+) server")
    return boom


@pytest.fixture
def fake_indigo(monkeypatch):
    fake = MagicMock(name="indigo")
    fake.PluginBase = _FakePluginBase
    fake.server.version = "2026.1.0"
    monkeypatch.setitem(sys.modules, "indigo", fake)
    return fake


@pytest.fixture
def fatal_indexing(monkeypatch, fake_indigo):
    import indexer

    monkeypatch.setattr(indexer, "Indexer", _fatal("Indexer construction"))
    for coll in (fake_indigo.devices, fake_indigo.variables, fake_indigo.actionGroups):
        coll.subscribeToChanges.side_effect = _fatal("subscribeToChanges")
    return fake_indigo


def _load_plugin_module():
    spec = importlib.util.spec_from_file_location("_lite_plugin_gate", PLUGIN_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rpc(handler, method, params=None):
    response = handler.handle_request(
        http_method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                         "params": params or {}}),
    )
    assert response["status"] == 200, response
    return json.loads(response["content"])["result"]


def _make_plugin(module, prefs):
    return module.Plugin("com.simons-plugins.indigo-mcp-lite", "Indigo MCP Lite",
                         "2026.15.0", prefs)


def test_slim_startup_skips_index_and_subscriptions(fatal_indexing):
    module = _load_plugin_module()
    plugin = _make_plugin(module, {"logLevel": "INFO"})
    plugin.startup()

    assert plugin.indexer is None
    for coll in (fatal_indexing.devices, fatal_indexing.variables,
                 fatal_indexing.actionGroups):
        coll.subscribeToChanges.assert_not_called()

    init = _rpc(plugin.mcp_handler, "initialize", {
        "protocolVersion": "2025-11-25",
        "clientInfo": {"name": "t", "version": "1"},
    })
    assert init["instructions"] == module.SERVER_INSTRUCTIONS_SLIM
    tools = _rpc(plugin.mcp_handler, "tools/list")["tools"]
    assert len(tools) == 33
    assert "find_devices" not in {t["name"] for t in tools}

    # One info line naming the counts and the way back.
    lines = [c.args[0] for c in plugin.logger.info.call_args_list]
    gate = [line for line in lines if "/v3/api/mcp" in line]
    assert len(gate) == 1, lines
    assert "2026.1.0" in gate[0] and "53" in gate[0] and "33" in gate[0]
    assert "Legacy tools" in gate[0]


@pytest.mark.parametrize("pref", [True, "true"])
def test_legacy_pref_restores_full_surface_on_2026_1(fake_indigo, monkeypatch, pref):
    # Prefs can hold the checkbox as a bool or as the string "true".
    import indexer

    built = []

    class _StubIndexer:
        def __init__(self, **_):
            built.append(self)

        def build(self):
            pass

    monkeypatch.setattr(indexer, "Indexer", _StubIndexer)
    module = _load_plugin_module()
    plugin = _make_plugin(module, {"legacyTools": pref})
    plugin.startup()

    assert len(built) == 1
    fake_indigo.devices.subscribeToChanges.assert_called_once()
    assert plugin.mcp_handler.instructions == module.SERVER_INSTRUCTIONS
    names = {t["name"] for t in _rpc(plugin.mcp_handler, "tools/list")["tools"]}
    assert len(names) == 86
    assert "find_devices" in names


def test_legacy_pref_string_false_stays_slim(fatal_indexing):
    module = _load_plugin_module()
    plugin = _make_plugin(module, {"legacyTools": "false"})
    plugin.startup()
    assert plugin.indexer is None
    assert plugin.mcp_handler.instructions == module.SERVER_INSTRUCTIONS_SLIM


def test_changing_legacy_pref_warns_restart_needed(fatal_indexing):
    module = _load_plugin_module()
    plugin = _make_plugin(module, {})
    plugin.closedPrefsConfigUi({"legacyTools": True}, False)
    warnings = [c.args[0] for c in plugin.logger.warning.call_args_list]
    assert any("Legacy tools" in w and "restart" in w for w in warnings), warnings


def test_reindex_menu_explains_missing_index_on_slim_server(fatal_indexing):
    module = _load_plugin_module()
    plugin = _make_plugin(module, {})
    plugin.startup()
    plugin.logger.warning.reset_mock()
    plugin.menuReindexNow()
    message = plugin.logger.warning.call_args.args[0]
    assert "find_devices is not registered" in message
