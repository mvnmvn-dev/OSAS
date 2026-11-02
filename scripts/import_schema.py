"""Import schema.sql (+ optional seed) into PostgreSQL (e.g. Render).

Usage:
  Set DATABASE_URL or PG* env vars, then:

     py scripts/import_schema.py
     py scripts/import_schema.py --seed
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from scripts.db_bootstrap import apply_schema, bootstrap_with_app, ensure_database  # noqa: E402
from config import Config  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="store_true", help="Also seed users and campus data")
    args = parser.parse_args()

    database = Config.PGDATABASE or "osas_bcp"
    print(f"Connecting to {Config.PGUSER}@{Config.PGHOST}:{Config.PGPORT}/{database} ...")
    ensure_database(database)
    apply_schema(database)
    print("Schema import complete.")

    if args.seed:
        os.environ.setdefault("AUTO_DB_BOOTSTRAP", "0")
        bootstrap_with_app(app=None)
        from app import app

        from scripts.db_bootstrap import seed_campus

        seed_campus(app)
        print(f"Seed complete. Deploy login: {Config.SEED_USERNAME} / {Config.SEED_PASSWORD}")


if __name__ == "__main__":
    main()
