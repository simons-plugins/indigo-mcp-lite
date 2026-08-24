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

# A real but empty <Actions/> (UK-Trains / mqtt-device-sniffer shape,
# per the 2026-08-24 15-plugin sweep) -- parses fine, zero actions,
# and must be distinguishable from "no Actions.xml file at all".
_EMPTY_ACTIONS_XML = """<?xml version="1.0"?>
<Actions/>
"""

# One real action plus a genuine separator plus an entry that's
# missing <CallbackMethod> (indistinguishable from a separator to
# _parse_action_element, but a real-world malformed entry would look
# just like this) -- both must be counted, neither swallowed.
_SKIP_ACTIONS_XML = """<?xml version="1.0"?>
<Actions>
    <Action id="realAction">
        <Name>Real Action</Name>
        <CallbackMethod>realAction</CallbackMethod>
    </Action>
    <Action id="sep1"/>
    <Action id="malformedNoCallback">
        <Name>Malformed Missing Callback</Name>
    </Action>
</Actions>
"""

# Every non-"class=self, has <Option>" shape a <List> can take: the
# three resolvable Indigo built-in collections, an opaque plugin-
# owned class, a bare <List method=...> with no class attribute at
# all (the Sonos shape), and a genuine static enumerated list --
# proof the inversion doesn't over-flag real static lists.
_DYNAMIC_LIST_ACTIONS_XML = """<?xml version="1.0"?>
<Actions>
    <Action id="pickDevice">
        <Name>Pick Device</Name>
        <CallbackMethod>pickDevice</CallbackMethod>
        <ConfigUI>
            <Field type="menu" id="target_device">
                <Label>Device:</Label>
                <List class="indigo.devices"/>
            </Field>
        </ConfigUI>
    </Action>
    <Action id="pickActionGroup">
        <Name>Pick Action Group</Name>
        <CallbackMethod>pickActionGroup</CallbackMethod>
        <ConfigUI>
            <Field type="menu" id="target_group">
                <Label>Group:</Label>
                <List class="indigo.actionGroups"/>
            </Field>
        </ConfigUI>
    </Action>
    <Action id="pickVariable">
        <Name>Pick Variable</Name>
        <CallbackMethod>pickVariable</CallbackMethod>
        <ConfigUI>
            <Field type="menu" id="target_var">
                <Label>Variable:</Label>
                <List class="indigo.variables"/>
            </Field>
        </ConfigUI>
    </Action>
    <Action id="pickPluginThing">
        <Name>Pick Plugin Thing</Name>
        <CallbackMethod>pickPluginThing</CallbackMethod>
        <ConfigUI>
            <Field type="menu" id="thing">
                <Label>Thing:</Label>
                <List class="plugin" method="getThings"/>
            </Field>
        </ConfigUI>
    </Action>
    <Action id="pickBareList">
        <Name>Pick Bare List</Name>
        <CallbackMethod>pickBareList</CallbackMethod>
        <ConfigUI>
            <Field type="menu" id="bare">
                <Label>Bare:</Label>
                <List method="getBareList"/>
            </Field>
        </ConfigUI>
    </Action>
    <Action id="pickStatic">
        <Name>Pick Static</Name>
        <CallbackMethod>pickStatic</CallbackMethod>
        <ConfigUI>
            <Field type="menu" id="opt">
                <Label>Option:</Label>
                <List>
                    <Option value="0">Zero</Option>
                    <Option value="1">One</Option>
                </List>
            </Field>
        </ConfigUI>
    </Action>
</Actions>
"""

# A comma-separated self-scoped filter (accept if ANY alternative
# matches) and a filter that isn't self-scoped at all (skip
# validation rather than guess).
_COMMA_FILTER_ACTIONS_XML = """<?xml version="1.0"?>
<Actions>
    <Action id="multiFilterAction" deviceFilter="self.sprinkler, self.zone">
        <Name>Multi Filter</Name>
        <CallbackMethod>multiFilterAction</CallbackMethod>
    </Action>
    <Action id="nonSelfFilterAction" deviceFilter="indigo.relay">
        <Name>Non Self Filter</Name>
        <CallbackMethod>nonSelfFilterAction</CallbackMethod>
    </Action>
</Actions>
"""


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


def _fake_plugin(enabled=True, running=True, execute_result=None, forbid_execute=False):
    p = MagicMock()
    p.isEnabled = MagicMock(return_value=enabled)
    p.isRunning = MagicMock(return_value=running)
    if forbid_execute:
        def _raise(*a, **k):
            raise AssertionError("executeAction must not be called")
        p.executeAction = MagicMock(side_effect=_raise)
    else:
        p.executeAction = MagicMock(return_value=execute_result)
    return p


