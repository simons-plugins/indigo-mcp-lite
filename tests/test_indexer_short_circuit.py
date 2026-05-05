"""TDD tests for the static-field short-circuit in
``on_*_updated`` handlers.

The short-circuit is the load-bearing piece at scale: ~2,000
devices × constant deviceUpdated firing for state changes (every
brightness change, every sensor reading) = thousands of FTS5
writes per minute without it. Each on_*_updated builds a fresh
snapshot of static fields and compares against the cached one;
equality means no reindex.
"""
from unittest.mock import MagicMock


class _AttrList(list):
    pass


def _fake_device(id_, name, type_id="dimmer", folder_id=0,
                 description="", model="", address=""):
    d = MagicMock()
    d.id = id_; d.name = name; d.deviceTypeId = type_id
    d.folderId = folder_id; d.description = description
    d.model = model; d.address = address
    return d


def _fake_variable(id_, name, value="", folder_id=0):
    v = MagicMock()
    v.id = id_; v.name = name; v.value = value; v.folderId = folder_id
    return v


def _fake_action(id_, name, folder_id=0, description=""):
    a = MagicMock()
    a.id = id_; a.name = name; a.folderId = folder_id; a.description = description
    return a


def _wire(mock_indigo, devs=None, vars_=None, actions=None,
          dev_folders=None, var_folders=None):
    d = _AttrList(devs or [])
    d.folders = MagicMock()
    d.folders.getName.side_effect = lambda fid: (dev_folders or {}).get(fid, "")
    mock_indigo.devices = d

    v = _AttrList(vars_ or [])
    v.folders = MagicMock()
    v.folders.getName.side_effect = lambda fid: (var_folders or {}).get(fid, "")
    mock_indigo.variables = v

    mock_indigo.actionGroups = _AttrList(actions or [])


# ----- devices -----------------------------------------------------------


def test_on_device_updated_skips_when_only_state_changed(mock_indigo):
    """A brightness flip / state-only change should NOT reindex —
    the static fields haven't changed."""
    from indexer import Indexer

    dev = _fake_device(1, "Lamp", description="orig")
    _wire(mock_indigo, devs=[dev])
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    # New device object, same static fields — but state changed.
    updated = _fake_device(1, "Lamp", description="orig")
    updated.brightness = 99
    updated.onState = True

    assert idx.on_device_updated(updated) is False


def test_on_device_updated_reindexes_when_name_changed(mock_indigo):
    from indexer import Indexer

    dev = _fake_device(1, "Lamp")
    _wire(mock_indigo, devs=[dev])
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    renamed = _fake_device(1, "Bedside Lamp")
    assert idx.on_device_updated(renamed) is True

    new_name = idx.connection.execute(
        "SELECT name FROM entities WHERE entity_id=1"
    ).fetchone()[0]
    assert new_name == "Bedside Lamp"


def test_on_device_updated_reindexes_when_folder_changed(mock_indigo):
    from indexer import Indexer

    dev = _fake_device(1, "Lamp", folder_id=10)
    _wire(mock_indigo, devs=[dev], dev_folders={10: "Kitchen", 20: "Bedroom"})
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    moved = _fake_device(1, "Lamp", folder_id=20)
    assert idx.on_device_updated(moved) is True

    new_folder = idx.connection.execute(
        "SELECT folder FROM entities WHERE entity_id=1"
    ).fetchone()[0]
    assert new_folder == "Bedroom"


def test_on_device_updated_reindexes_when_description_changed(mock_indigo):
    from indexer import Indexer

    dev = _fake_device(1, "Lamp", description="orig")
    _wire(mock_indigo, devs=[dev])
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    edited = _fake_device(1, "Lamp", description="new desc")
    assert idx.on_device_updated(edited) is True


def test_on_device_updated_unknown_id_treated_as_create(mock_indigo):
    """If we somehow get an updated event for a device the indexer
    has no snapshot for (e.g. plugin missed a created event during a
    transient outage), treat it as a create — better than dropping
    the row entirely."""
    from indexer import Indexer

    _wire(mock_indigo, devs=[])
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    new_dev = _fake_device(99, "Surprise Lamp")
    assert idx.on_device_updated(new_dev) is True
    assert idx.connection.execute(
        "SELECT count(*) FROM entities WHERE entity_id=99"
    ).fetchone()[0] == 1


# ----- variables ---------------------------------------------------------


def test_on_variable_updated_skips_when_only_value_changed(mock_indigo):
    """Variable value flips constantly — must NOT trigger reindex.
    This is the most important variable short-circuit case."""
    from indexer import Indexer

    var = _fake_variable(1, "doorOpen", value="false")
    _wire(mock_indigo, vars_=[var])
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    flipped = _fake_variable(1, "doorOpen", value="true")
    assert idx.on_variable_updated(flipped) is False


def test_on_variable_updated_reindexes_when_name_changed(mock_indigo):
    from indexer import Indexer

    var = _fake_variable(1, "doorOpen")
    _wire(mock_indigo, vars_=[var])
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    renamed = _fake_variable(1, "frontDoorOpen")
    assert idx.on_variable_updated(renamed) is True


# ----- action groups -----------------------------------------------------


def test_on_action_updated_reindexes_when_description_changed(mock_indigo):
    from indexer import Indexer

    action = _fake_action(11, "Goodnight", description="dim everything")
    _wire(mock_indigo, actions=[action])
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    edited = _fake_action(11, "Goodnight", description="dim and lock")
    assert idx.on_action_updated(edited) is True


def test_on_action_updated_skips_when_unchanged(mock_indigo):
    from indexer import Indexer

    action = _fake_action(11, "Goodnight")
    _wire(mock_indigo, actions=[action])
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    same = _fake_action(11, "Goodnight")
    assert idx.on_action_updated(same) is False
