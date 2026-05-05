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

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500


def _normalise_pagination(args):
    """Coerce + clamp ``limit`` and ``offset`` for find_devices.

    Mirrors ``tools.lookup._normalize_pagination`` in spirit (so the
    wire shape matches the lookup tools) but lives here separately
    because find_devices uses a slightly different fetch pattern
    (LIMIT/OFFSET on the SQL query, not Python slicing of a list).
    """
    raw_limit = args.get("limit", _DEFAULT_LIMIT)
    limit = max(1, min(_MAX_LIMIT, int(raw_limit)))
    offset = max(0, int(args.get("offset", 0)))
    return limit, offset


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


def _build_where_clause(type_filter, entity_type_filter):
    """Return the trailing ``AND ...`` clauses + parameter list shared
    by the count and result queries.

    Pulled out so the COUNT(*) query and the SELECT query stay
    perfectly in sync — any filter added on one side must apply to
    the other or pagination metadata silently drifts.
    """
    parts = []
    params = []
    if type_filter is not None:
        parts.append("AND type_label = ?")
        params.append(type_filter)
    if entity_type_filter is not None:
        placeholders = ", ".join(["?"] * len(entity_type_filter))
        parts.append(f"AND entity_type IN ({placeholders})")
        params.extend(entity_type_filter)
    return " ".join(parts), params


def _execute_search(query, indexer, *, type_filter=None, entity_type_filter=None,
                     limit, offset):
    """Run the FTS5 MATCH query plus any SQL WHERE filters and return
    ``(rows, total_count)``.

    The total_count comes from a separate ``COUNT(*)`` query so
    pagination metadata stays correct under ``LIMIT``/``OFFSET`` —
    if we just used ``len(rows)`` callers couldn't distinguish "this
    is the last page" from "this is one of many pages".
    """
    where_extra, where_params = _build_where_clause(
        type_filter, entity_type_filter
    )

    count_sql = f"SELECT COUNT(*) FROM entities WHERE entities MATCH ? {where_extra}"
    total_count = indexer.connection.execute(
        count_sql, [query] + where_params
    ).fetchone()[0]

    select_sql = (
        "SELECT entity_type, entity_id, name, description, type_label, "
        f"folder, bm25(entities, {_BM25_WEIGHTS}) AS score "
        f"FROM entities WHERE entities MATCH ? {where_extra} "
        "ORDER BY score LIMIT ? OFFSET ?"
    )
    cur = indexer.connection.execute(
        select_sql, [query] + where_params + [limit, offset]
    )
    rows = [
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
    return rows, total_count


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
    limit, offset = _normalise_pagination(args)

    try:
        results, total_count = _execute_search(
            fts_query, indexer,
            type_filter=type_filter,
            entity_type_filter=entity_type_filter,
            limit=limit, offset=offset,
        )
    except sqlite3.OperationalError as exc:
        # FTS5 raises OperationalError for malformed query syntax —
        # bare AND/OR at unexpected positions, unmatched parens,
        # standalone column-scope tokens. Retry the whole query as
        # a quoted phrase so it falls back to a literal-substring
        # match. Anything that's still malformed after wrapping
        # bubbles up — that's a bug worth knowing about.
        if "fts5" not in str(exc).lower() and "syntax" not in str(exc).lower():
            raise
        escaped = query.replace('"', '""')
        fts_query = _build_match_query(f'"{escaped}"', args.get("room"))
        results, total_count = _execute_search(
            fts_query, indexer,
            type_filter=type_filter,
            entity_type_filter=entity_type_filter,
            limit=limit, offset=offset,
        )

    return {
        "results": results,
        "total_count": total_count,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(results) < total_count,
    }
