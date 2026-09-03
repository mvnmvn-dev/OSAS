import mysql.connector
from mysql.connector import Error
from flask import current_app, g


def get_db():
    if "db" not in g:
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
    return rows


def query_one(sql, params=None):
    rows = query_all(sql, params)
    return rows[0] if rows else None


def execute(sql, params=None):
    db = get_db()
    cursor = db.cursor()
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
