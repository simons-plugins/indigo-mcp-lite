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
_VALID_ENTITY_TYPES = ("device", "variable", "action")


def _build_match_query(query, room):
    """Compose the FTS5 MATCH expression from the user query + room.

    ``room`` is folded into the MATCH expression as a column-scoped
    phrase (``folder:"<room>"``) rather than a SQL WHERE on the
    ``folder`` column — FTS5's column scope plays better with
    porter-tokenized matching (case-insensitive, stemming-aware) and
    matches our intuition that room is part of the search query, not
    a separate filter axis.
    """
    parts = [query]
    if room is not None:
        if not isinstance(room, str) or not room.strip():
            raise ValueError("room must be a non-empty string when provided")
        # Escape embedded double-quotes by doubling per FTS5 phrase rules.
        escaped = room.replace('"', '""')
        parts.append(f'folder:"{escaped}"')
    return " AND ".join(parts)


def _normalise_entity_type(value):
    """Coerce ``entity_type`` arg to a tuple of valid type strings."""
    if value is None:
        return None
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError(
            "entity_type must be a string or list of strings"
        )
    out = []
    for item in items:
        if not isinstance(item, str) or item not in _VALID_ENTITY_TYPES:
            raise ValueError(
                f"entity_type values must be one of: "
                f"{', '.join(_VALID_ENTITY_TYPES)} (got {item!r})"
            )
        out.append(item)
    if not out:
        raise ValueError("entity_type must not be empty")
    return tuple(out)


def _execute_search(query, indexer, *, type_filter=None, entity_type_filter=None):
    """Run the FTS5 MATCH query plus any SQL WHERE filters.

    ``query`` is the already-composed FTS5 expression (with ``room``
    folded in by the caller). ``type_filter`` is the device-type
    string (e.g. ``"dimmer"``); ``entity_type_filter`` is the tuple
    of allowed entity types (or None for all).
    """
    sql_parts = [
        "SELECT entity_type, entity_id, name, description, type_label,",
        f"folder, bm25(entities, {_BM25_WEIGHTS}) AS score",
        "FROM entities WHERE entities MATCH ?",
    ]
    params = [query]

    if type_filter is not None:
        sql_parts.append("AND type_label = ?")
        params.append(type_filter)
    if entity_type_filter is not None:
        placeholders = ", ".join(["?"] * len(entity_type_filter))
        sql_parts.append(f"AND entity_type IN ({placeholders})")
        params.extend(entity_type_filter)

    sql_parts.append("ORDER BY score")

    cur = indexer.connection.execute(" ".join(sql_parts), params)
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

    ``query`` is required (non-empty string). Optional filters:
    ``room`` (folder name, case-insensitive), ``type`` (deviceTypeId
    exact match), ``entity_type`` (string or list of
    device/variable/action). Pagination and FTS5-syntax fallback land
    in Tasks 6.10-6.11.
    """
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query required (non-empty string)")

    type_filter = args.get("type")
    if type_filter is not None and not isinstance(type_filter, str):
        raise ValueError("type must be a string when provided")

    entity_type_filter = _normalise_entity_type(args.get("entity_type"))

    fts_query = _build_match_query(query, args.get("room"))

    results = _execute_search(
        fts_query, indexer,
        type_filter=type_filter,
        entity_type_filter=entity_type_filter,
    )
    return {
        "results": results,
        "total_count": len(results),
        "offset": 0,
        "limit": len(results),
        "has_more": False,
    }
