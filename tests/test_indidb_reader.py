"""Tests for indidb_reader — streaming .indiDb automation parser.

Fixture XML mirrors the real database shapes captured in issue #38's
schema recon: typed-XML dicts/vectors, anonymous nested ActionGroup
inside triggers/schedules, and every Action Class code observed live.
"""

import os

import pytest

from indidb_reader import IndiDbReader

LONG_SCRIPT = "x = 1\n" * 500  # 3000 chars > 2000 truncation bound

FIXTURE = f"""<?xml version="1.0" encoding="UTF-8"?>
<Database type="dict">
  <TDTriggerList type="vector">
    <TDTrigger type="dict">
      <ActionGroup type="dict">
        <ActionSteps type="vector">
          <Action type="dict">
            <Class type="integer">1</Class>
            <DeviceAction type="integer">4</DeviceAction>
            <DeviceActionValue type="integer">0</DeviceActionValue>
            <DeviceID type="integer">111</DeviceID>
          </Action>
          <Action type="dict">
            <Class type="integer">0</Class>
          </Action>
          <Action type="dict">
            <Class type="integer">3</Class>
            <DeviceID type="integer">333</DeviceID>
            <HVACAction type="integer">0</HVACAction>
            <HVACActionValue type="real">13.00</HVACActionValue>
          </Action>
        </ActionSteps>
      </ActionGroup>
      <Condition type="dict">
        <ConditionList type="dict">
          <Conditions type="vector">
            <Condition type="dict">
              <DevComp type="integer">1</DevComp>
              <DevID type="integer">111</DevID>
              <DevState type="string">onOffState</DevState>
              <DevValue type="string" />
              <DevValue2 type="string" />
              <Type type="integer">7</Type>
            </Condition>
            <Condition type="dict">
              <ConditionList type="dict">
                <Conditions type="vector">
                  <Condition type="dict">
                    <CompareVarToValue type="bool">true</CompareVarToValue>
                    <Type type="integer">3</Type>
                    <VarID type="integer">555</VarID>
                    <VarID2 type="integer">0</VarID2>
                    <VarState type="integer">0</VarState>
                    <VarValue type="string" />
                    <VarValue2 type="string" />
                  </Condition>
                  <Condition type="dict">
                    <EndTimeDate type="string">22:00</EndTimeDate>
                    <StartTimeDate type="string">08:00</StartTimeDate>
                    <TimeDateCompareOperator type="integer">2</TimeDateCompareOperator>
                    <Type type="integer">5</Type>
                  </Condition>
                </Conditions>
                <Logic type="integer">0</Logic>
              </ConditionList>
              <Type type="integer">100</Type>
            </Condition>
          </Conditions>
          <Logic type="integer">1</Logic>
        </ConditionList>
        <Type type="integer">100</Type>
      </Condition>
      <Enabled type="bool">true</Enabled>
      <FolderID type="integer">10</FolderID>
      <ID type="integer">300</ID>
      <Name type="string">Zone 2.5 Heating</Name>
    </TDTrigger>
  </TDTriggerList>
  <TriggerList type="vector">
    <Trigger type="dict">
      <ActionGroup type="dict">
        <ActionSteps type="vector">
          <Action type="dict">
            <ActionGroupID type="integer">100</ActionGroupID>
            <Class type="integer">100</Class>
          </Action>
          <Action type="dict">
            <Class type="integer">999</Class>
            <DeviceID type="integer">444</DeviceID>
            <PluginID type="string">com.ssi.indigoplugin.Sonos</PluginID>
            <TypeIdPlugin type="string">actionStop</TypeIdPlugin>
            <TypeLabelPlugin type="string">Sonos: Stop</TypeLabelPlugin>
          </Action>
          <Action type="dict">
            <Class type="integer">42</Class>
          </Action>
        </ActionSteps>
      </ActionGroup>
      <Condition type="dict">
        <Type type="integer">0</Type>
      </Condition>
      <DeviceID type="integer">111</DeviceID>
      <DeviceStateChange type="integer">2</DeviceStateChange>
      <DeviceStateSelector type="string">onOffState</DeviceStateSelector>
      <Enabled type="bool">false</Enabled>
      <FolderID type="integer">10</FolderID>
      <ID type="integer">400</ID>
      <Name type="string">Door opened</Name>
    </Trigger>
    <Trigger type="dict">
      <ActionGroup type="dict">
        <ActionSteps type="vector" />
      </ActionGroup>
      <Condition type="dict">
        <Type type="integer">0</Type>
      </Condition>
      <Enabled type="bool">true</Enabled>
      <ID type="integer">401</ID>
      <Name type="string">Var watcher</Name>
      <VarChange type="integer">1</VarChange>
      <VarID type="integer">555</VarID>
      <VarValue type="string">true</VarValue>
    </Trigger>
  </TriggerList>
  <ActionGroupList type="vector">
    <ActionGroup type="dict">
      <ActionSteps type="vector">
        <Action type="dict">
          <Class type="integer">1</Class>
          <DeviceAction type="integer">7</DeviceAction>
          <DeviceActionValue type="integer">250</DeviceActionValue>
          <DeviceID type="integer">111</DeviceID>
        </Action>
        <Action type="dict">
          <Class type="integer">9</Class>
          <DeviceAction type="integer">101</DeviceAction>
          <DeviceActionValue type="integer">0</DeviceActionValue>
          <DeviceID type="integer">222</DeviceID>
        </Action>
        <Action type="dict">
          <Class type="integer">201</Class>
          <VarAction type="integer">0</VarAction>
          <VarID type="integer">555</VarID>
          <VarValue type="string">true</VarValue>
        </Action>
        <Action type="dict">
          <Class type="integer">101</Class>
          <ScriptSource type="string">{LONG_SCRIPT}</ScriptSource>
          <ScriptType type="integer">0</ScriptType>
        </Action>
        <Action type="dict">
          <ActionGroupID type="integer">200</ActionGroupID>
          <Class type="integer">100</Class>
        </Action>
      </ActionSteps>
      <FolderID type="integer">20</FolderID>
      <ID type="integer">100</ID>
      <Name type="string">Morning.Scene</Name>
    </ActionGroup>
    <ActionGroup type="dict">
      <ActionSteps type="vector">
        <Action type="dict">
          <ActionGroupID type="integer">100</ActionGroupID>
          <Class type="integer">100</Class>
        </Action>
        <Action type="dict">
          <Class type="integer">101</Class>
          <ScriptSource type="string">print("hi")</ScriptSource>
          <ScriptType type="integer">0</ScriptType>
        </Action>
      </ActionSteps>
      <ID type="integer">200</ID>
      <Name type="string">Chained</Name>
    </ActionGroup>
  </ActionGroupList>
</Database>
"""


