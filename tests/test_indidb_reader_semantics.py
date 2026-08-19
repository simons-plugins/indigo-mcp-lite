"""Tests for the three .indiDb facts the IOM cannot answer:

- sunrise/sunset conditions (issue #53) — bare Type 1/2 conditions
- plugin-action parameters (issue #54) — ``MetaProps[PluginID]``
- a schedule's firing rule (issue #55) — TimeType/Time/SunDelta/
  Countdown plus the DateType day rule

Its own fixture rather than the shared one in ``test_indidb_reader``:
the firing rule needs one schedule per TimeType, and the shapes here
are copied from a live-server census rather than invented.
"""

import pytest

from indidb_reader import IndiDbReader

FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<Database type="dict">
  <TDTriggerList type="vector">
    <TDTrigger type="dict">
      <ActionGroup type="dict">
        <ActionSteps type="vector">
          <Action type="dict">
            <Class type="integer">999</Class>
            <MetaProps type="dict">
              <com.autologplugin.indigoplugin.zigbee2mqtt type="dict">
                <dimmer_device_id type="string">51886070</dimmer_device_id>
                <setWhiteLevel type="bool">false</setWhiteLevel>
                <setWhiteTemperature type="bool">true</setWhiteTemperature>
                <whiteLevel type="string">100</whiteLevel>
                <whiteTemperature type="string">4000</whiteTemperature>
              </com.autologplugin.indigoplugin.zigbee2mqtt>
            </MetaProps>
            <PluginID type="string">com.autologplugin.indigoplugin.zigbee2mqtt</PluginID>
            <TypeIdPlugin type="string">setWhiteLevelTemperature</TypeIdPlugin>
            <TypeLabelPlugin type="string">Set White Level / Temperature</TypeLabelPlugin>
          </Action>
        </ActionSteps>
      </ActionGroup>
      <Condition type="dict">
        <Type type="integer">0</Type>
      </Condition>
      <DateType type="integer">0</DateType>
      <ID type="integer">600</ID>
      <Name type="string">schKitchen Lights 6am</Name>
      <RandomizeAmount type="integer">0</RandomizeAmount>
      <RepeatInterval type="integer">1</RepeatInterval>
      <Time type="integer">21600</Time>
      <TimeType type="integer">0</TimeType>
    </TDTrigger>
    <TDTrigger type="dict">
      <ActionGroup type="dict">
        <ActionSteps type="vector" />
      </ActionGroup>
      <DateType type="integer">0</DateType>
      <ID type="integer">601</ID>
      <Name type="string">Evening indoor lights</Name>
      <RandomizeAmount type="integer">900</RandomizeAmount>
      <RepeatInterval type="integer">1</RepeatInterval>
      <SunDelta type="integer">-1800</SunDelta>
      <Time type="integer">4294967295</Time>
      <TimeType type="integer">2</TimeType>
    </TDTrigger>
    <TDTrigger type="dict">
      <ActionGroup type="dict">
        <ActionSteps type="vector" />
      </ActionGroup>
      <Countdown type="integer">60</Countdown>
      <DateType type="integer">0</DateType>
      <ID type="integer">602</ID>
      <Name type="string">Heating on kitchen</Name>
      <RepeatInterval type="integer">1</RepeatInterval>
      <TimeType type="integer">3</TimeType>
    </TDTrigger>
    <TDTrigger type="dict">
      <ActionGroup type="dict">
        <ActionSteps type="vector" />
      </ActionGroup>
      <DateType type="integer">1</DateType>
      <DayFlag type="integer">62</DayFlag>
      <ID type="integer">603</ID>
      <Name type="string">Workday hours on</Name>
      <RepeatInterval type="integer">1</RepeatInterval>
      <Time type="integer">32400</Time>
      <TimeType type="integer">0</TimeType>
    </TDTrigger>
    <TDTrigger type="dict">
      <ActionGroup type="dict">
        <ActionSteps type="vector" />
      </ActionGroup>
      <DateType type="integer">9</DateType>
      <ID type="integer">604</ID>
      <Name type="string">Unknown date rule</Name>
      <SunDelta type="integer">0</SunDelta>
      <TimeType type="integer">1</TimeType>
    </TDTrigger>
  </TDTriggerList>
  <TriggerList type="vector">
    <Trigger type="dict">
      <ActionGroup type="dict">
        <ActionSteps type="vector">
          <Action type="dict">
            <Class type="integer">999</Class>
            <DeviceID type="integer">772478931</DeviceID>
            <MetaProps type="dict">
              <com.morris.default-dimmer-level type="dict">
                <defaultLevel type="string">100</defaultLevel>
                <liveUpdate type="bool">true</liveUpdate>
                <zones type="vector">
                  <Item type="string">a</Item>
                  <Item type="string">b</Item>
                </zones>
                <retries type="integer">3</retries>
                <fade type="real">1.5</fade>
                <broken type="integer">not-a-number</broken>
              </com.morris.default-dimmer-level>
            </MetaProps>
            <PluginID type="string">com.morris.default-dimmer-level</PluginID>
            <TypeIdPlugin type="string">setDefaultDimmerLevel</TypeIdPlugin>
            <TypeLabelPlugin type="string">Set Default Level</TypeLabelPlugin>
          </Action>
          <Action type="dict">
            <Class type="integer">999</Class>
            <PluginID type="string">com.ssi.indigoplugin.Sonos</PluginID>
            <TypeIdPlugin type="string">actionStop</TypeIdPlugin>
            <TypeLabelPlugin type="string">Sonos: Stop</TypeLabelPlugin>
          </Action>
          <Action type="dict">
            <Class type="integer">999</Class>
            <MetaProps type="dict">
              <com.other.plugin type="dict">
                <stray type="string">value</stray>
              </com.other.plugin>
            </MetaProps>
            <PluginID type="string">com.ssi.indigoplugin.Sonos</PluginID>
            <TypeIdPlugin type="string">actionPlay</TypeIdPlugin>
            <TypeLabelPlugin type="string">Sonos: Play</TypeLabelPlugin>
          </Action>
        </ActionSteps>
      </ActionGroup>
      <Condition type="dict">
        <ConditionList type="dict">
          <Conditions type="vector">
            <Condition type="dict">
              <ObjVers type="integer">6</ObjVers>
              <Type type="integer">2</Type>
            </Condition>
            <Condition type="dict">
              <ObjVers type="integer">6</ObjVers>
              <Type type="integer">1</Type>
            </Condition>
            <Condition type="dict">
              <ObjVers type="integer">6</ObjVers>
              <Type type="integer">77</Type>
            </Condition>
          </Conditions>
          <Logic type="integer">1</Logic>
        </ConditionList>
        <Type type="integer">100</Type>
      </Condition>
      <ID type="integer">700</ID>
      <Name type="string">Kitchen Lights On - Daytime</Name>
    </Trigger>
  </TriggerList>
  <ActionGroupList type="vector" />
