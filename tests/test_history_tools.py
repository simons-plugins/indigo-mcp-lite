"""Tests for the SQL Logger history tools and the trimmed HistoryDB.

The SQLite path is exercised against a real temp-file database — the
strict column allowlist, bucketing, and read-only PRAGMA are asserted
on actual sqlite3 behaviour, not mocks. The PG path is only tested at
the env-construction level (no live psql in CI).
"""

import os
import sqlite3
import time
from unittest.mock import MagicMock, patch

import pytest

from history_db import HistoryDB
from tools.history import (
    _list_columns_handler,
    _query_sql_logger_handler,
)


# ----- fixtures ----------------------------------------------------------


@pytest.fixture
def sqlite_db(tmp_path):
    """Real SQL-Logger-shaped SQLite DB with one device table."""
    path = str(tmp_path / "indigo_history.sqlite")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE device_history_42 ("
        "id INTEGER PRIMARY KEY, ts TIMESTAMP, "
        "onOffState BOOLEAN, brightness INTEGER, sensorValue REAL)"
    )
    now = time.time()
    rows = [
        (i, _ts(now - 600 * i), i % 2 == 0, 10 * i, 20.5 + i)
        for i in range(5)
    ]
    conn.executemany(
        "INSERT INTO device_history_42 VALUES (?, ?, ?, ?, ?)", rows
    )
    conn.commit()
    conn.close()
    return path


def _ts(epoch):
    import datetime
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")


def _db(sqlite_path):
    return HistoryDB(db_type="sqlite", logger=MagicMock(), sqlite_path=sqlite_path)


def _provider(db):
    return lambda: db


# ----- HistoryDB against real SQLite -------------------------------------


def test_get_columns_excludes_id_ts(sqlite_db):
    cols = _db(sqlite_db).get_columns(42)
    names = {c["name"] for c in cols}
    assert names == {"onOffState", "brightness", "sensorValue"}


def test_query_history_case_folds_column(sqlite_db):
    result = _db(sqlite_db).query_history(42, "sensorvalue", "1h")
    assert result["type"] == "float"
    assert result["points"]
    assert result["current"] is not None


def test_query_history_unknown_column_raises_with_available(sqlite_db):
    with pytest.raises(ValueError, match="available: .*brightness"):
        _db(sqlite_db).query_history(42, 'evil"; DROP TABLE x;--', "1h")


def test_query_history_missing_table_raises(sqlite_db):
    with pytest.raises(ValueError, match="no SQL Logger history for device 999"):
        _db(sqlite_db).query_history(999, "brightness", "1h")


def test_query_history_bool_column_maps_to_floats(sqlite_db):
    result = _db(sqlite_db).query_history(42, "onOffState", "1h")
    assert result["type"] == "bool"
    assert set(p["v"] for p in result["points"]) <= {0.0, 1.0}


def test_query_history_bucketed_numeric(sqlite_db):
    result = _db(sqlite_db).query_history(42, "brightness", "24h")
    assert result["points"]
    assert result["min"] is not None and result["max"] is not None


def test_sqlite_is_read_only(sqlite_db):
    db = _db(sqlite_db)
    with pytest.raises(Exception):
        db._execute_sqlite("INSERT INTO device_history_42 (id) VALUES (99)")


def test_get_device_tables(sqlite_db):
    assert _db(sqlite_db).get_device_tables() == [42]


# ----- PG env hardening ---------------------------------------------------


def test_pg_env_sets_read_only_and_password():
    db = HistoryDB(db_type="postgresql", logger=MagicMock(), pg_password="pw")
    captured = {}

    def fake_run(cmd, capture_output, text, timeout, env):
        captured["env"] = env
        return type("R", (), {"returncode": 0, "stdout": "1\n", "stderr": ""})()

    with patch("history_db.subprocess.run", side_effect=fake_run):
        db._execute_pg("SELECT 1")
    assert captured["env"]["PGOPTIONS"] == "-c default_transaction_read_only=on"
    assert captured["env"]["PGPASSWORD"] == "pw"


# ----- tool handlers ------------------------------------------------------


def test_tool_query_happy_path(sqlite_db):
    result = _query_sql_logger_handler(
        {"device_id": 42, "column": "brightness", "time_range": "1h"},
        _provider(_db(sqlite_db)),
    )
    assert result["points"]


def test_tool_query_defaults_time_range(sqlite_db):
    result = _query_sql_logger_handler(
        {"device_id": 42, "column": "brightness"}, _provider(_db(sqlite_db))
    )
    assert "points" in result


def test_tool_query_rejects_bad_time_range(sqlite_db):
    with pytest.raises(ValueError, match="time_range"):
        _query_sql_logger_handler(
            {"device_id": 42, "column": "brightness", "time_range": "99d"},
            _provider(_db(sqlite_db)),
        )


def test_tool_query_rejects_non_string_column():
    with pytest.raises(ValueError, match="column"):
        _query_sql_logger_handler(
            {"device_id": 42, "column": 7}, _provider(None)
        )


def test_tool_query_unconfigured_friendly_error():
    with pytest.raises(ValueError, match="not configured"):
        _query_sql_logger_handler(
            {"device_id": 42, "column": "brightness"}, lambda: None
        )


def test_tool_query_db_failure_becomes_value_error(sqlite_db):
    db = _db(sqlite_db)
    db.query_history = MagicMock(side_effect=RuntimeError("psql exploded"))
    with pytest.raises(ValueError, match="SQL Logger query failed"):
        _query_sql_logger_handler(
            {"device_id": 42, "column": "brightness"}, _provider(db)
        )


def test_tool_list_columns(sqlite_db):
    result = _list_columns_handler({"device_id": 42}, _provider(_db(sqlite_db)))
    assert result["device_id"] == 42
    assert {"name": "brightness", "type": "int"} in result["columns"]


def test_tool_list_columns_missing_table(sqlite_db):
    with pytest.raises(ValueError, match="999"):
        _list_columns_handler({"device_id": 999}, _provider(_db(sqlite_db)))


def test_register_all_registers_history_tools(mock_indigo):
    from tool_registry import register_all

    handler = MagicMock()
    register_all(handler, indigo_module=mock_indigo)
    names = [
        (call.kwargs.get("name") or (call.args[0] if call.args else None))
        for call in handler.register_tool.call_args_list
    ]
    assert "query_sql_logger" in names
    assert "list_sql_logger_columns" in names