@pytest.fixture
def db_file(tmp_path):
    path = tmp_path / "Test House.indiDb"
    path.write_text(FIXTURE, encoding="utf-8")
    return path


@pytest.fixture
def reader(db_file, mock_indigo):
    mock_indigo.server.getDbFilePath.return_value = str(db_file)
    return IndiDbReader(indigo_module=mock_indigo)


def test_parses_all_three_collections(reader):
    data = reader.automations()
    assert set(data["schedule"]) == {300}
    assert set(data["trigger"]) == {400, 401}
    assert set(data["action_group"]) == {100, 200}
    assert data["skipped_automations"] == 0


def test_schedule_record_shape_and_dotted_name(reader):
    sched = reader.automations()["schedule"][300]
    assert sched["name"] == "Zone 2.5 Heating"
    assert sched["enabled"] is True
    assert sched["folder_id"] == 10


def test_nested_action_group_not_promoted_to_top_level(reader):
    # The anonymous ActionGroup inside schedule 300 / triggers 400-401
    # must not appear as a real action group.
    assert set(reader.automations()["action_group"]) == {100, 200}


def test_step_order_and_class_0_skipped(reader):
    steps = reader.automations()["schedule"][300]["steps"]
    # Class 0 placeholder dropped; order preserved: device then hvac.
    assert [s["type"] for s in steps] == ["device_action", "hvac_action"]