def _fake_device(plugin_id, device_type_id):
    """A device double that satisfies a `self.<device_type_id>`
    deviceFilter for `plugin_id` -- MagicMock auto-vivifies attributes,
    so a bare MagicMock() would satisfy the OLD existence-only check
    but not the ownership check the fix in #71's review adds."""
    dev = MagicMock()
    dev.pluginId = plugin_id
    dev.deviceTypeId = device_type_id
    return dev


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


def test_list_plugin_actions_resolvable_indigo_list_classes_name_the_tool(
    mock_indigo, tmp_path
):
    """indigo.devices/actionGroups/variables menus ARE resolvable by
    the caller -- just via a different lite tool, not free text and
    not an unresolvable plugin callback like class="self"."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_DYNAMIC_LIST_ACTIONS_XML)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}

    device_prop = next(
        p for p in by_id["pickDevice"]["props"] if p["id"] == "target_device"
    )
    assert device_prop["values"] == "dynamic"
    assert device_prop["values_source"] == "indigo.devices"
    assert device_prop["values_source_tool"] == "list_devices"
    assert by_id["pickDevice"]["has_dynamic_fields"] is True

    group_prop = next(
        p for p in by_id["pickActionGroup"]["props"] if p["id"] == "target_group"
    )
    assert group_prop["values"] == "dynamic"
    assert group_prop["values_source"] == "indigo.actionGroups"
    assert group_prop["values_source_tool"] == "list_action_groups"

    var_prop = next(
        p for p in by_id["pickVariable"]["props"] if p["id"] == "target_var"
    )
    assert var_prop["values"] == "dynamic"
    assert var_prop["values_source"] == "indigo.variables"
    assert var_prop["values_source_tool"] == "list_variables"


def test_list_plugin_actions_plugin_class_list_dynamic_with_no_tool(mock_indigo, tmp_path):
    """class="plugin" (or anything else non-resolvable) is still
    flagged dynamic with whatever class/method is present, but gets
    no values_source_tool since lite can't resolve it either."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_DYNAMIC_LIST_ACTIONS_XML)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}

    prop = next(p for p in by_id["pickPluginThing"]["props"] if p["id"] == "thing")
    assert prop["values"] == "dynamic"
    assert prop["values_source"] == "getThings"
    assert "values_source_tool" not in prop


def test_list_plugin_actions_bare_list_no_class_still_flagged_dynamic(
    mock_indigo, tmp_path
):
    """The Sonos shape: a <List method=...> with NO class attribute
    at all. The old class=="self" check missed this entirely --
    zero <Option> children is what makes a list dynamic, not the
    class value."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_DYNAMIC_LIST_ACTIONS_XML)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}

    prop = next(p for p in by_id["pickBareList"]["props"] if p["id"] == "bare")
    assert prop["values"] == "dynamic"
    assert prop["values_source"] == "getBareList"
    assert by_id["pickBareList"]["has_dynamic_fields"] is True


def test_list_plugin_actions_static_list_with_options_never_flagged_dynamic(
    mock_indigo, tmp_path
):
    """Proof the inversion doesn't over-flag: a real enumerated
    <List><Option> set (no class attribute at all here) must still
    read as static, not dynamic."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_DYNAMIC_LIST_ACTIONS_XML)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}

    prop = next(p for p in by_id["pickStatic"]["props"] if p["id"] == "opt")
    assert prop["values"] == [
        {"value": "0", "label": "Zero"},
        {"value": "1", "label": "One"},
    ]
    assert by_id["pickStatic"]["has_dynamic_fields"] is False


def test_list_plugin_actions_hidden_flag(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}
    assert by_id["internalSync"]["hidden"] is True
    assert by_id["internalSync"]["ui_path"] == "hidden"
    assert by_id["setStandbyMode"]["hidden"] is False


