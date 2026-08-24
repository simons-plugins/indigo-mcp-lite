"""TDD tests for list_plugin_actions / plugin_execute_action (issue #71).

Covers the degradation paths named in the issue and the workspace
testing convention, PLUS the five defects and ten follow-on items
found across #71's review rounds (round 2 and round 3 -- see PR #73):

- ``getPlugin`` never raises (confirmed live jarvis 2026-08-24) --
  ``isInstalled()`` is the only real not-installed signal.
- an unknown/disabled-plugin action id is NOT a silent no-op --
  Indigo raises ``InvalidAction``/``PluginDisabled`` itself
  (confirmed live) -- so our pre-checks are a better-error layer, not
  a safety net, and an unreadable Actions.xml degrades to "attempt
  the dispatch anyway" rather than refusing the call.
- a call that reaches ``executeAction`` and then fails must never say
  "NOT dispatched" -- the action may have partially completed.
- real-world Actions.xml field shapes (button/hidden/Description/
  bare ``default``/id-less fields and actions) that the original
  parser mishandled.
- static ``<List><Option>`` enum values are enforced at dispatch,
  same as prop keys.

Fixture Actions.xml is modeled on the real
``netro/Netro Sprinklers.indigoPlugin/.../Actions.xml`` and
``indigo-auto-lights/Auto Lights.indigoPlugin/.../Actions.xml`` shapes,
plus a dedicated ``_FIELD_SHAPES_ACTIONS_XML`` for the real-world
field/action shapes named above.
"""
import os
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
    <Action id="nonSelfFilterWithPropsAction" deviceFilter="indigo.relay">
        <Name>Non Self Filter With Props</Name>
        <CallbackMethod>nonSelfFilterWithPropsAction</CallbackMethod>
        <ConfigUI>
            <Field type="checkbox" id="flag">
                <Label>Flag:</Label>
            </Field>
        </ConfigUI>
    </Action>
</Actions>
"""

# Real-world field/action shapes the original parser mishandled
# (review round 3, item G): a button field, a hidden="true" field, a
# real prop field carrying <Description> instead of/alongside
# <Label>, a field using the bare `default` attribute instead of
# `defaultValue`, a field with NO id at all, and an <Action> with NO
# id at all.
_FIELD_SHAPES_ACTIONS_XML = """<?xml version="1.0"?>
<Actions>
    <Action id="richAction">
        <Name>Rich Action</Name>
        <CallbackMethod>richAction</CallbackMethod>
        <ConfigUI>
            <Field type="button" id="refreshButton">
                <Label>Refresh</Label>
            </Field>
            <Field type="textfield" id="description" hidden="true">
                <Label>Description (computed)</Label>
            </Field>
            <Field type="textfield" id="notes_field">
                <Label>Notes:</Label>
                <Description>The more useful sentence lives here.</Description>
            </Field>
            <Field type="textfield" id="legacy_default" default="42">
                <Label>Legacy default:</Label>
            </Field>
            <Field type="textfield" id="both_defaults" default="old" defaultValue="new">
                <Label>Both defaults:</Label>
            </Field>
            <Field type="textfield">
                <Label>No id at all:</Label>
            </Field>
        </ConfigUI>
    </Action>
    <Action>
        <Name>No Id Action</Name>
        <CallbackMethod>noIdAction</CallbackMethod>
    </Action>
</Actions>
"""


# ---------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------

def _make_bundle(plugins_dir, plugin_id, *, actions_xml=_ACTIONS_XML, name=None):
    """Build a fake ``*.indigoPlugin`` bundle with a real Info.plist
    and (optionally) a real Actions.xml on disk. Returns the bundle
    path."""
    bundle_name = name or plugin_id.replace(".", "_") + ".indigoPlugin"
    bundle = plugins_dir / bundle_name
    server_plugin = bundle / "Contents" / "Server Plugin"
    server_plugin.mkdir(parents=True, exist_ok=True)
    with open(bundle / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump({"CFBundleIdentifier": plugin_id}, fh)
    if actions_xml is not None:
        (server_plugin / "Actions.xml").write_text(actions_xml)
    return bundle


def _raise_if_touched(*_args, **_kwargs):
    raise AssertionError("executeAction must not be called")


class _FakePlugin:
    """Real (non-Mock) plugin double.

    #71 review round 3 (item F): MagicMock auto-vivifies every
    unstubbed attribute as a truthy MagicMock -- which is exactly why
    an isRunning() gap could go untested for two review rounds (round
    1 didn't check it at all; round 2 only pinned isEnabled). A plain
    object exposing ONLY the attributes this module actually uses
    means any access to something unstubbed raises AttributeError
    instead of quietly returning "yes".
    """

    def __init__(self, *, installed=True, enabled=True, running=True,
                 plugin_folder_path="", execute_result=None,
                 forbid_execute=False):
        self._installed = installed
        self._enabled = enabled
        self._running = running
        self.pluginFolderPath = plugin_folder_path
        self.executeAction = MagicMock(
            side_effect=_raise_if_touched if forbid_execute else None,
            return_value=execute_result,
        )

    def isInstalled(self):
        return self._installed

    def isEnabled(self):
        return self._enabled

    def isRunning(self):
        return self._running


def _fake_plugin(**kwargs):
    return _FakePlugin(**kwargs)


def _fake_device(plugin_id, device_type_id):
    """A device double that satisfies a `self.<device_type_id>`
    deviceFilter for `plugin_id` -- MagicMock auto-vivifies attributes,
    so a bare MagicMock() would satisfy an existence-only check but
    not the ownership check (review round 2, item 4)."""
    dev = MagicMock()
    dev.pluginId = plugin_id
    dev.deviceTypeId = device_type_id
    return dev


def _install(mock_indigo, tmp_path, plugin_id, *, actions_xml=_ACTIONS_XML,
             disabled_subdir=False, installed=True, enabled=True, running=True,
             execute_result=None, forbid_execute=False):
    """Create a real bundle on disk (Info.plist + optional
    Actions.xml) and wire ``mock_indigo.server.getPlugin`` to return a
    ``_FakePlugin`` whose ``pluginFolderPath`` points at it.

    #71 review round 3 (item B) deleted the ``getInstallFolderPath``
    filesystem-scan lookup in favour of reading ``pluginFolderPath``
    straight off the plugin object (proven correct live, including
    for a disabled plugin) -- so the double's path must be real and
    must match the bundle actually written to disk. ``disabled_subdir``
    only affects where the bundle lives on disk now; lookup no longer
    depends on which subdirectory it's in. Returns the configured
    plugin double so a test can still tweak
    ``executeAction.side_effect`` etc. afterward.
    """
    subdir = "Plugins (Disabled)" if disabled_subdir else "Plugins"
    plugins_dir = tmp_path / subdir
    plugins_dir.mkdir(parents=True, exist_ok=True)
    bundle = _make_bundle(plugins_dir, plugin_id, actions_xml=actions_xml)
    plugin = _fake_plugin(
        installed=installed, enabled=enabled, running=running,
        plugin_folder_path=str(bundle),
        execute_result=execute_result, forbid_execute=forbid_execute,
    )
    mock_indigo.server.getPlugin.return_value = plugin
    return plugin


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

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)

    assert result["plugin_id"] == PLUGIN_ID
    assert result["enabled"] is True
    assert result["running"] is True
    ids = {a["id"] for a in result["results"]}
    # sep1 is a spacer, excluded
    assert ids == {"setStandbyMode", "startZoneWithDelay", "reportWeather",
                    "internalSync", "noop"}
    assert result["total_count"] == len(result["results"])
    by_id = {a["id"]: a for a in result["results"]}
    # No id-less fields anywhere in this fixture -- skipped_fields
    # must not appear at all (absent, not zero).
    assert "skipped_fields" not in by_id["setStandbyMode"]


def test_list_plugin_actions_excludes_separator(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    assert all(a["id"] != "sep1" for a in result["results"])


def test_list_plugin_actions_label_field_becomes_note_not_prop(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}

    standby = by_id["setStandbyMode"]
    prop_ids = {p["id"] for p in standby["props"]}
    assert "standby_label" not in prop_ids
    assert prop_ids == {"mode"}
    assert any("automated functions" in note for note in standby["notes"])


def test_list_plugin_actions_resolvable_indigo_list_classes_name_the_tool(
    mock_indigo, tmp_path
):
    """indigo.devices/actionGroups/variables menus ARE resolvable by
    the caller -- just via a different lite tool, not free text and
    not an unresolvable plugin callback like class="self"."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_DYNAMIC_LIST_ACTIONS_XML)

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

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}

    prop = next(p for p in by_id["pickStatic"]["props"] if p["id"] == "opt")
    assert prop["values"] == [
        {"value": "0", "label": "Zero"},
        {"value": "1", "label": "One"},
    ]
    assert by_id["pickStatic"]["has_dynamic_fields"] is False


