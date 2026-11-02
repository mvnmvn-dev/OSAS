"""Idempotent schema + seed for PostgreSQL (Render, Railway, local).
Safe to run every deploy: applies schema.sql when `users` is missing, then seeds
the software-engineer login and campus demo data.
"""
from __future__ import annotations
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import psycopg2  # noqa: E402
from psycopg2 import sql as psycopg2_sql  # noqa: E402
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402
from config import Config  # noqa: E402

SCHEMA = ROOT / "schema.sql"


def _connect_kwargs(database: str | None = None) -> dict:
    kwargs = {
        "host": Config.PGHOST,
        "port": Config.PGPORT,
        "user": Config.PGUSER,
        "password": Config.PGPASSWORD,
        "dbname": database or Config.PGDATABASE or "osas_bcp",
        "connect_timeout": 60,
    }
    if Config.PGSSL:
        kwargs["sslmode"] = "require"
    return kwargs


def _connect(database: str | None = None):
    if Config.DATABASE_URL and not database:
        return psycopg2.connect(Config.DATABASE_URL)
    return psycopg2.connect(**_connect_kwargs(database))


def ensure_database(name: str) -> None:
    """Create the database locally when the role is allowed. Render skips this."""
    safe = "".join(c for c in name if c.isalnum() or c in ("_",))
    if safe != name or not safe:
        raise SystemExit(f"Refusing unsafe database name: {name!r}")
    if Config.DATABASE_URL:
        return
    try:
        kwargs = _connect_kwargs("postgres")
        conn = psycopg2.connect(**kwargs)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (safe,))
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{safe}"')
            cur.close()
        finally:
            conn.close()
    except Exception as exc:
        print(f"Skipping CREATE DATABASE ({exc}). Using existing database.")


def table_exists(database: str, table: str) -> bool:
    conn = _connect(None if Config.DATABASE_URL else database)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table,),
        )
        count = cur.fetchone()[0]
        cur.close()
        return count > 0
    finally:
        conn.close()


def apply_schema(database: str) -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    # Strip comments before splitting: semicolons inside comments otherwise
    # leave trailing comment text that can be mistaken for an SQL statement.
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    sql = re.sub(r"(?m)^\s*--.*$", "", sql)
    conn = _connect(None if Config.DATABASE_URL else database)
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


def migrate_shared_admin_email(database: str) -> None:
    """Drop the legacy email-only unique constraint so admins can share an inbox."""
    conn = _connect(None if Config.DATABASE_URL else database)
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT conname FROM pg_constraint
               WHERE conrelid = 'users'::regclass AND contype = 'u'
                 AND pg_get_constraintdef(oid) = 'UNIQUE (email)'"""
        )
        for (constraint_name,) in cur.fetchall():
            cur.execute(
                psycopg2_sql.SQL("ALTER TABLE users DROP CONSTRAINT {}").format(
                    psycopg2_sql.Identifier(constraint_name)
                )
            )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def _seed_users(database: str) -> None:
    rows = [
        (
            Config.SEED_USERNAME,
            generate_password_hash(Config.SEED_PASSWORD),
            Config.SEED_EMAIL,
            Config.SEED_FULL_NAME,
            Config.SEED_ROLE,
            Config.SEED_CAMPUS_ID,
        ),
        (
            "admin",
            generate_password_hash("password123"),
            Config.SEED_EMAIL,
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
    conn = _connect(None if Config.DATABASE_URL else database)
    try:
        cur = conn.cursor()
        for row in rows:
            cur.execute(
                """INSERT INTO users
                   (username,password_hash,email,full_name,role,campus_id)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (username) DO NOTHING""",
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
    database = Config.PGDATABASE or "osas_bcp"
    status = {
        "database": database,
        "host": Config.PGHOST,
        "port": Config.PGPORT,
        "schema_applied": False,
        "seeded": False,
    }
    if not SCHEMA.exists():
        raise FileNotFoundError(f"Missing {SCHEMA}")
    print(
        f"Bootstrap: {Config.PGUSER}@{Config.PGHOST}:{Config.PGPORT}/{database} "
        f"(ssl={Config.PGSSL})"
    )
    ensure_database(database)
    if not table_exists(database, "users"):
        print("Applying schema.sql ...")
        apply_schema(database)
        status["schema_applied"] = True
        print("Schema applied.")
    else:
        print("Schema already present — skipping create.")
        if not table_exists(database, "login_otp_challenges"):
            print("Applying login_otp_challenges migration ...")
            apply_schema(database)
            status["schema_applied"] = True
    migrate_shared_admin_email(database)
    print("Seeding software.engineer / demo users / campus ...")
    _seed_users(database)
    status["seeded"] = True
    if app is not None:
        seed_campus(app)
    print("Bootstrap complete.")
    print(
        f"Deploy login: {Config.SEED_USERNAME}  (change SEED_PASSWORD on Render if needed)"
    )
    return status


def main() -> None:
    bootstrap_with_app(app=None)
    from app import app
    seed_campus(app)
    print("Campus seed complete.")


if __name__ == "__main__":
    main()
