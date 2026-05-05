"""System tools — server-level queries and plugin lifecycle.

Phase 5 surface: ``query_event_log`` over the live Indigo event log,
``list_plugins`` / ``get_plugin_by_id`` / ``get_plugin_status`` over
the installed plugin catalogue, and ``restart_plugin`` for one-shot
plugin lifecycle control. Tool registrations follow the same
``lambda **args:`` shape as Phases 3-4 (handoff adjudication #6).
"""

from datetime import datetime


_DEFAULT_LOG_LIMIT = 50
_MAX_LOG_LIMIT = 500
_VALID_LEVELS = ("INFO", "WARNING", "ERROR")
# Substrings that mark a log message as a given severity. Indigo's
# event log doesn't expose a structured level, so we approximate by
# matching well-known prefixes the SDK uses when calling
# ``indigo.server.error`` / ``log("Warning: …")`` etc. ERROR pulls in
# the word "Exception" too because plugin tracebacks emit that line.
_LEVEL_KEYWORDS = {
    "ERROR": ("error", "exception"),
    "WARNING": ("warning",),
    "INFO": (),  # everything matches INFO — used as the no-op level
}


def _parse_log_timestamp(value):
    """Parse an Indigo log entry's TimeStamp into a naive datetime.

    Indigo emits two formats: ``YYYY-MM-DD HH:MM:SS.fff`` (the file
    log shape) and ISO 8601 with timezone (the live-log shape). We
    handle both and strip any tzinfo so comparisons against the
    user's ``since`` argument (which we also normalise) are coherent.
    Returns None for any unparseable input — callers should drop
    those rows when filtering by time.
    """
    if value is None:
        return None
    if hasattr(value, "astimezone"):
        try:
            local = value.astimezone() if value.tzinfo else value
            return local.replace(tzinfo=None)
        except Exception:  # pragma: no cover - defensive
            return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        local = dt.astimezone() if dt.tzinfo else dt
        return local.replace(tzinfo=None)


def _query_event_log_handler(args, indigo_module):
    """Return paginated, structured live-event-log entries.

    ``limit`` clamps to [1, 500] (default 50). ``since`` (optional
    ISO 8601 string) filters to entries strictly after that instant —
    entries whose timestamp can't be parsed are dropped under
    ``since`` filtering, since dropping them is safer than including
    them at the wrong sort position. ``level`` (optional INFO /
    WARNING / ERROR) is a substring-match approximation because
    Indigo's log doesn't expose a structured severity field — see
    ``_LEVEL_KEYWORDS`` for the mapping. ``INFO`` matches everything
    by design, mirroring Indigo's "all messages are info unless they
    say otherwise" convention.
    """
    raw_limit = args.get("limit", _DEFAULT_LOG_LIMIT)
    limit = max(1, min(_MAX_LOG_LIMIT, int(raw_limit)))

    since_dt = None
    since_raw = args.get("since")
    if since_raw is not None:
        try:
            normalised = str(since_raw).replace("Z", "+00:00")
            since_dt = datetime.fromisoformat(normalised)
            if since_dt.tzinfo is not None:
                since_dt = since_dt.astimezone().replace(tzinfo=None)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"since must be an ISO 8601 datetime, got {since_raw!r}"
            ) from exc

    level = None
    level_raw = args.get("level")
    if level_raw is not None:
        if not isinstance(level_raw, str):
            raise ValueError(
                "level must be a string (one of: INFO, WARNING, ERROR)"
            )
        level = level_raw.upper()
        if level not in _VALID_LEVELS:
            raise ValueError(
                f"level must be one of: {', '.join(_VALID_LEVELS)}; "
                f"got {level_raw!r}"
            )

    raw_entries = indigo_module.server.getEventLogList(
        returnAsList=True, showTimeStamp=True, lineCount=limit
    ) or []

    results = []
    for entry in raw_entries:
        try:
            d = dict(entry)
        except (TypeError, ValueError):
            continue
        ts_raw = d.get("TimeStamp")
        message = str(d.get("Message", ""))

        if since_dt is not None:
            entry_dt = _parse_log_timestamp(ts_raw)
            if entry_dt is None or entry_dt < since_dt:
                continue

        if level is not None and level != "INFO":
            keywords = _LEVEL_KEYWORDS[level]
            if not any(k in message.lower() for k in keywords):
                continue

        results.append({
            "timestamp": str(ts_raw) if ts_raw is not None else "",
            "source": str(d.get("TypeStr", "")),
            "message": message,
        })

    return {
        "results": results,
        "total_count": len(results),
        "limit": limit,
    }


def _require_plugin_id(args):
    """Pull a non-empty ``plugin_id`` string out of args or raise.

    Plugin ids are reverse-DNS strings (e.g. ``com.foo.bar``) — we
    don't enforce that pattern here since Indigo handles the
    not-found case, but the type and emptiness checks live here so
    every plugin tool surfaces the same error shape.
    """
    raw = args.get("plugin_id")
    if not isinstance(raw, str) or not raw:
        raise ValueError("plugin_id must be a non-empty string")
    return raw


