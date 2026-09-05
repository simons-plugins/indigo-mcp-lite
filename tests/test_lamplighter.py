"""Tests for the lamplighter_* tools (PRD 5.12).

One test per promise the family makes, phrased adversarially: not
"does it find the zone?" but "when could this report success having
changed nothing?". The degradation paths get the same weight as the
happy ones, because every one of them is a way to hand a model an
honest-looking answer produced when the code could not do its job.

Three conventions carried from tests/test_auto_lights.py and the
workspace testing note:

- "nothing was written" is made FATAL, not merely unasserted: every
  refusal test compares the config file's BYTES against what it held
  before and asserts no backup file was created either. A refusal that
  leaves a half-written file or an orphan backup fails here.
- "this was not called" is made fatal by handing the code a dependency
  that raises ``_MustNotBeCalled`` -- a BaseException, so the module's
  own ``except Exception`` guards cannot swallow it into a friendly
  error and hide the violation.
- ``_sleep`` is patched out module-wide so the reload poll runs its
  twenty iterations instantly; the tests that care about reload
  DETECTION drive the change from inside the patched sleep.
"""
import json
from unittest.mock import MagicMock

import pytest


PLUGIN_ID = "com.simons-plugins.indigo-lamplighter"


class _MustNotBeCalled(BaseException):
    """Raised by a dependency the test forbids touching.

    A BaseException on purpose: ``tools/lamplighter.py`` wraps
    ``getPlugin``/``executeAction``/the device sweep in ``except
    Exception`` handlers that turn faults into friendly errors, so an
    ordinary exception here would be absorbed and the test would pass
    while the forbidden call happened.
    """


# ---------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """The reload poll waits 0.5s x 20. Never in a unit test."""
    import tools.lamplighter as lamplighter_module

    monkeypatch.setattr(lamplighter_module, "_sleep", lambda _seconds: None)


def _sample_config():
    return {
        "version": 1,
        "reconcile_seconds": 60,
        "echo_window_seconds": 15,
        "zones": [
            {
                "name": "Kitchen",
                "enabled": True,
                "presence_devices": [1465867145, 735515977],
                "hold_seconds": 300,
                "lux": {"device": 1616814762, "dark_below": 2200,
                        "hysteresis": 300},
                "lights": [772478931, 144694384],
                "override": {"duration_minutes": 60, "extend_minutes": 30},
                "periods": [
                    {"name": "Overnight", "from": "00:00", "to": "06:00",
                     "mode": "on_and_off",
                     "levels": {"772478931": "leave", "144694384": 30}},
                    {"name": "Dusk", "from": "sunset-30m", "to": "19:00",
                     "mode": "on_and_off",
                     "levels": {"772478931": 50, "144694384": 100}},
                ],
            },
            {
                # override.enabled false -> Lamplighter never locks it.
                "name": "Hallway",
                "enabled": True,
                "presence_devices": [710473944],
                "hold_seconds": 120,
                "lux": None,
                "lights": [400000001],
                "override": {"enabled": False},
                "periods": [
                    {"name": "All day", "from": "00:00", "to": "00:00",
                     "mode": "on_and_off", "levels": {"400000001": "on"}},
                ],
            },
        ],
    }


def _write_config(mock_indigo, tmp_path, config):
    """Write ``config`` at Lamplighter's real Preferences path under
    ``tmp_path`` and point ``getInstallFolderPath`` at it.

    Note the layout: ``Preferences/Plugins/<plugin id>/lamplighter.json``
    -- one directory level different from Auto Lights'
    ``Preferences/<plugin id>/config/``. Getting this wrong reads as
    "the plugin is not installed", so it is pinned here rather than
    left implicit.
    """
    config_dir = tmp_path / "Preferences" / "Plugins" / PLUGIN_ID
    config_dir.mkdir(parents=True)
    config_path = config_dir / "lamplighter.json"
    config_path.write_text(json.dumps(config, indent=2))
    mock_indigo.server.getInstallFolderPath.return_value = str(tmp_path)
    return config_path


def _fake_plugin(enabled=True, execute_result=None):
    p = MagicMock()
    p.isEnabled = MagicMock(return_value=enabled)
    p.executeAction = MagicMock(return_value=execute_result)
    return p


def _install(mock_indigo, enabled=True, execute_result=None):
    plugin = _fake_plugin(enabled=enabled, execute_result=execute_result)
    mock_indigo.server.getPlugin.return_value = plugin
    return plugin


def _plain_dict_indigo(mock_indigo):
    """Make ``mock_indigo.Dict`` behave like the real ``indigo.Dict``:
    a distinguishable object built from the plain dict passed in, so a
    test can assert the CONVERTED object (not the plain dict) is what
    reached executeAction. Mirrors test_plugin_actions.py."""
    def _to_dict(plain):
        wrapped = dict(plain)
        wrapped["__is_indigo_dict__"] = True
        return wrapped
    mock_indigo.Dict = MagicMock(side_effect=_to_dict)
    return mock_indigo


def _wrapped(props):
    """What ``_plain_dict_indigo`` turns ``props`` into."""
    out = dict(props)
    out["__is_indigo_dict__"] = True
    return out


def _zone_device(dev_id, zone_name, on=True, states=None, name=None,
                 decoy_plugin_props_zone=None):
    """A Lamplighter zone device shaped the way lite actually sees one.

    The three prop dictionaries are NOT interchangeable and the fake
    must not pretend they are:

    - ``pluginProps`` is scoped to the CALLING plugin, so for a device
      Lamplighter created it is EMPTY from lite's process. Defaulting
      it to ``{}`` here is the whole point: a fake that put the zone
      name in it would let a ``pluginProps`` read pass in tests and
      fail on every live server (it did -- jarvis, 2026-09-05).
    - ``ownerProps`` is the creating plugin's props. This is where the
      zone name genuinely is.
    - ``globalProps[<plugin id>]`` is the same data the long way round,
      readable by anyone, and the fallback for Indigo < API 1.20.

    ``decoy_plugin_props_zone`` deliberately puts a WRONG name in
    ``pluginProps`` for the mutation test below.
    """
    d = MagicMock()
    d.id = dev_id
    d.name = name or f"{zone_name} Lights"
    d.deviceTypeId = "lamplighter_zone"
    d.onState = on
    d.pluginProps = (
        {} if decoy_plugin_props_zone is None
        else {"zone_name": decoy_plugin_props_zone}
    )
    d.ownerProps = {"zone_name": zone_name}
    d.globalProps = {PLUGIN_ID: {"zone_name": zone_name}}
    d.states = {
        "state": "vacant",
        "explain": f"{zone_name}: vacant in Dusk.",
        "presence_active": False,
        "presence_last_seen": "2026-09-05T10:00:00",
        "lux": "1800",
        "dark": True,
        "period": "Dusk",
        "override_device": "",
        "override_expires": "",
        "desired_summary": "off",
        "evaluations_today": 12,
        "writes_today": 3,
        "overrides_today": 0,
        "last_trigger": "presence",
    }
    if states:
        d.states.update(states)
    return d


def _controller_device(dev_id=9001, on=True, states=None):
    d = MagicMock()
    d.id = dev_id
    d.name = "Lamplighter Controller"
    d.deviceTypeId = "lamplighter_controller"
    d.onState = on
    # The controller carries no zone_name; all three dicts are explicit
    # so a stray MagicMock can never masquerade as one.
    d.pluginProps = {}
    d.ownerProps = {}
    d.globalProps = {PLUGIN_ID: {}}
    d.states = {
        "config_status": "ok",
        # The two states the reload check leans on. config_loaded_at is
        # stamped only on a SUCCESSFUL load, so it is the signal that
        # separates "reloaded" from "looked and refused".
        "config_loaded_at": "2026-09-05T16:40:00",
        "config_zone_count": 2,
        "zones": 2,
        "zones_enabled": 2,
        "zones_overridden": 0,
        "evaluations_today": 24,
        "writes_today": 6,
        "overrides_today": 1,
    }
    if states:
        d.states.update(states)
    return d


def _other_device(dev_id=5, type_id="zwColorDimmerType"):
    d = MagicMock()
    d.id = dev_id
    d.name = "Some Lamp"
    d.deviceTypeId = type_id
    d.onState = True
    d.pluginProps = {}
    d.ownerProps = {}
    d.globalProps = {}
    d.states = {}
    return d


#: Sentinel so ``verdict=None`` can mean "the action answered nothing"
#: rather than "use the default verdict" -- the distinction the
#: unusable-answer test turns on.
_DEFAULT_VERDICT = object()


def _validating_plugin(mock_indigo, verdict=_DEFAULT_VERDICT):
    """A Lamplighter whose validate_config says yes and whose other
    actions do nothing."""
    if verdict is _DEFAULT_VERDICT:
        verdict = {
            "ok": True, "zones": ["Kitchen", "Hallway"],
            "enabled": ["Kitchen", "Hallway"],
        }
    plugin = _install(mock_indigo)
    plugin.executeAction = MagicMock(return_value=verdict)
    return plugin


def _prepare_update(mock_indigo, tmp_path, devices,
                    verdict=_DEFAULT_VERDICT):
    """Config on disk, a validating Lamplighter, and a device list."""
    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    _plain_dict_indigo(mock_indigo)
    plugin = _validating_plugin(mock_indigo, verdict=verdict)
    mock_indigo.devices = devices
    return config_path, plugin


