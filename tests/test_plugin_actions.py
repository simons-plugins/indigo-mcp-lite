"""TDD tests for list_plugin_actions / plugin_execute_action (issue #71).

Covers the degradation paths named in the issue and the workspace
testing convention: a disabled plugin must fail BEFORE executeAction
is ever touched; an unknown action id must list the real ones; an
unknown prop key must be rejected (never silently dropped — that's
the "silently seeds nothing" cross-plugin trap this tool exists to
close); a plain-dict props argument must reach executeAction as an
indigo.Dict; a fire-and-forget action must report "dispatched", not a
confirmed effect; and a malformed/missing Actions.xml must raise
rather than read as "this plugin has no actions".

Fixture Actions.xml is modeled on the real
``netro/Netro Sprinklers.indigoPlugin/.../Actions.xml`` and
``indigo-auto-lights/Auto Lights.indigoPlugin/.../Actions.xml`` shapes:
a deviceFilter + dynamic menu action, a static-list menu action, a
label+checkbox action with no device filter, a hidden action, a bare
separator, and a no-ConfigUI action.
"""
import plistlib
from unittest.mock import MagicMock

import pytest

_ACTIONS_XML = """<?xml version="1.0"?>
<Actions>
    <Action id="setStandbyMode" uiPath="DeviceActions">
        <Name>Set Standby Mode</Name>
        <CallbackMethod>setStandbyMode</CallbackMethod>
        <ConfigUI>
            <Field type="label" id="standby_label">
                <Label>Enabling standby mode turns off all automated functions.</Label>
            </Field>
            <Field type="checkbox" id="mode">
                <Label>Standby mode:</Label>
            </Field>
        </ConfigUI>
    </Action>

    <Action id="startZoneWithDelay" deviceFilter="self.sprinkler" uiPath="DeviceActions">
        <Name>Start Zone with Delay</Name>
        <CallbackMethod>startZoneWithDelay</CallbackMethod>
        <ConfigUI>
            <Field type="menu" id="zone" defaultValue="-1">
                <Label>Zone:</Label>
                <List class="self" method="getZoneList" dynamicReload="true"/>
            </Field>
            <Field type="textfield" id="duration" defaultValue="15">
                <Label>Duration (minutes):</Label>
            </Field>
            <Field type="label" id="duration_label" alignWithControl="true">
                <Label>Watering duration in minutes (1-180). Default: 15</Label>
            </Field>
        </ConfigUI>
    </Action>

    <Action id="reportWeather" uiPath="DeviceActions">
        <Name>Report Local Weather</Name>
        <CallbackMethod>reportWeather</CallbackMethod>
        <ConfigUI>
            <Field type="menu" id="condition" defaultValue="0">
                <Label>Weather Condition:</Label>
                <List>
                    <Option value="0">Clear</Option>
                    <Option value="1">Cloudy</Option>
                </List>
            </Field>
        </ConfigUI>
    </Action>

    <Action id="internalSync" uiPath="hidden">
        <Name>Internal Sync</Name>
        <CallbackMethod>internalSync</CallbackMethod>
    </Action>

    <Action id="sep1"/>

    <Action id="noop">
        <Name>No Op</Name>
        <CallbackMethod>noop</CallbackMethod>
    </Action>
</Actions>
"""

PLUGIN_ID = "com.example.widget"


# ---------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------