def test_list_plugin_actions_dynamic_menu_flagged(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)

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

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}
    assert by_id["internalSync"]["hidden"] is True
    assert by_id["internalSync"]["ui_path"] == "hidden"
    assert by_id["setStandbyMode"]["hidden"] is False


def test_list_plugin_actions_no_configui_flagged_props_undeclared(mock_indigo, tmp_path):
    """An action with NO <ConfigUI> at all (internalSync, hidden --
    the MQTT Connector fetchQueuedMessage shape) must NOT read as
    "takes no arguments": props_undeclared:true plus a reason."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID)

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

    _install(mock_indigo, tmp_path, PLUGIN_ID, disabled_subdir=True, enabled=False)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    assert result["enabled"] is False
    assert result["total_count"] == 5


def test_list_plugin_actions_reports_running_distinct_from_enabled(mock_indigo, tmp_path):
    """enabled and running can differ (a crashed-but-enabled plugin
    is a real Indigo state) -- both must be reported, not just
    enabled."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, enabled=True, running=False)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    assert result["enabled"] is True
    assert result["running"] is False


def test_list_plugin_actions_no_actions_xml_is_legit_empty(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=None)

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

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)

    assert result["skipped_actions"] == 2
    assert set(result["skipped_action_ids"]) == {"sep1", "malformedNoCallback"}
    assert [a["id"] for a in result["results"]] == ["realAction"]
    assert result["total_count"] == 1
    assert "no_actions_reason" not in result


def test_list_plugin_actions_malformed_xml_raises_not_empty(mock_indigo, tmp_path):
    """A parse failure must raise, never read as 'this plugin has no
    actions' -- the mutation this test exists to kill. Unlike
    plugin_execute_action (which degrades to attempting the dispatch
    anyway -- see the D-tagged tests below), list_plugin_actions is
    read-only discovery with nothing to fall back to, so it still
    refuses outright."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml="<Actions><Action")

    with pytest.raises(ValueError, match="could not be parsed"):
        _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)


def test_list_plugin_actions_unknown_plugin_id_names_it(mock_indigo, tmp_path):
    """getPlugin never raises (confirmed live jarvis 2026-08-24) --
    the real shape is a plugin object with isInstalled() False, not
    a lookup exception."""
    from tools.plugin_actions import _list_plugin_actions_handler

    mock_indigo.server.getPlugin.return_value = _fake_plugin(installed=False)
    with pytest.raises(ValueError, match="com.unknown"):
        _list_plugin_actions_handler({"plugin_id": "com.unknown"}, mock_indigo)


def test_list_plugin_actions_io_fault_checking_actions_xml_raises_with_errno(
    mock_indigo, tmp_path, monkeypatch
):
    """review round 4, item 2: os.path.isfile swallows a real OSError
    (permission denial, a stale network handle, an unmounted volume --
    this workspace runs bundles off /Volumes/...) as False --
    indistinguishable from "genuinely doesn't exist". A positive claim
    ("plugin bundle declares no Actions.xml") derived from a check
    that couldn't even look is the same lie round 3 deleted from the
    filesystem-scan bundle lookup, relocated here. Must raise, naming
    the errno, not report a soft empty result.

    Post-merge review item 7: must be a RuntimeError (never a
    ValueError) -- no argument change fixes a stale filesystem handle,
    so mcp_handler must route this to its back-off bucket, not its
    self-correct-and-retry one."""
    import tools.plugin_actions as plugin_actions_module
    from tools.plugin_actions import _list_plugin_actions_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID)
    actions_xml_path = plugin_actions_module._actions_xml_path(plugin.pluginFolderPath)

    real_stat = os.stat

    def _fake_stat(path, *a, **k):
        if path == actions_xml_path:
            raise PermissionError(13, "Permission denied")
        return real_stat(path, *a, **k)

    monkeypatch.setattr(plugin_actions_module.os, "stat", _fake_stat)

    with pytest.raises(RuntimeError, match="errno 13") as excinfo:
        _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    assert not isinstance(excinfo.value, ValueError)


def test_list_plugin_actions_genuinely_missing_actions_xml_still_soft_empty(
    mock_indigo, tmp_path, monkeypatch
):
    """The inverse of the I/O-fault test above: FileNotFoundError
    specifically still means the softer, legitimate "no Actions.xml"
    empty result -- proves the new os.stat-based check didn't turn
    EVERY path into a hard failure, only the ones that couldn't be
    determined at all."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=None)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    assert result["no_actions_reason"] == "plugin bundle declares no Actions.xml"


def test_list_plugin_actions_relative_plugin_folder_path_refused(mock_indigo, tmp_path):
    """review round 4, item 7: an empty/relative pluginFolderPath must
    not be silently os.path.join'd into a relative Actions.xml path
    (resolved against the plugin host's cwd, in principle reading a
    totally different bundle's Actions.xml) -- must be a named,
    explicit refusal instead."""
    from tools.plugin_actions import _list_plugin_actions_handler

    plugin = _fake_plugin(plugin_folder_path="Contents/Server Plugin")
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError, match="not an absolute path"):
        _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)


def test_list_plugin_actions_empty_plugin_folder_path_refused(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    plugin = _fake_plugin(plugin_folder_path="")
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError, match="not an absolute path"):
        _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)


def test_list_plugin_actions_rejects_unknown_args(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    with pytest.raises(ValueError, match="unknown argument"):
        _list_plugin_actions_handler(
            {"plugin_id": PLUGIN_ID, "bogus": 1}, mock_indigo
        )


# ---------------------------------------------------------------------
# list_plugin_actions -- real-world field shapes (review round 3, G)
# ---------------------------------------------------------------------

def test_list_plugin_actions_button_field_excluded_from_props(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_FIELD_SHAPES_ACTIONS_XML)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}
    rich = by_id["richAction"]
    prop_ids = {p["id"] for p in rich["props"]}
    assert "refreshButton" not in prop_ids
    assert any("Refresh" in note for note in rich["notes"])


def test_list_plugin_actions_hidden_field_flagged(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_FIELD_SHAPES_ACTIONS_XML)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}
    rich = by_id["richAction"]
    desc_prop = next(p for p in rich["props"] if p["id"] == "description")
    assert desc_prop.get("hidden") is True

    # A normal field must NOT be flagged hidden.
    legacy = next(p for p in rich["props"] if p["id"] == "legacy_default")
    assert "hidden" not in legacy


def test_list_plugin_actions_description_becomes_note(mock_indigo, tmp_path):
    """<Description> (13 live) was dropped entirely before this fix;
    it's kept as calling guidance, same as <Label> text, and applies
    to real prop fields too, not just label/separator fields."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_FIELD_SHAPES_ACTIONS_XML)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}
    rich = by_id["richAction"]
    assert any("more useful sentence" in note for note in rich["notes"])
    # the field itself is still a normal prop, unaffected
    notes_field = next(p for p in rich["props"] if p["id"] == "notes_field")
    assert notes_field["label"] == "Notes:"


def test_list_plugin_actions_default_attribute_fallback(mock_indigo, tmp_path):
    """The parser read only defaultValue before this fix, so 26 real
    fields using a bare `default` attribute reported default: null."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_FIELD_SHAPES_ACTIONS_XML)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}
    rich = by_id["richAction"]
    legacy = next(p for p in rich["props"] if p["id"] == "legacy_default")
    assert legacy["default"] == "42"


def test_list_plugin_actions_defaultValue_wins_over_default(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_FIELD_SHAPES_ACTIONS_XML)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}
    rich = by_id["richAction"]
    both = next(p for p in rich["props"] if p["id"] == "both_defaults")
    assert both["default"] == "new"


