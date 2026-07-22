"""Read-only access to Indigo SQL Logger history (SQLite or PostgreSQL).

Trimmed copy of home-intelligence's ``history_db.py`` (the digest-only
rollup/energy helpers are removed); keep the query paths textually
aligned with HI's copy when fixing bugs — see workspace note "refactor
to shared lib is future work".

Differences from the HI original, by design:

- ``query_history`` enforces a strict column allowlist: a requested
  column that doesn't match the table's actual columns raises
  ``ValueError`` naming the available columns, instead of falling
  through with the client-supplied string into an SQL identifier.
- The PostgreSQL path sets ``PGOPTIONS=-c default_transaction_read_only=on``
  so the server itself refuses writes, mirroring SQLite's
  ``PRAGMA query_only = ON``.

Stdlib only: SQLite via ``sqlite3``; PostgreSQL by shelling out to the
``psql`` CLI (Postgres.app path probed first) — no psycopg2.
"""
import glob
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone


# Time bucket sizes for downsampling (in seconds)
RANGE_BUCKETS = {
    "1h":  None,      # raw data, no bucketing
    "6h":  120,       # 2 minute buckets
    "24h": 300,       # 5 minute buckets
    "7d":  1800,      # 30 minute buckets
    "30d": 10800,     # 3 hour buckets
}

RANGE_DELTAS = {
    "1h":  timedelta(hours=1),
    "6h":  timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d":  timedelta(days=7),
    "30d": timedelta(days=30),
}