def _make_bundle(plugins_dir, plugin_id, *, actions_xml=_ACTIONS_XML, name=None):
    """Build a fake ``*.indigoPlugin`` bundle with Info.plist and
    (optionally) Actions.xml, mirroring test_system_plugins.py's
    ``_make_bundle`` plus the Server Plugin/Actions.xml layer this
    module actually reads. ``actions_xml=None`` omits the file
    entirely (bundle declares no actions)."""
    bundle_name = name or plugin_id.replace(".", "_") + ".indigoPlugin"
    bundle = plugins_dir / bundle_name
    server_plugin = bundle / "Contents" / "Server Plugin"
    server_plugin.mkdir(parents=True, exist_ok=True)
    with open(bundle / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump({"CFBundleIdentifier": plugin_id}, fh)
    if actions_xml is not None:
        (server_plugin / "Actions.xml").write_text(actions_xml)
    return bundle


def _install(mock_indigo, tmp_path, plugin_id, *, actions_xml=_ACTIONS_XML,
             disabled_subdir=False):
    """Install one bundle under Plugins/ (or Plugins (Disabled)/) and
    point getInstallFolderPath at tmp_path."""
    subdir = "Plugins (Disabled)" if disabled_subdir else "Plugins"
    plugins_dir = tmp_path / subdir
    plugins_dir.mkdir(parents=True, exist_ok=True)
    _make_bundle(plugins_dir, plugin_id, actions_xml=actions_xml)
    mock_indigo.server.getInstallFolderPath.return_value = str(tmp_path)


def _fake_plugin(enabled=True, execute_result=None, forbid_execute=False):
    p = MagicMock()
    p.isEnabled = MagicMock(return_value=enabled)
    if forbid_execute:
        def _raise(*a, **k):
            raise AssertionError("executeAction must not be called")
        p.executeAction = MagicMock(side_effect=_raise)
    else:
        p.executeAction = MagicMock(return_value=execute_result)
    return p


def _plain_dict_indigo(mock_indigo):
    """Make mock_indigo.Dict behave like the real indigo.Dict: a
    distinguishable object built from the plain dict passed in, so
    tests can assert the converted object (not the plain dict) is
    what reaches executeAction."""
    def _to_dict(plain):
        wrapped = dict(plain)
        wrapped["__is_indigo_dict__"] = True
        return wrapped
    mock_indigo.Dict = MagicMock(side_effect=_to_dict)
    return mock_indigo


# ---------------------------------------------------------------------
# list_plugin_actions
# ---------------------------------------------------------------------

def test_list_plugin_actions_happy_path(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    mock_indigo.server.getPlugin.return_value = _fake_plugin(enabled=True)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)

    assert result["plugin_id"] == PLUGIN_ID
    assert result["enabled"] is True
    ids = {a["id"] for a in result["results"]}
    # sep1 is a spacer, excluded
    assert ids == {"setStandbyMode", "startZoneWithDelay", "reportWeather",
                    "internalSync", "noop"}
    assert result["total_count"] == len(result["results"])


def test_list_plugin_actions_excludes_separator(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    assert all(a["id"] != "sep1" for a in result["results"])


def test_list_plugin_actions_label_field_becomes_note_not_prop(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}

    standby = by_id["setStandbyMode"]
    prop_ids = {p["id"] for p in standby["props"]}
    assert "standby_label" not in prop_ids
    assert prop_ids == {"mode"}
    assert any("automated functions" in note for note in standby["notes"])


def test_list_plugin_actions_dynamic_menu_flagged(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}
    start_zone = by_id["startZoneWithDelay"]

    assert start_zone["has_dynamic_fields"] is True
    zone_prop = next(p for p in start_zone["props"] if p["id"] == "zone")
    assert zone_prop["values"] == "dynamic"
    assert zone_prop["values_source"] == "getZoneList"
    # never presented as free text
    duration_prop = next(p for p in start_zone["props"] if p["id"] == "duration")
    assert duration_prop.get("values") is None
    assert duration_prop["default"] == "15"
    # the label sibling field became a note, not a prop
    assert any("1-180" in note for note in start_zone["notes"])
    assert start_zone["device_required"] is True
    assert start_zone["device_filter"] == "self.sprinkler"


def test_list_plugin_actions_static_menu_values_enumerated(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}
    weather = by_id["reportWeather"]
    condition_prop = next(p for p in weather["props"] if p["id"] == "condition")
    assert condition_prop["values"] == [
        {"value": "0", "label": "Clear"},
        {"value": "1", "label": "Cloudy"},
    ]
    assert weather["has_dynamic_fields"] is False


def test_list_plugin_actions_hidden_flag(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}
    assert by_id["internalSync"]["hidden"] is True
    assert by_id["internalSync"]["ui_path"] == "hidden"
    assert by_id["setStandbyMode"]["hidden"] is False


def test_list_plugin_actions_disabled_plugin_bundle_still_lists(mock_indigo, tmp_path):
    """A plugin installed under Plugins (Disabled)/ still has its
    actions listed -- enabled:false tells the caller a subsequent
    plugin_execute_action call will fail."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, disabled_subdir=True)
    mock_indigo.server.getPlugin.return_value = _fake_plugin(enabled=False)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    assert result["enabled"] is False
    assert result["total_count"] == 5


def test_list_plugin_actions_no_actions_xml_is_legit_empty(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=None)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    assert result["results"] == []
    assert result["total_count"] == 0
    assert result["no_actions_reason"] == "plugin bundle declares no Actions.xml"


def test_list_plugin_actions_malformed_xml_raises_not_empty(mock_indigo, tmp_path):
    """A parse failure must raise, never read as 'this plugin has no
    actions' -- the mutation this test exists to kill."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml="<Actions><Action")
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    with pytest.raises(ValueError, match="could not be parsed"):
        _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)


def test_list_plugin_actions_unknown_plugin_id_names_it(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    mock_indigo.server.getPlugin.side_effect = KeyError("nope")
    with pytest.raises(ValueError, match="com.unknown"):
        _list_plugin_actions_handler({"plugin_id": "com.unknown"}, mock_indigo)


def test_list_plugin_actions_no_bundle_found_raises(mock_indigo, tmp_path):
    """getPlugin resolves (e.g. mocked) but the filesystem scan finds
    no matching bundle."""
    from tools.plugin_actions import _list_plugin_actions_handler

    (tmp_path / "Plugins").mkdir()
    mock_indigo.server.getInstallFolderPath.return_value = str(tmp_path)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    with pytest.raises(ValueError, match="no installed bundle"):
        _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)


def test_list_plugin_actions_rejects_unknown_args(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    with pytest.raises(ValueError, match="unknown argument"):
        _list_plugin_actions_handler(
            {"plugin_id": PLUGIN_ID, "bogus": 1}, mock_indigo
        )


# ---------------------------------------------------------------------
# plugin_execute_action -- disabled plugin never reaches executeAction
# ---------------------------------------------------------------------

def test_execute_action_disabled_plugin_fatal_and_never_dispatched(mock_indigo, tmp_path):
    """The convention's isolation test: hand it a plugin double whose
    executeAction raises AssertionError if touched, then assert we
    got the isEnabled ValueError instead."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin = _fake_plugin(enabled=False, forbid_execute=True)
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError, match="not enabled/running"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
        )
    plugin.executeAction.assert_not_called()


# ---------------------------------------------------------------------
# plugin_execute_action -- unknown action id
# ---------------------------------------------------------------------

def test_execute_action_unknown_action_id_lists_real_ones(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin = _fake_plugin(enabled=True, forbid_execute=True)
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError) as excinfo:
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "bogusAction"}, mock_indigo
        )
    message = str(excinfo.value)
    assert "bogusAction" in message
    for real_id in ("setStandbyMode", "startZoneWithDelay", "reportWeather",
                     "internalSync", "noop"):
        assert real_id in message
    plugin.executeAction.assert_not_called()


# ---------------------------------------------------------------------
# plugin_execute_action -- unknown prop key
# ---------------------------------------------------------------------

def test_execute_action_unknown_prop_key_names_declared_fields(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin = _fake_plugin(enabled=True, forbid_execute=True)
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError) as excinfo:
        _plugin_execute_action_handler(
            {
                "plugin_id": PLUGIN_ID,
                "action_id": "setStandbyMode",
                "props": {"modee": True},  # typo
            },
            mock_indigo,
        )
    message = str(excinfo.value)
    assert "modee" in message
    assert "mode" in message
    plugin.executeAction.assert_not_called()


# ---------------------------------------------------------------------
# plugin_execute_action -- plain dict -> indigo.Dict conversion
# ---------------------------------------------------------------------

def test_execute_action_converts_plain_dict_to_indigo_dict(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    _plain_dict_indigo(mock_indigo)
    plugin = _fake_plugin(enabled=True)
    mock_indigo.server.getPlugin.return_value = plugin

    result = _plugin_execute_action_handler(
        {
            "plugin_id": PLUGIN_ID,
            "action_id": "setStandbyMode",
            "props": {"mode": True},
        },
        mock_indigo,
    )

    mock_indigo.Dict.assert_called_once_with({"mode": True})
    _, kwargs = plugin.executeAction.call_args
    assert kwargs["props"] == {"mode": True, "__is_indigo_dict__": True}
    assert result["result"] == "dispatched"


# ---------------------------------------------------------------------
# plugin_execute_action -- fire-and-forget vs returned value
# ---------------------------------------------------------------------

def test_execute_action_none_return_reports_dispatched_not_confirmed(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin = _fake_plugin(enabled=True, execute_result=None)
    mock_indigo.server.getPlugin.return_value = plugin

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
    )
    assert result["result"] == "dispatched"
    assert "value" not in result
    assert result["props_not_supplied"] == []


def test_execute_action_non_none_return_reports_returned_value(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin = _fake_plugin(enabled=True, execute_result="ok-42")
    mock_indigo.server.getPlugin.return_value = plugin

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
    )
    assert result["result"] == "returned"
    assert result["value"] == "ok-42"


# ---------------------------------------------------------------------
# plugin_execute_action -- malformed Actions.xml
# ---------------------------------------------------------------------

def test_execute_action_malformed_xml_raises(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml="<Actions><Action")
    plugin = _fake_plugin(enabled=True, forbid_execute=True)
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError, match="cannot be validated"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
        )
    plugin.executeAction.assert_not_called()


def test_execute_action_missing_actions_xml_raises(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=None)
    plugin = _fake_plugin(enabled=True, forbid_execute=True)
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError, match="cannot be validated"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
        )
    plugin.executeAction.assert_not_called()


# ---------------------------------------------------------------------
# plugin_execute_action -- deviceFilter / device_id
# ---------------------------------------------------------------------

def test_execute_action_device_required_without_device_id_refused(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin = _fake_plugin(enabled=True, forbid_execute=True)
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError, match="requires a device"):
        _plugin_execute_action_handler(
            {
                "plugin_id": PLUGIN_ID,
                "action_id": "startZoneWithDelay",
                "props": {"zone": "1", "duration": "15"},
            },
            mock_indigo,
        )
    plugin.executeAction.assert_not_called()


def test_execute_action_device_id_validated_against_real_devices(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin = _fake_plugin(enabled=True, forbid_execute=True)
    mock_indigo.server.getPlugin.return_value = plugin
    mock_indigo.devices.__getitem__ = MagicMock(side_effect=KeyError())

    with pytest.raises(ValueError, match="no device with id 999"):
        _plugin_execute_action_handler(
            {
                "plugin_id": PLUGIN_ID,
                "action_id": "startZoneWithDelay",
                "device_id": 999,
                "props": {"zone": "1", "duration": "15"},
            },
            mock_indigo,
        )
    plugin.executeAction.assert_not_called()


def test_execute_action_valid_device_id_dispatches_with_deviceId_kwarg(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin = _fake_plugin(enabled=True)
    mock_indigo.server.getPlugin.return_value = plugin
    mock_indigo.devices.__getitem__ = MagicMock(return_value=MagicMock())

    result = _plugin_execute_action_handler(
        {
            "plugin_id": PLUGIN_ID,
            "action_id": "startZoneWithDelay",
            "device_id": 12345,
            "props": {"zone": "1", "duration": "15"},
        },
        mock_indigo,
    )
    assert result["device_id"] == 12345
    _, kwargs = plugin.executeAction.call_args
    assert kwargs["deviceId"] == 12345


# ---------------------------------------------------------------------
# plugin_execute_action -- props_not_supplied
# ---------------------------------------------------------------------

def test_execute_action_reports_props_not_supplied(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin = _fake_plugin(enabled=True)
    mock_indigo.server.getPlugin.return_value = plugin
    mock_indigo.devices.__getitem__ = MagicMock(return_value=MagicMock())

    result = _plugin_execute_action_handler(
        {
            "plugin_id": PLUGIN_ID,
            "action_id": "startZoneWithDelay",
            "device_id": 1,
            "props": {"zone": "1"},  # duration not supplied
        },
        mock_indigo,
    )
    assert result["props_not_supplied"] == ["duration"]


# ---------------------------------------------------------------------
# plugin_execute_action -- prop value type validation
# ---------------------------------------------------------------------

def test_execute_action_rejects_non_scalar_prop_value(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin = _fake_plugin(enabled=True, forbid_execute=True)
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError, match="mode"):
        _plugin_execute_action_handler(
            {
                "plugin_id": PLUGIN_ID,
                "action_id": "setStandbyMode",
                "props": {"mode": {"nested": True}},
            },
            mock_indigo,
        )
    plugin.executeAction.assert_not_called()


# ---------------------------------------------------------------------
# plugin_execute_action -- other cheap-validation / plumbing
# ---------------------------------------------------------------------

def test_execute_action_rejects_unknown_top_level_args(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    with pytest.raises(ValueError, match="unknown argument"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop", "bogus": 1}, mock_indigo
        )


def test_execute_action_action_raising_is_surfaced(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin = _fake_plugin(enabled=True)
    plugin.executeAction.side_effect = Exception("plugin-side validation failed")
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError, match="plugin-side validation failed"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
        )


def test_execute_action_unknown_plugin_id_names_it(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    mock_indigo.server.getPlugin.side_effect = KeyError("nope")
    with pytest.raises(ValueError, match="com.unknown"):
        _plugin_execute_action_handler(
            {"plugin_id": "com.unknown", "action_id": "noop"}, mock_indigo
        )


# ---------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------

def test_register_wires_both_tools(mock_indigo):
    from tools.plugin_actions import register

    handler = MagicMock()
    register(handler, indigo_module=mock_indigo)

    names = [call.kwargs["name"] for call in handler.register_tool.call_args_list]
    assert names == ["list_plugin_actions", "plugin_execute_action"]


def test_register_all_includes_plugin_actions_tools(mock_indigo):
    from tool_registry import register_all

    handler = MagicMock()
    register_all(handler, indigo_module=mock_indigo)

    names = [
        (call.kwargs.get("name") or (call.args[0] if call.args else None))
        for call in handler.register_tool.call_args_list
    ]
    assert "list_plugin_actions" in names
    assert "plugin_execute_action" in names