def _recording_sleep(monkeypatch, on_sleep=None):
    """Replace the poll's sleep with a recorder, optionally driving the
    world forward on the Nth call. Returns the list of sleeps, so a
    test can assert the poll STOPPED rather than ran its full cap."""
    import tools.lamplighter as lamplighter_module

    calls = []

    def _fake(seconds):
        calls.append(seconds)
        if on_sleep is not None:
            on_sleep(len(calls))

    monkeypatch.setattr(lamplighter_module, "_sleep", _fake)
    return calls


class _UnreadableDevices:
    """A device collection that will not iterate -- the IOM being
    unavailable, not a house with no devices."""

    def __init__(self, message="IOM unavailable"):
        self.message = message

    def __iter__(self):
        raise RuntimeError(self.message)


class _UnreadableDevice:
    """One device whose type cannot be read; counts as an
    attr_read_error and poisons the whole observation."""

    @property
    def deviceTypeId(self):
        raise RuntimeError("stale device handle")


def _backups(tmp_path):
    return sorted(tmp_path.glob("**/*.pre-lamplighter_update_zone-*"))


def _temps(tmp_path):
    return sorted(tmp_path.glob("**/*.tmp-*"))


# ---------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------

def test_register_registers_the_whole_family(mock_indigo):
    """All eight tools register, under the lamplighter_ prefix the
    README grouping rule depends on."""
    from tools.lamplighter import register

    handler = MagicMock()
    register(handler, indigo_module=mock_indigo)
    names = {
        call.kwargs.get("name") for call in handler.register_tool.call_args_list
    }
    assert names == {
        "lamplighter_list_zones", "lamplighter_get_zone",
        "lamplighter_update_zone", "lamplighter_reset_override",
        "lamplighter_lock_zone", "lamplighter_set_enabled",
        "lamplighter_reconcile_now", "lamplighter_explain",
    }


def test_registry_registers_lamplighter_tools(mock_indigo):
    """tool_registry wires the family in, not just the module itself."""
    from tool_registry import register_all

    handler = MagicMock()
    register_all(handler, indigo_module=mock_indigo)
    names = {
        call.kwargs.get("name") for call in handler.register_tool.call_args_list
    }
    assert "lamplighter_list_zones" in names
    assert "lamplighter_explain" in names


# ---------------------------------------------------------------------
# lamplighter_list_zones
# ---------------------------------------------------------------------

def test_list_zones_joins_config_to_live_devices(mock_indigo, tmp_path):
    """A zone row carries both halves: the config the plugin reads and
    the live state only the device knows."""
    from tools.lamplighter import _list_zones_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    mock_indigo.devices = [
        _other_device(),
        _zone_device(101, "Kitchen"),
        _zone_device(102, "Hallway", on=False),
        _controller_device(),
    ]

    result = _list_zones_handler({}, mock_indigo)

    assert result["total_count"] == 2
    by_name = {z["name"]: z for z in result["zones"]}
    kitchen = by_name["Kitchen"]
    assert kitchen["lights"] == [772478931, 144694384]
    assert kitchen["presence_devices"] == [1465867145, 735515977]
    assert kitchen["hold_seconds"] == 300
    assert kitchen["lux"]["dark_below"] == 2200
    assert kitchen["override"] == {"duration_minutes": 60, "extend_minutes": 30}
    assert kitchen["periods"] == ["Overnight", "Dusk"]
    assert kitchen["device"]["id"] == 101
    assert kitchen["device"]["state"] == "vacant"
    assert kitchen["device"]["explain"] == "Kitchen: vacant in Dusk."
    assert kitchen["device"]["period"] == "Dusk"
    assert kitchen["device"]["evaluations_today"] == 12
    # Zone enable is the relay's own on/off, not a config field.
    assert by_name["Hallway"]["device"]["enabled"] is False

    assert result["controller"]["id"] == 9001
    assert result["controller"]["config_status"] == "ok"
    assert result["controller"]["zones_enabled"] == 2
    assert result["zones_without_device"] == []
    assert result["orphan_zone_devices"] == []
    assert "skipped_controller" not in result


def test_list_zones_flags_a_configured_zone_with_no_device(
        mock_indigo, tmp_path):
    """A zone Lamplighter never made a device for is a real problem;
    it must not read as a zone with nothing to report."""
    from tools.lamplighter import _list_zones_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    mock_indigo.devices = [_zone_device(101, "Kitchen"), _controller_device()]

    result = _list_zones_handler({}, mock_indigo)

    by_name = {z["name"]: z for z in result["zones"]}
    assert by_name["Hallway"]["device"] is None
    assert result["zones_without_device"] == ["Hallway"]


def test_list_zones_flags_a_device_with_no_configured_zone(
        mock_indigo, tmp_path):
    """The other direction: a zone device left behind by a deleted or
    renamed config zone, and one carrying no zone_name at all."""
    from tools.lamplighter import _list_zones_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    stray = _zone_device(103, "Conservatory")
    # A zone device made by hand with no zone_name prop at all: none of
    # the three dictionaries carries one.
    nameless = _zone_device(104, "")
    nameless.ownerProps = {}
    nameless.globalProps = {PLUGIN_ID: {}}
    mock_indigo.devices = [
        _zone_device(101, "Kitchen"), _zone_device(102, "Hallway"),
        stray, nameless, _controller_device(),
    ]

    result = _list_zones_handler({}, mock_indigo)

    orphans = {o["id"]: o for o in result["orphan_zone_devices"]}
    assert orphans[103]["zone_name"] == "Conservatory"
    assert orphans[104]["zone_name"] == ""
    assert result["zones_without_device"] == []


