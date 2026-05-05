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
        repeatedly during startup recovery. Sweeps in
        device → variable → action order; the order is incidental
        for FTS5 (results rank by bm25 not insertion) but predictable
        ordering helps debugging.
        """
        cur = self.connection.cursor()
        cur.execute("DROP TABLE IF EXISTS entities")
        cur.execute(_SCHEMA)
        self._snapshots = {}

        for dev in self.indigo.devices:
            self._insert_device(cur, dev)
        for var in self.indigo.variables:
            self._insert_variable(cur, var)
        for action in self.indigo.actionGroups:
            self._insert_action(cur, action)

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

    # ---------------------------------------------------------------
    # Variable indexing helpers
    # ---------------------------------------------------------------

    def _variable_folder_name(self, folder_id):
        """Resolve a variable folder id to its display name.

        Same shape as ``_device_folder_name`` but goes through
        ``indigo.variables.folders``.
        """
        try:
            return self.indigo.variables.folders.getName(folder_id) or ""
        except Exception:
            return ""

    def _variable_snapshot(self, var):
        """Static-field tuple for a variable. Variable values change
        constantly (any rule-engine update flips them) — they are
        NOT included so the short-circuit holds for value updates."""
        return (
            getattr(var, "name", ""),
            getattr(var, "folderId", 0),
        )

    def _insert_variable(self, cursor, var):
        """Insert one variable row + cache its snapshot."""
        folder_name = self._variable_folder_name(getattr(var, "folderId", 0))
        cursor.execute(
            "INSERT INTO entities (entity_type, entity_id, name, description, "
            "type_label, folder, aliases, extra) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "variable",
                getattr(var, "id", 0),
                getattr(var, "name", "") or "",
                "",          # variables have no description in the IOM
                "",          # no type_label
                folder_name,
                "",          # no alias expansion
                "",          # no model/address
            ),
        )
        self._snapshots[("variable", getattr(var, "id", 0))] = self._variable_snapshot(var)

    # ---------------------------------------------------------------
    # Action group indexing helpers
    # ---------------------------------------------------------------

    def _action_snapshot(self, action):
        """Static-field tuple for an action group."""
        return (
            getattr(action, "name", ""),
            getattr(action, "description", "") or "",
            getattr(action, "folderId", 0),
        )

    def _insert_action(self, cursor, action):
        """Insert one action group row + cache its snapshot.

        Action groups don't have a separate folder collection in the
        IOM — they share the variable folder space conceptually but
        the SDK doesn't expose a public ``getName``-equivalent for
        ``indigo.actionGroups.folders`` reliably across Indigo
        versions. Leave folder empty for now; users typically
        organise action groups by name prefix anyway.
        """
        cursor.execute(
            "INSERT INTO entities (entity_type, entity_id, name, description, "
            "type_label, folder, aliases, extra) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "action",
                getattr(action, "id", 0),
                getattr(action, "name", "") or "",
                getattr(action, "description", "") or "",
                "",          # no type_label
                "",          # no folder name
                "",          # no aliases
                "",          # no extra
            ),
        )
        self._snapshots[("action", getattr(action, "id", 0))] = self._action_snapshot(action)

    # ---------------------------------------------------------------
    # Update handlers — the load-bearing short-circuit lives here.
    # ---------------------------------------------------------------
    #
    # Each on_*_updated returns True if it reindexed, False if it
    # short-circuited. Plugin.py's deviceUpdated callback doesn't
    # check the return — it's surfaced for tests + future telemetry.
    # ---------------------------------------------------------------

    def _replace_row(self, entity_type, entity_id, insert_fn, entity):
        """Common upsert path: delete existing row, run the
        type-specific insert helper. Caller manages the snapshot
        cache around this so the cache and the row stay in lockstep
        even if commit fails partway."""
        cur = self.connection.cursor()
        cur.execute(
            "DELETE FROM entities WHERE entity_type=? AND entity_id=?",
            (entity_type, entity_id),
        )
        insert_fn(cur, entity)
        self.connection.commit()

    def on_device_updated(self, dev):
        """Reindex a device only if a static field changed.

        Compares a freshly-derived snapshot of the post-update
        device against the cached snapshot. Returns False (no work)
        for state-only changes, True (reindex committed) for static-
        field changes. Unknown ids fall through to create-style
        handling so a missed deviceCreated event doesn't permanently
        leave the entity unindexed.
        """
        entity_id = getattr(dev, "id", 0)
        new_snapshot = self._device_snapshot(dev)
        if self._snapshots.get(("device", entity_id)) == new_snapshot:
            return False
        self._replace_row("device", entity_id, self._insert_device, dev)
        # _insert_device already updates _snapshots, so we're done.
        return True

    def on_variable_updated(self, var):
        """Reindex a variable only if a static field changed.

        Variable VALUES change constantly (any rule update flips
        them). Per ``_variable_snapshot`` only ``(name, folderId)``
        are tracked, so a value flip naturally short-circuits.
        """
        entity_id = getattr(var, "id", 0)
        new_snapshot = self._variable_snapshot(var)
        if self._snapshots.get(("variable", entity_id)) == new_snapshot:
            return False
        self._replace_row("variable", entity_id, self._insert_variable, var)
        return True

    def on_action_updated(self, action):
        """Reindex an action group only if a static field changed.

        Action groups don't change as often as devices/variables but
        the same short-circuit pattern applies for consistency.
        """
        entity_id = getattr(action, "id", 0)
        new_snapshot = self._action_snapshot(action)
        if self._snapshots.get(("action", entity_id)) == new_snapshot:
            return False
        self._replace_row("action", entity_id, self._insert_action, action)
        return True

    # ---------------------------------------------------------------
    # Create handlers — straight insert, no short-circuit possible
    # since we have no prior snapshot to compare against.
    # ---------------------------------------------------------------

    def on_device_created(self, dev):
        cur = self.connection.cursor()
        self._insert_device(cur, dev)
        self.connection.commit()

    def on_variable_created(self, var):
        cur = self.connection.cursor()
        self._insert_variable(cur, var)
        self.connection.commit()

    def on_action_created(self, action):
        cur = self.connection.cursor()
        self._insert_action(cur, action)
        self.connection.commit()

    # ---------------------------------------------------------------
    # Delete handlers — drop the row + snapshot. Safe for unknown ids
    # because Indigo can fire deleted events for entities we never
    # indexed (plugin restart races).
    # ---------------------------------------------------------------

    def _drop_entity(self, entity_type, entity_id):
        cur = self.connection.cursor()
        cur.execute(
            "DELETE FROM entities WHERE entity_type=? AND entity_id=?",
            (entity_type, entity_id),
        )
        self.connection.commit()
        self._snapshots.pop((entity_type, entity_id), None)

    def on_device_deleted(self, dev):
        self._drop_entity("device", getattr(dev, "id", 0))

    def on_variable_deleted(self, var):
        self._drop_entity("variable", getattr(var, "id", 0))

    def on_action_deleted(self, action):
        self._drop_entity("action", getattr(action, "id", 0))
