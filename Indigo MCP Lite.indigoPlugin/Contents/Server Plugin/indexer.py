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
        """Drop and recreate the entities table.

        Idempotent — safe to call from a Reindex Now menu action or
        repeatedly during startup recovery. Subsequent phase tasks
        (6.3, 6.4) extend this to populate rows from the live
        ``indigo.devices`` / ``indigo.variables`` / ``indigo.
        actionGroups`` collections.
        """
        cur = self.connection.cursor()
        cur.execute("DROP TABLE IF EXISTS entities")
        cur.execute(_SCHEMA)
        self._snapshots = {}
        self.connection.commit()
