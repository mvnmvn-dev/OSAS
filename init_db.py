import os

from config import Config
from scripts.db_bootstrap import apply_schema, ensure_database, _seed_users

os.environ.setdefault("AUTO_DB_BOOTSTRAP", "0")


ensure_database(Config.PGDATABASE or "osas_bcp")
apply_schema(Config.PGDATABASE or "osas_bcp")
_seed_users(Config.PGDATABASE or "osas_bcp")

from app import app  # noqa: E402
from services.safety_service import ensure_campus_seed  # noqa: E402

with app.app_context():
    ensure_campus_seed()
    print("OSAS PostgreSQL database initialized.")
    print(f"Deploy login: {Config.SEED_USERNAME} / {Config.SEED_PASSWORD}")
    print("Local demo also seeded: admin / security.head · password123")
