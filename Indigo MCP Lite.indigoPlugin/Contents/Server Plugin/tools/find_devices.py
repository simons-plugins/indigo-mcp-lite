"""find_devices — FTS5-backed natural-language entity search.

Phase 6's headline tool. Wraps a SQLite FTS5 ``MATCH`` query against
the live in-memory index built by ``indexer.py``. Caller passes a
free-text query (``"kitchen"``, ``"leak sensor"``, ``"bedroom
dimmer"``); we run that against the ``entities`` virtual table and
rank by bm25 weights tuned to favour name + folder matches over
description / extra matches.

See workspace design doc §7.4 for the search-quality rationale and
issue #5 for the planned XKCD synonym layer (not yet wired).
"""

import sqlite3


# bm25 column weights (per-column tunable, lower = matches more
# important since FTS5 emits negative scores). Schema column order
# is fixed by the CREATE VIRTUAL TABLE statement in indexer.py:
#
#   entity_type, entity_id, name, description,
#   type_label, folder, aliases, extra
#
# Weights below match that order. ``entity_type`` and ``entity_id``
# are zero-weighted because they're routing/identity fields that
# shouldn't influence rank. ``name=3.0`` outranks ``aliases=2.0``
# so a literal name match beats alias-only expansion (verified in
# tests). ``folder=1.5`` ranks slightly above description so
# room-scoped queries surface room-tagged entities before random
# description hits.
_BM25_WEIGHTS = "0, 0, 3.0, 1.0, 1.0, 1.5, 2.0, 0.5"


def _execute_search(query, indexer):
    """Run the FTS5 MATCH query and return list-of-dict rows.

    Caller provides the FTS5-syntax query string. Score is the
    bm25 value SQLite emits — more-negative = better. Sorting
    ascending puts the best matches first.
    """
    sql = (
        "SELECT entity_type, entity_id, name, description, type_label, "
        f"folder, bm25(entities, {_BM25_WEIGHTS}) AS score "
        "FROM entities WHERE entities MATCH ? ORDER BY score"
    )
    cur = indexer.connection.execute(sql, (query,))
    return [
        {
            "entity_type": r[0],
            "id": r[1],
            "name": r[2],
            "description": r[3],
            "type_label": r[4],
            "folder": r[5],
            "score": r[6],
        }
        for r in cur.fetchall()
    ]


def _find_devices_handler(args, *, indexer):
    """Run ``find_devices`` against the FTS5 index.

    ``query`` is required (non-empty string). Filters and pagination
    are layered on in subsequent tasks (6.9, 6.10).
    """
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query required (non-empty string)")

    results = _execute_search(query, indexer)
    return {
        "results": results,
        "total_count": len(results),
        "offset": 0,
        "limit": len(results),
        "has_more": False,
    }
