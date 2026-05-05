"""TDD tests for Indexer.build() inserting device rows.

Indexer takes ``indigo.devices`` (a list-like) and inserts one row
per device with name/description/type_label/folder/aliases/extra
populated. Folder names come from ``indigo.devices.folders.getName``.
Aliases come from ``TYPE_ALIASES`` lookup on ``deviceTypeId``.
"""
from unittest.mock import MagicMock


def _fake_device(id_, name, type_id="dimmer", folder_id=0,
                 description="", model="", address=""):
    d = MagicMock()
    d.id = id_
    d.name = name
    d.deviceTypeId = type_id
    d.folderId = folder_id
    d.description = description
    d.model = model
    d.address = address
    return d


class _AttrList(list):
    """``list`` subclass that allows attribute assignment.

    Real ``indigo.devices`` is iterable AND has ``.folders``; a
    plain ``list`` rejects ``.folders = …``. This subclass mirrors
    that shape just enough for tests.
    """


def _wire_devices(mock_indigo, devices, folder_names=None):
    """Set up mock_indigo.devices as a list-with-folders so the
    indexer's ``self.indigo.devices.folders.getName`` call works."""
    devs = _AttrList(devices)
    folders = MagicMock()
    folders.getName.side_effect = lambda fid: (folder_names or {}).get(fid, "")
    devs.folders = folders
    mock_indigo.devices = devs
    mock_indigo.variables = []
    mock_indigo.actionGroups = []


def test_build_indexes_all_devices(mock_indigo):
    from indexer import Indexer

    _wire_devices(
        mock_indigo,
        [_fake_device(1, "Kitchen Dimmer", folder_id=10),
         _fake_device(2, "Bedroom Lamp", folder_id=11)],
        folder_names={10: "Kitchen", 11: "Bedroom"},
    )

    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    count = idx.connection.execute(
        "SELECT count(*) FROM entities WHERE entity_type='device'"
    ).fetchone()[0]
    assert count == 2


def test_indexed_device_includes_aliases(mock_indigo):
    from indexer import Indexer

    _wire_devices(
        mock_indigo,
        [_fake_device(1, "Kitchen Dimmer", type_id="dimmer")],
        folder_names={0: "Kitchen"},
    )

    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    row = idx.connection.execute(
        "SELECT aliases FROM entities WHERE entity_id=1"
    ).fetchone()
    # 'dimmer' alias from TYPE_ALIASES expands to "light lamp bulb fixture lighting"
    assert "light" in row[0]
    assert "lamp" in row[0]


def test_indexed_device_includes_folder_name(mock_indigo):
    from indexer import Indexer

    _wire_devices(
        mock_indigo,
        [_fake_device(1, "Lamp", folder_id=42)],
        folder_names={42: "Living Room"},
    )

    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    row = idx.connection.execute(
        "SELECT folder FROM entities WHERE entity_id=1"
    ).fetchone()
    assert row[0] == "Living Room"


def test_indexed_device_includes_model_and_address_in_extra(mock_indigo):
    from indexer import Indexer

    _wire_devices(
        mock_indigo,
        [_fake_device(1, "Lamp", model="Hue White", address="ABC123")],
    )

    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    row = idx.connection.execute(
        "SELECT extra FROM entities WHERE entity_id=1"
    ).fetchone()
    assert "Hue White" in row[0]
    assert "ABC123" in row[0]


def test_indexed_device_unknown_type_has_empty_aliases(mock_indigo):
    """Devices whose deviceTypeId isn't in TYPE_ALIASES still get
    indexed (just without alias expansion). Better than failing the
    whole build for one unknown type."""
    from indexer import Indexer

    _wire_devices(
        mock_indigo,
        [_fake_device(1, "Some Thing", type_id="custom_unknown_type")],
    )

    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    row = idx.connection.execute(
        "SELECT aliases, name FROM entities WHERE entity_id=1"
    ).fetchone()
    assert row[0] == ""
    assert row[1] == "Some Thing"


def test_build_populates_snapshot_cache(mock_indigo):
    """Snapshot cache is keyed by (entity_type, id) — Task 6.5
    short-circuit relies on it being populated during the initial
    sweep."""
    from indexer import Indexer

    _wire_devices(
        mock_indigo,
        [_fake_device(1, "Kitchen Dimmer", folder_id=10,
                       description="orig", model="X", address="Y")],
        folder_names={10: "Kitchen"},
    )

    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    assert ("device", 1) in idx._snapshots