def test_list_plugin_actions_id_less_field_excluded_from_props(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_FIELD_SHAPES_ACTIONS_XML)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}
    rich = by_id["richAction"]
    # 6 fields total; button (note), hidden description, notes_field,
    # legacy_default, both_defaults are real props (5); the id-less
    # field is dropped entirely.
    assert None not in {p["id"] for p in rich["props"]}
    assert len(rich["props"]) == 4


def test_list_plugin_actions_id_less_field_counted_in_skipped_fields(mock_indigo, tmp_path):
    """review round 4, 'should fix': an id-less <Field> was dropped
    with NO counter at all, unlike an id-less <Action> (which has
    skipped_actions) -- an action whose fields all lack ids would be
    indistinguishable from a genuinely bare <ConfigUI/>."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_FIELD_SHAPES_ACTIONS_XML)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    by_id = {a["id"]: a for a in result["results"]}
    assert by_id["richAction"]["skipped_fields"] == 1


def test_list_plugin_actions_template_include_gets_distinct_reason(mock_indigo, tmp_path):
    """review round 4, 'should fix': a ConfigUI built from a
    <Template file="..."> include yields zero <Field> children, so it
    was mislabelled "declares no ConfigUI fields" -- a different fact
    from a genuinely field-less action, since fields DO exist there,
    they just aren't readable without resolving the include."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(
        mock_indigo, tmp_path, PLUGIN_ID,
        actions_xml=(
            '<?xml version="1.0"?>\n<Actions>\n'
            '    <Action id="templatedAction">\n'
            "        <Name>Templated Action</Name>\n"
            "        <CallbackMethod>templatedAction</CallbackMethod>\n"
            "        <ConfigUI>\n"
            '            <Template file="shared_fields.xml"/>\n'
            "        </ConfigUI>\n"
            "    </Action>\n</Actions>\n"
        ),
    )

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    action = result["results"][0]
    assert action["props"] == []
    assert action["props_undeclared"] is True
    assert "<Template>" in action["props_undeclared_reason"]
    assert "no ConfigUI fields" not in action["props_undeclared_reason"]


def test_list_plugin_actions_all_fields_idless_gets_distinct_reason(mock_indigo, tmp_path):
    """Post-merge review, MEDIUM item 8: an action whose <ConfigUI>
    fields ALL lack an id ends up with an empty props list (same as a
    genuinely field-less action) AND skipped_fields > 0 in the SAME
    object -- "declares no ConfigUI fields" is false there, fields
    demonstrably exist, they just can't be referenced. A third reason
    variant, the same fix <Template> already got."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(
        mock_indigo, tmp_path, PLUGIN_ID,
        actions_xml=(
            '<?xml version="1.0"?>\n<Actions>\n'
            '    <Action id="allIdless">\n'
            "        <Name>All Idless</Name>\n"
            "        <CallbackMethod>allIdless</CallbackMethod>\n"
            "        <ConfigUI>\n"
            "            <Field type=\"textfield\">\n"
            "                <Label>No id 1</Label>\n"
            "            </Field>\n"
            "            <Field type=\"checkbox\">\n"
            "                <Label>No id 2</Label>\n"
            "            </Field>\n"
            "        </ConfigUI>\n"
            "    </Action>\n</Actions>\n"
        ),
    )

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    action = result["results"][0]
    assert action["props"] == []
    assert action["skipped_fields"] == 2
    assert action["props_undeclared"] is True
    assert "declares no ConfigUI fields" not in action["props_undeclared_reason"]
    assert "2 field" in action["props_undeclared_reason"]


def test_list_plugin_actions_id_less_action_routed_through_skipped_bookkeeping(
    mock_indigo, tmp_path
):
    """An <Action> with Name/CallbackMethod but no id attribute can't
    be looked up by id at all -- it must not appear in results (which
    would silently collide under a None key) and must still be
    visible via skipped_actions rather than vanishing outright."""
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_FIELD_SHAPES_ACTIONS_XML)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    ids = {a["id"] for a in result["results"]}
    assert None not in ids
    assert ids == {"richAction"}
    assert result["skipped_actions"] == 1


# ---------------------------------------------------------------------
# plugin_execute_action -- isInstalled / not-installed (review round 3, A)
# ---------------------------------------------------------------------

def test_execute_action_unknown_plugin_id_names_it(mock_indigo, tmp_path):
    """getPlugin never raises (confirmed live jarvis 2026-08-24) --
    the real shape is a plugin object with isInstalled() False.
    forbid_execute=True proves the not-installed check runs before
    any dispatch attempt (review round 3, F: this test previously
    omitted that, passing incidentally rather than by assertion)."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _fake_plugin(installed=False, forbid_execute=True)
    mock_indigo.server.getPlugin.return_value = plugin
    with pytest.raises(ValueError, match="com.unknown"):
        _plugin_execute_action_handler(
            {"plugin_id": "com.unknown", "action_id": "noop"}, mock_indigo
        )
    plugin.executeAction.assert_not_called()


def test_execute_action_installed_but_disabled_still_names_not_enabled(
    mock_indigo, tmp_path
):
    """A genuinely installed-but-disabled plugin must get the
    not-enabled message, not the not-installed one -- proves
    isInstalled() and isEnabled() are checked as distinct signals."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, enabled=False,
                       forbid_execute=True)
    with pytest.raises(ValueError, match="not enabled"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
        )
    plugin.executeAction.assert_not_called()


# ---------------------------------------------------------------------
# plugin_execute_action -- disabled / not-running never reach dispatch
# ---------------------------------------------------------------------

def test_execute_action_disabled_plugin_fatal_and_never_dispatched(mock_indigo, tmp_path):
    """The convention's isolation test: hand it a plugin double whose
    executeAction raises AssertionError if touched, then assert we
    got the isEnabled ValueError instead."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, enabled=False,
                       forbid_execute=True)

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

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, enabled=True,
                       running=False, forbid_execute=True)

    with pytest.raises(ValueError, match="not running"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
        )
    plugin.executeAction.assert_not_called()


def test_execute_action_enabled_gate_runs_before_action_validation(mock_indigo, tmp_path):
    """review round 3, F1: a mutation moving the enabled/running gate
    to just after Actions.xml validation must be caught. Actions.xml
    is READABLE here on purpose: D's relaxation means an unreadable
    Actions.xml no longer raises at all (it degrades gracefully), so
    that combination can no longer distinguish check order -- a
    disabled plugin + a genuinely UNKNOWN action id in a readable
    Actions.xml is the pairing that still does: _try_validate_action
    DOES raise for that case, so if it ran first we'd see the
    unknown-action-id message instead of the enabled one."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, enabled=False,
                       forbid_execute=True)

    with pytest.raises(ValueError, match="not enabled"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "totallyBogusActionId"},
            mock_indigo,
        )
    plugin.executeAction.assert_not_called()


# ---------------------------------------------------------------------
# plugin_execute_action -- unknown action id (Actions.xml readable)
# ---------------------------------------------------------------------

def test_execute_action_unknown_action_id_lists_real_ones(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, forbid_execute=True)

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


def test_execute_action_unknown_action_id_survives_id_less_actions_present(
    mock_indigo, tmp_path
):
    """review round 3, G: an id-less <Action> used to reach
    sorted(by_id) and raise an opaque TypeError from inside the
    unknown-action-id error path, replacing the self-correcting
    message with a Python internal. Must still get the normal
    message."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID,
                       actions_xml=_FIELD_SHAPES_ACTIONS_XML, forbid_execute=True)

    with pytest.raises(ValueError) as excinfo:
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "totallyBogus"}, mock_indigo
        )
    message = str(excinfo.value)
    assert "totallyBogus" in message
    assert "richAction" in message
    plugin.executeAction.assert_not_called()


# ---------------------------------------------------------------------
# plugin_execute_action -- unknown prop key / static option values
# ---------------------------------------------------------------------

def test_execute_action_unknown_prop_key_names_declared_fields(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, forbid_execute=True)

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


