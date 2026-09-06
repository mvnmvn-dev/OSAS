import os
import re
import sqlite3

import mysql.connector
from flask import current_app, g


def _sqlite_sql(sql):
    """Translate the small MySQL dialect used by test paths to SQLite."""
    sql = sql.replace("%s", "?")
    sql = re.sub(r"\s+ON DUPLICATE KEY UPDATE.*$", "", sql, flags=re.IGNORECASE | re.DOTALL)
    sql = re.sub(r"\bNOW\(\)", "CURRENT_TIMESTAMP", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bCURDATE\(\)", "CURRENT_DATE", sql, flags=re.IGNORECASE)
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
        if dictionary:
            return _SQLiteCursor(cursor)
        return _SQLiteCursor(cursor)

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


def get_db():
    if "db" not in g:
        if current_app.config.get("TESTING"):
            g.db = _get_test_db()
        else:
            g.db = mysql.connector.connect(
                host=current_app.config["MYSQL_HOST"],
                port=current_app.config["MYSQL_PORT"],
                user=current_app.config["MYSQL_USER"],
                password=current_app.config["MYSQL_PASSWORD"],
                database=current_app.config["MYSQL_DATABASE"],
                autocommit=False,
            )
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
    return rows


def query_one(sql, params=None):
    rows = query_all(sql, params)
    return rows[0] if rows else None


def execute(sql, params=None):
    db = get_db()
    cursor = db.cursor()
    # INSERT ... ON DUPLICATE KEY UPDATE is used by integration fixtures.
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
