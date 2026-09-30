"""Import schema.sql (+ optional seed) into a remote MySQL (e.g. Railway).

Usage:
  1. In Railway MySQL → Connect → enable public networking / copy host, port, user, password
  2. Set env vars (or put them in a temporary .env), then:

     py scripts/import_schema.py
     py scripts/import_schema.py --seed

Never commit real Railway passwords.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import mysql.connector

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schema.sql"


def connect():
    load_dotenv(ROOT / ".env")
    host = os.getenv("MYSQL_HOST") or os.getenv("MYSQLHOST") or "localhost"
    port = int(os.getenv("MYSQL_PORT") or os.getenv("MYSQLPORT") or "3306")
    user = os.getenv("MYSQL_USER") or os.getenv("MYSQLUSER") or "root"
    password = os.getenv("MYSQL_PASSWORD") or os.getenv("MYSQLPASSWORD") or ""
    # Connect without selecting a DB first — schema.sql creates osas_bcp
    ssl = (os.getenv("MYSQL_SSL") or "0").lower() in ("1", "true", "yes")
    kwargs = {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "allow_local_infile": True,
        "connection_timeout": 30,
    }
    if ssl:
        kwargs["ssl_disabled"] = False
        kwargs["ssl_verify_cert"] = False
        kwargs["ssl_verify_identity"] = False
    else:
        # Some Railway public proxies still negotiate TLS; try without verify first
        kwargs["ssl_disabled"] = True
    print(f"Connecting to {user}@{host}:{port} (ssl={ssl}) ...")
    return mysql.connector.connect(**kwargs)


def run_sql_file(cursor, path: Path):
    sql = path.read_text(encoding="utf-8")
    # mysql.connector can run multi-statement with multi=True on execute loops
    for result in cursor.execute(sql, multi=True):
        try:
            result.fetchall()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="store_true", help="Also run init_db seed after schema")
    args = parser.parse_args()

    if not SCHEMA.exists():
        print(f"Missing {SCHEMA}", file=sys.stderr)
        sys.exit(1)

    conn = connect()
    cursor = conn.cursor()
    print(f"Importing {SCHEMA.name} ...")
    run_sql_file(cursor, SCHEMA)
    conn.commit()
    cursor.close()
    conn.close()
    print("Schema import complete (database osas_bcp + tables).")

    if args.seed:
        # init_db expects app context + MYSQL_DATABASE=osas_bcp
        os.environ.setdefault("MYSQL_DATABASE", "osas_bcp")
        sys.path.insert(0, str(ROOT))
        from app import app
        with app.app_context():
            from services.safety_service import ensure_campus_seed
            from models.database import execute
            from werkzeug.security import generate_password_hash

            execute(
                """INSERT IGNORE INTO users (username,password_hash,email,full_name,role,campus_id)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                ("admin", generate_password_hash("password123"), "admin@bcp.edu.ph", "OSAS Administrator", "admin", "OSAS-0001"),
            )
            execute(
                """INSERT IGNORE INTO users (username,password_hash,email,full_name,role,campus_id)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                ("security.head", generate_password_hash("password123"), "security@bcp.edu.ph", "Security Head", "security_head", "OSAS-0002"),
            )
            ensure_campus_seed()
        print("Seed complete. Demo password: password123 (change after first login).")


if __name__ == "__main__":
    main()
