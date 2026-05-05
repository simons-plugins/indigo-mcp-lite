"""TDD tests for Indexer.build() inserting variable + action group rows.

Variables and action groups are simpler than devices — no aliases
(empty string), no type_label, no extra. Folder names come from
``indigo.variables.folders.getName``. Action groups don't have a
separate folder collection in the SDK; they live alongside variables
in the same folder space (verified against jarvis).
"""
from unittest.mock import MagicMock


class _AttrList(list):
    """list subclass that accepts attribute assignment, matching the
    real ``indigo.devices`` / ``indigo.variables`` shape."""


def _fake_variable(id_, name, value="", folder_id=0):
    v = MagicMock()
    v.id = id_
    v.name = name
    v.value = value
    v.folderId = folder_id
    return v


def _fake_action(id_, name, folder_id=0, description=""):
    a = MagicMock()
    a.id = id_
    a.name = name
    a.folderId = folder_id
    a.description = description
    return a


def _wire(mock_indigo, *, variables=(), actions=(), var_folders=None):
    devs = _AttrList()
    devs.folders = MagicMock(getName=MagicMock(return_value=""))
    mock_indigo.devices = devs

    vars_ = _AttrList(variables)
    vars_.folders = MagicMock()
    vars_.folders.getName.side_effect = lambda fid: (var_folders or {}).get(fid, "")
    mock_indigo.variables = vars_

    mock_indigo.actionGroups = _AttrList(actions)


def test_build_indexes_variables(mock_indigo):
    from indexer import Indexer

    _wire(
        mock_indigo,
        variables=[
            _fake_variable(1, "doorOpen", value="true", folder_id=5),
            _fake_variable(2, "houseMode", value="home"),
        ],
        var_folders={5: "Sensors"},
    )

    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    rows = idx.connection.execute(
        "SELECT entity_id, name, folder FROM entities "
        "WHERE entity_type='variable' ORDER BY entity_id"
    ).fetchall()
    assert rows == [(1, "doorOpen", "Sensors"), (2, "houseMode", "")]


def test_build_indexes_action_groups(mock_indigo):
    from indexer import Indexer

    _wire(
        mock_indigo,
        actions=[
            _fake_action(11, "Goodnight"),
            _fake_action(12, "Morning Routine", description="wake the house"),
        ],
    )

    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    rows = idx.connection.execute(
        "SELECT entity_id, name, description FROM entities "
        "WHERE entity_type='action' ORDER BY entity_id"
    ).fetchall()
    assert rows == [
        (11, "Goodnight", ""),
        (12, "Morning Routine", "wake the house"),
    ]


def test_variable_has_empty_aliases_and_extra(mock_indigo):
    """Variables have no type-id concept so aliases stay empty.
    Same for the ``extra`` column."""
    from indexer import Indexer

    _wire(mock_indigo, variables=[_fake_variable(1, "doorOpen")])

    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    row = idx.connection.execute(
        "SELECT aliases, extra, type_label FROM entities WHERE entity_id=1"
    ).fetchone()
    assert row == ("", "", "")


def test_build_populates_snapshot_cache_for_variables_and_actions(mock_indigo):
    from indexer import Indexer

    _wire(
        mock_indigo,
        variables=[_fake_variable(1, "doorOpen")],
        actions=[_fake_action(11, "Goodnight")],
    )

    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    assert ("variable", 1) in idx._snapshots
    assert ("action", 11) in idx._snapshots


def test_build_indexes_all_three_entity_types(mock_indigo):
    """One device + one variable + one action — total_count = 3."""
    from indexer import Indexer

    devs = _AttrList()
    dev = MagicMock()
    dev.id = 1; dev.name = "Lamp"; dev.deviceTypeId = "dimmer"
    dev.folderId = 0; dev.description = ""; dev.model = ""; dev.address = ""
    devs.append(dev)
    devs.folders = MagicMock(getName=MagicMock(return_value=""))
    mock_indigo.devices = devs

    vars_ = _AttrList([_fake_variable(2, "v")])
    vars_.folders = MagicMock(getName=MagicMock(return_value=""))
    mock_indigo.variables = vars_

    mock_indigo.actionGroups = _AttrList([_fake_action(3, "a")])

    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    by_type = dict(idx.connection.execute(
        "SELECT entity_type, count(*) FROM entities GROUP BY entity_type"
    ).fetchall())
    assert by_type == {"device": 1, "variable": 1, "action": 1}