def test_list_plugin_actions_no_configui_flagged_props_undeclared(mock_indigo, tmp_path):
    """An action with NO <ConfigUI> at all (internalSync, hidden --
    the MQTT Connector fetchQueuedMessage shape) must NOT read as
    "takes no arguments": props_undeclared:true plus a reason,
    distinct from an action that genuinely declares zero-but-real
    fields is impossible to distinguish from "declares nothing", so
    both collapse to the same honest signal."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}

    internal_sync = by_id["internalSync"]
    assert internal_sync["props"] == []
    assert internal_sync["props_undeclared"] is True
    assert "no ConfigUI fields" in internal_sync["props_undeclared_reason"]

    # An action that DOES declare fields must not be flagged.
    standby = by_id["setStandbyMode"]
    assert standby["props"]
    assert "props_undeclared" not in standby


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


def test_list_plugin_actions_reports_running_distinct_from_enabled(mock_indigo, tmp_path):
    """enabled and running can differ (a crashed-but-enabled plugin
    is a real Indigo state) -- both must be reported, not just
    enabled."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    mock_indigo.server.getPlugin.return_value = _fake_plugin(enabled=True, running=False)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    assert result["enabled"] is True
    assert result["running"] is False


def test_list_plugin_actions_no_actions_xml_is_legit_empty(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=None)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    assert result["results"] == []
    assert result["total_count"] == 0
    assert result["no_actions_reason"] == "plugin bundle declares no Actions.xml"


def test_list_plugin_actions_real_empty_actions_xml_has_distinct_reason(mock_indigo, tmp_path):
    """A real <Actions/> that parses fine but declares zero actions
    (the UK-Trains / mqtt-device-sniffer shape) must NOT report the
    same reason as a missing Actions.xml file -- two different facts,
    two different strings."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_EMPTY_ACTIONS_XML)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    assert result["results"] == []
    assert result["total_count"] == 0
    assert result["no_actions_reason"] == "Actions.xml declares no callable actions"
    assert result["no_actions_reason"] != "plugin bundle declares no Actions.xml"
    assert "skipped_actions" not in result


def test_list_plugin_actions_reports_skipped_actions_and_ids(mock_indigo, tmp_path):
    """Excluded <Action> elements (separator + a genuinely malformed
    entry missing CallbackMethod) must be counted, not silently
    dropped -- and the callable action alongside them must still come
    back intact."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_SKIP_ACTIONS_XML)
    mock_indigo.server.getPlugin.return_value = _fake_plugin()

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)

    assert result["skipped_actions"] == 2
    assert set(result["skipped_action_ids"]) == {"sep1", "malformedNoCallback"}
    assert [a["id"] for a in result["results"]] == ["realAction"]
    assert result["total_count"] == 1
    assert "no_actions_reason" not in result


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

    with pytest.raises(ValueError, match="not enabled"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
        )
    plugin.executeAction.assert_not_called()


def test_execute_action_enabled_but_not_running_fatal_and_never_dispatched(
    mock_indigo, tmp_path
):
    """isEnabled() True but isRunning() False (crashed, or still
    starting) is a real, distinct Indigo state -- see
    system.py's _serialize_plugin -- and must fail the call with
    accurate wording, not the "not enabled" text from the check
    above."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin = _fake_plugin(enabled=True, running=False, forbid_execute=True)
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError, match="not running"):
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


def test_execute_action_undeclared_props_pass_through_unchecked(mock_indigo, tmp_path):
    """The primary #71 use case: a hidden action with NO declared
    ConfigUI fields (internalSync -- the MQTT Connector
    fetchQueuedMessage / uiPath="hidden" shape) must still be
    callable with arbitrary props. An absent allowlist is not an
    empty one -- refusing this would make the whole hidden-action
    class uncallable."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin = _fake_plugin(enabled=True)
    mock_indigo.server.getPlugin.return_value = plugin

    result = _plugin_execute_action_handler(
        {
            "plugin_id": PLUGIN_ID,
            "action_id": "internalSync",
            "props": {"message_type": "queued", "anything_else": 5},
        },
        mock_indigo,
    )
    assert result["result"] == "dispatched"
    assert result["props_validated"] is False
    assert "props_validated_reason" in result
    assert result["props_not_supplied"] == []
    plugin.executeAction.assert_called_once()


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
# plugin_execute_action -- skipped_ids threaded through dispatch
# ---------------------------------------------------------------------