class HistoryDB:
    """Read-only access to Indigo SQL Logger database."""

    def __init__(self, db_type, logger, sqlite_path=None,
                 pg_host=None, pg_port=None, pg_user=None, pg_password=None, pg_database=None):
        self.db_type = db_type
        self.logger = logger
        self.sqlite_path = sqlite_path
        self.pg_config = {
            "host": pg_host or "127.0.0.1",
            "port": int(pg_port or 5432),
            "user": pg_user or "postgres",
            "password": pg_password or "",
            "database": pg_database or "indigo_history",
        }

    # Recognisable fragments of psql stderr mapped to an actionable
    # one-liner. Matched on lowercased stderr; the first hit wins, so
    # specific patterns MUST precede generic ones. In particular
    # ``does not exist`` is a substring of BOTH
    # ``role "X" does not exist`` AND ``database "Y" does not exist`` —
    # if the generic "does not exist" rule matched first, database
    # errors would be misclassified as role errors.
    _PG_ERROR_HINTS = (
        ("password authentication failed", "wrong password — check 'Password' in Plugin Configure"),
        ("connection refused", "Postgres isn't accepting connections on this host/port — is Postgres.app running?"),
        ("could not translate host name", "hostname didn't resolve — check 'Host' in Plugin Configure"),
        ("database \"", "database doesn't exist — check 'Database name' in Plugin Configure"),
        ("does not exist", "role (user) not found in Postgres — check 'Username' field in Plugin Configure (case-sensitive)"),
    )

    def _diagnose_pg_error(self, stderr: str) -> str:
        """Extract an actionable hint from psql stderr. Falls back to the
        raw stderr on no match so we never swallow useful diagnostics —
        the hint augments, doesn't replace."""
        lower = stderr.lower()
        for needle, hint in self._PG_ERROR_HINTS:
            if needle in lower:
                return hint
        return "unrecognised Postgres error (see raw stderr above)"

    def test_connection(self):
        """Test that we can connect and read the database.

        Logs at ``error`` on failure with a classified hint, so the user
        sees *what* to fix rather than just the raw psql stderr."""
        try:
            if self.db_type == "sqlite":
                conn = sqlite3.connect(self.sqlite_path)
                conn.execute("PRAGMA query_only = ON")
                conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
                conn.close()
            else:
                _, rows = self._execute_pg("SELECT 1 AS test")
                if not rows:
                    raise Exception("PostgreSQL query returned no results")
            return True
        except Exception as e:
            msg = str(e)
            if self.db_type == "postgresql":
                hint = self._diagnose_pg_error(msg)
                self.logger.error(
                    f"SQL Logger connection test failed: {hint}. Raw: {msg}"
                )
            else:
                self.logger.error(f"SQL Logger connection test failed: {msg}")
            return False

    def _execute_sqlite(self, sql, params=()):
        """Execute a read-only SQLite query and return rows."""
        conn = sqlite3.connect(self.sqlite_path)
        try:
            conn.execute("PRAGMA query_only = ON")
            cursor = conn.execute(sql, params)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            return columns, rows
        finally:
            conn.close()

    def _execute_pg(self, sql, params=()):
        """Execute a PostgreSQL query via psql CLI and return rows as tuples."""
        # Substitute parameters into SQL (psql doesn't support parameterised queries)
        # Only used for simple string substitution (timestamps, table names)
        if params:
            # Replace %s placeholders with properly quoted values
            parts = sql.split("%s")
            assembled = parts[0]
            for i, param in enumerate(params):
                escaped = str(param).replace("'", "''")
                assembled += f"'{escaped}'" + parts[i + 1]
            sql = assembled

        # Find psql - Postgres.app doesn't add to system PATH
        psql = "/Applications/Postgres.app/Contents/Versions/latest/bin/psql"
        if not os.path.exists(psql):
            # Try version-specific path
            matches = glob.glob("/Applications/Postgres.app/Contents/Versions/*/bin/psql")
            psql = matches[0] if matches else "psql"

        cmd = [
            psql,
            "-h", self.pg_config["host"],
            "-p", str(self.pg_config["port"]),
            "-U", self.pg_config["user"],
            "-d", self.pg_config["database"],
            "--no-align",       # unaligned output
            "--field-separator", "\t",
            "--tuples-only",    # no headers/footer for data queries
            "--pset", "null=",  # empty string for NULLs
            "-c", sql,
        ]

        # Server-side read-only enforcement: every statement runs in a
        # read-only transaction, mirroring SQLite's PRAGMA query_only.
        env = os.environ.copy()
        env["PGOPTIONS"] = "-c default_transaction_read_only=on"
        if self.pg_config["password"]:
            env["PGPASSWORD"] = self.pg_config["password"]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)

        if result.returncode != 0:
            raise Exception(f"psql error: {result.stderr.strip()}")

        rows = []
        for line in result.stdout.strip().split("\n"):
            if line:
                rows.append(tuple(line.split("\t")))
        return [], rows  # columns not easily parsed from tuples-only mode

    def _execute(self, sql, params=()):
        """Execute query on configured backend."""
        if self.db_type == "sqlite":
            return self._execute_sqlite(sql, params)
        else:
            return self._execute_pg(sql, params)

    def get_device_tables(self):
        """Return list of device IDs that have history tables."""
        if self.db_type == "sqlite":
            sql = "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'device_history_%'"
        else:
            sql = "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'device_history_%'"

        try:
            _, rows = self._execute(sql)
            device_ids = []
            for row in rows:
                table_name = row[0]
                parts = table_name.split("device_history_")
                if len(parts) == 2 and parts[1].isdigit():
                    device_ids.append(int(parts[1]))
            return device_ids
        except Exception as e:
            msg = str(e)
            if self.db_type == "postgresql":
                hint = self._diagnose_pg_error(msg)
                self.logger.error(
                    f"SQL Logger list-tables failed: {hint}. Raw: {msg}"
                )
            else:
                self.logger.error(f"Error listing device tables: {msg}")
            return []

    def get_columns(self, device_id):
        """Return list of columns and their types for a device history table."""
        table_name = f"device_history_{device_id}"
        try:
            if self.db_type == "sqlite":
                sql = f'SELECT name, type FROM pragma_table_info("{table_name}")'
                _, rows = self._execute(sql)
            else:
                sql = ("SELECT column_name, data_type FROM information_schema.columns "
                       "WHERE table_name = %s AND table_schema = 'public'")
                _, rows = self._execute(sql, (table_name,))

            columns = []
            for name, col_type in rows:
                if name in ("id", "ts"):
                    continue
                # Normalise type names
                col_type_lower = col_type.lower()
                if col_type_lower in ("bool", "boolean"):
                    mapped = "bool"
                elif col_type_lower in ("integer", "int", "bigint", "smallint"):
                    mapped = "int"
                elif col_type_lower in ("real", "float", "double precision", "numeric"):
                    mapped = "float"
                else:
                    mapped = "text"
                columns.append({"name": name, "type": mapped})
            return columns
        except Exception as e:
            self.logger.error(f"Error getting columns for device {device_id}: {e}")
            return []

    def query_history(self, device_id, column, time_range="24h", max_points=300):
        """
        Query device history for a specific column over a time range.
        Returns dict with points, min, max, current values.

        Timestamps in the SQL Logger database are stored in GMT.

        The requested ``column`` MUST match one of the table's actual
        columns (case-insensitively); anything else raises ValueError
        naming the available columns. The matched exact-case name is the
        only string that ever reaches SQL, so a hostile column value
        cannot be smuggled into the quoted identifier.
        """
        table_name = f"device_history_{device_id}"
        bucket_seconds = RANGE_BUCKETS.get(time_range)
        delta = RANGE_DELTAS.get(time_range, timedelta(hours=24))

        # Calculate start time in GMT (SQL Logger stores GMT timestamps)
        start_time = datetime.now(timezone.utc) - delta
        start_ts = start_time.strftime("%Y-%m-%d %H:%M:%S")

        columns_info = self.get_columns(device_id)
        if not columns_info:
            raise ValueError(
                f"no SQL Logger history for device {device_id} "
                f"(table {table_name} missing or unreadable)"
            )
        match = next(
            (c for c in columns_info if c["name"].lower() == column.lower()),
            None,
        )
        if match is None:
            available = ", ".join(c["name"] for c in columns_info)
            raise ValueError(
                f"column {column!r} not found for device {device_id}; "
                f"available: {available}"
            )
        col_type = match["type"]
        column = match["name"]  # exact case from DB — the only string used in SQL

        try:
            if col_type == "bool" or bucket_seconds is None:
                # Boolean data or short range: return raw rows
                points = self._query_raw(table_name, column, start_ts)
            else:
                # Numeric data with bucketing
                points = self._query_bucketed(table_name, column, start_ts, bucket_seconds)

            if not points:
                return {
                    "points": [],
                    "min": None,
                    "max": None,
                    "current": None,
                    "type": col_type,
                }

            values = [p["v"] for p in points if p["v"] is not None]
            return {
                "points": points,
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "current": values[-1] if values else None,
                "type": col_type,
            }
        except Exception as e:
            self.logger.error(f"Error querying history for device {device_id}, column {column}: {e}")
            raise

    def _query_raw(self, table_name, column, start_ts):
        """Return raw data points (no aggregation)."""
        if self.db_type == "sqlite":
            sql = (
                f'SELECT strftime("%s", ts) as epoch, "{column}" '
                f'FROM "{table_name}" '
                f'WHERE ts >= ? AND "{column}" IS NOT NULL '
                f'ORDER BY ts'
            )
            _, rows = self._execute(sql, (start_ts,))
        else:
            sql = (
                f'SELECT EXTRACT(EPOCH FROM ts)::bigint as epoch, "{column}" '
                f'FROM "{table_name}" '
                f'WHERE ts >= %s AND "{column}" IS NOT NULL '
                f'ORDER BY ts'
            )
            _, rows = self._execute(sql, (start_ts,))

        points = []
        for row in rows:
            epoch_raw = row[0]
            value_raw = row[1]
            if epoch_raw is None or epoch_raw == "":
                continue
            epoch = int(epoch_raw)
            # Handle booleans (PG returns 't'/'f' strings via psql)
            if isinstance(value_raw, bool):
                value = 1.0 if value_raw else 0.0
            elif isinstance(value_raw, str):
                if value_raw.lower() in ("t", "true"):
                    value = 1.0
                elif value_raw.lower() in ("f", "false"):
                    value = 0.0
                elif value_raw == "":
                    continue
                else:
                    value = float(value_raw)
            elif value_raw is not None:
                value = float(value_raw)
            else:
                continue
            points.append({"t": epoch, "v": value})
        return points

    def _query_bucketed(self, table_name, column, start_ts, bucket_seconds):
        """Return aggregated data points using time buckets."""
        if self.db_type == "sqlite":
            sql = (
                f'SELECT (CAST(strftime("%s", ts) AS INTEGER) / {bucket_seconds}) * {bucket_seconds} as bucket, '
                f'AVG("{column}") as avg_val '
                f'FROM "{table_name}" '
                f'WHERE ts >= ? AND "{column}" IS NOT NULL '
                f'GROUP BY bucket '
                f'ORDER BY bucket'
            )
            _, rows = self._execute(sql, (start_ts,))
        else:
            sql = (
                f'SELECT (EXTRACT(EPOCH FROM ts)::bigint / {bucket_seconds}) * {bucket_seconds} as bucket, '
                f'AVG("{column}") as avg_val '
                f'FROM "{table_name}" '
                f'WHERE ts >= %s AND "{column}" IS NOT NULL '
                f'GROUP BY bucket '
                f'ORDER BY bucket'
            )
            _, rows = self._execute(sql, (start_ts,))

        points = []
        for row in rows:
            epoch_raw = row[0]
            value_raw = row[1]
            if epoch_raw is None or epoch_raw == "":
                continue
            if value_raw is None or value_raw == "":
                continue
            epoch = int(epoch_raw)
            value = round(float(value_raw), 2)
            points.append({"t": epoch, "v": value})
        return points

    def close(self):
        """No persistent connections to close (SQLite opens per-query, PG uses psql CLI)."""
        pass