def test_device_action_step_decoding(reader):
    step = reader.automations()["schedule"][300]["steps"][0]
    assert step == {
        "type": "device_action", "class": 1, "device_id": 111,
        "action_code": 4, "action_label": "turn_on", "action_value": 0,
    }


def test_hvac_action_step_decoding(reader):
    step = reader.automations()["schedule"][300]["steps"][1]
    assert step["action_label"] == "set_heat_setpoint"
    assert step["action_value"] == 13.0
    assert step["device_id"] == 333


def test_universal_action_class_9_uses_universal_labels(reader):
    steps = reader.automations()["action_group"][100]["steps"]
    universal = steps[1]
    assert universal["class"] == 9
    assert universal["action_code"] == 101
    assert universal["action_label"] == "energy_reset"


def test_variable_action_step_decoding(reader):
    step = reader.automations()["action_group"][100]["steps"][2]
    assert step == {
        "type": "variable_action", "class": 201, "variable_id": 555,
        "action_code": 0, "action_label": "set_value", "value": "true",
    }


def test_script_step_truncated_to_2000_chars(reader):
    step = reader.automations()["action_group"][100]["steps"][3]
    assert step["type"] == "embedded_script"
    assert step["script_type_label"] == "python"
    assert len(step["source"]) == 2000
    assert step["truncated"] is True


def test_short_script_not_truncated(reader):
    step = reader.automations()["action_group"][200]["steps"][1]
    assert step["source"] == 'print("hi")'
    assert step["truncated"] is False


def test_plugin_action_step_decoding(reader):
    step = reader.automations()["trigger"][400]["steps"][1]
    assert step == {
        "type": "plugin_action", "class": 999, "label": "Sonos: Stop",
        "plugin_id": "com.ssi.indigoplugin.Sonos",
        "plugin_type_id": "actionStop", "device_id": 444,
    }


def test_unknown_class_kept_numeric_not_guessed(reader):
    step = reader.automations()["trigger"][400]["steps"][2]
    assert step == {"type": "unknown", "class": 42}


def test_execute_action_group_step(reader):
    step = reader.automations()["trigger"][400]["steps"][0]
    assert step == {
        "type": "execute_action_group", "class": 100,
        "action_group_id": 100,
    }


def test_condition_tree_nested_logic(reader):
    cond = reader.automations()["schedule"][300]["conditions"]
    assert cond["logic"] == "all" and cond["logic_code"] == 1
    leaf, nested = cond["conditions"]
    assert leaf["type"] == "device_state"
    assert leaf["device_id"] == 111
    assert leaf["state"] == "onOffState"
    assert leaf["comparator_code"] == 1
    assert leaf["comparator"] == "is_false"
    assert nested["logic"] == "any" and nested["logic_code"] == 0
    var_leaf, time_leaf = nested["conditions"]
    assert var_leaf["type"] == "variable"
    assert var_leaf["variable_id"] == 555
    assert var_leaf["comparator"] == "is_true"
    assert var_leaf["compare_var_to_value"] is True
    assert "variable_id2" not in var_leaf  # 0 = unused second operand
    assert time_leaf["type"] == "time_date"
    assert time_leaf["start"] == "08:00"
    assert time_leaf["operator_code"] == 2


def test_type_0_condition_collapses_to_none(reader):
    assert reader.automations()["trigger"][400]["conditions"] is None


def test_trigger_watch_device_fields(reader):
    watch = reader.automations()["trigger"][400]["watch"]
    assert watch["device_id"] == 111
    assert watch["state_selector"] == "onOffState"
    assert watch["state_change_code"] == 2


def test_trigger_watch_variable_fields(reader):
    watch = reader.automations()["trigger"][401]["watch"]
    assert watch["variable_id"] == 555
    assert watch["value"] == "true"