</Database>
"""


@pytest.fixture
def reader(tmp_path, mock_indigo):
    path = tmp_path / "Semantics.indiDb"
    path.write_text(FIXTURE, encoding="utf-8")
    mock_indigo.server.getDbFilePath.return_value = str(path)
    return IndiDbReader(indigo_module=mock_indigo)


@pytest.fixture
def schedules(reader):
    return reader.automations()["schedule"]


# ---------------------------------------------------------------------
# #53 — sunrise/sunset conditions
# ---------------------------------------------------------------------

def test_sun_conditions_decoded_not_unknown(reader):
    tree = reader.automations()["trigger"][700]["conditions"]
    daylight, dark, _ = tree["conditions"]
    assert daylight == {"type": "sun", "type_code": 2, "state": "daylight"}
    assert dark == {"type": "sun", "type_code": 1, "state": "dark"}


def test_unrecognised_condition_type_still_unknown(reader):
    tree = reader.automations()["trigger"][700]["conditions"]
    assert tree["conditions"][2] == {"type": "unknown", "type_code": 77}


# ---------------------------------------------------------------------
# #54 — plugin-action parameters
# ---------------------------------------------------------------------

def test_plugin_props_passed_through_verbatim(schedules):
    step = schedules[600]["steps"][0]
    assert step["props"] == {
        "dimmer_device_id": "51886070",
        "setWhiteLevel": False,
        "setWhiteTemperature": True,
        "whiteLevel": "100",
        "whiteTemperature": "4000",
    }


def test_plugin_props_decode_every_value_type(reader):
    step = reader.automations()["trigger"][700]["steps"][0]
    assert step["props"] == {
        "defaultLevel": "100",
        "liveUpdate": True,
        "zones": ["a", "b"],
        "retries": 3,
        "fade": 1.5,
        # Unparseable number degrades to raw text rather than vanishing.
        "broken": "not-a-number",
    }


def test_plugin_step_without_metaprops_omits_props(reader):
    step = reader.automations()["trigger"][700]["steps"][1]
    assert "props" not in step
    assert step["plugin_type_id"] == "actionStop"


def test_props_read_only_from_the_steps_own_plugin_key(reader):
    # MetaProps keyed by a different plugin id is not this step's
    # configuration — surfacing it would attribute the wrong params.
    step = reader.automations()["trigger"][700]["steps"][2]
    assert "props" not in step


# ---------------------------------------------------------------------
# #55 — schedule firing rule
# ---------------------------------------------------------------------

def test_absolute_schedule_reports_clock_time(schedules):
    assert schedules[600]["schedule"] == {
        "time_type": "absolute", "time_type_code": 0,
        "time": "06:00:00", "time_seconds": 21600,
        "date_type": "days_interval", "date_type_code": 0,
        "repeat_interval_days": 1,
    }


def test_sunset_schedule_reports_offset_and_randomization(schedules):
    assert schedules[601]["schedule"] == {
        "time_type": "sunset", "time_type_code": 2,
        "sun_offset_seconds": -1800,
        "randomize_seconds": 900,
        "date_type": "days_interval", "date_type_code": 0,
        "repeat_interval_days": 1,
    }


def test_sun_relative_schedule_ignores_the_time_sentinel(schedules):
    # Time carries 4294967295 when sun-relative — it must never be
    # formatted into a plausible-looking clock time.
    assert "time" not in schedules[601]["schedule"]
    assert "time_seconds" not in schedules[601]["schedule"]


def test_countdown_schedule_reports_interval(schedules):
    timing = schedules[602]["schedule"]
    assert timing["time_type"] == "countdown"
    assert timing["interval_seconds"] == 60


def test_days_of_week_decoded_from_bitmask(schedules):
    timing = schedules[603]["schedule"]
    assert timing["date_type"] == "days_of_week"
    assert timing["days_of_week"] == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    assert "repeat_interval_days" not in timing
    assert timing["time"] == "09:00:00"


def test_unlabelled_date_type_kept_numeric_not_guessed(schedules):
    timing = schedules[604]["schedule"]
    assert timing["date_type"] is None
    assert timing["date_type_code"] == 9
    assert timing["time_type"] == "sunrise"
    assert timing["sun_offset_seconds"] == 0


def test_triggers_carry_no_schedule_block(reader):
    assert "schedule" not in reader.automations()["trigger"][700]
