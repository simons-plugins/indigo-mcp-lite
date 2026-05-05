"""TDD tests for on_*_created and on_*_deleted handlers."""
from unittest.mock import MagicMock


class _AttrList(list):
    pass


def _fake_device(id_, name, **kwargs):
    d = MagicMock()
    d.id = id_; d.name = name
    d.deviceTypeId = kwargs.get("type_id", "dimmer")
    d.folderId = kwargs.get("folder_id", 0)
    d.description = kwargs.get("description", "")
    d.model = kwargs.get("model", "")
    d.address = kwargs.get("address", "")
    return d


def _fake_variable(id_, name, value="", folder_id=0):
    v = MagicMock()
    v.id = id_; v.name = name; v.value = value; v.folderId = folder_id
    return v


def _fake_action(id_, name, folder_id=0, description=""):
    a = MagicMock()
    a.id = id_; a.name = name; a.folderId = folder_id; a.description = description
    return a


def _wire_empty(mock_indigo):
    d = _AttrList(); d.folders = MagicMock(getName=MagicMock(return_value=""))
    mock_indigo.devices = d
    v = _AttrList(); v.folders = MagicMock(getName=MagicMock(return_value=""))
    mock_indigo.variables = v
    mock_indigo.actionGroups = _AttrList()


# ----- create ------------------------------------------------------------


def test_on_device_created_inserts_row_and_snapshot(mock_indigo):
    from indexer import Indexer

    _wire_empty(mock_indigo)
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    new_dev = _fake_device(7, "New Lamp")
    idx.on_device_created(new_dev)

    assert ("device", 7) in idx._snapshots
    row = idx.connection.execute(
        "SELECT name FROM entities WHERE entity_id=7"
    ).fetchone()
    assert row[0] == "New Lamp"


def test_on_variable_created_inserts_row(mock_indigo):
    from indexer import Indexer

    _wire_empty(mock_indigo)
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    new_var = _fake_variable(8, "newVar")
    idx.on_variable_created(new_var)

    assert ("variable", 8) in idx._snapshots
    assert idx.connection.execute(
        "SELECT count(*) FROM entities WHERE entity_type='variable' "
        "AND entity_id=8"
    ).fetchone()[0] == 1


def test_on_action_created_inserts_row(mock_indigo):
    from indexer import Indexer

    _wire_empty(mock_indigo)
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    new_action = _fake_action(9, "newAction")
    idx.on_action_created(new_action)

    assert ("action", 9) in idx._snapshots
    assert idx.connection.execute(
        "SELECT count(*) FROM entities WHERE entity_type='action' "
        "AND entity_id=9"
    ).fetchone()[0] == 1


# ----- delete ------------------------------------------------------------


def test_on_device_deleted_removes_row_and_snapshot(mock_indigo):
    from indexer import Indexer

    _wire_empty(mock_indigo)
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()
    dev = _fake_device(7, "Lamp")
    idx.on_device_created(dev)

    idx.on_device_deleted(dev)

    assert ("device", 7) not in idx._snapshots
    assert idx.connection.execute(
        "SELECT count(*) FROM entities WHERE entity_id=7"
    ).fetchone()[0] == 0


def test_on_variable_deleted_removes_row(mock_indigo):
    from indexer import Indexer

    _wire_empty(mock_indigo)
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()
    var = _fake_variable(8, "v")
    idx.on_variable_created(var)

    idx.on_variable_deleted(var)

    assert ("variable", 8) not in idx._snapshots


def test_on_action_deleted_removes_row(mock_indigo):
    from indexer import Indexer

    _wire_empty(mock_indigo)
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()
    action = _fake_action(9, "a")
    idx.on_action_created(action)

    idx.on_action_deleted(action)

    assert ("action", 9) not in idx._snapshots


def test_on_device_deleted_is_safe_for_unknown_id(mock_indigo):
    """Indigo can fire deleted for an entity we never indexed (e.g.
    plugin started after the device was already deleted but the
    callback queue still has the event). Deleting a non-existent row
    should be a no-op, not an error."""
    from indexer import Indexer

    _wire_empty(mock_indigo)
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    phantom = _fake_device(999, "Ghost")
    idx.on_device_deleted(phantom)
    # No exception, no snapshot to remove, no row to drop — clean.
    assert ("device", 999) not in idx._snapshots
