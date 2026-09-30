"""Idempotent schema+seed for Railway private network (run before gunicorn).

Uses MYSQL_* / MYSQLHOST-style env from the web service. Safe to run every deploy:
creates the configured database if needed, applies schema.sql when `users` is missing,
then seeds demo admin accounts + campus data.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mysql.connector import connect  # noqa: E402
from config import Config  # noqa: E402

SCHEMA = ROOT / "schema.sql"


def _connect_kwargs(database: str | None = None) -> dict:
    kwargs = {
        "host": Config.MYSQL_HOST,
        "port": Config.MYSQL_PORT,
        "user": Config.MYSQL_USER,
        "password": Config.MYSQL_PASSWORD,
        "connection_timeout": 60,
        "allow_local_infile": True,
    }
    if database:
        kwargs["database"] = database
    if Config.MYSQL_SSL:
        kwargs["ssl_disabled"] = False
        kwargs["ssl_verify_cert"] = False
        kwargs["ssl_verify_identity"] = False
    else:
        kwargs["ssl_disabled"] = True
    return kwargs


def ensure_database(name: str) -> None:
    safe = "".join(c for c in name if c.isalnum() or c in ("_",))
    if safe != name or not safe:
        raise SystemExit(f"Refusing unsafe database name: {name!r}")
    conn = connect(**_connect_kwargs())
    try:
        cur = conn.cursor()
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{safe}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def table_exists(database: str, table: str) -> bool:
    conn = connect(**_connect_kwargs(database))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
            """,
            (database, table),
        )
        count = cur.fetchone()[0]
        cur.close()
        return count > 0
    finally:
        conn.close()


def apply_schema(database: str) -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    conn = connect(**_connect_kwargs(database))
    try:
        cur = conn.cursor()
        for statement in sql.split(";"):
            statement = statement.strip()
            if not statement:
                continue
            upper = statement.upper()
            if upper.startswith("CREATE DATABASE") or upper.startswith("USE "):
                continue
            cur.execute(statement)
        conn.commit()
        cur.close()
    finally:
        conn.close()


def _seed_users(database: str) -> None:
    from werkzeug.security import generate_password_hash

    rows = [
        (
            "admin",
            generate_password_hash("password123"),
            "admin@bcp.edu.ph",
            "OSAS Administrator",
            "admin",
            "OSAS-0001",
        ),
        (
            "security.head",
            generate_password_hash("password123"),
            "security@bcp.edu.ph",
            "Security Head",
            "security_head",
            "OSAS-0002",
        ),
    ]
    conn = connect(**_connect_kwargs(database))
    try:
        cur = conn.cursor()
        for row in rows:
            cur.execute(
                """INSERT IGNORE INTO users
                   (username,password_hash,email,full_name,role,campus_id)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                row,
            )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def seed_campus(app) -> None:
    with app.app_context():
        from services.safety_service import ensure_campus_seed

        ensure_campus_seed()


def bootstrap_with_app(app=None) -> dict:
    """Create schema/seed if needed. Returns a small status dict for health/logs."""
    database = Config.MYSQL_DATABASE or "osas_bcp"
    status = {
        "database": database,
        "host": Config.MYSQL_HOST,
        "port": Config.MYSQL_PORT,
        "schema_applied": False,
        "seeded": False,
    }
    if not SCHEMA.exists():
        raise FileNotFoundError(f"Missing {SCHEMA}")

    print(
        f"Bootstrap: {Config.MYSQL_USER}@{Config.MYSQL_HOST}:{Config.MYSQL_PORT}/{database} "
        f"(ssl={Config.MYSQL_SSL})"
    )
    ensure_database(database)
    if not table_exists(database, "users"):
        print("Applying schema.sql ...")
        apply_schema(database)
        status["schema_applied"] = True
        print("Schema applied.")
    else:
        print("Schema already present — skipping create.")

    print("Seeding demo users / campus (INSERT IGNORE) ...")
    _seed_users(database)
    status["seeded"] = True
    if app is not None:
        seed_campus(app)
    print("Bootstrap complete.")
    return status


def main() -> None:
    # Avoid importing app until after schema exists (fresh deploys).
    bootstrap_with_app(app=None)
    from app import app

    seed_campus(app)
    print("Campus seed complete.")


if __name__ == "__main__":
    main()