def test_list_zones_missing_config_raises_not_empty_result(
        mock_indigo, tmp_path):
    """Missing config -> ValueError, never {"zones": [], "total_count": 0}.
    An unusable precondition is a failed call."""
    from tools.lamplighter import _list_zones_handler

    mock_indigo.server.getInstallFolderPath.return_value = str(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        _list_zones_handler({}, mock_indigo)


def test_list_zones_corrupt_config_raises(mock_indigo, tmp_path):
    """A file that parses but is not a Lamplighter config must be
    refused, not read as a house with no zones."""
    from tools.lamplighter import _list_zones_handler

    _write_config(mock_indigo, tmp_path, {"version": 1, "rooms": []})
    with pytest.raises(ValueError, match="does not look like"):
        _list_zones_handler({}, mock_indigo)


def test_list_zones_accepts_the_starter_document(mock_indigo, tmp_path):
    """A fresh install's `{"zones": []}` is unconfigured, NOT corrupt --
    calling it broken would tell a new user their file is wrong."""
    from tools.lamplighter import _list_zones_handler

    _write_config(mock_indigo, tmp_path, {"version": 1, "zones": []})
    mock_indigo.devices = [_controller_device(states={"zones": 0})]

    result = _list_zones_handler({}, mock_indigo)
    assert result["total_count"] == 0
    assert result["zones"] == []


def test_list_zones_unreadable_device_list_raises_not_empty_join(
        mock_indigo, tmp_path):
    """If the device sweep fails, every zone would look device-less and
    every live state unknown-but-fine. That is the confident wrong
    answer; the call must fail instead."""
    from tools.lamplighter import _list_zones_handler

    _write_config(mock_indigo, tmp_path, _sample_config())

    class _Boom:
        def __iter__(self):
            raise RuntimeError("IOM unavailable")

    mock_indigo.devices = _Boom()
    with pytest.raises(ValueError, match="device list could not be read"):
        _list_zones_handler({}, mock_indigo)


def test_list_zones_counts_unreadable_devices_rather_than_dropping_them(
        mock_indigo, tmp_path):
    """One broken device must not silently shrink the answer."""
    from tools.lamplighter import _list_zones_handler

    _write_config(mock_indigo, tmp_path, _sample_config())

    class _BadDevice:
        @property
        def deviceTypeId(self):
            raise RuntimeError("stale device handle")

    mock_indigo.devices = [
        _zone_device(101, "Kitchen"), _BadDevice(), _controller_device(),
    ]
    result = _list_zones_handler({}, mock_indigo)
    assert result["attr_read_errors"] == 1


def test_list_zones_reports_a_missing_controller_rather_than_pretending(
        mock_indigo, tmp_path):
    from tools.lamplighter import _list_zones_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    mock_indigo.devices = [_zone_device(101, "Kitchen")]

    result = _list_zones_handler({}, mock_indigo)
    assert result["controller"] is None
    assert "no lamplighter_controller device" in result["skipped_controller"]


def test_list_zones_refuses_to_guess_between_two_controllers(
        mock_indigo, tmp_path):
    from tools.lamplighter import _list_zones_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    mock_indigo.devices = [
        _controller_device(9001), _controller_device(9002),
    ]
    result = _list_zones_handler({}, mock_indigo)
    assert result["controller"] is None
    assert "9001" in result["skipped_controller"]
    assert "9002" in result["skipped_controller"]


def test_list_zones_rejects_unknown_args(mock_indigo, tmp_path):
    from tools.lamplighter import _list_zones_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    with pytest.raises(ValueError, match="unknown argument"):
        _list_zones_handler({"bogus": 1}, mock_indigo)


# ---------------------------------------------------------------------
# how a zone device is matched to its zone.
#
# Found by live test on jarvis, 2026-09-05, running in-process under
# the Indigo plugin host: the join read `dev.pluginProps`, which Indigo
# scopes to the CALLING plugin, so it was empty for every Lamplighter
# device and the whole join silently collapsed -- zones reported
# device-less, devices reported orphaned, and update_zone unable to
# ever see a reload. The unit tests passed throughout, because the fake
# put the zone name in pluginProps. These four pin the fix.
# ---------------------------------------------------------------------

def test_zone_name_is_read_from_owner_props_not_plugin_props(
        mock_indigo, tmp_path):
    """Kills the mutation "read pluginProps".

    This is the exact live shape: a Lamplighter zone device seen from
    lite's process has an EMPTY pluginProps and its zone name in
    ownerProps. Reading pluginProps makes both configured zones look
    device-less and both devices look orphaned -- the join collapses
    entirely -- so this fails loudly under the mutation.
    """
    from tools.lamplighter import _list_zones_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    kitchen_dev = _zone_device(101, "Kitchen")
    assert kitchen_dev.pluginProps == {}, "the fake must mirror the live shape"
    mock_indigo.devices = [
        kitchen_dev, _zone_device(102, "Hallway"), _controller_device(),
    ]

    result = _list_zones_handler({}, mock_indigo)

    assert result["zones_without_device"] == []
    assert result["orphan_zone_devices"] == []
    by_name = {z["name"]: z for z in result["zones"]}
    assert by_name["Kitchen"]["device"]["id"] == 101
    assert by_name["Hallway"]["device"]["id"] == 102


def test_zone_name_ignores_a_decoy_in_caller_scoped_plugin_props(
        mock_indigo, tmp_path):
    """The sharper half of the same mutation: reading pluginProps must
    not merely fail to find the zone, it must not find the WRONG one.

    The decoy is deliberately unrealistic (lite writes no props onto
    Lamplighter's devices, so live pluginProps is empty) -- its job is
    to make "read pluginProps" produce a visibly wrong join rather than
    an empty one, so the mutation cannot hide behind a device simply
    being absent from the fixture.
    """
    from tools.lamplighter import _get_zone_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    mock_indigo.devices = [
        _zone_device(101, "Kitchen", decoy_plugin_props_zone="Hallway"),
        _controller_device(),
    ]

    result = _get_zone_handler({"zone": "Kitchen"}, mock_indigo)
    assert result["device"] is not None, result.get("skipped_device")
    assert result["device"]["id"] == 101
    assert result["device"]["zone_name"] == "Kitchen"

    # ...and the decoy must not have stolen Hallway's slot either.
    hallway = _get_zone_handler({"zone": "Hallway"}, mock_indigo)
    assert hallway["device"] is None


def test_zone_name_falls_back_to_global_props_without_owner_props(
        mock_indigo, tmp_path):
    """ownerProps is API 1.20. globalProps[<plugin id>] is the same
    data by the long route and has been readable by anyone since 1.0,
    so an older server must still join."""
    from tools.lamplighter import _list_zones_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    dev = _zone_device(101, "Kitchen")
    del dev.ownerProps  # pre-1.20: the attribute does not exist at all
    mock_indigo.devices = [dev, _controller_device()]

    result = _list_zones_handler({}, mock_indigo)
    by_name = {z["name"]: z for z in result["zones"]}
    assert by_name["Kitchen"]["device"]["id"] == 101
    assert result["orphan_zone_devices"] == []


def test_zone_name_ignores_a_non_string_prop_value(mock_indigo, tmp_path):
    """A zone name must be a real string, never str()-ed from whatever
    the attribute happened to hold.

    This is the guard on the failure mode that let the pluginProps bug
    through review: a MagicMock (or any object) coerced with str()
    yields a plausible-looking non-empty name that matches no zone, so
    the device is quietly filed as an orphan instead of the read being
    recognised as broken.
    """
    from tools.lamplighter import _device_zone_name

    dev = _zone_device(101, "Kitchen")
    dev.ownerProps = {"zone_name": object()}
    dev.globalProps = {PLUGIN_ID: {"zone_name": None}}

    assert _device_zone_name(dev) == ""


# ---------------------------------------------------------------------
# lamplighter_get_zone
# ---------------------------------------------------------------------

def test_get_zone_returns_config_states_and_explain(mock_indigo, tmp_path):
    """The whole config block, every published state, and the live
    explain line -- the three things that answer "what is this zone
    set to and what is it doing"."""
    from tools.lamplighter import _get_zone_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    mock_indigo.devices = [_zone_device(101, "Kitchen"), _controller_device()]

    result = _get_zone_handler({"zone": "Kitchen"}, mock_indigo)

    assert result["config"]["periods"][1]["levels"] == {
        "772478931": 50, "144694384": 100,
    }
    assert result["config"]["hold_seconds"] == 300
    assert result["device"]["states"]["dark"] is True
    assert result["device"]["states"]["desired_summary"] == "off"
    assert result["explain"] == "Kitchen: vacant in Dusk."


def test_get_zone_unknown_zone_names_it_and_the_known_ones(
        mock_indigo, tmp_path):
    from tools.lamplighter import _get_zone_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    with pytest.raises(ValueError, match="no Lamplighter zone named 'Attic'"):
        _get_zone_handler({"zone": "Attic"}, mock_indigo)


def test_get_zone_says_so_when_the_zone_has_no_device(mock_indigo, tmp_path):
    """explain: None must be distinguishable from "the zone has nothing
    to say"."""
    from tools.lamplighter import _get_zone_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    mock_indigo.devices = [_controller_device()]

    result = _get_zone_handler({"zone": "Kitchen"}, mock_indigo)
    assert result["device"] is None
    assert result["explain"] is None
    assert "live state and explain line are unavailable" in (
        " ".join(result["skipped_device"].split())
    )


def test_get_zone_distinguishes_an_unnamed_device_from_no_device(
        mock_indigo, tmp_path):
    """Kills the mutation "report skipped_device and stop there".

    "This zone has no device" and "there IS a zone device but its
    zone_name could not be read" are different problems with different
    fixes -- create the device, versus set the prop -- and the unnamed
    device may well BE this zone's. skipped_device alone cannot tell
    them apart, so a caller acting on it would create a duplicate.
    """
    from tools.lamplighter import _get_zone_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    nameless = _zone_device(104, "", name="Kitchen Lights")
    nameless.ownerProps = {}
    nameless.globalProps = {PLUGIN_ID: {}}
    mock_indigo.devices = [nameless, _controller_device()]

    result = _get_zone_handler({"zone": "Kitchen"}, mock_indigo)

    assert result["device"] is None
    assert "skipped_device" in result
    assert result["unnamed_zone_devices"] == [
        {"id": 104, "name": "Kitchen Lights"},
    ]


def test_get_zone_omits_unnamed_devices_when_there_are_none(
        mock_indigo, tmp_path):
    """The key must be absent, not an empty list -- an empty list reads
    as "checked, none found" only if it is always present, and a noisy
    always-present key trains callers to ignore it."""
    from tools.lamplighter import _get_zone_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    mock_indigo.devices = [_zone_device(101, "Kitchen"), _controller_device()]

    result = _get_zone_handler({"zone": "Kitchen"}, mock_indigo)
    assert "unnamed_zone_devices" not in result


def test_get_zone_requires_a_zone(mock_indigo, tmp_path):
    from tools.lamplighter import _get_zone_handler

    mock_indigo.server.getInstallFolderPath.side_effect = _MustNotBeCalled(
        "argument validation must happen before any file read"
    )
    with pytest.raises(ValueError, match="zone must be a non-empty string"):
        _get_zone_handler({}, mock_indigo)


# ---------------------------------------------------------------------
# lamplighter_update_zone -- happy path
# ---------------------------------------------------------------------

def test_update_zone_validates_the_whole_document_then_writes(
        mock_indigo, tmp_path):
    """The patch is applied, the WHOLE proposed document (not just the
    zone) goes to Lamplighter's validator, and only then is the file
    replaced."""
    from tools.lamplighter import _update_zone_handler

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    _plain_dict_indigo(mock_indigo)
    plugin = _validating_plugin(mock_indigo)
    mock_indigo.devices = [_zone_device(101, "Kitchen"), _controller_device()]

    result = _update_zone_handler(
        {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
    )

    plugin.executeAction.assert_called_once()
    args, kwargs = plugin.executeAction.call_args
    assert args[0] == "validate_config"
    assert kwargs["waitUntilDone"] is True
    sent = json.loads(kwargs["props"]["config_json"])
    assert kwargs["props"]["__is_indigo_dict__"] is True
    # The whole document, not the one zone.
    assert [z["name"] for z in sent["zones"]] == ["Kitchen", "Hallway"]
    assert sent["zones"][0]["hold_seconds"] == 600
    assert sent["echo_window_seconds"] == 15

    written = json.loads(config_path.read_text())
    assert written["zones"][0]["hold_seconds"] == 600
    # Untouched keys survive the merge patch.
    assert written["zones"][0]["lights"] == [772478931, 144694384]
    assert result["written"] == str(config_path)
    assert result["created"] is False
    assert result["validated_zones"] == ["Kitchen", "Hallway"]


def test_update_zone_backs_the_old_file_up_before_replacing_it(
        mock_indigo, tmp_path):
    """The backup must hold the PREVIOUS bytes, so the write is
    reversible by copying one file back."""
    from tools.lamplighter import _update_zone_handler

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    original_bytes = config_path.read_bytes()
    _plain_dict_indigo(mock_indigo)
    _validating_plugin(mock_indigo)
    mock_indigo.devices = [_controller_device()]

    result = _update_zone_handler(
        {"zone": "Kitchen", "patch": {"hold_seconds": 900}}, mock_indigo
    )

    backups = _backups(tmp_path)
    assert len(backups) == 1
    assert backups[0].read_bytes() == original_bytes
    assert result["backup"] == str(backups[0])
    assert config_path.read_bytes() != original_bytes
    # os.replace, not a partial write left lying about.
    assert _temps(tmp_path) == []


def test_update_zone_null_in_patch_removes_the_key(mock_indigo, tmp_path):
    """RFC 7386: null means delete, never "set to null"."""
    from tools.lamplighter import _update_zone_handler

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    _plain_dict_indigo(mock_indigo)
    _validating_plugin(mock_indigo)
    mock_indigo.devices = [_controller_device()]

    _update_zone_handler(
        {"zone": "Kitchen", "patch": {"override": {"extend_minutes": None}}},
        mock_indigo,
    )

    written = json.loads(config_path.read_text())
    assert written["zones"][0]["override"] == {"duration_minutes": 60}


def test_update_zone_replaces_arrays_wholesale(mock_indigo, tmp_path):
    """Merge-patch arrays REPLACE. Appending instead would quietly add
    lights to a zone the author meant to shrink."""
    from tools.lamplighter import _update_zone_handler

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    _plain_dict_indigo(mock_indigo)
    _validating_plugin(mock_indigo)
    mock_indigo.devices = [_controller_device()]

    _update_zone_handler(
        {"zone": "Kitchen", "patch": {"lights": [772478931]}}, mock_indigo
    )

    written = json.loads(config_path.read_text())
    assert written["zones"][0]["lights"] == [772478931]


def test_update_zone_creates_an_unknown_zone_from_a_complete_object(
        mock_indigo, tmp_path):
    from tools.lamplighter import _update_zone_handler

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    _plain_dict_indigo(mock_indigo)
    _validating_plugin(mock_indigo, verdict={
        "ok": True, "zones": ["Kitchen", "Hallway", "Study"],
        "enabled": ["Kitchen", "Hallway"],
    })
    mock_indigo.devices = [_controller_device()]

    result = _update_zone_handler({
        "zone": "Study",
        "patch": {
            "enabled": False,
            "presence_devices": [111],
            "hold_seconds": 240,
            "lux": None,
            "lights": [222],
            "periods": [{"name": "Evening", "from": "17:00", "to": "23:00",
                         "mode": "on_and_off", "levels": {"222": 40}}],
        },
    }, mock_indigo)

    assert result["created"] is True
    written = json.loads(config_path.read_text())
    assert [z["name"] for z in written["zones"]] == [
        "Kitchen", "Hallway", "Study",
    ]
    study = written["zones"][2]
    assert study["hold_seconds"] == 240
    assert study["name"] == "Study"


def test_update_zone_create_keeps_an_explicit_null(mock_indigo, tmp_path):
    """`lux: null` in a CREATE is a stated "no daylight gate", not a key
    to delete. Lamplighter's schema requires `lux` to be PRESENT and
    allows it to be null, so applying RFC 7386's remove-on-null here
    would drop exactly the key the author set and fail validation for a
    mistake they did not make. The create path takes the object
    verbatim; only the EDIT path is a merge patch.

    Asserted on the bytes that reach validate_config as well as on the
    file, because a document that loses the key never gets as far as
    being written.
    """
    from tools.lamplighter import _update_zone_handler

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    _plain_dict_indigo(mock_indigo)
    plugin = _validating_plugin(mock_indigo, verdict={
        "ok": True, "zones": ["Kitchen", "Hallway", "Study"], "enabled": [],
    })
    mock_indigo.devices = [_controller_device()]

    _update_zone_handler({
        "zone": "Study",
        "patch": {"presence_devices": [111], "hold_seconds": 240,
                  "lux": None, "lights": [222],
                  "periods": [{"name": "Evening", "from": "17:00",
                               "to": "23:00", "mode": "on_and_off",
                               "levels": {"222": 40}}]},
    }, mock_indigo)

    _args, kwargs = plugin.executeAction.call_args
    sent = json.loads(kwargs["props"]["config_json"])
    assert "lux" in sent["zones"][2], sent["zones"][2]
    assert sent["zones"][2]["lux"] is None

    written = json.loads(config_path.read_text())
    assert "lux" in written["zones"][2]
    assert written["zones"][2]["lux"] is None


def test_update_zone_create_rejects_a_patch_naming_a_different_zone(
        mock_indigo, tmp_path):
    """Creating "Study" from a block called "Studio" would silently make
    the wrong zone and leave lamplighter_get_zone('Study') broken."""
    from tools.lamplighter import _update_zone_handler

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    original_bytes = config_path.read_bytes()
    _plain_dict_indigo(mock_indigo)
    mock_indigo.server.getPlugin.side_effect = _MustNotBeCalled(
        "must refuse before asking Lamplighter to validate"
    )

    with pytest.raises(ValueError, match="but the patch names it 'Studio'"):
        _update_zone_handler(
            {"zone": "Study", "patch": {"name": "Studio", "lights": [1]}},
            mock_indigo,
        )

    assert config_path.read_bytes() == original_bytes
    assert _backups(tmp_path) == []


def test_update_zone_warns_when_the_patch_renames_a_zone(
        mock_indigo, tmp_path):
    """A rename resets Lamplighter's persisted state (it is keyed by
    name) -- allowed, but never silent."""
    from tools.lamplighter import _update_zone_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    _plain_dict_indigo(mock_indigo)
    _validating_plugin(mock_indigo, verdict={
        "ok": True, "zones": ["Kitchenette", "Hallway"], "enabled": [],
    })
    mock_indigo.devices = [_controller_device()]

    result = _update_zone_handler(
        {"zone": "Kitchen", "patch": {"name": "Kitchenette"}}, mock_indigo
    )
    assert result["zone"] == "Kitchenette"
    assert any("persisted state" in w for w in result["warnings"])


# ---------------------------------------------------------------------
# lamplighter_update_zone -- refusals. Every one of these asserts the
# file bytes are unchanged AND no backup exists.
# ---------------------------------------------------------------------

def test_update_zone_validation_failure_writes_nothing(mock_indigo, tmp_path):
    """Lamplighter's "no" must reach the caller with its JSON pointer
    intact, and must leave the file byte-identical -- not "written but
    invalid"."""
    from tools.lamplighter import _update_zone_handler

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    original_bytes = config_path.read_bytes()
    _plain_dict_indigo(mock_indigo)
    _validating_plugin(mock_indigo, verdict={
        "ok": False, "path": "zones/0/hold_seconds",
        "message": "hold_seconds must be an integer between 0 and 86400",
    })

    with pytest.raises(ValueError) as exc:
        _update_zone_handler(
            {"zone": "Kitchen", "patch": {"hold_seconds": "ages"}}, mock_indigo
        )
    text = str(exc.value)
    assert "zones/0/hold_seconds" in text
    assert "hold_seconds must be an integer" in text
    assert "Nothing was written" in text

    assert config_path.read_bytes() == original_bytes
    assert _backups(tmp_path) == []
    assert _temps(tmp_path) == []


def test_update_zone_unusable_validation_answer_writes_nothing(
        mock_indigo, tmp_path):
    """A validator that answers nothing has not said "ok". Treating a
    None as a pass would install an unchecked config."""
    from tools.lamplighter import _update_zone_handler

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    original_bytes = config_path.read_bytes()
    _plain_dict_indigo(mock_indigo)
    _validating_plugin(mock_indigo, verdict=None)

    with pytest.raises(ValueError, match="no usable answer"):
        _update_zone_handler(
            {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
        )

    assert config_path.read_bytes() == original_bytes
    assert _backups(tmp_path) == []


def test_update_zone_refuses_when_the_plugin_is_disabled(
        mock_indigo, tmp_path):
    """A config the plugin cannot validate is not a config to install."""
    from tools.lamplighter import _update_zone_handler

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    original_bytes = config_path.read_bytes()
    _plain_dict_indigo(mock_indigo)
    plugin = _install(mock_indigo, enabled=False)
    plugin.executeAction.side_effect = _MustNotBeCalled(
        "a disabled plugin must be refused before dispatch"
    )

    with pytest.raises(ValueError, match="not enabled/running"):
        _update_zone_handler(
            {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
        )

    assert config_path.read_bytes() == original_bytes
    assert _backups(tmp_path) == []


def test_update_zone_refuses_when_the_file_moved_between_read_and_write(
        mock_indigo, tmp_path, monkeypatch):
    """Somebody saving the file while this call is in flight must not
    have their edit silently overwritten."""
    import tools.lamplighter as lamplighter_module

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    original_bytes = config_path.read_bytes()
    _plain_dict_indigo(mock_indigo)
    _validating_plugin(mock_indigo)

    real_stat = lamplighter_module._stat(str(config_path))

    class _FakeStat:
        def __init__(self, mtime_ns, size):
            self.st_mtime_ns = mtime_ns
            self.st_size = size

    moved = _FakeStat(real_stat.st_mtime_ns + 1_000_000_000, real_stat.st_size)
    calls = {"n": 0}

    def fake_stat(path):
        calls["n"] += 1
        return real_stat if calls["n"] == 1 else moved

    monkeypatch.setattr(lamplighter_module, "_stat", fake_stat)

    with pytest.raises(ValueError, match="changed on disk"):
        lamplighter_module._update_zone_handler(
            {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
        )

    assert calls["n"] == 2
    assert config_path.read_bytes() == original_bytes
    assert _backups(tmp_path) == []


def test_update_zone_backup_failure_leaves_the_live_config_alone(
        mock_indigo, tmp_path, monkeypatch):
    """A disk-full backup must raise the module's friendly error, not a
    raw OSError, and must not go on to replace the config."""
    import tools.lamplighter as lamplighter_module

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    original_bytes = config_path.read_bytes()
    _plain_dict_indigo(mock_indigo)
    _validating_plugin(mock_indigo)
    real_open = open

    def fake_open(file, *args, **kwargs):
        if ".pre-lamplighter_update_zone-" in str(file):
            raise OSError("disk full")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(lamplighter_module, "open", fake_open, raising=False)

    with pytest.raises(ValueError, match="backup"):
        lamplighter_module._update_zone_handler(
            {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
        )

    assert config_path.read_bytes() == original_bytes
    assert _temps(tmp_path) == []


def test_update_zone_empty_patch_is_refused(mock_indigo, tmp_path):
    """An empty patch would rewrite the file (new mtime, a spurious
    hot reload) for no change at all."""
    from tools.lamplighter import _update_zone_handler

    config_path = _write_config(mock_indigo, tmp_path, _sample_config())
    original_bytes = config_path.read_bytes()
    mock_indigo.server.getPlugin.side_effect = _MustNotBeCalled(
        "an empty patch must be refused during argument validation"
    )

    with pytest.raises(ValueError, match="patch is empty"):
        _update_zone_handler({"zone": "Kitchen", "patch": {}}, mock_indigo)

    assert config_path.read_bytes() == original_bytes
    assert _backups(tmp_path) == []


def test_update_zone_non_object_patch_is_refused_before_any_read(
        mock_indigo):
    """Cheap argument validation runs before indigo is touched at all."""
    from tools.lamplighter import _update_zone_handler

    mock_indigo.server.getInstallFolderPath.side_effect = _MustNotBeCalled(
        "argument validation must happen before any file read"
    )
    with pytest.raises(ValueError, match="patch must be a JSON object"):
        _update_zone_handler(
            {"zone": "Kitchen", "patch": [{"hold_seconds": 600}]}, mock_indigo
        )


# ---------------------------------------------------------------------
# lamplighter_update_zone -- reload detection
# ---------------------------------------------------------------------

def test_update_zone_reports_reload_evidence_when_it_sees_it(
        mock_indigo, tmp_path, monkeypatch):
    """`reloaded: true` requires STRONG evidence and must say which.

    Kills the mutation "treat any observable change as a reload":
    config_loaded_at is stamped only on a successful load, so it is the
    one signal that distinguishes a reload from a load that was
    attempted and refused.
    """
    import tools.lamplighter as lamplighter_module

    controller = _controller_device()
    _prepare_update(
        mock_indigo, tmp_path, [_zone_device(101, "Kitchen"), controller]
    )

    def _reload_on_first_sleep(_n):
        controller.states["config_loaded_at"] = "2026-09-05T16:47:12"

    _recording_sleep(monkeypatch, _reload_on_first_sleep)

    result = lamplighter_module._update_zone_handler(
        {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
    )
    assert result["reloaded"] is True
    assert result["reload_evidence_strength"] == "strong"
    assert "config_loaded_at moved" in result["reload_evidence"]
    assert "reload_note" not in result
    assert "reload_check_skipped" not in result


def test_update_zone_returns_as_soon_as_strong_evidence_appears(
        mock_indigo, tmp_path, monkeypatch):
    """Kills the mutation "poll the full 10s regardless".

    Once config_loaded_at has moved the question is answered and a live
    caller is waiting; sitting out the remaining nineteen ticks buys
    nothing. The sleep count IS the assertion -- it is not observable
    from the payload.
    """
    import tools.lamplighter as lamplighter_module

    controller = _controller_device()
    _prepare_update(
        mock_indigo, tmp_path, [_zone_device(101, "Kitchen"), controller]
    )

    def _reload_immediately(_n):
        controller.states["config_loaded_at"] = "2026-09-05T16:47:12"

    sleeps = _recording_sleep(monkeypatch, _reload_immediately)

    result = lamplighter_module._update_zone_handler(
        {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
    )
    assert result["reloaded"] is True
    assert len(sleeps) == 1, (
        "the poll must return on the first strong observation, not run "
        f"its whole cap; it slept {len(sleeps)} times"
    )


def test_update_zone_explain_change_alone_is_weak_evidence(
        mock_indigo, tmp_path, monkeypatch):
    """Kills the mutation "an explain change means it reloaded".

    explain is rewritten on every re-plan, and a re-plan happens on any
    input edge -- somebody walking past a presence sensor mid-call
    produces exactly this. It is reported, but it is not a reload.
    """
    import tools.lamplighter as lamplighter_module

    zone_dev = _zone_device(101, "Kitchen")
    _prepare_update(
        mock_indigo, tmp_path, [zone_dev, _controller_device()]
    )

    def _replan_on_first_sleep(_n):
        zone_dev.states["explain"] = "Kitchen: occupied in Dusk."

    _recording_sleep(monkeypatch, _replan_on_first_sleep)

    result = lamplighter_module._update_zone_handler(
        {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
    )
    assert result["reloaded"] is False
    assert result["reload_evidence_strength"] == "weak"
    assert "explain line changed" in result["reload_evidence"]
    assert "only weak evidence" in result["reload_note"]


def test_update_zone_config_status_change_alone_is_weak_evidence(
        mock_indigo, tmp_path, monkeypatch):
    """config_status moves when a load is ATTEMPTED -- including one
    that was attempted and REFUSED, which is the opposite of what the
    caller wants to hear. Weak, never strong."""
    import tools.lamplighter as lamplighter_module

    controller = _controller_device()
    _prepare_update(
        mock_indigo, tmp_path, [_zone_device(101, "Kitchen"), controller]
    )

    def _status_moves(_n):
        controller.states["config_status"] = (
            "lamplighter.json is invalid at zones/0/lux: bad"
        )

    _recording_sleep(monkeypatch, _status_moves)

    result = lamplighter_module._update_zone_handler(
        {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
    )
    assert result["reloaded"] is False
    assert result["reload_evidence_strength"] == "weak"
    assert "config_status changed" in result["reload_evidence"]


def test_update_zone_weak_evidence_does_not_end_the_wait(
        mock_indigo, tmp_path, monkeypatch):
    """Kills the mutation "return on the first evidence of any kind".

    A sensor tripping in the first half second must not stop the poll
    before the real reload lands three seconds later.
    """
    import tools.lamplighter as lamplighter_module

    zone_dev = _zone_device(101, "Kitchen")
    controller = _controller_device()
    _prepare_update(mock_indigo, tmp_path, [zone_dev, controller])

    def _weak_then_strong(n):
        if n == 1:
            zone_dev.states["explain"] = "Kitchen: occupied in Dusk."
        if n == 3:
            controller.states["config_loaded_at"] = "2026-09-05T16:47:35"

    sleeps = _recording_sleep(monkeypatch, _weak_then_strong)

    result = lamplighter_module._update_zone_handler(
        {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
    )
    assert result["reloaded"] is True
    assert result["reload_evidence_strength"] == "strong"
    assert len(sleeps) == 3


def test_update_zone_config_zone_count_change_is_strong_evidence(
        mock_indigo, tmp_path, monkeypatch):
    """Only the loader writes the configured-zone count."""
    import tools.lamplighter as lamplighter_module

    controller = _controller_device()
    _prepare_update(
        mock_indigo, tmp_path, [_zone_device(101, "Kitchen"), controller]
    )

    def _count_moves(_n):
        controller.states["config_zone_count"] = 3

    _recording_sleep(monkeypatch, _count_moves)

    result = lamplighter_module._update_zone_handler(
        {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
    )
    assert result["reloaded"] is True
    assert result["reload_evidence_strength"] == "strong"
    assert "config_zone_count changed" in result["reload_evidence"]


# ---------------------------------------------------------------------
# lamplighter_update_zone -- a failed observation is never evidence
# ---------------------------------------------------------------------

def test_update_zone_unreadable_baseline_skips_the_check_and_never_sleeps(
        mock_indigo, tmp_path, monkeypatch):
    """Kills the mutation "poll anyway when there is no baseline".

    With no baseline there is nothing to compare against, so ten
    seconds of polling proves nothing -- and `reloaded: false` must be
    left UNCLAIMED, with no reassuring "nothing changed" note, because
    nothing was looked at. The reason must carry the swallowed error
    text or the caller cannot tell a broken IOM from a quiet one.
    """
    import tools.lamplighter as lamplighter_module

    _prepare_update(
        mock_indigo, tmp_path, _UnreadableDevices("IOM went away")
    )
    sleeps = _recording_sleep(monkeypatch)

    result = lamplighter_module._update_zone_handler(
        {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
    )
    assert result["status"] == "ok"
    assert result["reloaded"] is False
    assert "IOM went away" in result["reload_check_skipped"]
    assert "reload_note" not in result, (
        "a benign note would describe an observation that never happened"
    )
    assert sleeps == [], "nothing to compare against: do not wait"


def test_update_zone_attr_read_errors_are_not_an_observation(
        mock_indigo, tmp_path, monkeypatch):
    """Kills the mutation "compare a half-read sweep anyway".

    The device that failed to read may be the very controller the
    comparison rests on, so a sweep with attr_read_errors can neither
    confirm nor deny. The distinct note must say the reload was not
    OBSERVED (rather than that nothing changed), and the error count
    must reach the payload.
    """
    import tools.lamplighter as lamplighter_module

    devices = [_zone_device(101, "Kitchen"), _controller_device()]
    _prepare_update(mock_indigo, tmp_path, devices)

    def _break_a_device(n):
        if n == 1:
            devices.append(_UnreadableDevice())

    _recording_sleep(monkeypatch, _break_a_device)

    result = lamplighter_module._update_zone_handler(
        {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
    )
    assert result["reloaded"] is False
    assert result["reload_evidence"] is None
    assert result["attr_read_errors"] == 1
    assert "could NOT BE OBSERVED" in result["reload_note"]
    assert "not looked at" in result["reload_note"]
    assert "reload_check_skipped" not in result


def test_update_zone_controller_vanishing_is_not_an_observation(
        mock_indigo, tmp_path, monkeypatch):
    """Without a single controller, config_loaded_at cannot be read at
    all, so no post-write poll is an observation -- and the note must
    say so rather than claiming nothing changed."""
    import tools.lamplighter as lamplighter_module

    devices = [_zone_device(101, "Kitchen"), _controller_device()]
    _prepare_update(mock_indigo, tmp_path, devices)

    def _remove_controller(n):
        if n == 1:
            devices[:] = [_zone_device(101, "Kitchen")]

    _recording_sleep(monkeypatch, _remove_controller)

    result = lamplighter_module._update_zone_handler(
        {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
    )
    assert result["reloaded"] is False
    assert "could NOT BE OBSERVED" in result["reload_note"]
    assert "no lamplighter_controller device" in result["reload_note"]


# ---------------------------------------------------------------------
# lamplighter_update_zone -- anything after os.replace
# ---------------------------------------------------------------------

def test_update_zone_post_write_fault_says_the_file_was_written(
        mock_indigo, tmp_path, monkeypatch):
    """Kills the mutation "let a post-write exception escape unwrapped".

    Past os.replace the write has SUCCEEDED. A raise that reaches the
    caller looking like a failed call invites the obvious response --
    retry -- which re-applies a patch that already landed. Both paths
    must be in the text so the edit can be found and undone.
    """
    import tools.lamplighter as lamplighter_module

    config_path, _plugin = _prepare_update(
        mock_indigo, tmp_path, [_controller_device()]
    )

    def _boom(*_a, **_k):
        raise RuntimeError("device list exploded after the write")

    monkeypatch.setattr(lamplighter_module, "_wait_for_reload", _boom)

    with pytest.raises(RuntimeError) as exc:
        lamplighter_module._update_zone_handler(
            {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
        )
    text = str(exc.value)
    assert "WAS written" in text
    assert str(config_path) in text
    assert str(_backups(tmp_path)[0]) in text
    assert "must NOT be blindly re-applied" in text
    assert not isinstance(exc.value, (ValueError, TypeError))

    # ...and the write really did land, which is why the wording matters.
    written = json.loads(config_path.read_text())
    assert written["zones"][0]["hold_seconds"] == 600


def test_update_zone_validation_refusal_without_a_message_says_so(
        mock_indigo, tmp_path):
    """An ok:false carrying no message must not render as the word
    "None", which reads like a reason and is not one."""
    from tools.lamplighter import _update_zone_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    _plain_dict_indigo(mock_indigo)
    _validating_plugin(mock_indigo, verdict={"ok": False})

    with pytest.raises(ValueError) as exc:
        _update_zone_handler(
            {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
        )
    text = str(exc.value)
    assert "ok:false with no message" in text
    assert ": None." not in text


def test_update_zone_reload_false_is_not_claimed_as_failure(
        mock_indigo, tmp_path):
    """No observable change is NOT proof the plugin ignored the write --
    an edit that changes nothing a device publishes produces no signal.
    The payload must say so rather than implying the write failed."""
    from tools.lamplighter import _update_zone_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    _plain_dict_indigo(mock_indigo)
    _validating_plugin(mock_indigo)
    mock_indigo.devices = [_zone_device(101, "Kitchen"), _controller_device()]

    result = _update_zone_handler(
        {"zone": "Kitchen", "patch": {"hold_seconds": 600}}, mock_indigo
    )
    assert result["reloaded"] is False
    assert result["reload_evidence"] is None
    assert "NOT evidence" in result["reload_note"]
    assert result["status"] == "ok"


def test_update_zone_new_zone_device_appearing_counts_as_reload(
        mock_indigo, tmp_path, monkeypatch):
    """For a created zone the device does not exist yet, so its
    appearance is the signal -- not a config_status that never moves."""
    import tools.lamplighter as lamplighter_module

    _write_config(mock_indigo, tmp_path, _sample_config())
    _plain_dict_indigo(mock_indigo)
    _validating_plugin(mock_indigo, verdict={
        "ok": True, "zones": ["Kitchen", "Hallway", "Study"], "enabled": [],
    })
    devices = [_controller_device()]
    mock_indigo.devices = devices

    def _create_device_on_sleep(_seconds):
        if len(devices) == 1:
            devices.append(_zone_device(105, "Study"))

    monkeypatch.setattr(lamplighter_module, "_sleep", _create_device_on_sleep)

    result = lamplighter_module._update_zone_handler({
        "zone": "Study",
        "patch": {"presence_devices": [111], "hold_seconds": 240,
                  "lux": None, "lights": [222],
                  "periods": [{"name": "Evening", "from": "17:00",
                               "to": "23:00", "mode": "on_and_off",
                               "levels": {"222": 40}}]},
    }, mock_indigo)

    assert result["reloaded"] is True
    assert "device appeared" in result["reload_evidence"]


# ---------------------------------------------------------------------
# lamplighter_reset_override
# ---------------------------------------------------------------------

def test_reset_override_all_zones_uses_lamplighters_own_all_value(
        mock_indigo, tmp_path):
    """"All zones" is the literal "__all__" Lamplighter's picker uses;
    an empty string or the word "all" would be an unknown zone name and
    a silent no-op. The config is not read at all for this path."""
    from tools.lamplighter import _reset_override_handler

    _plain_dict_indigo(mock_indigo)
    plugin = _install(mock_indigo)
    mock_indigo.server.getInstallFolderPath.side_effect = _MustNotBeCalled(
        "the all-zones path needs no config read"
    )

    result = _reset_override_handler({}, mock_indigo)

    plugin.executeAction.assert_called_once_with(
        "reset_override",
        props=_wrapped({"zone_name": "__all__"}),
        waitUntilDone=True,
    )
    assert result["scope"] == "all zones"
    assert result["zone"] is None


def test_reset_override_one_zone_validates_then_dispatches(
        mock_indigo, tmp_path):
    from tools.lamplighter import _reset_override_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    _plain_dict_indigo(mock_indigo)
    plugin = _install(mock_indigo)

    result = _reset_override_handler({"zone": "Hallway"}, mock_indigo)

    plugin.executeAction.assert_called_once_with(
        "reset_override",
        props=_wrapped({"zone_name": "Hallway"}),
        waitUntilDone=True,
    )
    assert result["zone"] == "Hallway"


def test_reset_override_unknown_zone_never_reaches_the_plugin(
        mock_indigo, tmp_path):
    """Lamplighter's action silently no-ops on a name it does not know,
    so this validation is the only thing between "no such zone" and a
    success that did nothing."""
    from tools.lamplighter import _reset_override_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    mock_indigo.server.getPlugin.side_effect = _MustNotBeCalled(
        "an unknown zone must be refused before dispatch"
    )
    with pytest.raises(ValueError, match="no Lamplighter zone named 'Attic'"):
        _reset_override_handler({"zone": "Attic"}, mock_indigo)


# ---------------------------------------------------------------------
# lamplighter_lock_zone
# ---------------------------------------------------------------------

def test_lock_zone_dispatches_with_the_zone_name(mock_indigo, tmp_path):
    from tools.lamplighter import _lock_zone_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    _plain_dict_indigo(mock_indigo)
    plugin = _install(mock_indigo)

    result = _lock_zone_handler({"zone": "Kitchen"}, mock_indigo)

    plugin.executeAction.assert_called_once_with(
        "lock_zone",
        props=_wrapped({"zone_name": "Kitchen"}),
        waitUntilDone=True,
    )
    assert result["zone"] == "Kitchen"


def test_lock_zone_refuses_a_zone_that_never_takes_overrides(
        mock_indigo, tmp_path):
    """Hallway has override.enabled false: Lamplighter would log a
    warning and lock nothing. Readable from the config we already hold,
    so it becomes a failed call rather than a quiet no-op."""
    from tools.lamplighter import _lock_zone_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    mock_indigo.server.getPlugin.side_effect = _MustNotBeCalled(
        "a never-lock zone must be refused before dispatch"
    )
    with pytest.raises(ValueError, match="override.enabled set to false"):
        _lock_zone_handler({"zone": "Hallway"}, mock_indigo)


def test_lock_zone_unknown_zone_never_reaches_the_plugin(
        mock_indigo, tmp_path):
    from tools.lamplighter import _lock_zone_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    mock_indigo.server.getPlugin.side_effect = _MustNotBeCalled(
        "an unknown zone must be refused before dispatch"
    )
    with pytest.raises(ValueError, match="no Lamplighter zone named 'Attic'"):
        _lock_zone_handler({"zone": "Attic"}, mock_indigo)


# ---------------------------------------------------------------------
# lamplighter_set_enabled
# ---------------------------------------------------------------------

def test_set_enabled_zone_sends_the_string_the_action_reads(
        mock_indigo, tmp_path):
    """Lamplighter parses `enabled` as a STRING against
    on/true/1/yes. A JSON boolean would cross the bridge as "True" and
    work only by accident of Python's repr."""
    from tools.lamplighter import _set_enabled_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    _plain_dict_indigo(mock_indigo)
    plugin = _install(mock_indigo)

    _set_enabled_handler({"zone": "Kitchen", "enabled": True}, mock_indigo)
    plugin.executeAction.assert_called_once_with(
        "set_zone_enabled",
        props=_wrapped({"zone_name": "Kitchen", "enabled": "on"}),
        waitUntilDone=True,
    )


def test_set_enabled_false_sends_off(mock_indigo, tmp_path):
    from tools.lamplighter import _set_enabled_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    _plain_dict_indigo(mock_indigo)
    plugin = _install(mock_indigo)

    _set_enabled_handler({"zone": "Kitchen", "enabled": False}, mock_indigo)
    plugin.executeAction.assert_called_once_with(
        "set_zone_enabled",
        props=_wrapped({"zone_name": "Kitchen", "enabled": "off"}),
        waitUntilDone=True,
    )


def _switching_controller(mock_indigo, dev_id=9001, on=True,
                          obeys=True, plugin_enabled=True):
    """A controller device plus a turnOn/turnOff pair that actually
    moves its onState -- the way a live, running Lamplighter behaves.

    ``obeys=False`` models the failure the confirm read exists to
    catch: the command is accepted by Indigo and the device never
    changes, because nothing is on the other end of it.
    """
    controller = _controller_device(dev_id, on=on)
    mock_indigo.devices = [controller, _zone_device(101, "Kitchen")]
    _install(mock_indigo, enabled=plugin_enabled)

    def _set(value):
        def _do(_id):
            if obeys:
                controller.onState = value
        return _do

    mock_indigo.device.turnOn = MagicMock(side_effect=_set(True))
    mock_indigo.device.turnOff = MagicMock(side_effect=_set(False))
    return controller


def test_plugin_wide_enable_is_gated_on_the_plugin_being_available(
        mock_indigo):
    """Kills the mutation "drop the plugin gate from the device path".

    This replaces a test that asserted getPlugin must NOT be called --
    which pinned the bug as intended behaviour. The controller device
    OUTLIVES the plugin: with Lamplighter uninstalled or disabled its
    device still sits in the Indigo database, so turnOn/turnOff
    succeeds at the Indigo level, changes no automation whatsoever, and
    without the gate the tool reports ok. The gate must run, and the
    write must not.
    """
    from tools.lamplighter import _set_enabled_handler

    _switching_controller(mock_indigo, plugin_enabled=False)

    with pytest.raises(ValueError) as exc:
        _set_enabled_handler({"enabled": False}, mock_indigo)
    assert PLUGIN_ID in str(exc.value)
    assert "The plugin-wide enable was NOT performed" in str(exc.value)
    mock_indigo.server.getPlugin.assert_called_with(PLUGIN_ID)
    mock_indigo.device.turnOff.assert_not_called()
    mock_indigo.device.turnOn.assert_not_called()


def test_plugin_wide_enable_switches_the_controller_and_reads_it_back(
        mock_indigo):
    """Lamplighter's Actions.xml has no global-enable action; the
    controller relay's own on/off IS it. The result must report what
    the device says AFTER the write, not merely that a command was
    sent."""
    from tools.lamplighter import _set_enabled_handler

    _switching_controller(mock_indigo, on=True)
    mock_indigo.server.getInstallFolderPath.side_effect = _MustNotBeCalled(
        "the plugin-wide enable needs no config read"
    )

    result = _set_enabled_handler({"enabled": False}, mock_indigo)

    mock_indigo.device.turnOff.assert_called_once_with(9001)
    mock_indigo.device.turnOn.assert_not_called()
    assert result["status"] == "ok"
    assert result["applied"] is True
    assert result["enabled_before"] is True
    assert result["enabled_after"] is False
    assert result["scope"] == "plugin"
    assert result["controller_id"] == 9001


def test_plugin_wide_enable_that_did_not_take_is_not_status_ok(
        mock_indigo):
    """Kills the mutation "return status ok without reading it back".

    A half-started Lamplighter still reports isEnabled() true while its
    device callbacks do nothing, so the command lands and the state
    never moves. Reporting ok there is the exact silent no-op this
    family exists to prevent.
    """
    from tools.lamplighter import _set_enabled_handler

    _switching_controller(mock_indigo, on=True, obeys=False)

    result = _set_enabled_handler({"enabled": False}, mock_indigo)

    mock_indigo.device.turnOff.assert_called_once_with(9001)
    assert result["status"] != "ok"
    assert result["status"] == "not_applied"
    assert result["applied"] is False
    assert result["enabled_after"] is True
    assert "NOT take effect" in result["note"]


def test_plugin_wide_enable_already_in_the_wanted_state_is_applied(
        mock_indigo):
    """Switching an already-off controller off changes nothing and is
    still correctly applied -- the check compares against what was
    ASKED FOR, not against what moved. A "did it change?" test would
    call this a failure."""
    from tools.lamplighter import _set_enabled_handler

    _switching_controller(mock_indigo, on=False)

    result = _set_enabled_handler({"enabled": False}, mock_indigo)
    assert result["applied"] is True
    assert result["status"] == "ok"
    assert result["enabled_before"] is False
    assert result["enabled_after"] is False


def test_plugin_wide_enable_unreadable_readback_is_unconfirmed(
        mock_indigo):
    """Kills the mutation "a failed read-back means it did not apply".

    If the controller cannot be re-read, whether the write took effect
    is UNKNOWN -- neither confirmed nor denied. Reporting applied:false
    would blame the write for a broken read.
    """
    from tools.lamplighter import _set_enabled_handler

    controller = _switching_controller(mock_indigo, on=True)

    def _break_everything(_id):
        controller.onState = False
        mock_indigo.devices = _UnreadableDevices("IOM went away")

    mock_indigo.device.turnOff = MagicMock(side_effect=_break_everything)

    result = _set_enabled_handler({"enabled": False}, mock_indigo)
    assert result["status"] == "unconfirmed"
    assert result["applied"] is None
    assert result["enabled_after"] is None
    assert "IOM went away" in result["note"]
    assert "UNKNOWN" in result["note"]


def test_plugin_wide_enable_dispatch_fault_is_a_was_dispatched_error(
        mock_indigo):
    """Kills the mutation "let turnOn/turnOff raise unwrapped".

    The command may have taken effect before the error, so this belongs
    in the back-off bucket with the WAS-DISPATCHED warning, not in the
    fix-your-arguments one.
    """
    from tools.lamplighter import _set_enabled_handler

    _switching_controller(mock_indigo, on=False)
    mock_indigo.device.turnOn = MagicMock(
        side_effect=RuntimeError("server went away")
    )

    with pytest.raises(RuntimeError) as exc:
        _set_enabled_handler({"enabled": True}, mock_indigo)
    text = str(exc.value)
    assert "WAS DISPATCHED" in text
    assert "Do NOT blindly retry" in text
    assert not isinstance(exc.value, (ValueError, TypeError))


def test_set_enabled_without_a_controller_refuses_rather_than_no_op(
        mock_indigo, tmp_path):
    from tools.lamplighter import _set_enabled_handler

    mock_indigo.devices = [_zone_device(101, "Kitchen")]
    with pytest.raises(ValueError, match="plugin-wide enable could not be set"):
        _set_enabled_handler({"enabled": True}, mock_indigo)
    mock_indigo.device.turnOn.assert_not_called()


def test_set_enabled_refuses_to_pick_between_two_controllers(mock_indigo):
    from tools.lamplighter import _set_enabled_handler

    mock_indigo.devices = [_controller_device(9001), _controller_device(9002)]
    with pytest.raises(ValueError, match="plugin-wide enable could not be set"):
        _set_enabled_handler({"enabled": True}, mock_indigo)
    mock_indigo.device.turnOn.assert_not_called()
    mock_indigo.device.turnOff.assert_not_called()


def test_set_enabled_requires_a_boolean(mock_indigo):
    from tools.lamplighter import _set_enabled_handler

    mock_indigo.server.getInstallFolderPath.side_effect = _MustNotBeCalled(
        "argument validation must happen before any file read"
    )
    for bad in ({}, {"enabled": "on"}, {"enabled": 1}, {"zone": "Kitchen"}):
        with pytest.raises(ValueError, match="enabled must be true or false"):
            _set_enabled_handler(bad, mock_indigo)


def test_set_enabled_unknown_zone_never_reaches_the_plugin(
        mock_indigo, tmp_path):
    from tools.lamplighter import _set_enabled_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    mock_indigo.server.getPlugin.side_effect = _MustNotBeCalled(
        "an unknown zone must be refused before dispatch"
    )
    with pytest.raises(ValueError, match="no Lamplighter zone named 'Attic'"):
        _set_enabled_handler({"zone": "Attic", "enabled": True}, mock_indigo)


# ---------------------------------------------------------------------
# lamplighter_reconcile_now
# ---------------------------------------------------------------------

def test_reconcile_now_dispatches_with_no_props(mock_indigo):
    from tools.lamplighter import _reconcile_now_handler

    _plain_dict_indigo(mock_indigo)
    plugin = _install(mock_indigo)

    assert _reconcile_now_handler({}, mock_indigo) == {"status": "ok"}
    plugin.executeAction.assert_called_once_with(
        "reconcile_now", props=_wrapped({}), waitUntilDone=True
    )


def test_reconcile_now_rejects_unknown_args(mock_indigo):
    from tools.lamplighter import _reconcile_now_handler

    mock_indigo.server.getPlugin.side_effect = _MustNotBeCalled(
        "argument validation must happen before dispatch"
    )
    with pytest.raises(ValueError, match="unknown argument"):
        _reconcile_now_handler({"zone": "Kitchen"}, mock_indigo)


# ---------------------------------------------------------------------
# lamplighter_explain
# ---------------------------------------------------------------------

class _FakeIndigoDict:
    """Stand-in for indigo.Dict: has .items(), is not a dict."""

    def __init__(self, data):
        self._data = data

    def items(self):
        return self._data.items()


class _FakeIndigoList:
    """Stand-in for indigo.List: iterable, is not a list."""

    def __init__(self, items):
        self._items = items

    def __iter__(self):
        return iter(self._items)


def test_explain_returns_the_actions_answer_through_json_safe(mock_indigo):
    """The action answers in indigo.Dict/List containers, which are not
    JSON-serialisable. They must arrive as plain dicts and lists."""
    from tools.lamplighter import _explain_handler

    _plain_dict_indigo(mock_indigo)
    answer = _FakeIndigoDict({
        "ok": True,
        "zone": "Kitchen",
        "at": "2026-09-05T18:30:00",
        "explain": "Kitchen: occupied and dark in Dusk.",
        "desired": _FakeIndigoList([
            _FakeIndigoDict({"device": 772478931, "name": "Strips",
                             "level": 50}),
            _FakeIndigoDict({"device": 144694384, "name": "Pendants",
                             "level": "off"}),
        ]),
    })
    plugin = _install(mock_indigo, execute_result=answer)

    result = _explain_handler({"zone": "Kitchen"}, mock_indigo)

    plugin.executeAction.assert_called_once_with(
        "explain_zone",
        props=_wrapped({"zone_name": "Kitchen"}),
        waitUntilDone=True,
    )
    assert isinstance(result, dict)
    assert isinstance(result["desired"], list)
    assert result["desired"][0] == {
        "device": 772478931, "name": "Strips", "level": 50,
    }
    assert result["desired"][1]["level"] == "off"
    # And it must actually survive the wire.
    json.dumps(result)


def test_explain_passes_a_dry_run_time_through(mock_indigo):
    from tools.lamplighter import _explain_handler

    _plain_dict_indigo(mock_indigo)
    plugin = _install(mock_indigo, execute_result={
        "ok": True, "zone": "Kitchen", "at": "2026-09-05T23:30:00",
        "explain": "would be off duty", "desired": [],
    })

    _explain_handler(
        {"zone": "Kitchen", "at": "2026-09-05T23:30"}, mock_indigo
    )
    plugin.executeAction.assert_called_once_with(
        "explain_zone",
        props=_wrapped({"zone_name": "Kitchen", "at": "2026-09-05T23:30"}),
        waitUntilDone=True,
    )


def test_explain_rejects_an_unparseable_time_before_dispatch(mock_indigo):
    from tools.lamplighter import _explain_handler

    mock_indigo.server.getPlugin.side_effect = _MustNotBeCalled(
        "a malformed `at` must be caught during argument validation"
    )
    with pytest.raises(ValueError, match="is not a time this tool understands"):
        _explain_handler({"zone": "Kitchen", "at": "half past six"}, mock_indigo)


def test_explain_turns_the_plugins_refusal_into_a_failed_call(mock_indigo):
    """`ok: false` is a refusal, not an answer. Returning it verbatim
    would let a caller read an absent explanation as "no reasoning"."""
    from tools.lamplighter import _explain_handler

    _plain_dict_indigo(mock_indigo)
    _install(mock_indigo, execute_result={
        "ok": False, "message": "no zone named 'Attic'; zones are: Kitchen",
    })

    with pytest.raises(ValueError, match="no zone named 'Attic'"):
        _explain_handler({"zone": "Attic"}, mock_indigo)


def test_explain_refusal_without_a_message_does_not_say_none(mock_indigo):
    """Kills the mutation "interpolate payload['message'] raw".

    An ok:false carrying no message renders as the word "None", which
    reads like a reason Lamplighter gave and is not one. The caller is
    usually a model relaying to a human; "could not explain zone
    'Kitchen': None" invites it to report None as the cause.
    """
    from tools.lamplighter import _explain_handler

    _plain_dict_indigo(mock_indigo)
    _install(mock_indigo, execute_result={"ok": False})

    with pytest.raises(ValueError) as exc:
        _explain_handler({"zone": "Kitchen"}, mock_indigo)
    text = str(exc.value)
    assert "ok:false with no message" in text
    assert "None" not in text


def test_explain_no_answer_at_all_is_a_failed_call(mock_indigo):
    """A None back from waitUntilDone=True means the action raised.
    Never an empty explanation."""
    from tools.lamplighter import _explain_handler

    _plain_dict_indigo(mock_indigo)
    _install(mock_indigo, execute_result=None)

    with pytest.raises(ValueError, match="no usable answer"):
        _explain_handler({"zone": "Kitchen"}, mock_indigo)


# ---------------------------------------------------------------------
# the shared plugin gate + post-dispatch faults
# ---------------------------------------------------------------------

def test_unavailable_plugin_names_both_causes_and_both_remedies(
        mock_indigo):
    """Kills the mutation "say only: installed but not enabled".

    indigo.server.getPlugin() never raises -- it hands back a handle
    for a plugin id that names nothing at all, whose isEnabled() is
    simply false. So a false isEnabled() is EITHER "not installed" OR
    "installed but disabled", and this layer cannot tell them apart.
    Asserting only the second sends somebody who has never installed
    Lamplighter to a Plugins menu that does not list it.
    """
    from tools.lamplighter import _reconcile_now_handler

    _plain_dict_indigo(mock_indigo)
    plugin = _install(mock_indigo, enabled=False)
    plugin.executeAction.side_effect = _MustNotBeCalled(
        "an unavailable plugin must be refused before dispatch"
    )

    with pytest.raises(ValueError) as exc:
        _reconcile_now_handler({}, mock_indigo)
    text = str(exc.value)
    assert PLUGIN_ID in text
    assert "Action 'reconcile_now' was NOT performed" in text
    # Both causes...
    assert "NOT INSTALLED" in text
    assert "not enabled/running" in text
    # ...and both remedies.
    assert "enable it there" in text
    assert "install it first" in text


def test_state_changing_action_fault_is_a_was_dispatched_runtime_error(
        mock_indigo, tmp_path):
    """A fault AFTER dispatch may have half-happened. RuntimeError (not
    ValueError) so mcp_handler routes it to the back-off bucket instead
    of telling the model to fix its arguments and retry."""
    from tools.lamplighter import _lock_zone_handler

    _write_config(mock_indigo, tmp_path, _sample_config())
    _plain_dict_indigo(mock_indigo)
    plugin = _install(mock_indigo)
    plugin.executeAction.side_effect = RuntimeError("plugin host went away")

    with pytest.raises(RuntimeError) as exc:
        _lock_zone_handler({"zone": "Kitchen"}, mock_indigo)
    text = str(exc.value)
    assert "WAS DISPATCHED" in text
    assert "Do NOT" in text
    assert not isinstance(exc.value, (ValueError, TypeError))


def test_read_only_action_fault_says_retrying_is_safe(mock_indigo):
    """explain_zone decides nothing and writes nothing, so the
    WAS-DISPATCHED warning would be a lie here -- but it is still a
    RuntimeError, because nothing is wrong with the arguments."""
    from tools.lamplighter import _explain_handler

    _plain_dict_indigo(mock_indigo)
    plugin = _install(mock_indigo)
    plugin.executeAction.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError) as exc:
        _explain_handler({"zone": "Kitchen"}, mock_indigo)
    text = str(exc.value)
    assert "retrying is safe" in text
    assert "WAS DISPATCHED" not in text
