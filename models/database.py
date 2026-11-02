import os
import re
import sqlite3

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import current_app, g, has_app_context

_TABLES_WITHOUT_ID = frozenset({"patrol_team_members", "patrol_route_checkpoints"})


def _field_to_case(match):
    col = match.group(1).strip()
    raw_vals = match.group(2).split(",")
    when_clauses = []
    for idx, val in enumerate(raw_vals):
        clean_val = val.strip().strip("'\"")
        when_clauses.append(f"WHEN {col} = '{clean_val}' THEN {idx + 1}")
    return f"(CASE {' '.join(when_clauses)} ELSE {len(raw_vals) + 1} END)"


def _translate_mysql_helpers(sql):
    sql = re.sub(r"\bFIELD\s*\(\s*([^,]+)\s*,\s*([^)]+)\)", _field_to_case, sql, flags=re.IGNORECASE)
    sql = re.sub(
        r"TIMESTAMPDIFF\(\s*MINUTE\s*,\s*([^,]+),\s*([^)]+)\)",
        r"(EXTRACT(EPOCH FROM ((\2) - (\1))) / 60.0)",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"ON DUPLICATE KEY UPDATE\s+([^\n;]+)",
        lambda m: "ON CONFLICT DO UPDATE SET "
        + re.sub(r"VALUES\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", r"EXCLUDED.\1", m.group(1), flags=re.IGNORECASE),
        sql,
        flags=re.IGNORECASE,
    )
    if re.search(r"\bINSERT\s+IGNORE\s+INTO\b", sql, re.IGNORECASE):
        sql = re.sub(r"\bINSERT\s+IGNORE\s+INTO\b", "INSERT INTO", sql, count=1, flags=re.IGNORECASE)
        if "ON CONFLICT" not in sql.upper():
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return sql