def _serialize_plugin(plugin):
    """Stable dict shape for an Indigo plugin object.

    Status methods (``isEnabled``/``isRunning``/``isInstalled``) are
    callables, not properties, so each is invoked once per call —
    don't replace the parens with attribute access.
    """
    return {
        "plugin_id": getattr(plugin, "pluginId", ""),
        "display_name": getattr(plugin, "pluginDisplayName", ""),
        "version": getattr(plugin, "pluginVersion", ""),
        "folder_path": getattr(plugin, "pluginFolderPath", ""),
        "enabled": bool(plugin.isEnabled()),
        "running": bool(plugin.isRunning()),
        "installed": bool(plugin.isInstalled()),
    }


def _lookup_plugin_or_raise(indigo_module, plugin_id):
    """Resolve a plugin id to a plugin object, mapping any SDK error
    (KeyError / IndexError / ValueError / generic Exception) into a
    clean ValueError so the JSON-RPC layer maps it to ``isError``."""
    try:
        return indigo_module.server.getPlugin(plugin_id)
    except (KeyError, IndexError, ValueError, Exception) as exc:
        raise ValueError(f"no plugin with id {plugin_id!r}") from exc


def _list_plugins_handler(_args, indigo_module):
    """Return all installed plugins, ordered as Indigo lists them."""
    ids = indigo_module.server.getPluginList() or []
    results = []
    for pid in ids:
        try:
            plugin = indigo_module.server.getPlugin(pid)
        except Exception:
            # Plugins can disappear between getPluginList and the
            # individual getPlugin call (install/uninstall race);
            # skip rather than fail the whole listing.
            continue
        results.append(_serialize_plugin(plugin))
    return {"results": results, "total_count": len(results)}


def _get_plugin_by_id_handler(args, indigo_module):
    """Return one serialised plugin by id."""
    plugin_id = _require_plugin_id(args)
    return _serialize_plugin(_lookup_plugin_or_raise(indigo_module, plugin_id))


def _get_plugin_status_handler(args, indigo_module):
    """Return runtime state (enabled/running/installed) only.

    Subset of ``get_plugin_by_id`` for callers who just want the
    health flags without the static metadata. Cheaper at the wire
    layer for poll-style use cases.
    """
    plugin_id = _require_plugin_id(args)
    plugin = _lookup_plugin_or_raise(indigo_module, plugin_id)
    return {
        "plugin_id": getattr(plugin, "pluginId", plugin_id),
        "enabled": bool(plugin.isEnabled()),
        "running": bool(plugin.isRunning()),
        "installed": bool(plugin.isInstalled()),
    }


_PLUGIN_ID_SCHEMA = {
    "type": "object",
    "required": ["plugin_id"],
    "properties": {"plugin_id": {"type": "string"}},
}


def register(handler, *, indigo_module):
    """Register every system tool onto the given MCPHandler."""
    handler.register_tool(
        name="query_event_log",
        description=(
            "Read entries from Indigo's live event log. ``limit`` "
            "(default 50, max 500) caps the number of entries fetched. "
            "``since`` (optional ISO 8601 datetime) filters to entries "
            "strictly after that instant — unparseable timestamps are "
            "dropped under since-filtering. ``level`` (optional INFO / "
            "WARNING / ERROR) approximates severity by matching well-"
            "known message prefixes; INFO matches everything by design."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "since": {"type": "string"},
                "level": {"type": "string",
                          "enum": list(_VALID_LEVELS)},
            },
        },
        handler=lambda **args: _query_event_log_handler(args, indigo_module),
    )
    handler.register_tool(
        name="list_plugins",
        description=(
            "List every installed Indigo plugin with id, display name, "
            "version, install path, and live enabled/running/installed "
            "flags. Plugins that disappear between enumeration and "
            "lookup (rare install/uninstall race) are skipped silently."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=lambda **args: _list_plugins_handler(args, indigo_module),
    )
    handler.register_tool(
        name="get_plugin_by_id",
        description=(
            "Return a single plugin by id with full metadata + live "
            "enabled/running/installed flags. Raises if the id is not "
            "installed."
        ),
        input_schema=_PLUGIN_ID_SCHEMA,
        handler=lambda **args: _get_plugin_by_id_handler(args, indigo_module),
    )
    handler.register_tool(
        name="get_plugin_status",
        description=(
            "Return only the live status flags (enabled/running/"
            "installed) for one plugin. Cheaper than get_plugin_by_id "
            "for poll-style health checks where the static metadata "
            "isn't needed."
        ),
        input_schema=_PLUGIN_ID_SCHEMA,
        handler=lambda **args: _get_plugin_status_handler(args, indigo_module),
    )