def test_execute_action_rejects_out_of_range_static_option_value(mock_indigo, tmp_path):
    """review round 3, H: a static <List><Option> enum's VALUES were
    never enforced, only its key -- condition="7" against an action
    declaring only 0/1 returned a clean "dispatched"."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, forbid_execute=True)

    with pytest.raises(ValueError) as excinfo:
        _plugin_execute_action_handler(
            {
                "plugin_id": PLUGIN_ID,
                "action_id": "reportWeather",
                "props": {"condition": "7"},
            },
            mock_indigo,
        )
    message = str(excinfo.value)
    assert "condition" in message
    assert "7" in message
    plugin.executeAction.assert_not_called()


def test_execute_action_accepts_int_value_matching_string_option(mock_indigo, tmp_path):
    """An int 0 must match a declared Option value="0" -- config
    values are strings in the XML but callers reasonably pass ints."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID)

    result = _plugin_execute_action_handler(
        {
            "plugin_id": PLUGIN_ID,
            "action_id": "reportWeather",
            "props": {"condition": 0},
        },
        mock_indigo,
    )
    assert result["props_validated"] is True
    plugin.executeAction.assert_called_once()


def test_execute_action_option_with_no_value_attribute_not_accepted_as_none(
    mock_indigo, tmp_path
):
    """review round 4, 'should fix': an <Option> missing its value
    attribute parses to {"value": None, ...}; the old allowlist
    comprehension did str(None) == "None", so a caller passing the
    literal string "None" was silently accepted as a valid declared
    value. The valueless option itself must be excluded from the
    allowlist entirely, and the literal string "None" must still be
    rejected as out-of-range."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(
        mock_indigo, tmp_path, PLUGIN_ID,
        actions_xml=(
            '<?xml version="1.0"?>\n<Actions>\n'
            '    <Action id="pickOpt">\n'
            "        <Name>Pick Opt</Name>\n"
            "        <CallbackMethod>pickOpt</CallbackMethod>\n"
            "        <ConfigUI>\n"
            '            <Field type="menu" id="opt">\n'
            "                <Label>Option:</Label>\n"
            "                <List>\n"
            '                    <Option>No Value Here</Option>\n'
            '                    <Option value="1">One</Option>\n'
            "                </List>\n"
            "            </Field>\n"
            "        </ConfigUI>\n"
            "    </Action>\n</Actions>\n"
        ),
        forbid_execute=True,
    )

    with pytest.raises(ValueError, match="not a valid value"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "pickOpt", "props": {"opt": "None"}},
            mock_indigo,
        )
    plugin.executeAction.assert_not_called()


# ---------------------------------------------------------------------
# plugin_execute_action / list_plugin_actions -- skipped_options
# (post-merge review, MEDIUM item 6): valueless <Option>s counted and
# surfaced; when EVERY option in a field lacks a value, the enum is
# UNENFORCEABLE (not "every value is invalid") -- pass through rather
# than refusing every call with allowed:[], an unactionable refusal
# loop for the model.
# ---------------------------------------------------------------------

_ALL_VALUELESS_OPTIONS_ACTIONS_XML = (
    '<?xml version="1.0"?>\n<Actions>\n'
    '    <Action id="pickBrokenOpt">\n'
    "        <Name>Pick Broken Opt</Name>\n"
    "        <CallbackMethod>pickBrokenOpt</CallbackMethod>\n"
    "        <ConfigUI>\n"
    '            <Field type="menu" id="opt">\n'
    "                <Label>Option:</Label>\n"
    "                <List>\n"
    "                    <Option>First</Option>\n"
    "                    <Option>Second</Option>\n"
    "                </List>\n"
    "            </Field>\n"
    "        </ConfigUI>\n"
    "    </Action>\n</Actions>\n"
)


def test_list_plugin_actions_skipped_options_counted(mock_indigo, tmp_path):
    from tools.plugin_actions import _list_plugin_actions_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID,
              actions_xml=_ALL_VALUELESS_OPTIONS_ACTIONS_XML)

    result = _list_plugin_actions_handler({"plugin_id": PLUGIN_ID}, mock_indigo)
    action = result["results"][0]
    assert action["skipped_options"] == 2


def test_execute_action_all_valueless_options_treated_as_unenforceable(
    mock_indigo, tmp_path
):
    """The allowlist ends up empty (every <Option> lacked a value),
    but options genuinely existed -- must NOT refuse every call with
    allowed:[] (nothing for the model to retry toward); pass the value
    through unchecked instead, same as a dynamic menu."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID,
                       actions_xml=_ALL_VALUELESS_OPTIONS_ACTIONS_XML)

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "pickBrokenOpt",
         "props": {"opt": "anything"}},
        mock_indigo,
    )
    assert result["props_validated"] is True
    assert result["skipped_options"] == 2
    plugin.executeAction.assert_called_once()


def test_execute_action_dynamic_list_value_not_enforced(mock_indigo, tmp_path):
    """A dynamic menu's legal values are correctly unknowable -- must
    NOT be enforced the way a static enum's are."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID)
    mock_indigo.devices.__getitem__ = MagicMock(
        return_value=_fake_device(PLUGIN_ID, "sprinkler")
    )

    result = _plugin_execute_action_handler(
        {
            "plugin_id": PLUGIN_ID,
            "action_id": "startZoneWithDelay",
            "device_id": 1,
            "props": {"zone": "99999", "duration": "15"},  # dynamic, unenforceable
        },
        mock_indigo,
    )
    assert result["props_validated"] is True
    plugin.executeAction.assert_called_once()


# ---------------------------------------------------------------------
# plugin_execute_action -- undeclared props (action HAS no fields)
# ---------------------------------------------------------------------

def test_execute_action_undeclared_props_pass_through_unchecked(mock_indigo, tmp_path):
    """The primary #71 use case: a hidden action with NO declared
    ConfigUI fields (internalSync -- the MQTT Connector
    fetchQueuedMessage / uiPath="hidden" shape) must still be
    callable with arbitrary props. An absent allowlist is not an
    empty one -- refusing this would make the whole hidden-action
    class uncallable. Actions.xml WAS readable and the action WAS
    found there, so action_validated is true even though
    props_validated is false -- and result is "completed_unverified"
    (review round 4, item 4), not byte-identical to a fully-verified
    "completed"."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID)

    result = _plugin_execute_action_handler(
        {
            "plugin_id": PLUGIN_ID,
            "action_id": "internalSync",
            "props": {"message_type": "queued", "anything_else": 5},
        },
        mock_indigo,
    )
    assert result["result"] == "completed_unverified"
    assert result["action_validated"] is True
    assert "action_validated_reason" not in result
    assert result["props_validated"] is False
    assert "props_validated_reason" in result
    assert "no ConfigUI fields" in result["props_validated_reason"]
    assert "silently discarded" in result["props_validated_reason"]
    # review round 4, item 3: absent != empty -- props_not_supplied is
    # a positive "nothing missing" claim on the validated path only.
    assert "props_not_supplied" not in result
    plugin.executeAction.assert_called_once()


def test_execute_action_declared_case_still_hard_rejects(mock_indigo, tmp_path):
    """Regression guard: the undeclared-props fix must not weaken the
    declared case -- an action that DOES declare fields keeps
    hard-rejecting unknown prop keys."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, forbid_execute=True)

    with pytest.raises(ValueError, match="unknown prop"):
        _plugin_execute_action_handler(
            {
                "plugin_id": PLUGIN_ID,
                "action_id": "setStandbyMode",
                "props": {"modee": True},
            },
            mock_indigo,
        )
    plugin.executeAction.assert_not_called()


# ---------------------------------------------------------------------
# plugin_execute_action -- Actions.xml unreadable: ATTEMPT the
# dispatch anyway, but ONLY when nothing device/prop-shaped could go
# wrong silently (review round 3, D; narrowed by round 4, item 1 --
# deviceFilter has NO Indigo backstop, unlike a bad action id, and
# the degraded path can't know the deviceFilter OR the declared props
# at all)
# ---------------------------------------------------------------------

def test_execute_action_missing_xml_no_props_no_device_dispatches(mock_indigo, tmp_path):
    """The one shape the degraded path may still attempt: no props,
    no device_id. Indigo's own InvalidAction (with waitUntilDone
    forced true) is the only thing that could catch a wrong id here,
    and there's nothing else -- no device, no props -- that could go
    wrong silently."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=None)

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "someRealAction"}, mock_indigo,
    )
    plugin.executeAction.assert_called_once()
    assert result["action_validated"] is False
    assert "no Actions.xml" in result["action_validated_reason"]
    assert result["props_validated"] is False
    assert "could not be validated" in result["props_validated_reason"]
    assert "props_not_supplied" not in result
    assert result["result"] == "completed_unverified"
    # Post-merge review item 3: the module docstring overstated
    # InvalidAction's backstop here as covering "the one shape" fully
    # -- it only catches a wrong action id, not a valid action
    # dispatched without a device it actually requires (executeAction
    # defaults deviceId to 0 regardless). The third unknowable must be
    # named in the payload alongside action_validated_reason.
    assert "device_requirement_unknown_reason" in result
    assert "device" in result["device_requirement_unknown_reason"]