def _pg_sql(sql):
    sql = _translate_mysql_helpers(sql)
    sql = re.sub(r"\bCURDATE\(\)", "CURRENT_DATE", sql, flags=re.IGNORECASE)
    sql = re.sub(
        r"DATE_ADD\(\s*NOW\(\)\s*,\s*INTERVAL\s+(\d+)\s+DAY\s*\)",
        r"(NOW() + INTERVAL '\1 day')",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"CURRENT_DATE\s*\+\s*INTERVAL\s+(\d+)\s+DAY",
        r"(CURRENT_DATE + INTERVAL '\1 day')",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"CURRENT_DATE\s*-\s*INTERVAL\s+(\d+)\s+DAY",
        r"(CURRENT_DATE - INTERVAL '\1 day')",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"NOW\(\)\s*-\s*INTERVAL\s+%s\s+DAY",
        r"(NOW() - (%s * INTERVAL '1 day'))",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"NOW\(\)\s*-\s*INTERVAL\s+(\d+)\s+DAY",
        r"(NOW() - INTERVAL '\1 day')",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"NOW\(\)\s*-\s*INTERVAL\s+(\d+)\s+HOUR",
        r"(NOW() - INTERVAL '\1 hour')",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(r"\bLIKE\b", "ILIKE", sql, flags=re.IGNORECASE)
    return sql


def _sqlite_sql(sql):
    sql = _translate_mysql_helpers(sql)
    sql = sql.replace("%s", "?")
    if re.search(r"\bON CONFLICT\b", sql, re.IGNORECASE):
        sql = re.sub(r"\s+ON CONFLICT\b.*$", "", sql, flags=re.IGNORECASE | re.DOTALL)
        sql = re.sub(r"^\s*INSERT\s+INTO", "INSERT OR REPLACE INTO", sql, count=1, flags=re.IGNORECASE)
    sql = re.sub(r"\bNOW\(\)", "CURRENT_TIMESTAMP", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bCURDATE\(\)", "CURRENT_DATE", sql, flags=re.IGNORECASE)
    sql = re.sub(
        r"DATE_ADD\(\s*CURRENT_TIMESTAMP\s*,\s*INTERVAL\s+(\d+)\s+DAY\s*\)",
        r"datetime('now', '+\1 days')",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"CURRENT_DATE\s*\+\s*INTERVAL\s+(\d+)\s+DAY",
        r"date('now', '+\1 days')",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"CURRENT_DATE\s*-\s*INTERVAL\s+(\d+)\s+DAY",
        r"date('now', '-\1 days')",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"CURRENT_TIMESTAMP\s*-\s*INTERVAL\s+\?\s+DAY",
        r"datetime('now', '-' || ? || ' days')",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"CURRENT_TIMESTAMP\s*-\s*INTERVAL\s+(\d+)\s+DAY",
        r"datetime('now', '-\1 days')",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"CURRENT_TIMESTAMP\s*-\s*INTERVAL\s+(\d+)\s+HOUR",
        r"datetime('now', '-\1 hours')",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"\(EXTRACT\(EPOCH FROM \(\((.+?)\) - \((.+?)\)\)\) / 60\.0\)",
        r"((julianday(\1) - julianday(\2)) * 24 * 60)",
        sql,
        flags=re.IGNORECASE,
    )
    return sql


class _SQLiteCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=()):
        return self._cursor.execute(_sqlite_sql(sql), params)

    def executemany(self, sql, params_list):
        return self._cursor.executemany(_sqlite_sql(sql), params_list)

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchone(self):
        return self._cursor.fetchone()

    def close(self):
        self._cursor.close()

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount


class _SQLiteConnection:
    def __init__(self, path):
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")

    def cursor(self, dictionary=False):
        cursor = self._connection.cursor()
        return _SQLiteCursor(cursor)

    def commit(self):
        self._connection.commit()

    def close(self):
        self._connection.close()


class _PgCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self._lastrowid = None

    def execute(self, sql, params=()):
        sql = _pg_sql(sql)
        insert = re.match(r"^\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", sql, re.IGNORECASE)
        table = insert.group(1).lower() if insert else None
        if table and table not in _TABLES_WITHOUT_ID and "RETURNING" not in sql.upper():
            sql = sql.rstrip().rstrip(";") + " RETURNING id"
        result = self._cursor.execute(sql, params or ())
        if table and table not in _TABLES_WITHOUT_ID and "RETURNING" in sql.upper():
            row = self._cursor.fetchone()
            if row is None:
                self._lastrowid = None
            elif isinstance(row, dict):
                self._lastrowid = row.get("id")
            else:
                self._lastrowid = row[0]
        return result

    def executemany(self, sql, params_list):
        return self._cursor.executemany(_pg_sql(sql), params_list)

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchone(self):
        return self._cursor.fetchone()

    def close(self):
        self._cursor.close()

    @property
    def lastrowid(self):
        return self._lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount


class _PgConnection:
    def __init__(self, raw):
        self._connection = raw

    def cursor(self, dictionary=False):
        factory = RealDictCursor if dictionary else None
        cursor = self._connection.cursor(cursor_factory=factory) if factory else self._connection.cursor()
        return _PgCursor(cursor)

    def commit(self):
        self._connection.commit()

    def close(self):
        self._connection.close()


def _get_test_db():
    from models.test_database import SCHEMA

    path = current_app.config.get(
        "TEST_DATABASE", os.path.join(os.path.abspath(os.path.dirname(__file__)), "test.sqlite3")
    )
    db = _SQLiteConnection(path)
    db._connection.executescript(SCHEMA)
    return db


def postgres_connect(database=None):
    from config import Config

    cfg = current_app.config if has_app_context() else {}
    dsn = cfg.get("DATABASE_URL") or Config.DATABASE_URL
    host = cfg.get("PGHOST", Config.PGHOST)
    port = cfg.get("PGPORT", Config.PGPORT)
    user = cfg.get("PGUSER", Config.PGUSER)
    password = cfg.get("PGPASSWORD", Config.PGPASSWORD)
    dbname = database or cfg.get("PGDATABASE", Config.PGDATABASE)
    ssl = cfg.get("PGSSL", Config.PGSSL)
    if dsn and not database:
        return psycopg2.connect(dsn)
    connect_kwargs = {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "dbname": dbname,
        "connect_timeout": 60,
    }
    if ssl:
        connect_kwargs["sslmode"] = "require"
    return psycopg2.connect(**connect_kwargs)


def get_db():
    if "db" not in g:
        if current_app.config.get("TESTING"):
            g.db = _get_test_db()
        else:
            g.db = _PgConnection(postgres_connect())
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query_all(sql, params=None):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(sql, params or ())
    rows = cursor.fetchall()
    cursor.close()
    if rows and isinstance(rows[0], sqlite3.Row):
        return [dict(row) for row in rows]
    return [dict(row) for row in rows] if rows else []


def query_one(sql, params=None):
    rows = query_all(sql, params)
    return rows[0] if rows else None


def execute(sql, params=None):
    db = get_db()
    cursor = db.cursor()
    if current_app.config.get("TESTING") and "ON DUPLICATE KEY UPDATE" in sql.upper():
        sql = re.sub(r"\s+ON DUPLICATE KEY UPDATE.*$", "", sql, flags=re.IGNORECASE | re.DOTALL)
        sql = re.sub(r"^\s*INSERT\s+INTO", "INSERT OR REPLACE INTO", sql, count=1, flags=re.IGNORECASE)
    cursor.execute(sql, params or ())
    db.commit()
    last_id = cursor.lastrowid
    rowcount = cursor.rowcount
    cursor.close()
    return last_id, rowcount


def execute_many(sql, params_list):
    db = get_db()
    cursor = db.cursor()
    cursor.executemany(sql, params_list)
    db.commit()
    cursor.close()