def test_execute_action_skipped_id_reports_could_not_be_validated(mock_indigo, tmp_path):
    """An id that IS in the XML but couldn't be parsed as callable
    (missing <Name>/<CallbackMethod>) must NOT be told "no such
    action" -- that would be a false statement. It gets told the real
    reason instead."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_SKIP_ACTIONS_XML)
    plugin = _fake_plugin(enabled=True, forbid_execute=True)
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError, match="could not be validated"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "malformedNoCallback"},
            mock_indigo,
        )
    plugin.executeAction.assert_not_called()


def test_execute_action_unknown_id_notes_skipped_count(mock_indigo, tmp_path):
    """A genuinely unknown id (not even among the skipped ones) still
    gets the real known-ids list, plus a note that some declared ids
    could not be parsed -- so the caller isn't misled into thinking
    the XML holds exactly the ids listed."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_SKIP_ACTIONS_XML)
    plugin = _fake_plugin(enabled=True, forbid_execute=True)
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError) as excinfo:
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "totallyBogus"}, mock_indigo
        )
    message = str(excinfo.value)
    assert "totallyBogus" in message
    assert "realAction" in message
    assert "2" in message  # skipped_ids count note (sep1 + malformedNoCallback)
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
    mock_indigo.devices.__getitem__ = MagicMock(
        return_value=_fake_device(PLUGIN_ID, "sprinkler")
    )

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
# plugin_execute_action -- deviceFilter OWNERSHIP (not just existence)
# ---------------------------------------------------------------------

def test_execute_action_device_owned_by_wrong_plugin_rejected(mock_indigo, tmp_path):
    """Existence alone (_lookup_or_raise) doesn't prove ownership --
    Netro's startZoneWithDelay (filter self.sprinkler) must NOT
    happily accept a device that belongs to some other plugin."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin = _fake_plugin(enabled=True, forbid_execute=True)
    mock_indigo.server.getPlugin.return_value = plugin
    mock_indigo.devices.__getitem__ = MagicMock(
        return_value=_fake_device("com.other.plugin", "sprinkler")
    )

    with pytest.raises(ValueError, match="does not match action"):
        _plugin_execute_action_handler(
            {
                "plugin_id": PLUGIN_ID,
                "action_id": "startZoneWithDelay",
                "device_id": 555,
                "props": {"zone": "1", "duration": "15"},
            },
            mock_indigo,
        )
    plugin.executeAction.assert_not_called()


def test_execute_action_device_wrong_device_type_rejected(mock_indigo, tmp_path):
    """Right plugin, wrong deviceTypeId -- self.sprinkler must not
    accept a "zone" device from the same plugin."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin = _fake_plugin(enabled=True, forbid_execute=True)
    mock_indigo.server.getPlugin.return_value = plugin
    mock_indigo.devices.__getitem__ = MagicMock(
        return_value=_fake_device(PLUGIN_ID, "zone")
    )

    with pytest.raises(ValueError, match="deviceTypeId"):
        _plugin_execute_action_handler(
            {
                "plugin_id": PLUGIN_ID,
                "action_id": "startZoneWithDelay",
                "device_id": 555,
                "props": {"zone": "1", "duration": "15"},
            },
            mock_indigo,
        )
    plugin.executeAction.assert_not_called()


def test_execute_action_comma_separated_filter_accepts_any_alternative(
    mock_indigo, tmp_path
):
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_COMMA_FILTER_ACTIONS_XML)
    plugin = _fake_plugin(enabled=True)
    mock_indigo.server.getPlugin.return_value = plugin
    mock_indigo.devices.__getitem__ = MagicMock(
        return_value=_fake_device(PLUGIN_ID, "zone")
    )

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "multiFilterAction", "device_id": 777},
        mock_indigo,
    )
    assert result["result"] == "dispatched"


def test_execute_action_non_self_filter_skips_ownership_validation(mock_indigo, tmp_path):
    """A deviceFilter that isn't self-scoped at all is left
    unvalidated rather than guessed at -- existence check still
    runs, but a device from a totally unrelated plugin dispatches."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_COMMA_FILTER_ACTIONS_XML)
    plugin = _fake_plugin(enabled=True)
    mock_indigo.server.getPlugin.return_value = plugin
    mock_indigo.devices.__getitem__ = MagicMock(
        return_value=_fake_device("com.completely.unrelated", "widget")
    )

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "nonSelfFilterAction", "device_id": 888},
        mock_indigo,
    )
    assert result["result"] == "dispatched"


# ---------------------------------------------------------------------
# plugin_execute_action -- props_not_supplied
# ---------------------------------------------------------------------

def test_execute_action_reports_props_not_supplied(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin = _fake_plugin(enabled=True)
    mock_indigo.server.getPlugin.return_value = plugin
    mock_indigo.devices.__getitem__ = MagicMock(
        return_value=_fake_device(PLUGIN_ID, "sprinkler")
    )

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
    assert result["props_validated"] is True
    assert "props_validated_reason" not in result


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
