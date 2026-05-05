"""FTS5-backed in-memory index of all Indigo entities.

Powers ``find_devices`` — Phase 6's natural-language entity search.
Lives entirely in ``:memory:`` SQLite. Rebuilt on plugin start, kept
live via subscriptions to ``deviceCreated``/``Updated``/``Deleted``
(and the variable + action group equivalents) wired in
``plugin.py``.

The state-update short-circuit (``on_*_updated`` returns False when
only state changed) is load-bearing at scale: with ~2,000 devices,
``deviceUpdated`` fires constantly (every brightness change, every
sensor reading), but only static-field changes affect the index.
Without the short-circuit FTS5 thrashes thousands of writes per
minute under normal Indigo activity.

See workspace design doc §7 for the column choice and tokenizer
rationale.
"""

import logging
import sqlite3
from typing import Optional

from type_aliases import TYPE_ALIASES


# Schema choices (workspace design doc §7.1):
# - ``entity_type`` ('device' / 'variable' / 'action') so a single
#   FTS5 table covers all three rather than three separate tables;
#   simpler to query and bm25-rank across types.
# - ``entity_id UNINDEXED`` because we never want to MATCH against the
#   numeric id but we DO want to read it back from result rows.
# - ``description``, ``type_label``, ``folder``, ``aliases``,
#   ``extra`` are all separately indexed columns so bm25 can weight
#   them independently. ``name`` is intentionally first so a column-
#   scoped query (``name:kitchen``) matches the natural intuition.
# - Tokenizer: ``porter unicode61 remove_diacritics 2`` —
#   ``porter`` for stemming (lights/light/lighting collapse), then
#   ``unicode61`` for non-ASCII handling, then aggressive diacritic
#   removal so "café" matches "cafe".
_SCHEMA = """
CREATE VIRTUAL TABLE entities USING fts5(
    entity_type,
    entity_id UNINDEXED,
    name,
    description,
    type_label,
    folder,
    aliases,
    extra,
    tokenize = 'porter unicode61 remove_diacritics 2'
)
"""


class Indexer:
    """In-memory FTS5 index covering devices, variables, and action groups.

    Dependency-injects the ``indigo`` module rather than importing
    it directly — same pattern as ``tools/lookup.py``, ``tools/
    control.py``, ``tools/system.py``. Keeps ``Indexer`` testable
    under pytest without monkey-patching ``sys.modules``.

    ``_snapshots`` caches the static-field snapshot for every
    indexed entity (keyed by ``(entity_type, entity_id)``). The
    short-circuit handlers compare a freshly-derived snapshot
    against the cached one; equality means no static field changed
    so the FTS5 row doesn't need rewriting.
    """

    def __init__(self, *, indigo_module, logger: Optional[logging.Logger] = None):
        self.indigo = indigo_module
        self.logger = logger or logging.getLogger("Plugin")
        self.connection = sqlite3.connect(":memory:")
        self._snapshots: dict = {}

    def build(self):
        """Drop and recreate the entities table, then populate from
        the live Indigo collections.

        Idempotent — safe to call from a Reindex Now menu action or
        repeatedly during startup recovery. Variables and action
        groups land in Task 6.4.
        """
        cur = self.connection.cursor()
        cur.execute("DROP TABLE IF EXISTS entities")
        cur.execute(_SCHEMA)
        self._snapshots = {}

        for dev in self.indigo.devices:
            self._insert_device(cur, dev)

        self.connection.commit()

    # ---------------------------------------------------------------
    # Device indexing helpers
    # ---------------------------------------------------------------

    def _device_folder_name(self, folder_id):
        """Resolve a device folder id to its display name.

        Uses ``indigo.devices.folders.getName`` (the read side of the
        SDK's folder collection). Returns ``""`` for the root folder
        or any unresolvable id — folders can be deleted out from
        under us, and an empty string is what FTS5 expects (NULL
        would reject the insert).
        """
        try:
            return self.indigo.devices.folders.getName(folder_id) or ""
        except Exception:
            return ""

    def _device_snapshot(self, dev):
        """Capture the static fields that affect the FTS5 row.

        Returns a tuple — hashable, cheap to compare in the short-
        circuit handler. Anything that could change ``name`` /
        ``description`` / ``type_label`` / ``folder`` / ``extra``
        belongs here. State fields (``brightness``, ``onState``,
        ``batteryLevel``, ``states[*]``) deliberately do not — they
        change constantly and must NOT trigger reindex.
        """
        return (
            getattr(dev, "name", ""),
            getattr(dev, "description", "") or "",
            getattr(dev, "deviceTypeId", "") or "",
            getattr(dev, "folderId", 0),
            getattr(dev, "model", "") or "",
            getattr(dev, "address", "") or "",
        )

    def _insert_device(self, cursor, dev):
        """Insert one device row + cache its snapshot.

        Centralised so the initial-sweep build, the create handler
        (Task 6.6), and the short-circuit reindex (Task 6.5) all
        share the same row shape and snapshot bookkeeping.
        """
        folder_name = self._device_folder_name(getattr(dev, "folderId", 0))
        aliases = TYPE_ALIASES.get(getattr(dev, "deviceTypeId", ""), "")
        extra = " ".join(
            s for s in (
                getattr(dev, "model", "") or "",
                getattr(dev, "address", "") or "",
            ) if s
        )
        cursor.execute(
            "INSERT INTO entities (entity_type, entity_id, name, description, "
            "type_label, folder, aliases, extra) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "device",
                getattr(dev, "id", 0),
                getattr(dev, "name", "") or "",
                getattr(dev, "description", "") or "",
                getattr(dev, "deviceTypeId", "") or "",
                folder_name,
                aliases,
                extra,
            ),
        )
        self._snapshots[("device", getattr(dev, "id", 0))] = self._device_snapshot(dev)