def test_execute_action_missing_xml_with_props_refused(mock_indigo, tmp_path):
    """review round 4, item 1: props on the degraded path are refused
    outright rather than sent unchecked -- we don't know the action's
    declared fields at all, so a mistyped prop name has no chance of
    being caught (Indigo silently drops it regardless)."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=None,
                       forbid_execute=True)

    with pytest.raises(ValueError, match="could not be validated"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "someRealAction",
             "props": {"whatever": "value"}},
            mock_indigo,
        )
    plugin.executeAction.assert_not_called()


def test_execute_action_missing_xml_with_device_id_refused_without_checking_existence(
    mock_indigo, tmp_path
):
    """review round 4, item 1: device_id on the degraded path is
    refused outright -- deviceFilter has NO Indigo-side backstop
    (unlike a bad action id), so this module is the only guard, and
    it can't validate a deviceFilter it doesn't know. The refusal
    must happen BEFORE even checking the device exists -- proven by a
    devices collection that raises AssertionError if touched at all."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=None,
                       forbid_execute=True)

    def _raise_if_touched(_key):
        raise AssertionError("device existence must not be checked here")

    mock_indigo.devices.__getitem__ = MagicMock(side_effect=_raise_if_touched)

    with pytest.raises(ValueError, match="could not be validated"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "someRealAction", "device_id": 999},
            mock_indigo,
        )
    plugin.executeAction.assert_not_called()


def test_execute_action_missing_xml_with_both_props_and_device_refused(
    mock_indigo, tmp_path
):
    """Both unchecked things named in one refusal, not just whichever
    was checked first."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=None,
                       forbid_execute=True)

    with pytest.raises(ValueError) as excinfo:
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "someRealAction",
             "props": {"x": 1}, "device_id": 5},
            mock_indigo,
        )
    message = str(excinfo.value)
    assert "props" in message
    assert "device_id" in message
    plugin.executeAction.assert_not_called()


def test_execute_action_malformed_xml_attempts_dispatch_anyway(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml="<Actions><Action")

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "someRealAction"}, mock_indigo
    )
    plugin.executeAction.assert_called_once()
    assert result["action_validated"] is False
    assert "could not be parsed" in result["action_validated_reason"]
    assert result["props_validated"] is False


def test_execute_action_wait_forced_true_on_degraded_dispatch(mock_indigo, tmp_path):
    """review round 4, PROBE 1: executeAction("bogusId",
    waitUntilDone=False) returns None with NO exception at all
    (confirmed live) -- InvalidAction only raises when
    waitUntilDone=True. The entire justification for dispatching on
    the degraded path is that InvalidAction catches a bad id, so
    waitUntilDone must be forced true there regardless of what the
    caller asked for, and the override disclosed rather than silently
    changing the caller's requested semantics.

    Post-merge review item 2: the payload's wait_until_done must
    report the EFFECTIVE value actually passed to executeAction (True
    here), not the caller's ignored request -- the old behaviour
    asserted wait_until_done:false alongside a result value the tool
    description says is unreachable when false. The caller's original
    request moves to wait_until_done_requested."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=None)

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "someRealAction",
         "wait_until_done": False},
        mock_indigo,
    )
    _, kwargs = plugin.executeAction.call_args
    assert kwargs["waitUntilDone"] is True
    assert result["wait_until_done"] is True  # the EFFECTIVE value used
    assert result["wait_until_done_requested"] is False  # the caller's request
    assert result["wait_until_done_overridden"] is True
    assert "wait_until_done_overridden_reason" in result


def test_execute_action_wait_not_overridden_on_validated_path(mock_indigo, tmp_path):
    """The override is specific to the degraded path -- a fully
    validated action must NOT have its explicit wait_until_done=False
    silently promoted to true."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID)

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "noop", "wait_until_done": False},
        mock_indigo,
    )
    _, kwargs = plugin.executeAction.call_args
    assert kwargs["waitUntilDone"] is False
    assert "wait_until_done_overridden" not in result


# ---------------------------------------------------------------------
# plugin_execute_action -- I/O fault vs. genuinely-absent Actions.xml,
# and a relative/empty pluginFolderPath (review round 4, items 2 & 7)
# ---------------------------------------------------------------------

def test_execute_action_io_fault_checking_actions_xml_is_a_hard_failure(
    mock_indigo, tmp_path, monkeypatch
):
    """A real I/O fault (not "genuinely absent") checking for
    Actions.xml must NOT quietly fold into the degraded "attempt the
    dispatch anyway" path -- that path's whole justification is
    "Actions.xml doesn't exist or won't parse", not "something may be
    badly wrong with this bundle". Must propagate as a hard failure,
    never dispatched, and carry the same WAS-NOT-dispatched guarantee
    every other pre-dispatch refusal in this module carries.

    Post-merge review item 7: must be a RuntimeError, not a
    ValueError -- no argument change fixes a stale filesystem handle."""
    import tools.plugin_actions as plugin_actions_module
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, forbid_execute=True)
    actions_xml_path = plugin_actions_module._actions_xml_path(plugin.pluginFolderPath)

    real_stat = os.stat

    def _fake_stat(path, *a, **k):
        if path == actions_xml_path:
            raise PermissionError(13, "Permission denied")
        return real_stat(path, *a, **k)

    monkeypatch.setattr(plugin_actions_module.os, "stat", _fake_stat)

    with pytest.raises(RuntimeError, match="errno 13") as excinfo:
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
        )
    assert not isinstance(excinfo.value, ValueError)
    assert "was NOT dispatched" in str(excinfo.value)
    plugin.executeAction.assert_not_called()


def test_execute_action_relative_plugin_folder_path_degrades_not_joins(
    mock_indigo, tmp_path
):
    """review round 4, item 7: a relative pluginFolderPath must
    become a NAMED degradation (action_validated:false), never a
    silent os.path.join into a path resolved against the plugin
    host's cwd. device_id/props still refused per item 1, since the
    action truly can't be validated."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _fake_plugin(plugin_folder_path="Contents/Server Plugin",
                           forbid_execute=True)
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError, match="could not be validated"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop", "props": {"x": 1}},
            mock_indigo,
        )
    plugin.executeAction.assert_not_called()


