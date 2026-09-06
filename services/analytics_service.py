"""Analytics event logging and dashboard aggregates."""
from models.database import execute, query_all, query_one
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
    """Return useful aggregate metrics for dashboards and future API consumers."""
    stats = {}
    queries = {
        "incidents_total": "SELECT COUNT(*) AS c FROM incidents",
        "incidents_open": "SELECT COUNT(*) AS c FROM incidents WHERE status IN ('reported','verified','responding')",
        "routes_total": "SELECT COUNT(*) AS c FROM patrol_routes",
        "routes_completed": "SELECT COUNT(*) AS c FROM patrol_routes WHERE status='completed'",
        "checkpoint_scans_7d": "SELECT COUNT(*) AS c FROM patrol_logs WHERE scanned_at >= NOW() - INTERVAL 7 DAY",
        "audit_entries": "SELECT COUNT(*) AS c FROM audit_logs",
    }
    for key, sql in queries.items():
        row = query_one(sql)
        stats[key] = int(row["c"] if row else 0)
    stats["incident_status"] = query_all("SELECT status, COUNT(*) AS count FROM incidents GROUP BY status")
    return stats