def test_mid_write_truncated_xml_raises_friendly_error(tmp_path, mock_indigo):
    path = tmp_path / "torn.indiDb"
    path.write_text(FIXTURE[: len(FIXTURE) // 2], encoding="utf-8")
    mock_indigo.server.getDbFilePath.return_value = str(path)
    reader = IndiDbReader(indigo_module=mock_indigo)
    with pytest.raises(ValueError, match="retry"):
        reader.automations()


def test_missing_file_points_at_path_permissions(tmp_path, mock_indigo):
    mock_indigo.server.getDbFilePath.return_value = str(tmp_path / "gone.indiDb")
    reader = IndiDbReader(indigo_module=mock_indigo)
    with pytest.raises(ValueError, match="check the database path/permissions"):
        reader.automations()


def test_permission_error_during_iterparse_points_at_path(
        db_file, mock_indigo, monkeypatch):
    mock_indigo.server.getDbFilePath.return_value = str(db_file)
    reader = IndiDbReader(indigo_module=mock_indigo)

    def _denied(*_a, **_k):
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr("indidb_reader.ET.iterparse", _denied)
    with pytest.raises(ValueError, match="check the database path/permissions"):
        reader.automations()


def test_generic_oserror_keeps_retry_hint(db_file, mock_indigo, monkeypatch):
    mock_indigo.server.getDbFilePath.return_value = str(db_file)
    reader = IndiDbReader(indigo_module=mock_indigo)

    def _disk_io(*_a, **_k):
        raise OSError("Input/output error")

    monkeypatch.setattr("indidb_reader.ET.iterparse", _disk_io)
    with pytest.raises(ValueError, match="retry"):
        reader.automations()


def test_db_path_unavailable_raises_friendly_error(mock_indigo):
    mock_indigo.server.getDbFilePath.side_effect = RuntimeError("no server")
    reader = IndiDbReader(indigo_module=mock_indigo)
    with pytest.raises(ValueError, match="database path unavailable"):
        reader.automations()


def test_db_path_non_string_raises_friendly_error(mock_indigo):
    mock_indigo.server.getDbFilePath.return_value = None
    reader = IndiDbReader(indigo_module=mock_indigo)
    with pytest.raises(ValueError, match="database path unavailable"):
        reader.automations()


def test_cache_reused_until_mtime_changes(db_file, reader):
    first = reader.automations()
    assert reader.automations() is first  # same key -> cached object

    # Rewrite with one automation renamed and force a different mtime
    # (same-second writes would otherwise defeat the test on coarse
    # filesystems).
    db_file.write_text(
        FIXTURE.replace("Door opened", "Door closed"), encoding="utf-8"
    )
    stat = os.stat(db_file)
    os.utime(db_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    second = reader.automations()
    assert second is not first
    assert second["trigger"][400]["name"] == "Door closed"


def test_db_path_switch_invalidates_cache(tmp_path, mock_indigo):
    first_db = tmp_path / "First House.indiDb"
    first_db.write_text(FIXTURE, encoding="utf-8")
    second_db = tmp_path / "Second House.indiDb"
    second_db.write_text(
        FIXTURE.replace("Door opened", "Door renamed"), encoding="utf-8"
    )
    mock_indigo.server.getDbFilePath.return_value = str(first_db)
    reader = IndiDbReader(indigo_module=mock_indigo)
    first = reader.automations()
    assert first["trigger"][400]["name"] == "Door opened"

    # Server switches databases (File -> Open): same reader, new path.
    mock_indigo.server.getDbFilePath.return_value = str(second_db)
    second = reader.automations()
    assert second is not first
    assert second["trigger"][400]["name"] == "Door renamed"


SKIP_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<Database type="dict">
  <ActionGroupList type="vector">
    <ActionGroup type="dict">
      <ActionSteps type="vector" />
      <Name type="string">Ghost Group</Name>
    </ActionGroup>
    <ActionGroup type="dict">
      <ActionSteps type="vector">
        <Action type="dict">
          <Class type="integer">1</Class>
          <DeviceAction type="integer">4</DeviceAction>
          <DeviceActionValue type="integer">0</DeviceActionValue>
          <DeviceID type="integer">111</DeviceID>
        </Action>
      </ActionSteps>
      <ID type="integer">900</ID>
      <Name type="string">Good Group</Name>
    </ActionGroup>
  </ActionGroupList>
</Database>
"""


def test_missing_id_automation_skipped_counted_and_logged(
        tmp_path, mock_indigo):
    from unittest.mock import MagicMock

    path = tmp_path / "skip.indiDb"
    path.write_text(SKIP_FIXTURE, encoding="utf-8")
    mock_indigo.server.getDbFilePath.return_value = str(path)
    logger = MagicMock()
    reader = IndiDbReader(indigo_module=mock_indigo, logger=logger)
    data = reader.automations()
    assert set(data["action_group"]) == {900}
    assert data["skipped_automations"] == 1
    warned = " ".join(str(c) for c in logger.warning.call_args_list)
    assert "ActionGroup" in warned and "Ghost Group" in warned


CORRUPT_INT_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<Database type="dict">
  <ActionGroupList type="vector">
    <ActionGroup type="dict">
      <ActionSteps type="vector">
        <Action type="dict">
          <Class type="integer">1</Class>
          <DeviceAction type="integer">4</DeviceAction>
          <DeviceActionValue type="integer">0</DeviceActionValue>
          <DeviceID type="integer">garbage</DeviceID>
        </Action>
      </ActionSteps>
      <ID type="integer">901</ID>
      <Name type="string">Corrupt Step Group</Name>
    </ActionGroup>
  </ActionGroupList>
</Database>
"""


def test_corrupt_integer_field_counted_not_silent(tmp_path, mock_indigo):
    from unittest.mock import MagicMock

    path = tmp_path / "corrupt.indiDb"
    path.write_text(CORRUPT_INT_FIXTURE, encoding="utf-8")
    mock_indigo.server.getDbFilePath.return_value = str(path)
    logger = MagicMock()
    reader = IndiDbReader(indigo_module=mock_indigo, logger=logger)
    data = reader.automations()
    step = data["action_group"][901]["steps"][0]
    assert step["device_id"] is None  # unparseable, never guessed
    assert data["skipped_automations"] == 1
    warned = " ".join(str(c) for c in logger.warning.call_args_list)
    assert "DeviceID" in warned and "garbage" in warned


def test_resolve_name_logs_unexpected_exception(mock_indigo, db_file):
    from unittest.mock import MagicMock

    mock_indigo.server.getDbFilePath.return_value = str(db_file)
    logger = MagicMock()
    reader = IndiDbReader(indigo_module=mock_indigo, logger=logger)
    mock_indigo.devices.__getitem__.side_effect = RuntimeError("IOM wedged")
    assert reader.resolve_name("devices", 111) is None
    assert logger.warning.called
    # Lookup-style misses stay quiet — no warning spam for deletions.
    logger.warning.reset_mock()
    mock_indigo.devices.__getitem__.side_effect = KeyError(111)
    assert reader.resolve_name("devices", 111) is None
    logger.warning.assert_not_called()


def test_resolve_name_defensive(mock_indigo, db_file):
    mock_indigo.server.getDbFilePath.return_value = str(db_file)
    reader = IndiDbReader(indigo_module=mock_indigo)

    class _Named:
        name = "Kitchen Light"

    store = {111: _Named()}
    mock_indigo.devices.__getitem__.side_effect = lambda i: store[i]
    assert reader.resolve_name("devices", 111) == "Kitchen Light"
    assert reader.resolve_name("devices", 999) is None  # KeyError -> None
    assert reader.resolve_name("devices", None) is None
    del mock_indigo.nonexistent
    assert reader.resolve_name("nonexistent", 1) is None