def test_execute_action_relative_plugin_folder_path_still_dispatches_bare_call(
    mock_indigo, tmp_path
):
    """With no props/device_id at all, the relative-path degradation
    is just another flavour of "action unvalidated" -- the narrowed
    origin case still gets through."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _fake_plugin(plugin_folder_path="Contents/Server Plugin")
    mock_indigo.server.getPlugin.return_value = plugin

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
    )
    plugin.executeAction.assert_called_once()
    assert result["action_validated"] is False
    assert "not an absolute path" in result["action_validated_reason"]


# ---------------------------------------------------------------------
# plugin_execute_action -- module-private exception type (review round
# 4, "should fix": a plain ValueError from anywhere inside
# _parse_actions_xml's transitive calls must NOT silently acquire
# "proceed with the write" semantics -- only _ActionsXmlError should
# ---------------------------------------------------------------------

def test_execute_action_unrelated_valueerror_is_not_treated_as_degraded(
    mock_indigo, tmp_path, monkeypatch
):
    """Simulates a hypothetical future bug: something transitively
    called by _parse_actions_xml raises a plain ValueError that is
    NOT an _ActionsXmlError. _try_validate_action must NOT catch it
    and degrade to "attempt dispatch anyway" -- it must propagate as
    a hard failure, proving the except clause is narrowed to the
    dedicated type rather than bare ValueError."""
    import tools.plugin_actions as plugin_actions_module
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, forbid_execute=True)

    def _raise_unrelated_valueerror(_path):
        raise ValueError("some unrelated bug, not an Actions.xml parse failure")

    monkeypatch.setattr(
        plugin_actions_module, "_parse_actions_xml", _raise_unrelated_valueerror
    )

    with pytest.raises(ValueError, match="unrelated bug"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
        )
    plugin.executeAction.assert_not_called()


# ---------------------------------------------------------------------
# post-dispatch honesty -- except BaseException (post-merge review,
# CRITICAL item 1: "raise RuntimeError(...) from exc" is NOT a
# re-raise -- it changes the class. A control-flow exception
# (KeyboardInterrupt/SystemExit) reaching this point must propagate
# COMPLETELY UNCHANGED so Indigo can act on it (a clean shutdown, not
# a caught-and-continued JSON-RPC error); the WAS-DISPATCHED
# disclosure moves to the logger instead, since the exception's own
# text can no longer carry it. The two tests below used to assert
# pytest.raises(RuntimeError, ...) here -- proven live to be the
# defect itself: a SystemExit(0) from executeAction became a
# RuntimeError, which mcp_handler's `except Exception` bucket then
# caught and turned into a -32603 JSON-RPC error, letting the request
# loop continue as though nothing happened.)
# ---------------------------------------------------------------------

def test_execute_action_base_exception_after_dispatch_still_discloses(
    mock_indigo, tmp_path
):
    """A control-flow exception after executeAction was genuinely
    called must propagate with its OWN type unchanged -- wrapping it
    into RuntimeError would let mcp_handler's Exception bucket catch
    and swallow it. The WAS-DISPATCHED disclosure still happens, via
    the logger, since the exception itself can no longer carry it."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin.executeAction.side_effect = KeyboardInterrupt()
    logger = MagicMock()

    with pytest.raises(KeyboardInterrupt):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo, logger=logger
        )
    assert logger.warning.called
    logged = " ".join(str(a) for a in logger.warning.call_args[0])
    assert "DISPATCHED" in logged


def test_execute_action_base_exception_after_dispatch_disclosure_survives_no_logger(
    mock_indigo, tmp_path
):
    """No logger supplied (register()'s default) must not itself raise
    or swallow the real exception -- the disclosure is simply skipped,
    not fatal."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin.executeAction.side_effect = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
        )


def test_execute_action_base_exception_during_serialization_still_discloses(
    mock_indigo, tmp_path, monkeypatch
):
    """Same guarantee for the post-success serialization guard: a
    control-flow exception must propagate unchanged, with the
    disclosure logged rather than folded into the exception text."""
    import tools.plugin_actions as plugin_actions_module
    from tools.plugin_actions import _plugin_execute_action_handler

    def _raiser(_value):
        raise KeyboardInterrupt()

    monkeypatch.setattr(plugin_actions_module, "_json_safe", _raiser)
    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, execute_result="some-value")
    logger = MagicMock()

    with pytest.raises(KeyboardInterrupt):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo, logger=logger
        )
    plugin.executeAction.assert_called_once()
    assert logger.warning.called


def test_execute_action_exception_after_dispatch_still_wraps_as_runtimeerror(
    mock_indigo, tmp_path
):
    """The Exception (non-control-flow) branch is unaffected by the
    BaseException split -- an ordinary post-dispatch failure still
    gets wrapped as RuntimeError with the WAS-DISPATCHED disclosure in
    its own message text, no logger required."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin.executeAction.side_effect = Exception("plugin blew up")

    with pytest.raises(RuntimeError, match="WAS DISPATCHED") as excinfo:
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
        )
    assert "Exception: plugin blew up" in str(excinfo.value)


# ---------------------------------------------------------------------
# plugin_execute_action -- post-dispatch honesty (review round 3, E)
# ---------------------------------------------------------------------

def test_execute_action_post_dispatch_failure_never_says_not_dispatched(
    mock_indigo, tmp_path
):
    """A mutation proved this unpinned: changing the post-executeAction
    error text to claim "nothing was performed" passed the entire
    suite. Every PRE-dispatch error correctly says "was NOT
    dispatched"; this one, which fires AFTER executeAction was
    genuinely called, must say the opposite and must not be
    ValueError/TypeError (mcp_handler routes those to the
    self-correct-and-retry bucket; a fault after an irreversible write
    needs the back-off bucket instead)."""
    from tools.plugin_actions import _plugin_execute_action_handler

    side_effects_recorded = []

    def _fail_after_recording(*_args, **_kwargs):
        side_effects_recorded.append("touched the plugin")
        raise Exception("plugin-side blew up")

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin.executeAction.side_effect = _fail_after_recording

    with pytest.raises(Exception) as excinfo:
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
        )
    assert side_effects_recorded == ["touched the plugin"]
    message = str(excinfo.value)
    assert "NOT performed" not in message
    assert "NOT dispatched" not in message
    assert "DISPATCHED" in message
    assert not isinstance(excinfo.value, ValueError)
    assert not isinstance(excinfo.value, TypeError)


def test_execute_action_serialization_failure_after_success_reports_dispatched(
    mock_indigo, tmp_path, monkeypatch
):
    """_json_safe sat OUTSIDE the try before this fix, so a
    serialization failure after a genuinely successful action would
    have reported as a failed write. It must carry the same
    already-happened guarantee."""
    import tools.plugin_actions as plugin_actions_module
    from tools.plugin_actions import _plugin_execute_action_handler

    def _raiser(_value):
        raise Exception("cannot serialize this")

    monkeypatch.setattr(plugin_actions_module, "_json_safe", _raiser)

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, execute_result="some-value")

    with pytest.raises(Exception) as excinfo:
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
        )
    plugin.executeAction.assert_called_once()
    message = str(excinfo.value)
    assert "NOT performed" not in message
    assert "NOT dispatched" not in message
    assert "already ran" in message
    assert not isinstance(excinfo.value, ValueError)
    assert not isinstance(excinfo.value, TypeError)


def test_execute_action_action_raising_after_dispatch_is_surfaced(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID)
    plugin.executeAction.side_effect = Exception("plugin-side validation failed")

    with pytest.raises(Exception, match="plugin-side validation failed"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
        )


# ---------------------------------------------------------------------
# plugin_execute_action -- wait_until_done (review round 3, F2/F3)
# ---------------------------------------------------------------------

def test_execute_action_wait_until_done_passed_through_when_false(mock_indigo, tmp_path):
    """Hardcoding waitUntilDone=True passed all prior tests -- none of
    them ever exercised a non-default value."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID)

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "noop", "wait_until_done": False},
        mock_indigo,
    )
    _, kwargs = plugin.executeAction.call_args
    assert kwargs["waitUntilDone"] is False
    assert result["wait_until_done"] is False


def test_execute_action_wait_until_done_defaults_true_and_is_echoed(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID)

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
    )
    _, kwargs = plugin.executeAction.call_args
    assert kwargs["waitUntilDone"] is True
    assert result["wait_until_done"] is True


def test_execute_action_wait_until_done_rejects_non_bool(mock_indigo, tmp_path):
    """Deleting the bool type-check passed all prior tests -- add a
    real assertion that a non-bool is rejected before dispatch."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, forbid_execute=True)

    with pytest.raises(ValueError, match="wait_until_done"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop", "wait_until_done": "yes"},
            mock_indigo,
        )
    plugin.executeAction.assert_not_called()


def test_execute_action_none_return_wait_true_fully_validated_reports_completed(
    mock_indigo, tmp_path
):
    """waitUntilDone=true + a None return + fully validated action AND
    props means executeAction ran to completion synchronously with
    everything confirmed beforehand -- the strongest claim this tool
    makes. Uses setStandbyMode (has a real declared "mode" field,
    supplied) rather than noop (no declared fields at all, which
    review round 4 downgrades to "completed_unverified" -- see
    test_execute_action_none_return_wait_true_but_unvalidated_reports_completed_unverified)."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, execute_result=None)

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "setStandbyMode",
         "props": {"mode": True}, "wait_until_done": True},
        mock_indigo,
    )
    assert result["result"] == "completed"
    assert result["props_validated"] is True


def test_execute_action_none_return_wait_true_but_unvalidated_reports_completed_unverified(
    mock_indigo, tmp_path
):
    """review round 4, item 4: result:"completed" was byte-identical
    on the fully-validated and wholly-unvalidated paths. noop has no
    declared ConfigUI fields at all, so props_validated is false even
    though the call ran to completion (waited, no exception) -- the
    result string itself must say so, not just a buried payload
    field a model might not read."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, execute_result=None)

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "noop", "wait_until_done": True},
        mock_indigo,
    )
    assert result["result"] == "completed_unverified"
    assert result["props_validated"] is False


