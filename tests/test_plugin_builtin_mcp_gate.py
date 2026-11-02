"""Plugin-level behaviour of the built-in MCP gate (lite ADR-0003).

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
from unittest.mock import MagicMock, PropertyMock

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
    assert gate[0].endswith("restart the plugin to restore them.")


@pytest.fixture
def stub_indexer(monkeypatch):
    """Replace Indexer with a recorder; returns the list of instances."""
    import indexer

    built = []

    class _StubIndexer:
        def __init__(self, **_):
            built.append(self)

        def build(self):
            pass

    monkeypatch.setattr(indexer, "Indexer", _StubIndexer)
    return built


@pytest.mark.parametrize("pref", [True, "true", "TRUE", 1])
def test_legacy_pref_restores_full_surface_on_2026_1(fake_indigo, stub_indexer, pref):
    # Prefs can hold the checkbox as a bool, an int or the string "true".
    built = stub_indexer
    module = _load_plugin_module()
    plugin = _make_plugin(module, {"legacyTools": pref})
    plugin.startup()

    assert len(built) == 1
    fake_indigo.devices.subscribeToChanges.assert_called_once()
    assert plugin.mcp_handler.instructions == module.SERVER_INSTRUCTIONS
    names = {t["name"] for t in _rpc(plugin.mcp_handler, "tools/list")["tools"]}
    assert len(names) == 86
    assert "find_devices" in names
    infos = [c.args[0] for c in plugin.logger.info.call_args_list]
    assert ('Indigo 2026.1.0 has its own MCP server at /v3/api/mcp; '
            '"Legacy tools" is on, so all 86 tools registered') in infos


def test_legacy_pref_string_false_stays_slim(fatal_indexing):
    module = _load_plugin_module()
    plugin = _make_plugin(module, {"legacyTools": "False"})
    plugin.startup()
    assert plugin.indexer is None
    assert plugin.mcp_handler.instructions == module.SERVER_INSTRUCTIONS_SLIM
    plugin.logger.warning.assert_not_called()


def test_legacy_pref_unrecognised_string_warns_and_stays_slim(fatal_indexing):
    module = _load_plugin_module()
    plugin = _make_plugin(module, {"legacyTools": "yes"})
    plugin.startup()
    assert plugin.indexer is None
    warnings = [c.args[0] for c in plugin.logger.warning.call_args_list]
    assert any("'yes'" in w for w in warnings), warnings


def test_unreadable_version_falls_back_to_full_surface(fake_indigo, stub_indexer):
    # indigo.server.version raising must not leave the plugin half-slim:
    # full tool set, index built, full instructions, and a warning.
    type(fake_indigo.server).version = PropertyMock(side_effect=RuntimeError("ipc down"))
    module = _load_plugin_module()
    plugin = _make_plugin(module, {})
    plugin.startup()

    assert len(stub_indexer) == 1
    assert plugin.indexer is stub_indexer[0]
    assert plugin.mcp_handler.instructions == module.SERVER_INSTRUCTIONS
    names = {t["name"] for t in _rpc(plugin.mcp_handler, "tools/list")["tools"]}
    assert len(names) == 86
    warnings = [c.args[0] for c in plugin.logger.warning.call_args_list]
    assert any("RuntimeError" in w and "registering all tools" in w for w in warnings), warnings
    infos = [c.args[0] for c in plugin.logger.info.call_args_list]
    assert "Indigo unknown: no built-in MCP server, all 86 tools registered" in infos


def test_pre_2026_1_startup_states_full_mode(fake_indigo, stub_indexer):
    fake_indigo.server.version = "2025.2.5"
    module = _load_plugin_module()
    plugin = _make_plugin(module, {})
    plugin.startup()
    infos = [c.args[0] for c in plugin.logger.info.call_args_list]
    assert "Indigo 2025.2.5: no built-in MCP server, all 86 tools registered" in infos


def test_registration_failure_is_logged_and_reraised(fatal_indexing, monkeypatch):
    import tool_registry

    def boom(*_args, **_kwargs):
        raise RuntimeError("tool module exploded")

    monkeypatch.setattr(tool_registry, "register_all", boom)
    module = _load_plugin_module()
    plugin = _make_plugin(module, {})
    with pytest.raises(RuntimeError, match="tool module exploded"):
        plugin.startup()
    message = plugin.logger.exception.call_args.args[0]
    assert "partial tool set" in message


def test_saving_unchanged_pref_in_another_representation_is_quiet(fatal_indexing):
    # Stored as bool True, dialog hands back "true": same value, no
    # restart warning.
    module = _load_plugin_module()
    plugin = _make_plugin(module, {"legacyTools": True})
    plugin.closedPrefsConfigUi({"legacyTools": "true"}, False)
    plugin.logger.warning.assert_not_called()


def test_changing_legacy_pref_before_2026_1_is_info_not_warning(fake_indigo):
    fake_indigo.server.version = "2025.2.5"
    module = _load_plugin_module()
    plugin = _make_plugin(module, {})
    plugin.closedPrefsConfigUi({"legacyTools": True}, False)
    plugin.logger.warning.assert_not_called()
    infos = [c.args[0] for c in plugin.logger.info.call_args_list]
    assert any("Legacy tools" in i and "no effect" in i for i in infos), infos


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
