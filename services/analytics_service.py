"""Lightweight analytics event logger used by auth and user services."""
from models.database import execute
import json


def log_event(event_type, user_id=None, entity_type=None, entity_id=None, metadata=None):
    try:
        execute(
            """INSERT INTO analytics_log (event_type, user_id, entity_type, entity_id, metadata)
               VALUES (%s, %s, %s, %s, %s)""",
            (
                event_type,
                user_id,
                entity_type,
                entity_id,
                json.dumps(metadata) if metadata is not None else None,
            ),
        )
    except Exception:
        # Auth flows must not fail if analytics table is unavailable
        pass


def get_dashboard_stats():
    return {}