def test_execute_action_none_return_wait_false_reports_dispatched(mock_indigo, tmp_path):
    """waitUntilDone=false + a None return means Indigo queued the
    action and returned without waiting at all -- weaker than
    "completed", not even confirmed at the plugin-callback level."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, execute_result=None)

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "noop", "wait_until_done": False},
        mock_indigo,
    )
    assert result["result"] == "dispatched"


def test_execute_action_non_none_return_reports_returned_value(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, execute_result="ok-42")

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
    )
    assert result["result"] == "returned"
    assert result["value"] == "ok-42"


# ---------------------------------------------------------------------
# plugin_execute_action -- plain dict -> indigo.Dict conversion
# ---------------------------------------------------------------------

def test_execute_action_converts_plain_dict_to_indigo_dict(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    _plain_dict_indigo(mock_indigo)
    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID)

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
    assert result["result"] == "completed"


# ---------------------------------------------------------------------
# plugin_execute_action -- deviceFilter / device_id
# ---------------------------------------------------------------------

def test_execute_action_device_required_without_device_id_refused(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, forbid_execute=True)

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


def test_execute_action_empty_device_filter_gap_reason_does_not_contradict_required(
    mock_indigo, tmp_path
):
    """Post-merge review, LOW item (:391/:829): deviceFilter="" (the
    attribute IS present, just empty) makes device_required:true --
    saying "action declares no deviceFilter" in the same response
    that just forced the caller to supply a device_id would
    contradict itself. Must say the deviceFilter is present but
    empty, distinct from the attribute being absent altogether."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(
        mock_indigo, tmp_path, PLUGIN_ID,
        actions_xml=(
            '<?xml version="1.0"?>\n<Actions>\n'
            '    <Action id="emptyFilterAction" deviceFilter="">\n'
            "        <Name>Empty Filter</Name>\n"
            "        <CallbackMethod>emptyFilterAction</CallbackMethod>\n"
            "    </Action>\n</Actions>\n"
        ),
    )
    mock_indigo.devices.__getitem__ = MagicMock(
        return_value=_fake_device(PLUGIN_ID, "widget")
    )

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "emptyFilterAction", "device_id": 7},
        mock_indigo,
    )
    assert result["device_validated"] is False
    assert "empty deviceFilter" in result["device_validated_reason"]
    assert "declares no deviceFilter" not in result["device_validated_reason"]


def test_execute_action_device_id_validated_against_real_devices(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, forbid_execute=True)
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

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID)
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


def test_execute_action_device_owned_by_wrong_plugin_rejected(mock_indigo, tmp_path):
    """Existence alone (_lookup_or_raise) doesn't prove ownership --
    Netro's startZoneWithDelay (filter self.sprinkler) must NOT
    happily accept a device that belongs to some other plugin."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, forbid_execute=True)
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

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, forbid_execute=True)
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
    """"self.sprinkler, self.zone" is fully self-scoped (every
    alternative), so device_validated must be True when the device
    matches ANY alternative -- result is "completed_unverified" (not
    plain "completed") only because multiFilterAction has no declared
    props at all, unrelated to the device check."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID,
                       actions_xml=_COMMA_FILTER_ACTIONS_XML)
    mock_indigo.devices.__getitem__ = MagicMock(
        return_value=_fake_device(PLUGIN_ID, "zone")
    )

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "multiFilterAction", "device_id": 777},
        mock_indigo,
    )
    assert result["result"] == "completed_unverified"
    assert result["device_validated"] is True
    assert "device_validated_reason" not in result
    plugin.executeAction.assert_called_once()


def test_execute_action_non_self_filter_skips_ownership_validation(mock_indigo, tmp_path):
    """A deviceFilter that isn't self-scoped at all is left
    unvalidated rather than guessed at -- existence check still
    runs, but a device from a totally unrelated plugin dispatches,
    and device_validated:false discloses that ownership specifically
    was never confirmed."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID,
                       actions_xml=_COMMA_FILTER_ACTIONS_XML)
    mock_indigo.devices.__getitem__ = MagicMock(
        return_value=_fake_device("com.completely.unrelated", "widget")
    )

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "nonSelfFilterAction", "device_id": 888},
        mock_indigo,
    )
    assert result["result"] == "completed_unverified"
    assert result["device_validated"] is False
    assert "not self-scoped" in result["device_validated_reason"]
    plugin.executeAction.assert_called_once()


# ---------------------------------------------------------------------
# plugin_execute_action -- unified `verified` (post-merge review,
# HIGH item 5): completed_unverified must fire on device_validated
# being False too, not just props_validated -- previously an action
# with fully-checked props but an unvalidated device silently reported
# "completed", the strongest claim this tool makes, despite device
# ownership never having been confirmed at all (which the module's own
# docstring argues is the WORSE of the two to get wrong, since props
# get a partial check and device ownership gets none from Indigo).
# ---------------------------------------------------------------------

def test_execute_action_device_unvalidated_but_props_validated_still_unverified(
    mock_indigo, tmp_path
):
    """The specific combination the old three-branch logic missed:
    props_validated:true (the action declares and the caller supplied
    a real field) alongside device_validated:false (non-self-scoped
    deviceFilter) must still report completed_unverified and
    verified:false -- not "completed", which would have claimed a
    fully confirmed call."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID,
              actions_xml=_COMMA_FILTER_ACTIONS_XML, execute_result=None)
    mock_indigo.devices.__getitem__ = MagicMock(
        return_value=_fake_device("com.completely.unrelated", "widget")
    )

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "nonSelfFilterWithPropsAction",
         "device_id": 888, "props": {"flag": True}},
        mock_indigo,
    )
    assert result["props_validated"] is True
    assert result["device_validated"] is False
    assert result["verified"] is False
    assert result["result"] == "completed_unverified"


def test_execute_action_fully_validated_reports_verified_true(mock_indigo, tmp_path):
    """The positive case: props AND device both confirmed -> verified
    true, result completed -- proves the unification didn't just make
    everything unverified."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, execute_result=None)
    mock_indigo.devices.__getitem__ = MagicMock(
        return_value=_fake_device(PLUGIN_ID, "sprinkler")
    )

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "startZoneWithDelay",
         "device_id": 1, "props": {"zone": "1", "duration": "15"}},
        mock_indigo,
    )
    assert result["props_validated"] is True
    assert result["device_validated"] is True
    assert result["verified"] is True
    assert result["result"] == "completed"


def test_execute_action_verified_field_present_on_dispatched_branch(mock_indigo, tmp_path):
    """verified must be reported on the weakest branch too
    (wait_until_done:false, no completion signal at all) -- not just
    the completed/completed_unverified choice."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, execute_result=None)

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "noop", "wait_until_done": False},
        mock_indigo,
    )
    assert result["result"] == "dispatched"
    assert "verified" in result


def test_execute_action_verified_field_present_on_returned_branch(mock_indigo, tmp_path):
    """Same for the non-None-return branch."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, execute_result="ok-42")

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "noop"}, mock_indigo
    )
    assert result["result"] == "returned"
    assert "verified" in result


def test_execute_action_mixed_filter_does_not_falsely_reject_non_self_device(
    mock_indigo, tmp_path
):
    """review round 4, item 5: deviceFilter="self.zone, indigo.relay"
    (mixed) must NOT hard-reject a device that legitimately matches
    the non-self "indigo.relay" alternative -- the old any()-based
    gate would validate against ONLY the self.zone alternative and
    reject every real indigo.relay device with a false explanation."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(
        mock_indigo, tmp_path, PLUGIN_ID,
        actions_xml=(
            '<?xml version="1.0"?>\n<Actions>\n'
            '    <Action id="mixedFilterAction" '
            'deviceFilter="self.zone, indigo.relay">\n'
            "        <Name>Mixed Filter</Name>\n"
            "        <CallbackMethod>mixedFilterAction</CallbackMethod>\n"
            "    </Action>\n</Actions>\n"
        ),
    )
    # A device that matches NEITHER self.zone NOR belongs to this
    # plugin at all -- i.e. exactly the kind of device that would
    # legitimately satisfy "indigo.relay" (a built-in Indigo device
    # type, not scoped to any plugin).
    mock_indigo.devices.__getitem__ = MagicMock(
        return_value=_fake_device("com.totally.unrelated", "relay")
    )

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "mixedFilterAction", "device_id": 42},
        mock_indigo,
    )
    assert result["device_validated"] is False
    assert "mixes in a non-self-scoped" in result["device_validated_reason"]
    plugin.executeAction.assert_called_once()


