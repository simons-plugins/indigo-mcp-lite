"""SQL Logger history tools — read-only time-series over device history.

Ported from home-intelligence per the 2026-07 re-homing decision: SQL
Logger history is general Indigo data, so it belongs on the general
MCP surface. ``query_sql_logger`` keeps HI's exact tool name and wire
contract so existing prompts and docs carry over unchanged;
``list_sql_logger_columns`` is new — it answers "what can I chart for
this device?" which the old surface left to guesswork.

``history_db_provider`` is a zero-arg callable returning the current
HistoryDB (or None when unconfigured) — a provider rather than an
instance so plugin.py can swap the connection when prefs change
without re-registering tools.
"""

from tools.lookup import _require_int_id

_ALLOWED_TIME_RANGES = ("1h", "6h", "24h", "7d", "30d")

_UNCONFIGURED_MSG = (
    "SQL Logger access is not configured. Set the database type and "
    "connection details in the Indigo MCP Lite plugin config first."
)


def register(handler, *, history_db_provider, **_):
    """Register the SQL Logger history tools onto the given MCPHandler."""
    handler.register_tool(
        name="query_sql_logger",
        description=(
            "Query Indigo SQL Logger history for one device + column "
            "over a time range. Returns time-bucketed points plus "
            "min/max/current. Differs from query_event_log (narrated "
            "events); SQL Logger is a per-device-column time series. "
            "Use find_devices / list_devices for device IDs and "
            "list_sql_logger_columns for valid column names."
        ),
        input_schema={
            "type": "object",
            "required": ["device_id", "column"],
            "properties": {
                "device_id": {
                    "type": "integer",
                    "description": "Indigo device ID (integer).",
                },
                "column": {
                    "type": "string",
                    "description": (
                        "SQL Logger column name — e.g. `onOffState`, "
                        "`brightness`, `sensorValue`, `accumEnergyTotal`. "
                        "Case-folded internally; must match a real column "
                        "(see list_sql_logger_columns)."
                    ),
                },
                "time_range": {
                    "type": "string",
                    "enum": list(_ALLOWED_TIME_RANGES),
                    "description": (
                        "Pre-defined window + bucket size. `1h` is raw, "
                        "others bucket for readability. Default `24h`."
                    ),
                    "default": "24h",
                },
            },
            "additionalProperties": False,
        },
        handler=lambda **args: _query_sql_logger_handler(args, history_db_provider),
    )
    handler.register_tool(
        name="list_sql_logger_columns",
        description=(
            "List the SQL Logger history columns (name + type) recorded "
            "for one device — the valid `column` values for "
            "query_sql_logger. Errors if the device has no history table."
        ),
        input_schema={
            "type": "object",
            "required": ["device_id"],
            "properties": {"device_id": {"type": "integer"}},
            "additionalProperties": False,
        },
        handler=lambda **args: _list_columns_handler(args, history_db_provider),
    )


def _require_history_db(history_db_provider):
    history_db = history_db_provider()
    if history_db is None:
        raise ValueError(_UNCONFIGURED_MSG)
    return history_db


def _query_sql_logger_handler(args, history_db_provider):
    device_id = _require_int_id(args, key="device_id")
    column = args.get("column")
    if not isinstance(column, str) or not column.strip():
        raise ValueError("column must be a non-empty string (SQL Logger column name)")
    time_range = args.get("time_range", "24h")
    if time_range not in _ALLOWED_TIME_RANGES:
        raise ValueError(
            f"time_range must be one of {list(_ALLOWED_TIME_RANGES)}, "
            f"got {time_range!r}"
        )
    history_db = _require_history_db(history_db_provider)
    try:
        return history_db.query_history(
            device_id=device_id, column=column, time_range=time_range
        )
    except ValueError:
        raise  # already a friendly tool-result error (bad column, no table)
    except Exception as exc:
        # DB-level failures (connection, psql) surface as tool-result
        # errors, not protocol -32603 — the agent should adjust inputs
        # or report the config problem, not back off.
        raise ValueError(f"SQL Logger query failed: {exc}")


def _list_columns_handler(args, history_db_provider):
    device_id = _require_int_id(args, key="device_id")
    history_db = _require_history_db(history_db_provider)
    try:
        columns = history_db.get_columns(device_id)
    except Exception as exc:
        # Connection-level failure — distinct from "no history table".
        raise ValueError(f"SQL Logger query failed: {exc}")
    if not columns:
        raise ValueError(
            f"no SQL Logger history for device {device_id} "
            f"(table device_history_{device_id} missing or unreadable)"
        )
    return {"device_id": device_id, "columns": columns}
