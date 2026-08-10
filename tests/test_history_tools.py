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
        "onOffState BOOLEAN, brightness INTEGER, sensorValue REAL, "
        "operationState TEXT)"
    )
    # Anchor mid-bucket for the largest bucket size (30d → 10800s) so all
    # five rows always share one bucket — wall-clock `now` straddles a
    # bucket boundary ~22% of the time, which made the latest-per-bucket
    # test flaky. Nudged forward a bucket when the anchor would push the
    # newest rows outside the 1h raw window.
    now = (int(time.time()) // 10800) * 10800 + 5400
    if now < time.time() - 1200:
        now += 10800
    states = ["Ready", "Run", "Run", "Finished", "Idle"]
    rows = [
        (i, _ts(now - 600 * i), i % 2 == 0, 10 * i, 20.5 + i, states[i])
        for i in range(5)
    ]
    conn.executemany(
        "INSERT INTO device_history_42 VALUES (?, ?, ?, ?, ?, ?)", rows
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
    assert names == {"onOffState", "brightness", "sensorValue", "operationState"}


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


# ----- text columns: latest-per-bucket, never AVG (issue #49) -------------


def test_query_history_text_raw_returns_strings(sqlite_db):
    result = _db(sqlite_db).query_history(42, "operationState", "1h")
    assert result["type"] == "text"
    assert [p["v"] for p in result["points"]] == [
        "Idle", "Finished", "Run", "Run", "Ready"]
    assert result["min"] is None and result["max"] is None
    assert result["current"] == "Ready"


def test_query_history_text_bucketed_latest_per_bucket(sqlite_db):
    # 30d → 3h buckets: the fixture anchors all five rows into ONE bucket,
    # and the latest row ("Ready") must win over the earliest ("Idle") —
    # a MIN-vs-MAX regression flips this to "Idle".
    result = _db(sqlite_db).query_history(42, "operationState", "30d")
    assert result["type"] == "text"
    assert len(result["points"]) == 1
    assert result["points"][0]["v"] == "Ready"
    assert result["current"] == "Ready"


def _capture_pg_sql(db, *responses):
    """Run a query_history against canned psql stdout, returning the SQL
    strings handed to psql (the value after each -c)."""
    seen = []
    canned = list(responses)

    def fake_run(cmd, capture_output, text, timeout, env):
        seen.append(cmd[cmd.index("-c") + 1])
        return type("R", (), {"returncode": 0,
                              "stdout": canned.pop(0), "stderr": ""})()

    return seen, patch("history_db.subprocess.run", side_effect=fake_run)


def test_pg_text_column_uses_distinct_on_not_avg():
    db = HistoryDB(db_type="postgresql", logger=MagicMock())
    seen, patcher = _capture_pg_sql(
        db,
        "operationState\ttext\n",              # get_columns
        "1753100000\tRun\n1753100300\tReady\n",  # bucketed text rows
    )
    with patcher:
        result = db.query_history(42, "operationstate", "24h")
    query_sql = seen[-1]
    assert "AVG(" not in query_sql
    assert "DISTINCT ON (bucket)" in query_sql
    assert 'ORDER BY bucket, ts DESC' in query_sql
    assert [p["v"] for p in result["points"]] == ["Run", "Ready"]
    assert result["min"] is None and result["max"] is None
    assert result["current"] == "Ready"


def test_pg_numeric_still_uses_avg():
    db = HistoryDB(db_type="postgresql", logger=MagicMock())
    seen, patcher = _capture_pg_sql(
        db,
        "programProgress\tinteger\n",
        "1753100000\t40\n1753100300\t60\n",
    )
    with patcher:
        result = db.query_history(42, "programprogress", "24h")
    assert "AVG(" in seen[-1]
    assert [p["v"] for p in result["points"]] == [40.0, 60.0]


# ----- naive-local ts: AT TIME ZONE + local window (issue #48) ------------


def test_pg_epoch_extraction_interprets_ts_as_local():
    db = HistoryDB(db_type="postgresql", logger=MagicMock())
    seen, patcher = _capture_pg_sql(
        db,
        "onOffState\tboolean\n",
        "1753100000\tt\n",
    )
    with patcher:
        db.query_history(42, "onoffstate", "1h")
    assert "EXTRACT(EPOCH FROM (ts AT TIME ZONE 'Europe/London'))" in seen[-1]
    assert "EXTRACT(EPOCH FROM ts)" not in seen[-1]


def test_pg_bucketed_epoch_extraction_interprets_ts_as_local():
    db = HistoryDB(db_type="postgresql", logger=MagicMock())
    seen, patcher = _capture_pg_sql(
        db,
        "sensorValue\tdouble precision\n",
        "1753100000\t20.5\n",
    )
    with patcher:
        db.query_history(42, "sensorvalue", "24h")
    assert "EXTRACT(EPOCH FROM (ts AT TIME ZONE 'Europe/London'))" in seen[-1]


def test_pg_window_start_is_local_wall_time():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    db = HistoryDB(db_type="postgresql", logger=MagicMock())
    seen, patcher = _capture_pg_sql(
        db,
        "onOffState\tboolean\n",
        "1753100000\tt\n",
    )
    with patcher:
        db.query_history(42, "onoffstate", "1h")
    # WHERE ts >= '<start>' must be ~1h before *local* now, not UTC now.
    start_str = seen[-1].split("ts >= '")[1].split("'")[0]
    start = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
    expected = datetime.now(ZoneInfo("Europe/London")).replace(tzinfo=None) - timedelta(hours=1)
    assert abs((start - expected).total_seconds()) < 30


def test_pg_custom_timezone_reaches_sql_and_window():
    # Asia/Tokyo: UTC+9 year-round, no DST — unlike Europe/London this
    # assertion can never go season-blind (London == UTC all winter, so
    # a regression to UTC would pass a London-based test half the year).
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    db = HistoryDB(db_type="postgresql", logger=MagicMock(),
                   pg_timezone="Asia/Tokyo")
    seen, patcher = _capture_pg_sql(
        db,
        "onOffState\tboolean\n",
        "1753100000\tt\n",
    )
    with patcher:
        db.query_history(42, "onoffstate", "1h")
    assert "AT TIME ZONE 'Asia/Tokyo'" in seen[-1]
    start_str = seen[-1].split("ts >= '")[1].split("'")[0]
    start = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
    expected = datetime.now(
        ZoneInfo("Asia/Tokyo")).replace(tzinfo=None) - timedelta(hours=1)
    assert abs((start - expected).total_seconds()) < 30


def test_pg_text_raw_path_keeps_strings():
    # 1h + text: the only _pg_epoch() call site the bucketed tests miss.
    db = HistoryDB(db_type="postgresql", logger=MagicMock())
    seen, patcher = _capture_pg_sql(
        db,
        "operationState\ttext\n",
        "1753100000\tRun\n1753100600\tReady\n",
    )
    with patcher:
        result = db.query_history(42, "operationstate", "1h")
    assert "AVG(" not in seen[-1]
    assert "AT TIME ZONE 'Europe/London'" in seen[-1]
    assert [p["v"] for p in result["points"]] == ["Run", "Ready"]
    assert result["current"] == "Ready"


def test_pg_text_empty_string_is_a_real_value():
    # '' means "state cleared" — the WHERE clause excludes NULL, so an
    # empty psql field in the text path is a genuine value, and psql's
    # trailing "epoch\t" row must survive stdout parsing.
    db = HistoryDB(db_type="postgresql", logger=MagicMock())
    seen, patcher = _capture_pg_sql(
        db,
        "operationState\ttext\n",
        "1753100000\tRun\n1753100300\t\n",
    )
    with patcher:
        result = db.query_history(42, "operationstate", "24h")
    assert [p["v"] for p in result["points"]] == ["Run", ""]
    assert result["current"] == ""


def test_pg_timezone_pref_flows_through_and_validates():
    with patch.object(HistoryDB, "test_connection", return_value=True):
        db = HistoryDB.from_prefs(
            {"dbType": "postgresql", "pgTimezone": "America/New_York"},
            MagicMock())
    assert db.pg_timezone == "America/New_York"

    logger = MagicMock()
    db = HistoryDB(db_type="postgresql", logger=logger,
                   pg_timezone="Narnia/Lantern")
    assert db.pg_timezone == "Europe/London"
    warnings = " ".join(str(c) for c in logger.warning.call_args_list)
    assert "Narnia/Lantern" in warnings

    db = HistoryDB(db_type="postgresql", logger=MagicMock())
    assert db.pg_timezone == "Europe/London"

    db = HistoryDB(db_type="postgresql", logger=MagicMock(),
                   pg_timezone="   ")
    assert db.pg_timezone == "Europe/London"


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


# ----- review-driven coverage (from_prefs, PG parsing, hints order) ------


def test_from_prefs_none_and_blank_sqlite():
    assert HistoryDB.from_prefs({"dbType": "none"}, MagicMock()) is None
    logger = MagicMock()
    assert HistoryDB.from_prefs(
        {"dbType": "sqlite", "sqlitePath": " "}, logger) is None
    assert logger.warning.called


def test_from_prefs_sqlite_ready(sqlite_db):
    logger = MagicMock()
    db = HistoryDB.from_prefs(
        {"dbType": "sqlite", "sqlitePath": sqlite_db}, logger)
    assert db is not None and db.db_type == "sqlite"
    assert any("SQL Logger ready" in str(c) for c in logger.info.call_args_list)


def test_from_prefs_bad_pg_port_returns_none_logs_error():
    logger = MagicMock()
    db = HistoryDB.from_prefs(
        {"dbType": "postgresql", "pgPort": "not-a-port"}, logger)
    assert db is None
    assert logger.error.called


def test_from_prefs_failed_connection_keeps_instance(tmp_path):
    logger = MagicMock()
    db = HistoryDB.from_prefs(
        {"dbType": "sqlite", "sqlitePath": str(tmp_path / "missing.sqlite")},
        logger)
    assert db is not None
    assert logger.warning.called


def test_sqlite_wrong_path_does_not_create_file(tmp_path):
    path = tmp_path / "typo.sqlite"
    db = HistoryDB(db_type="sqlite", logger=MagicMock(), sqlite_path=str(path))
    assert db.test_connection() is False
    assert not path.exists()


def test_get_columns_connection_failure_propagates(tmp_path):
    db = HistoryDB(db_type="sqlite", logger=MagicMock(),
                   sqlite_path=str(tmp_path / "missing.sqlite"))
    with pytest.raises(Exception):
        db.get_columns(42)
    with pytest.raises(ValueError, match="SQL Logger query failed"):
        _list_columns_handler({"device_id": 42}, _provider(db))


def test_pg_stdout_parsing_through_query_history():
    db = HistoryDB(db_type="postgresql", logger=MagicMock())
    responses = [
        "onOffState\tboolean\nsensorValue\tdouble precision\n",  # get_columns
        "1753100000\tt\n1753100600\tf\n1753101200\t\n",           # raw rows
    ]

    def fake_run(cmd, capture_output, text, timeout, env):
        return type("R", (), {"returncode": 0,
                              "stdout": responses.pop(0), "stderr": ""})()

    with patch("history_db.subprocess.run", side_effect=fake_run):
        result = db.query_history(42, "onoffstate", "1h")
    assert result["type"] == "bool"
    assert [p["v"] for p in result["points"]] == [1.0, 0.0]


def test_pg_error_hints_order():
    db = HistoryDB(db_type="postgresql", logger=MagicMock())
    assert "database doesn't exist" in db._diagnose_pg_error(
        'FATAL: database "typo" does not exist')
    assert "role (user)" in db._diagnose_pg_error(
        'FATAL: role "Simon" does not exist')