def test_execute_action_mixed_filter_matching_self_alternative_validates_true(
    mock_indigo, tmp_path
):
    """Post-merge review, LOW item :815: a mixed filter must still be
    CHECKED against its self-scoped alternative(s) even though it
    can't be checked as a whole -- the old code skipped the check
    entirely for any non-wholly-self-scoped filter, so a device that
    demonstrably matched "self.zone" (the checkable alternative)
    reported device_validated:false ("not self-scoped") instead of
    true. Right not to reject on a mismatch; wrong to stop checking
    on a match."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(
        mock_indigo, tmp_path, PLUGIN_ID,
        actions_xml=(
            '<?xml version="1.0"?>\n<Actions>\n'
            '    <Action id="mixedFilterAction" '
            'deviceFilter="self.zone, indigo.relay">\n'
            "        <Name>Mixed Filter</Name>\n"
            "        <CallbackMethod>mixedFilterAction</CallbackMethod>\n"
            "    </Action>\n</Actions>\n"
        ),
    )
    mock_indigo.devices.__getitem__ = MagicMock(
        return_value=_fake_device(PLUGIN_ID, "zone")
    )

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "mixedFilterAction", "device_id": 43},
        mock_indigo,
    )
    assert result["device_validated"] is True
    assert "device_validated_reason" not in result
    plugin.executeAction.assert_called_once()


def test_execute_action_bare_self_plus_typed_alternative_describes_any_type(
    mock_indigo, tmp_path
):
    """review round 4, item 5's closing note: "self, self.foo" must
    describe itself as accepting ANY deviceTypeId (the bare "self"
    makes the "self.foo" alternative redundant), not "typeId in
    ['foo']" -- and the mismatch error must actually be reachable
    with a wrong-plugin device to exercise the description."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(
        mock_indigo, tmp_path, PLUGIN_ID,
        actions_xml=(
            '<?xml version="1.0"?>\n<Actions>\n'
            '    <Action id="bareSelfAction" deviceFilter="self, self.foo">\n'
            "        <Name>Bare Self</Name>\n"
            "        <CallbackMethod>bareSelfAction</CallbackMethod>\n"
            "    </Action>\n</Actions>\n"
        ),
        forbid_execute=True,
    )
    mock_indigo.devices.__getitem__ = MagicMock(
        return_value=_fake_device("com.other.plugin", "bar")
    )

    with pytest.raises(ValueError) as excinfo:
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "bareSelfAction", "device_id": 9},
            mock_indigo,
        )
    message = str(excinfo.value)
    assert "any deviceTypeId" in message
    assert "typeId in ['foo']" not in message
    plugin.executeAction.assert_not_called()


# ---------------------------------------------------------------------
# plugin_execute_action -- props_not_supplied (incl. hidden exclusion)
# ---------------------------------------------------------------------

def test_execute_action_reports_props_not_supplied(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID)
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


def test_execute_action_hidden_field_excluded_from_props_not_supplied(mock_indigo, tmp_path):
    """A hidden="true" field is runtime-computed by the plugin -- it
    must never appear in props_not_supplied, which would otherwise
    read as "the caller forgot something" for a field nobody is
    supposed to supply."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_FIELD_SHAPES_ACTIONS_XML)

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "richAction"}, mock_indigo
    )
    assert "description" not in result["props_not_supplied"]
    assert "legacy_default" in result["props_not_supplied"]


def test_execute_action_skipped_fields_surfaced_in_write_payload(mock_indigo, tmp_path):
    """Post-merge review item 4: skipped_fields (parsed on the action
    and already surfaced by list_plugin_actions) must also reach the
    plugin_execute_action payload -- previously the write handler
    silently dropped it, following list_plugin_actions'
    skipped_actions convention."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_FIELD_SHAPES_ACTIONS_XML)

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "richAction"}, mock_indigo
    )
    assert result["skipped_fields"] == 1


def test_execute_action_template_include_reason_reaches_write_payload(
    mock_indigo, tmp_path
):
    """Post-merge review item 4 (dangerous direction): for a
    <Template file="..."/> ConfigUI, the write handler used to emit a
    hardcoded "action declares no ConfigUI fields" -- FALSE for this
    shape (fields genuinely exist and may be required) and dangerous,
    since it tells the caller the opposite of the truth right where
    they decide whether to supply props. Must surface the action's
    own parse-time reason instead."""
    from tools.plugin_actions import _plugin_execute_action_handler

    _install(
        mock_indigo, tmp_path, PLUGIN_ID,
        actions_xml=(
            '<?xml version="1.0"?>\n<Actions>\n'
            '    <Action id="templatedAction">\n'
            "        <Name>Templated Action</Name>\n"
            "        <CallbackMethod>templatedAction</CallbackMethod>\n"
            "        <ConfigUI>\n"
            '            <Template file="shared_fields.xml"/>\n'
            "        </ConfigUI>\n"
            "    </Action>\n</Actions>\n"
        ),
    )

    result = _plugin_execute_action_handler(
        {"plugin_id": PLUGIN_ID, "action_id": "templatedAction",
         "props": {"whatever": "value"}},
        mock_indigo,
    )
    assert result["props_validated"] is False
    assert "<Template>" in result["props_validated_reason"]
    assert "declares no ConfigUI fields" not in result["props_validated_reason"]
    # The dispatch-specific caveat is still appended.
    assert "silently discarded" in result["props_validated_reason"]


# ---------------------------------------------------------------------
# plugin_execute_action -- prop value type validation
# ---------------------------------------------------------------------

def test_execute_action_rejects_non_scalar_prop_value(mock_indigo, tmp_path):
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, forbid_execute=True)

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
    """Isolation test (review round 3, F4): this refusal must happen
    before any Indigo call at all, including a plugin lookup."""
    from tools.plugin_actions import _plugin_execute_action_handler

    plugin = _fake_plugin(forbid_execute=True)
    mock_indigo.server.getPlugin.return_value = plugin

    with pytest.raises(ValueError, match="unknown argument"):
        _plugin_execute_action_handler(
            {"plugin_id": PLUGIN_ID, "action_id": "noop", "bogus": 1}, mock_indigo
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

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_SKIP_ACTIONS_XML,
                       forbid_execute=True)

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

    plugin = _install(mock_indigo, tmp_path, PLUGIN_ID, actions_xml=_SKIP_ACTIONS_XML,
                       forbid_execute=True)

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
# tool descriptions (review round 3, I) -- read by a model at runtime
# ---------------------------------------------------------------------

def test_list_plugin_actions_description_mentions_enabled_running_notes(mock_indigo):
    from tools.plugin_actions import register

    handler = MagicMock()
    register(handler, indigo_module=mock_indigo)
    call = next(
        c for c in handler.register_tool.call_args_list
        if c.kwargs["name"] == "list_plugin_actions"
    )
    description = call.kwargs["description"]
    assert "enabled" in description
    assert "running" in description
    assert "notes" in description


def test_plugin_execute_action_description_mentions_wait_and_device_and_not_supplied(
    mock_indigo
):
    from tools.plugin_actions import register

    handler = MagicMock()
    register(handler, indigo_module=mock_indigo)
    call = next(
        c for c in handler.register_tool.call_args_list
        if c.kwargs["name"] == "plugin_execute_action"
    )
    description = call.kwargs["description"]
    assert "wait_until_done" in description
    assert "device_id" in description
    assert "props_not_supplied" in description


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
