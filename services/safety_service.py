"""Campus safety service — MySQL-backed dashboard, CRUD, OSRM routes, exports."""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta
from io import BytesIO

from flask import current_app, url_for
from werkzeug.utils import secure_filename

from models.database import execute, query_all, query_one
from services.audit_service import write_audit
from services.notification_service import notify_staff
from services.pagination import DEFAULT_PER_PAGE, paginate_items
from services import osrm_client
from services import risk_scoring
from services import visitor_service
from services.workflow import validate_incident_transition

STATUS_UI = {
    "reported": ("Reported", "danger"),
    "verified": ("Verified", "warning"),
    "responding": ("Responding", "danger"),
    "resolved": ("Resolved", "success"),
    "closed": ("Closed", "success"),
    "scheduled": ("Scheduled", "warning"),
    "in_progress": ("In progress", "info"),
    "completed": ("Completed", "success"),
    "overdue": ("Overdue", "danger"),
    "planned": ("Planned", "success"),
    "cancelled": ("Cancelled", "warning"),
    "open": ("Open", "warning"),
    "mitigating": ("Mitigating", "warning"),
    "accepted": ("Accepted", "success"),
}


INCIDENT_TYPES = {"security", "safety_hazard", "medical", "fire", "earthquake", "lockdown", "other"}


PRIORITY_UI = {
    "critical": ("Critical", "critical"),
    "high": ("High", "high"),
    "medium": ("Medium", "medium"),
    "low": ("Low", "low"),
}


def _title_status(status):
    label, css = STATUS_UI.get((status or "").lower(), ((status or "Unknown").replace("_", " ").title(), "warning"))
    return label, css


def _priority_ui(priority):
    label, css = PRIORITY_UI.get((priority or "medium").lower(), ("Medium", "medium"))
    return label, css


def _now_label():
    return datetime.now().strftime("%I:%M %p").lstrip("0")


def _fmt_time(value):
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return value.strftime("%I:%M %p").lstrip("0")


def _fmt_datetime(value):
    if not value:
        return ""
    return value.strftime("%B %d, %Y") + " - " + value.strftime("%I:%M %p").lstrip("0")


def _relative_ago(value):
    if not value:
        return "Just now"
    if isinstance(value, str):
        return value
    delta = datetime.now() - value
    minutes = max(0, int(delta.total_seconds() // 60))
    if minutes < 1:
        return "Just now"
    if minutes == 1:
        return "1 minute ago"
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours == 1:
        return "1 hour ago"
    if hours < 24:
        return f"{hours} hours ago"
    days = hours // 24
    return "1 day ago" if days == 1 else f"{days} days ago"


def _incident_timeline(row):
    """Build a chronological timeline from existing incident timestamps."""
    events = []
    if row.get("reported_at"):
        events.append({"time": _fmt_time(row["reported_at"]), "text": "Incident reported", "at": row["reported_at"]})
    if row.get("checkpoint_name"):
        events.append({"time": _fmt_time(row.get("reported_at")), "text": f"Checkpoint: {row.get('checkpoint_code') or ''} · {row['checkpoint_name']}", "at": row.get("reported_at")})
    if row.get("verified_at"):
        events.append({"time": _fmt_time(row["verified_at"]), "text": "Incident verified", "at": row["verified_at"]})
    if row.get("team_name"):
        assign_at = row.get("verified_at") or row.get("reported_at")
        events.append({
            "time": _fmt_time(assign_at),
            "text": f"Assigned to {row['team_name']}",
            "at": assign_at,
        })
    elif row.get("assigned_to") and row.get("assignee_name"):
        assign_at = row.get("verified_at") or row.get("reported_at")
        events.append({
            "time": _fmt_time(assign_at),
            "text": f"Assigned to {row['assignee_name']}",
            "at": assign_at,
        })
    status = (row.get("status") or "").lower()
    if status == "responding":
        events.append({
            "time": _fmt_time(row.get("verified_at") or row.get("reported_at")),
            "text": "Response in progress",
            "at": row.get("verified_at") or row.get("reported_at"),
        })
    if row.get("resolved_at"):
        label = "Incident closed" if status == "closed" else "Area secured / incident resolved"
        events.append({"time": _fmt_time(row["resolved_at"]), "text": label, "at": row["resolved_at"]})
    seen = set()
    out = []
    for event in events:
        key = (event["time"], event["text"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"time": event["time"], "text": event["text"]})
    return out


def _campus_security_status(open_incidents, high_priority, critical_open, missed, coverage, readiness, updated_label="Updated just now"):
    """Derive campus status from real operational signals (no invented threat levels)."""
    now = datetime.now()
    stamp = now.strftime("%B %d, %Y") + " - " + now.strftime("%I:%M %p").lstrip("0")
    if critical_open > 0:
        return {
            "key": "alert",
            "label": "SECURITY ALERT",
            "tone": "critical",
            "datetime": stamp,
            "updated": updated_label,
            "detail": f"{critical_open} critical incident{'s' if critical_open != 1 else ''} require{'s' if critical_open == 1 else ''} immediate attention",
        }
    if readiness < 50 or (coverage < 50 and missed > 0):
        return {
            "key": "degraded",
            "label": "SYSTEM DEGRADED",
            "tone": "degraded",
            "datetime": stamp,
            "updated": updated_label,
            "detail": f"Readiness {readiness}% · patrol coverage {coverage}%",
        }
    if high_priority > 0 or open_incidents > 0 or missed > 0 or coverage < 80:
        return {
            "key": "attention",
            "label": "ATTENTION REQUIRED",
            "tone": "attention",
            "datetime": stamp,
            "updated": updated_label,
            "detail": f"{open_incidents} open incident{'s' if open_incidents != 1 else ''} · {missed} missed scan{'s' if missed != 1 else ''}",
        }
    return {
        "key": "operational",
        "label": "SYSTEM OPERATIONAL",
        "tone": "operational",
        "datetime": stamp,
        "updated": updated_label,
        "detail": "No critical issues requiring immediate attention",
    }


def _build_priority_actions(all_incidents, patrol_meta, actions, inspections, drills, urls):
    """Surface existing records that need attention — dashboard summary only."""
    items = []
    for incident in all_incidents:
        if incident["status_key"] in ("resolved", "closed"):
            continue
        priority = (incident.get("priority") or "medium").lower()
        if priority == "critical":
            items.append({
                "tone": "critical",
                "label": "Critical",
                "title": f"Incident at {incident['location']} requires response",
                "detail": incident["title"],
                "action": "View Incident",
                "url": urls["incidents"],
                "code": incident["code"],
            })
        elif priority == "high":
            items.append({
                "tone": "high",
                "label": "Attention",
                "title": incident["title"],
                "detail": f"{incident['location']} · {incident['time']}",
                "action": "View Incident",
                "url": urls["incidents"],
                "code": incident["code"],
            })
    if patrol_meta.get("missed", 0) > 0:
        items.append({
            "tone": "high",
            "label": "Attention",
            "title": f"{patrol_meta['missed']} checkpoint scan{'s' if patrol_meta['missed'] != 1 else ''} missed",
            "detail": f"Coverage at {patrol_meta.get('coverage', 0)}% this week",
            "action": "Review",
            "url": urls["patrol"],
            "code": None,
        })
    for action in actions[:2]:
        items.append({
            "tone": "medium",
            "label": "Pending",
            "title": action.get("title") or "OSAS action pending",
            "detail": action.get("detail") or "",
            "action": "Review",
            "url": urls["inspections"],
            "code": None,
        })
    for insp in inspections:
        if insp.get("status") in ("Scheduled", "Overdue", "Follow-up"):
            items.append({
                "tone": "medium",
                "label": "Pending",
                "title": "Inspection requires attention",
                "detail": f"{insp.get('zone')} · {insp.get('status')}",
                "action": "Review",
                "url": urls["inspections"],
                "code": None,
            })
            break
    for drill in drills:
        if drill.get("status") in ("Planned", "Scheduled"):
            items.append({
                "tone": "medium",
                "label": "Pending",
                "title": "Emergency drill confirmation required",
                "detail": drill.get("title") or drill.get("schedule") or "",
                "action": "Review",
                "url": urls["emergency"],
                "code": None,
            })
            break
    return items[:6]


def _build_live_activity(activity, all_incidents):
    """Dashboard live feed from existing audit + incident events."""
    feed = []
    for a in activity[:8]:
        detail = a.get("detail") or ""
        time_label = _now_label() if str(detail).startswith("Today") else (detail.split("·")[0].strip() if "·" in detail else "")
        feed.append({
            "time": time_label or "—",
            "title": a.get("title") or "System event",
            "detail": detail,
            "module": a.get("module") or "system",
        })
    for incident in all_incidents[:3]:
        if incident["status_key"] in ("reported", "verified", "responding"):
            feed.append({
                "time": incident.get("time") or "—",
                "title": f"Incident {incident['status'].lower()}",
                "detail": f"{incident['title']} · {incident['location']}",
                "module": "incident",
            })
    return feed[:10]


def _checkpoint_scan_history(checkpoint_id, limit=4):
    rows = query_all(
        """SELECT scanned_at, result FROM patrol_logs
           WHERE checkpoint_id=%s ORDER BY scanned_at DESC LIMIT %s""",
        (checkpoint_id, limit),
    )
    out = []
    for r in rows:
        result = (r.get("result") or "valid").lower()
        ok = result == "valid"
        out.append({
            "time": _fmt_time(r.get("scanned_at")),
            "label": "Scanned" if ok else result.replace("_", " ").title(),
            "ok": ok,
        })
    return out


# BCP MV Campus center — Lot 762, Topaz cor. Sapphire St., Brgy. San Agustin, Novaliches, QC
MV_CENTER_LAT = 14.72632
MV_CENTER_LNG = 121.03601

# Official BCP MV Campus geography from the OSAS GIS Pack v1.0.
CAMPUS_BUILDINGS = [
    ("MAIN", "Main Academic Building", 4, 14.72638, 121.03605),
    ("SCI", "Science / Laboratory Building", 3, 14.72655, 121.03692),
    ("SC", "Student Center", 2, 14.72618, 121.03635),
    ("ADMIN", "Administration Building", 3, 14.72678, 121.03602),
    ("SEC", "OSAS Command Center", 1, 14.72630, 121.03644),
]

CAMPUS_ZONES = [
    ("ZA", "Zone A · Main Gate & Visitor Entrance", "MAIN", "gate", 90),
    ("ZB", "Zone B · Academic Buildings & Classrooms", "MAIN", "building", 75),
    ("ZC", "Zone C · Science & Computer Laboratories", "SCI", "building", 82),
    ("ZD", "Zone D · Student Center & Open Court", "SC", "open_space", 84),
    ("ZE", "Zone E · Administration & Library", "ADMIN", "building", 55),
    ("ZF", "Zone F · Parking & Perimeter Fence", None, "parking", 90),
    ("SECZ", "OSAS Command Center Zone", "SEC", "building", 25),
    ("AZA", "Assembly Zone A · Topaz side", "MAIN", "assembly_area", 20),
    ("AZB", "Assembly Zone B · Sapphire side", "SCI", "assembly_area", 20),
]

CAMPUS_CHECKPOINTS = [
    # code, name, zone, lat, lng, official risk weight
    ("ENT", "Entrance", "ZA", 14.726050, 121.035880, 10),
    ("PG", "Prefect Guidance", "ZE", 14.726780, 121.036020, 6),
    ("OSAS", "OSAS", "SECZ", 14.726300, 121.036440, 4),
    ("ITS", "ITS", "ZC", 14.726120, 121.036820, 7),
]

OFFICIAL_ZONE_FREQUENCY = {
    "ZA": "Every 30 minutes",
    "ZB": "Every 45 minutes",
    "ZC": "Every 60 minutes",
    "ZD": "Every 30 minutes during breaks",
    "ZE": "Every 60 minutes",
    "ZF": "Every 45 minutes and during dismissal",
}

OFFICIAL_PATROL_ROUTES = {
    "Alpha": ["ENT", "PG", "OSAS", "ITS"],
    "Bravo": ["OSAS", "ITS", "PG", "ENT"],
    "Charlie": ["ENT", "ITS", "OSAS", "PG"],
    "Delta": ["PG", "ENT", "ITS", "OSAS"],
}

OFFICIAL_CHECKPOINT_RISK = {
    "ENT": ("Critical", 10),
    "PG": ("Medium", 6),
    "OSAS": ("Medium", 4),
    "ITS": ("High", 7),
}

ACTIVE_CHECKPOINT_CODES = {row[0] for row in CAMPUS_CHECKPOINTS}

# These operations touch many rows and issue multiple serial SQL queries.
# Cache them per Gunicorn worker instead of repeating them on every page view.
_dashboard_seed_ready = False
_dashboard_seed_lock = threading.Lock()
_risk_refresh_at = 0.0
_risk_refresh_lock = threading.Lock()
_RISK_REFRESH_INTERVAL_SECONDS = 300


def _prepare_dashboard_data():
    global _dashboard_seed_ready, _risk_refresh_at
    if not _dashboard_seed_ready:
        with _dashboard_seed_lock:
            if not _dashboard_seed_ready:
                ensure_campus_seed()
                _dashboard_seed_ready = True

    now = time.monotonic()
    if now - _risk_refresh_at >= _RISK_REFRESH_INTERVAL_SECONDS:
        with _risk_refresh_lock:
            now = time.monotonic()
            if now - _risk_refresh_at >= _RISK_REFRESH_INTERVAL_SECONDS:
                try:
                    risk_scoring.refresh_all_risk_scores()
                except Exception:
                    # Preserve current dashboard behavior if risk refresh fails.
                    pass
                finally:
                    _risk_refresh_at = time.monotonic()


def sync_mv_campus_geo():
    """Upsert the official GIS pack and retire former placeholder labels."""
    bmap = {r["building_code"]: r["id"] for r in query_all("SELECT id, building_code FROM buildings")}
    for code, name, floors, lat, lng in CAMPUS_BUILDINGS:
        execute("""UPDATE buildings SET building_name=%s, floors=%s, latitude=%s, longitude=%s WHERE building_code=%s""", (name, floors, lat, lng, code))
    for code, name, building_code, ztype, risk in CAMPUS_ZONES:
        if not query_one("SELECT id FROM campus_zones WHERE zone_code=%s", (code,)):
            execute("INSERT INTO campus_zones (zone_code, zone_name, building_id, zone_type, risk_score) VALUES (%s,%s,%s,%s,%s)", (code, name, bmap.get(building_code) if building_code else None, ztype, risk))
        execute("UPDATE campus_zones SET zone_name=%s, zone_type=%s, active=1 WHERE zone_code=%s", (name, ztype, code))
    zmap = {r["zone_code"]: r["id"] for r in query_all("SELECT id, zone_code FROM campus_zones")}
    legacy_checkpoint_map = {
        "G1": "ENT",
        "G2": "ENT",
        "G3": "ENT",
        "AD1": "PG",
        "AD4": "OSAS",
        "SEC": "OSAS",
        "LAB2": "ITS",
        "LAB1": "ITS",
    }
    for legacy_code, official_code in legacy_checkpoint_map.items():
        legacy = query_one("SELECT id FROM checkpoints WHERE checkpoint_code=%s", (legacy_code,))
        target = query_one("SELECT id FROM checkpoints WHERE checkpoint_code=%s", (official_code,))
        if legacy and not target and legacy_code != official_code:
            execute("UPDATE checkpoints SET checkpoint_code=%s WHERE id=%s", (official_code, legacy["id"]))
    for code, name, zcode, lat, lng, _risk in CAMPUS_CHECKPOINTS:
        if not query_one("SELECT id FROM checkpoints WHERE checkpoint_code=%s", (code,)):
            execute(
                """INSERT INTO checkpoints (checkpoint_code, checkpoint_name, zone_id, scan_token, latitude, longitude)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (code, name, zmap[zcode], f"tok-{code.lower()}-{secrets.token_hex(4)}", lat, lng),
            )
        execute(
            """UPDATE checkpoints SET checkpoint_name=%s, zone_id=%s, latitude=%s, longitude=%s, active=1
               WHERE checkpoint_code=%s""",
            (name, zmap[zcode], lat, lng, code),
        )
    # Retire former GIS-pack checkpoints that are no longer in the active set
    for row in query_all("SELECT id, checkpoint_code FROM checkpoints") or []:
        if row["checkpoint_code"] not in ACTIVE_CHECKPOINT_CODES:
            execute("UPDATE checkpoints SET active=0 WHERE id=%s", (row["id"],))
    for legacy_zone in ("G2", "QN", "S1", "SCL"):
        execute("UPDATE campus_zones SET active=0 WHERE zone_code=%s", (legacy_zone,))
    _ensure_incident_schema()
    cp_ent = query_one("SELECT id FROM checkpoints WHERE checkpoint_code='ENT' LIMIT 1")
    cp_its = query_one("SELECT id FROM checkpoints WHERE checkpoint_code='ITS' LIMIT 1")
    cp_pg = query_one("SELECT id FROM checkpoints WHERE checkpoint_code='PG' LIMIT 1")
    team_a = query_one("SELECT id FROM patrol_teams WHERE team_name='Team Alpha' LIMIT 1")
    team_b = query_one("SELECT id FROM patrol_teams WHERE team_name='Team Bravo' LIMIT 1")
    if cp_ent and team_a:
        execute("UPDATE incidents SET checkpoint_id=%s, team_id=%s WHERE incident_code='INC-24091' AND team_id IS NULL", (cp_ent["id"], team_a["id"]))
    if cp_its and team_b:
        execute("UPDATE incidents SET checkpoint_id=%s, team_id=%s WHERE incident_code='INC-24090' AND team_id IS NULL", (cp_its["id"], team_b["id"]))
    if cp_pg and team_a:
        execute("UPDATE incidents SET checkpoint_id=%s, team_id=%s WHERE incident_code='INC-24089' AND team_id IS NULL", (cp_pg["id"], team_a["id"]))
    if team_a:
        execute("UPDATE patrol_teams SET lead_name='Officer Cruz' WHERE id=%s AND (lead_name IS NULL OR lead_name='')", (team_a["id"],))
    if team_b:
        execute("UPDATE patrol_teams SET lead_name='Officer Santos' WHERE id=%s AND (lead_name IS NULL OR lead_name='')", (team_b["id"],))
    _sync_legacy_team_leads()


def _sync_legacy_team_leads():
    """Recover user-entered team leads from audit_logs for teams created prior to lead_name column."""
    try:
        import json
        teams = query_all("SELECT id, team_name, lead_name FROM patrol_teams") or []
        audits = query_all(
            "SELECT entity_id, new_values FROM audit_logs WHERE action = 'Team saved' ORDER BY id DESC"
        ) or []

        fallback_seeds = {
            "alpha": "Officer Cruz",
            "team alpha": "Officer Cruz",
            "bravo": "Officer Santos",
            "team bravo": "Officer Santos",
            "charlie": "Officer Reyes",
            "team charlie": "Officer Reyes",
            "delta": "Officer Ramos",
            "team delta": "Officer Ramos",
        }

        for t in teams:
            tid = t["id"]
            tname = (t.get("team_name") or "").strip()
            current_lead = (t.get("lead_name") or "").strip()

            if current_lead and current_lead != "OSAS Administrator":
                continue

            recovered_lead = None
            for a in audits:
                try:
                    nv = a.get("new_values")
                    if isinstance(nv, str):
                        nv = json.loads(nv)
                    if not isinstance(nv, dict):
                        continue

                    audit_name = (nv.get("name") or "").strip()
                    if a.get("entity_id") == tid or (audit_name and audit_name.lower() == tname.lower()):
                        lead_val = (nv.get("lead") or "").strip()
                        if lead_val and lead_val != "OSAS Administrator":
                            recovered_lead = lead_val
                            break
                except Exception:
                    continue

            if not recovered_lead:
                recovered_lead = fallback_seeds.get(tname.lower())

            if recovered_lead:
                execute("UPDATE patrol_teams SET lead_name = %s WHERE id = %s", (recovered_lead, tid))
    except Exception:
        pass


def _ensure_incident_schema():
    """Ensure incidents, patrol_teams, drills, and safety_inspections have extended columns in runtime database."""
    try:
        is_testing = current_app.config.get("TESTING")
        if is_testing:
            info = query_all("PRAGMA table_info(incidents)")
            cols = {r["name"] for r in info} if info else set()
            team_info = query_all("PRAGMA table_info(patrol_teams)")
            team_cols = {r["name"] for r in team_info} if team_info else set()
            drill_info = query_all("PRAGMA table_info(drills)")
            drill_cols = {r["name"] for r in drill_info} if drill_info else set()
            insp_info = query_all("PRAGMA table_info(safety_inspections)")
            insp_cols = {r["name"] for r in insp_info} if insp_info else set()
        else:
            # PostgreSQL/Neon does not support MySQL's SHOW COLUMNS syntax.
            def _pg_columns(table_name):
                rows = query_all(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s",
                    (table_name,),
                )
                return {row["column_name"] for row in rows} if rows else set()

            cols = _pg_columns("incidents")
            team_cols = _pg_columns("patrol_teams")
            drill_cols = _pg_columns("drills")
            insp_cols = _pg_columns("safety_inspections")

        if "checkpoint_id" not in cols:
            try:
                execute("ALTER TABLE incidents ADD COLUMN checkpoint_id INT NULL")
            except Exception:
                pass
        if "team_id" not in cols:
            try:
                execute("ALTER TABLE incidents ADD COLUMN team_id INT NULL")
            except Exception:
                pass
        if "lead_name" not in team_cols:
            try:
                execute("ALTER TABLE patrol_teams ADD COLUMN lead_name VARCHAR(150) NULL")
            except Exception:
                pass

        if "team_id" not in drill_cols:
            try:
                execute("ALTER TABLE drills ADD COLUMN team_id INT NULL")
            except Exception:
                pass
        if "checkpoint_id" not in drill_cols:
            try:
                execute("ALTER TABLE drills ADD COLUMN checkpoint_id INT NULL")
            except Exception:
                pass
        if "inspection_id" not in drill_cols:
            try:
                execute("ALTER TABLE drills ADD COLUMN inspection_id INT NULL")
            except Exception:
                pass

        if "checkpoint_id" not in insp_cols:
            try:
                execute("ALTER TABLE safety_inspections ADD COLUMN checkpoint_id INT NULL")
            except Exception:
                pass
        if "team_id" not in insp_cols:
            try:
                execute("ALTER TABLE safety_inspections ADD COLUMN team_id INT NULL")
            except Exception:
                pass
    except Exception:
        pass


def ensure_campus_seed():
    """Insert baseline campus geography if the DB is empty."""
    _ensure_incident_schema()
    _sync_legacy_team_leads()
    if current_app.config.get("TESTING"):
        if not query_one("SELECT id FROM campus_zones LIMIT 1"):
            execute(
                "INSERT INTO campus_zones (zone_code, zone_name, zone_type, risk_score) VALUES (%s,%s,%s,%s)",
                ("ZA", "Zone A · Main Gate & Visitor Entrance", "gate", 90),
            )
        if not query_one("SELECT id FROM patrol_teams LIMIT 1"):
            execute("INSERT INTO patrol_teams (team_name, active) VALUES ('Team Alpha', 1)")
            execute("INSERT INTO patrol_teams (team_name, active) VALUES ('Team Bravo', 1)")
        if not query_one("SELECT id FROM checkpoints LIMIT 1"):
            execute("INSERT INTO checkpoints (checkpoint_code, checkpoint_name, zone_id, scan_token) VALUES ('ENT', 'Main Gate Entrance', 1, 'tok-ent-test')")
        return True

    if query_one("SELECT id FROM buildings LIMIT 1"):
        sync_mv_campus_geo()
        return False

    for code, name, floors, lat, lng in CAMPUS_BUILDINGS:
        execute(
            "INSERT INTO buildings (building_code, building_name, floors, latitude, longitude) VALUES (%s,%s,%s,%s,%s)",
            (code, name, floors, lat, lng),
        )

    bmap = {r["building_code"]: r["id"] for r in query_all("SELECT id, building_code FROM buildings")}
    for code, name, building_code, ztype, risk in CAMPUS_ZONES:
        bid = bmap.get(building_code) if building_code else None
        execute(
            """INSERT INTO campus_zones (zone_code, zone_name, building_id, zone_type, risk_score)
               VALUES (%s,%s,%s,%s,%s)""",
            (code, name, bid, ztype, risk),
        )

    zmap = {r["zone_code"]: r["id"] for r in query_all("SELECT id, zone_code FROM campus_zones")}
    for code, name, zcode, lat, lng, _risk in CAMPUS_CHECKPOINTS:
        token = f"tok-{code.lower()}-{secrets.token_hex(4)}"
        execute(
            """INSERT INTO checkpoints (checkpoint_code, checkpoint_name, zone_id, scan_token, latitude, longitude)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (code, name, zmap[zcode], token, lat, lng),
        )

    admin = query_one("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
    lead_id = admin["id"] if admin else None
    execute("INSERT INTO patrol_teams (team_name, team_lead_id, active) VALUES (%s,%s,1)", ("Team Alpha", lead_id))
    execute("INSERT INTO patrol_teams (team_name, team_lead_id, active) VALUES (%s,%s,1)", ("Team Bravo", lead_id))

    team_a = query_one("SELECT id FROM patrol_teams WHERE team_name='Team Alpha' LIMIT 1")
    team_b = query_one("SELECT id FROM patrol_teams WHERE team_name='Team Bravo' LIMIT 1")
    cp_ent = query_one("SELECT id FROM checkpoints WHERE checkpoint_code='ENT' LIMIT 1")
    cp_its = query_one("SELECT id FROM checkpoints WHERE checkpoint_code='ITS' LIMIT 1")
    cp_pg = query_one("SELECT id FROM checkpoints WHERE checkpoint_code='PG' LIMIT 1")

    execute(
        """INSERT INTO incidents (incident_code, zone_id, checkpoint_id, team_id, reported_by, incident_type, title, description, priority, status)
           VALUES (%s,%s,%s,%s,%s,'security',%s,%s,'high','responding')""",
        ("INC-24091", zmap["ZA"], cp_ent["id"] if cp_ent else None, team_a["id"] if team_a else None, lead_id, "Unauthorized access attempt", "Attempted entry without ID at Main Gate · Topaz / Sapphire"),
    )
    execute(
        """INSERT INTO incidents (incident_code, zone_id, checkpoint_id, team_id, reported_by, incident_type, title, description, priority, status)
           VALUES (%s,%s,%s,%s,%s,'safety_hazard',%s,%s,'medium','verified')""",
        ("INC-24090", zmap["ZC"], cp_its["id"] if cp_its else None, team_b["id"] if team_b else None, lead_id, "Wet floor reported", "Spill on Science Laboratory Wing corridor"),
    )
    execute(
        """INSERT INTO incidents (incident_code, zone_id, checkpoint_id, team_id, reported_by, incident_type, title, description, priority, status)
           VALUES (%s,%s,%s,%s,%s,'medical',%s,%s,'high','resolved')""",
        ("INC-24089", zmap["ZD"], cp_pg["id"] if cp_pg else None, team_a["id"] if team_a else None, lead_id, "Medical assistance requested", "Student required first aid in Student Center lobby"),
    )

    execute(
        """INSERT INTO safety_inspections (inspection_code, zone_id, inspector_id, scheduled_date, status, notes)
           VALUES (%s,%s,%s,CURDATE() + INTERVAL 2 DAY,'scheduled',%s)""",
        ("INS-2026-031", zmap["ZC"], lead_id, "Fire safety equipment"),
    )
    execute(
        """INSERT INTO safety_inspections (inspection_code, zone_id, inspector_id, scheduled_date, status, notes)
           VALUES (%s,%s,%s,CURDATE() - INTERVAL 1 DAY,'overdue',%s)""",
        ("INS-2026-030", zmap["ZA"], lead_id, "Access control and lighting — corrective action open"),
    )

    execute(
        """INSERT INTO drills (drill_type, title, scheduled_at, lead_officer_id, status)
           VALUES ('fire',%s, DATE_ADD(NOW(), INTERVAL 9 DAY), %s, 'planned')""",
        ("Fire evacuation drill · MV Campus", lead_id),
    )
    execute(
        """INSERT INTO drills (drill_type, title, scheduled_at, lead_officer_id, status)
           VALUES ('earthquake',%s, DATE_ADD(NOW(), INTERVAL 16 DAY), %s, 'planned')""",
        ("Earthquake response drill · MV Campus", lead_id),
    )

    execute(
        """INSERT INTO evacuation_plans (building_id, floor_label, plan_name, primary_assembly_zone_id, version_no, created_by)
           VALUES (%s,'Floors 1–4','Main Academic Building Evacuation',%s,3,%s)""",
        (bmap["MAIN"], zmap["AZA"], lead_id),
    )
    execute(
        """INSERT INTO evacuation_plans (building_id, floor_label, plan_name, primary_assembly_zone_id, version_no, created_by)
           VALUES (%s,'Floors 1–3','Science Building Evacuation',%s,2,%s)""",
        (bmap["SCI"], zmap["AZB"], lead_id),
    )

    execute(
        """INSERT INTO first_aid_inventory (item_name, cabinet_location, quantity, reorder_level, status)
           VALUES ('First-aid kit','Science Building · Cabinet A',2,3,'low_stock')""",
    )
    execute(
        """INSERT INTO risk_assessments (zone_id, assessed_by, hazard_title, likelihood, severity, risk_score, status)
           VALUES (%s,%s,'Earthquake structural review',3,4,12,'open')""",
        (zmap["ZC"], lead_id),
    )

    risk_scoring.refresh_all_risk_scores()
    return True


def _incident_rows():
    _ensure_incident_schema()
    rows = query_all(
        """
        SELECT i.*, z.zone_name, z.zone_code,
               c.checkpoint_code, c.checkpoint_name,
               pt.team_name,
               u.full_name AS assignee_name
        FROM incidents i
        LEFT JOIN campus_zones z ON z.id = i.zone_id
        LEFT JOIN checkpoints c ON c.id = i.checkpoint_id
        LEFT JOIN patrol_teams pt ON pt.id = i.team_id
        LEFT JOIN users u ON u.id = i.assigned_to
        ORDER BY
            FIELD(i.priority, 'critical', 'high', 'medium', 'low'),
            FIELD(i.status, 'responding', 'verified', 'reported', 'resolved', 'closed'),
            i.reported_at DESC
        """
    )
    out = []
    for r in rows:
        label, css = _title_status(r["status"])
        p_label, p_css = _priority_ui(r.get("priority"))
        status_phrase = {
            "reported": "Reported",
            "verified": "Verified",
            "responding": "Response in progress",
            "resolved": "Resolved",
            "closed": "Closed",
        }.get((r.get("status") or "").lower(), label)
        location = r.get("zone_name")
        if r.get("checkpoint_name"):
            location = f"{r['checkpoint_name']} ({r.get('zone_name') or 'Zone'})" if r.get("zone_name") else r["checkpoint_name"]
        elif not location:
            desc = (r.get("description") or "").split("\n")[0].strip()
            location = desc[:80] if desc else "Unspecified zone"

        assigned_team = r.get("team_name") or r.get("assignee_name") or "Unassigned"
        out.append({
            "id": r["id"],
            "code": r["incident_code"],
            "title": r["title"],
            "location": location,
            "raw_location": (r.get("description") or "").split("\n")[0].strip(),
            "time": _fmt_time(r.get("reported_at")),
            "reported_full": _fmt_datetime(r.get("reported_at")),
            "status": label,
            "status_phrase": status_phrase,
            "status_key": r["status"],
            "status_class": css,
            "priority": (r.get("priority") or "medium").lower(),
            "priority_label": p_label,
            "priority_class": p_css,
            "team": assigned_team,
            "team_id": r.get("team_id"),
            "team_name": r.get("team_name"),
            "checkpoint_id": r.get("checkpoint_id"),
            "checkpoint_code": r.get("checkpoint_code"),
            "checkpoint_name": r.get("checkpoint_name"),
            "zone_id": r.get("zone_id"),
            "zone_name": r.get("zone_name"),
            "timeline": _incident_timeline(r),
            "updated_ago": _relative_ago(r.get("resolved_at") or r.get("verified_at") or r.get("reported_at")),
        })
    return out


def _checkpoint_lookup_rows():
    """Lightweight checkpoint options for forms that do not display patrol history."""
    rows = query_all(
        """SELECT c.id, c.checkpoint_code, c.checkpoint_name, c.scan_token,
                  c.latitude, c.longitude, z.zone_name, z.zone_code
           FROM checkpoints c JOIN campus_zones z ON z.id = c.zone_id
           WHERE c.active = 1 ORDER BY c.checkpoint_code"""
    )
    return [{
        "id": r["id"], "code": r["checkpoint_code"], "name": r["checkpoint_name"],
        "zone_name": r["zone_name"], "scan_token": r["scan_token"],
        "lat": float(r["latitude"]) if r["latitude"] is not None else None,
        "lng": float(r["longitude"]) if r["longitude"] is not None else None,
        "active": True, "risk": 0, "open_incidents": 0, "history": [],
        "last_scan": "—", "last_scan_ago": "No scans yet", "patrol_status": "Awaiting scan",
        "patrol_status_ok": False, "risk_level": "MEDIUM", "tone": "warning",
        "official_risk": "Medium", "official_score": 5,
        "patrol_frequency": OFFICIAL_ZONE_FREQUENCY.get(r.get("zone_code"), "Every 60 minutes"),
        "method": "QR", "map_class": None,
    } for r in rows]


def _checkpoint_rows():
    """Load dashboard checkpoints in a few queries, using the cached zone risk score."""
    rows = query_all(
        """SELECT c.*, z.zone_name, z.zone_code, z.risk_score AS zone_risk,
                  (SELECT MAX(pl.scanned_at) FROM patrol_logs pl WHERE pl.checkpoint_id = c.id) AS last_scan,
                  (SELECT COUNT(*) FROM incidents i WHERE (i.checkpoint_id = c.id OR (i.checkpoint_id IS NULL AND i.zone_id = c.zone_id)) AND i.status NOT IN ('resolved', 'closed')) AS open_incidents
           FROM checkpoints c JOIN campus_zones z ON z.id = c.zone_id
           WHERE c.active = 1 ORDER BY c.checkpoint_code"""
    )
    ids = [r["id"] for r in rows]
    histories = {checkpoint_id: [] for checkpoint_id in ids}
    if ids:
        history_rows = query_all(
            """SELECT checkpoint_id, scanned_at, result FROM (
                   SELECT checkpoint_id, scanned_at, result,
                          ROW_NUMBER() OVER (PARTITION BY checkpoint_id ORDER BY scanned_at DESC) AS rn
                   FROM patrol_logs WHERE checkpoint_id = ANY(%s)
               ) recent WHERE rn <= 4 ORDER BY checkpoint_id, scanned_at DESC""",
            (ids,),
        )
        for row in history_rows:
            result = (row.get("result") or "valid").lower()
            histories[row["checkpoint_id"]].append({
                "time": _fmt_time(row.get("scanned_at")),
                "label": "Scanned" if result == "valid" else result.replace("_", " ").title(),
                "ok": result == "valid",
            })
    out = []
    for r in rows:
        official_risk, official_score = OFFICIAL_CHECKPOINT_RISK.get(r["checkpoint_code"], ("Medium", 5))
        score = float(r.get("zone_risk") or official_score or 50)
        tone = "danger" if score >= 80 else "warning" if score >= 65 else "success"
        last = r.get("last_scan")
        history = histories.get(r["id"], [])
        delayed = bool(history and not history[0]["ok"])
        on_schedule = bool(last) and not delayed
        out.append({
            "id": r["id"], "code": r["checkpoint_code"], "name": r["checkpoint_name"],
            "method": "QR", "last_scan": last.strftime("%I:%M %p").lstrip("0") if last else "—",
            "last_scan_ago": _relative_ago(last) if last else "No scans yet", "risk": score,
            "official_risk": official_risk, "official_score": official_score,
            "patrol_frequency": OFFICIAL_ZONE_FREQUENCY.get(r.get("zone_code"), "Every 60 minutes"),
            "tone": tone, "lat": float(r["latitude"]) if r["latitude"] is not None else None,
            "lng": float(r["longitude"]) if r["longitude"] is not None else None,
            "scan_token": r["scan_token"],
            "map_class": {"ENT": "gate", "PG": "center", "OSAS": "command", "ITS": "science"}.get(r["checkpoint_code"]),
            "zone_name": r.get("zone_name"), "active": True,
            "patrol_status": "On Schedule" if on_schedule else ("Delayed" if delayed else "Awaiting scan"),
            "patrol_status_ok": on_schedule, "history": history,
            "risk_level": official_risk.upper(), "open_incidents": r.get("open_incidents") or 0,
        })
    return out


def _relative_updated_label(ts):
    if not ts:
        return "No module activity yet"
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(str(ts).replace("Z", ""))
        except ValueError:
            return str(ts)
    if getattr(ts, "tzinfo", None):
        ts = ts.replace(tzinfo=None)
    seconds = int((datetime.now() - ts).total_seconds())
    if seconds < 60:
        return "Updated just now"
    if seconds < 3600:
        return f"Updated {seconds // 60}m ago"
    if seconds < 86400:
        return f"Updated {seconds // 3600}h ago"
    return f"Updated {ts.strftime('%b %d, %I:%M %p').lstrip('0')}"


def _last_data_timestamp():
    row = query_one(
        """SELECT GREATEST(
             COALESCE((SELECT MAX(reported_at) FROM incidents), '1970-01-01'),
             COALESCE((SELECT MAX(scanned_at) FROM patrol_logs), '1970-01-01'),
             COALESCE((SELECT MAX(created_at) FROM audit_logs), '1970-01-01'),
             COALESCE((SELECT MAX(scheduled_date) FROM safety_inspections), '1970-01-01')
           ) AS ts"""
    ) or {}
    ts = row.get("ts")
    if not ts or str(ts).startswith("1970"):
        return None
    return ts


def _operational_metrics():
    """Fetch shared dashboard KPIs as a single aggregate round trip."""
    if current_app.config.get("TESTING"):
        row = query_one("""SELECT
            (SELECT COUNT(*) FROM patrol_routes WHERE created_at >= datetime('now','-7 days')) AS route_total,
            (SELECT SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) FROM patrol_routes WHERE created_at >= datetime('now','-7 days')) AS route_completed,
            (SELECT SUM(CASE WHEN status IN ('assigned','in_progress') THEN 1 ELSE 0 END) FROM patrol_routes WHERE created_at >= datetime('now','-7 days')) AS route_active,
            (SELECT COUNT(*) FROM patrol_logs WHERE scanned_at >= datetime('now','-7 days')) AS scan_total,
            (SELECT SUM(CASE WHEN result='valid' THEN 1 ELSE 0 END) FROM patrol_logs WHERE scanned_at >= datetime('now','-7 days')) AS scan_valid,
            (SELECT SUM(CASE WHEN result IN ('missed','late','invalid') THEN 1 ELSE 0 END) FROM patrol_logs WHERE scanned_at >= datetime('now','-7 days')) AS scan_missed,
            (SELECT COUNT(DISTINCT checkpoint_id) FROM patrol_logs WHERE result='valid' AND scanned_at >= datetime('now','-7 days')) AS covered_checkpoints,
            (SELECT COUNT(*) FROM checkpoints WHERE active=1) AS checkpoint_total,
            (SELECT COUNT(*) FROM safety_inspections) AS inspection_total,
            (SELECT SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) FROM safety_inspections) AS inspection_completed,
            (SELECT SUM(CASE WHEN status='scheduled' THEN 1 ELSE 0 END) FROM safety_inspections) AS inspection_scheduled,
            (SELECT SUM(CASE WHEN status='overdue' THEN 1 ELSE 0 END) FROM safety_inspections) AS inspection_overdue,
            (SELECT COUNT(*) FROM drills) AS drill_total,
            (SELECT SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) FROM drills) AS drill_completed,
            (SELECT SUM(CASE WHEN status IN ('planned','scheduled') THEN 1 ELSE 0 END) FROM drills) AS drill_upcoming,
            (SELECT AVG(risk_score) FROM risk_assessments WHERE status IN ('open','mitigating')) AS risk_average,
            (SELECT AVG((julianday(COALESCE(resolved_at,CURRENT_TIMESTAMP))-julianday(reported_at))*1440.0) FROM incidents WHERE reported_at >= datetime('now','-30 days')) AS avg_response,
            (SELECT COUNT(*) FROM patrol_teams WHERE active=1) AS team_total,
            (SELECT COUNT(*) FROM patrol_teams t WHERE active=1 AND EXISTS (SELECT 1 FROM patrol_shifts s WHERE s.team_id=t.id AND s.status='active')) AS team_on_duty,
            ((SELECT COUNT(*) FROM first_aid_inventory WHERE status IN ('low_stock','expired','out_of_stock')) + (SELECT COUNT(*) FROM risk_assessments WHERE status IN ('open','mitigating'))) AS actions_total,
            (SELECT COUNT(*) FROM (SELECT id FROM audit_logs ORDER BY created_at DESC LIMIT 50) recent) AS activity_total""") or {}
    else:
        row = query_one("""SELECT
            (SELECT COUNT(*) FROM patrol_routes WHERE created_at >= NOW() - INTERVAL '7 days') AS route_total,
            (SELECT COUNT(*) FILTER (WHERE status='completed') FROM patrol_routes WHERE created_at >= NOW() - INTERVAL '7 days') AS route_completed,
            (SELECT COUNT(*) FILTER (WHERE status IN ('assigned','in_progress')) FROM patrol_routes WHERE created_at >= NOW() - INTERVAL '7 days') AS route_active,
            (SELECT COUNT(*) FROM patrol_logs WHERE scanned_at >= NOW() - INTERVAL '7 days') AS scan_total,
            (SELECT COUNT(*) FILTER (WHERE result='valid') FROM patrol_logs WHERE scanned_at >= NOW() - INTERVAL '7 days') AS scan_valid,
            (SELECT COUNT(*) FILTER (WHERE result IN ('missed','late','invalid')) FROM patrol_logs WHERE scanned_at >= NOW() - INTERVAL '7 days') AS scan_missed,
            (SELECT COUNT(DISTINCT checkpoint_id) FROM patrol_logs WHERE result='valid' AND scanned_at >= NOW() - INTERVAL '7 days') AS covered_checkpoints,
            (SELECT COUNT(*) FROM checkpoints WHERE active=1) AS checkpoint_total,
            (SELECT COUNT(*) FROM safety_inspections) AS inspection_total,
            (SELECT COUNT(*) FILTER (WHERE status='completed') FROM safety_inspections) AS inspection_completed,
            (SELECT COUNT(*) FILTER (WHERE status='scheduled') FROM safety_inspections) AS inspection_scheduled,
            (SELECT COUNT(*) FILTER (WHERE status='overdue') FROM safety_inspections) AS inspection_overdue,
            (SELECT COUNT(*) FROM drills) AS drill_total,
            (SELECT COUNT(*) FILTER (WHERE status='completed') FROM drills) AS drill_completed,
            (SELECT COUNT(*) FILTER (WHERE status IN ('planned','scheduled')) FROM drills) AS drill_upcoming,
            (SELECT AVG(risk_score) FROM risk_assessments WHERE status IN ('open','mitigating')) AS risk_average,
            (SELECT AVG(EXTRACT(EPOCH FROM (COALESCE(resolved_at,NOW())-reported_at))/60.0) FROM incidents WHERE reported_at >= NOW() - INTERVAL '30 days') AS avg_response,
            (SELECT COUNT(*) FROM patrol_teams WHERE active=1) AS team_total,
            (SELECT COUNT(*) FROM patrol_teams t WHERE active=1 AND EXISTS (SELECT 1 FROM patrol_shifts s WHERE s.team_id=t.id AND s.status='active')) AS team_on_duty,
            ((SELECT COUNT(*) FROM first_aid_inventory WHERE status IN ('low_stock','expired','out_of_stock')) + (SELECT COUNT(*) FROM risk_assessments WHERE status IN ('open','mitigating'))) AS actions_total,
            (SELECT COUNT(*) FROM (SELECT id FROM audit_logs ORDER BY created_at DESC LIMIT 50) recent) AS activity_total""") or {}

    planned = int(row.get("route_total") or 0)
    completed = int(row.get("route_completed") or 0)
    scan_total = int(row.get("scan_total") or 0)
    covered_cp = int(row.get("covered_checkpoints") or 0)
    checkpoint_total = int(row.get("checkpoint_total") or 0)
    valid_scans = int(row.get("scan_valid") or 0)
    if scan_total:
        coverage = round(valid_scans / scan_total * 100)
    elif planned:
        coverage = round(completed / planned * 100)
    elif checkpoint_total:
        coverage = round(covered_cp / checkpoint_total * 100)
    else:
        coverage = 0
    insp_total = int(row.get("inspection_total") or 0)
    drill_total = int(row.get("drill_total") or 0)
    insp_completed = int(row.get("inspection_completed") or 0)
    drill_completed = int(row.get("drill_completed") or 0)
    insp_score = insp_completed / insp_total * 100 if insp_total else 0
    drill_score = drill_completed / drill_total * 100 if drill_total else 0
    risk_score = float(row.get("risk_average") or 0)
    readiness = round(max(0, min(100, insp_score * .4 + drill_score * .3 + (100 - min(100, risk_score * 5)) * .3))) if (insp_total or drill_total or risk_score) else 0
    minutes = row.get("avg_response")
    return {
        "coverage": coverage, "completed": completed, "planned": planned,
        "missed": int(row.get("scan_missed") or 0), "active_routes": int(row.get("route_active") or 0),
        "readiness": readiness,
        "avg_response": f"{max(0, round(float(minutes))):02d}m" if minutes is not None else "—",
        "covered_checkpoints": covered_cp, "checkpoint_total": checkpoint_total,
        "inspection_total": insp_total, "inspection_completed": insp_completed,
        "inspection_scheduled": int(row.get("inspection_scheduled") or 0),
        "inspection_overdue": int(row.get("inspection_overdue") or 0),
        "drill_total": drill_total, "drill_completed": drill_completed,
        "drill_upcoming": int(row.get("drill_upcoming") or 0),
        "team_total": int(row.get("team_total") or 0),
        "team_on_duty": int(row.get("team_on_duty") or 0),
        "actions_total": int(row.get("actions_total") or 0),
        "activity_total": int(row.get("activity_total") or 0),
    }


def _fill_daily_series(rows, days, value_keys=("count",)):
    """Ensure a continuous day series for charts (missing days = 0)."""
    by_day = {}
    for row in rows or []:
        day = row.get("day")
        if hasattr(day, "strftime"):
            key = day.strftime("%Y-%m-%d")
        else:
            key = str(day)[:10]
        by_day[key] = row
    labels = []
    series = {k: [] for k in value_keys}
    for offset in range(days - 1, -1, -1):
        day = (datetime.now() - timedelta(days=offset)).date()
        key = day.strftime("%Y-%m-%d")
        labels.append(day.strftime("%b %d"))
        src = by_day.get(key) or {}
        for k in value_keys:
            series[k].append(int(src.get(k) or 0))
    return labels, series


def _build_dashboard_analytics(
    *,
    open_incidents,
    counts,
    high_priority,
    patrol_meta,
    operational,
    readiness,
    insp_compliance,
    drill_completion,
    zones,
    on_duty_teams,
    team_count,
    actions_count,
    avg_risk,
):
    """Cross-module chart payloads and interpretive insights for Command Dashboard."""
    incident_rows = query_all(
        """SELECT DATE(reported_at) AS day, COUNT(*) AS count
           FROM incidents
           WHERE reported_at >= CURDATE() - INTERVAL 13 DAY
           GROUP BY DATE(reported_at)
           ORDER BY day"""
    )
    incident_labels, incident_series = _fill_daily_series(incident_rows, 14)

    status_order = ["reported", "verified", "responding", "resolved", "closed"]
    status_labels = []
    status_data = []
    for key in status_order:
        n = int(counts.get(key) or 0)
        if n or key in ("reported", "verified", "responding", "resolved"):
            status_labels.append(key.replace("_", " ").title())
            status_data.append(n)

    priority_rows = query_all(
        """SELECT LOWER(COALESCE(priority, 'medium')) AS priority, COUNT(*) AS count
           FROM incidents GROUP BY LOWER(COALESCE(priority, 'medium'))"""
    ) or []
    priority_map = {str(r["priority"]): int(r["count"] or 0) for r in priority_rows}
    priority_order = ["low", "medium", "high", "critical"]
    priority_labels = [p.title() for p in priority_order if priority_map.get(p) or p in ("low", "medium", "high")]
    priority_data = [priority_map.get(p.lower(), 0) for p in [x.lower() for x in priority_labels]]

    zone_labels = [z["name"] for z in (zones or [])[:5]]
    zone_data = [int(z["score"]) for z in (zones or [])[:5]]
    if not zone_labels:
        zone_rows = query_all(
            """SELECT zone_name AS name, ROUND(COALESCE(risk_score, 0)) AS score
               FROM campus_zones WHERE active = 1
               ORDER BY risk_score DESC LIMIT 5"""
        ) or []
        zone_labels = [r["name"] for r in zone_rows]
        zone_data = [int(r["score"] or 0) for r in zone_rows]

    patrol_rows = query_all(
        """SELECT DATE(scanned_at) AS day,
                  COUNT(*) FILTER (WHERE result = 'valid') AS valid,
                  COUNT(*) FILTER (WHERE result IN ('missed', 'late', 'invalid')) AS missed
           FROM patrol_logs
           WHERE scanned_at >= CURDATE() - INTERVAL 6 DAY
           GROUP BY DATE(scanned_at)
           ORDER BY day"""
    )
    patrol_labels, patrol_series = _fill_daily_series(patrol_rows, 7, ("valid", "missed"))

    module_rows = query_all(
        """SELECT COALESCE(NULLIF(entity_type, ''), 'system') AS module, COUNT(*) AS count
           FROM audit_logs
           WHERE created_at >= NOW() - INTERVAL 30 DAY
           GROUP BY COALESCE(NULLIF(entity_type, ''), 'system')
           ORDER BY count DESC
           LIMIT 6"""
    ) or []
    module_labels = [str(r["module"]).replace("_", " ").title() for r in module_rows]
    module_data = [int(r["count"] or 0) for r in module_rows]
    if not module_labels:
        module_labels = ["Incidents", "Patrol", "Inspections", "System"]
        module_data = [0, 0, 0, 0]

    total_inc = sum(int(counts.get(k) or 0) for k in status_order)
    resolved_rate = round(((counts.get("resolved", 0) + counts.get("closed", 0)) / total_inc) * 100) if total_inc else 0
    module_health = {
        "labels": ["Resolution", "Patrol", "Inspections", "Drills", "Readiness"],
        "data": [resolved_rate, int(patrol_meta.get("coverage") or 0), int(insp_compliance), int(drill_completion), int(readiness)],
        "targets": [85, 90, 80, 80, 85],
    }

    inspection_status = query_all(
        """SELECT status, COUNT(*) AS count FROM safety_inspections GROUP BY status"""
    ) or []
    insp_labels = [str(r["status"]).replace("_", " ").title() for r in inspection_status]
    insp_data = [int(r["count"] or 0) for r in inspection_status]

    team_duty = {
        "labels": ["On duty", "Standby"],
        "data": [int(on_duty_teams), max(0, int(team_count) - int(on_duty_teams))],
    }

    # Interpretive insights across modules
    insights = []
    if open_incidents:
        tone = "danger" if open_incidents >= 5 or high_priority else "warning"
        insights.append({
            "module": "Incidents",
            "title": f"{open_incidents:02d} open in the response queue",
            "text": f"{high_priority} high/critical and {counts.get('responding', 0)} actively responding. Avg response {operational.get('avg_response', '—')}.",
            "tone": tone,
            "url": url_for("safety.incidents"),
        })
    else:
        insights.append({
            "module": "Incidents",
            "title": "Incident queue is clear",
            "text": f"No open incidents. Resolution rate {resolved_rate}% across {total_inc} historical records.",
            "tone": "success",
            "url": url_for("safety.incidents"),
        })

    coverage = int(patrol_meta.get("coverage") or 0)
    insights.append({
        "module": "Patrol",
        "title": f"Coverage at {coverage}% this week",
        "text": f"{patrol_meta.get('completed', 0)} routes completed · {patrol_meta.get('missed', 0)} missed scans · {patrol_meta.get('active_routes', 0)} active.",
        "tone": "success" if coverage >= 90 else ("warning" if coverage >= 70 else "danger"),
        "url": url_for("safety.patrol"),
    })

    top_zone = zones[0] if zones else None
    if top_zone:
        insights.append({
            "module": "Risk",
            "title": f"{top_zone['name']} leads campus risk",
            "text": f"Score {top_zone['score']} · {top_zone.get('incidents', 'no open nearby')}. Avg checkpoint risk {avg_risk}.",
            "tone": top_zone.get("tone") or "warning",
            "url": url_for("safety.patrol"),
        })

    insights.append({
        "module": "Compliance",
        "title": f"Campus readiness {readiness}%",
        "text": f"Inspections {insp_compliance}% · drills {drill_completion}% · {actions_count} open OSAS actions · {on_duty_teams}/{team_count} teams on duty.",
        "tone": "success" if readiness >= 80 else ("warning" if readiness >= 60 else "danger"),
        "url": url_for("safety.inspections"),
    })

    summary = {
        "incident_total": total_inc,
        "open_incidents": open_incidents,
        "resolved_rate": resolved_rate,
        "patrol_coverage": coverage,
        "readiness": readiness,
        "avg_response": operational.get("avg_response", "—"),
        "avg_risk": avg_risk,
        "actions_open": actions_count,
    }

    return {
        "incident_trend": {"labels": incident_labels, "data": incident_series["count"]},
        "incident_status": {"labels": status_labels, "data": status_data},
        "incident_priority": {"labels": priority_labels, "data": priority_data},
        "zone_risk": {"labels": zone_labels, "data": zone_data},
        "patrol_trend": {
            "labels": patrol_labels,
            "valid": patrol_series["valid"],
            "missed": patrol_series["missed"],
        },
        "module_activity": {"labels": module_labels, "data": module_data},
        "module_health": module_health,
        "inspection_status": {"labels": insp_labels or ["None"], "data": insp_data or [0]},
        "team_duty": team_duty,
        "insights": insights,
        "summary": summary,
    }


def get_safety_dashboard(section="overview", status_filter=None):
    _prepare_dashboard_data()

    load_incidents = section in {"overview", "incidents"}
    load_checkpoint_details = section in {"overview", "emergency", "patrol", "teams"}
    load_checkpoint_lookups = section in {"incidents", "inspections", "osas"}
    load_checkpoints = load_checkpoint_details or load_checkpoint_lookups
    load_teams = section in {"overview", "incidents", "inspections", "osas", "emergency", "patrol", "teams"}
    load_inspections = section in {"overview", "inspections", "osas", "emergency"}
    load_actions = section in {"overview", "inspections", "osas"}
    load_drills = section in {"overview", "emergency"}
    load_plans = section in {"overview", "emergency"}
    load_activity = section in {"overview", "activity"}
    load_route = section in {"overview", "patrol"}
    load_zones = section in {"overview", "reports"}

    incidents = _incident_rows() if load_incidents else []
    all_incidents = incidents
    checkpoints = (_checkpoint_rows() if load_checkpoint_details else _checkpoint_lookup_rows()) if load_checkpoints else []
    all_checkpoints = checkpoints
    display_checkpoints = [c for c in checkpoints if c.get("active", True)]

    teams_raw = query_all(
        """
        SELECT t.*,
               COALESCE(NULLIF(t.lead_name, ''), CASE WHEN u.role = 'admin' THEN 'Unassigned' ELSE u.full_name END, 'Unassigned') AS lead_name,
               (SELECT COUNT(*) FROM patrol_team_members m WHERE m.team_id = t.id) AS members,
               (SELECT COUNT(*) FROM patrol_shifts s WHERE s.team_id = t.id AND s.status = 'active') AS active_shifts,
               (SELECT COUNT(*) FROM incidents i WHERE i.team_id = t.id AND i.status NOT IN ('resolved', 'closed')) AS active_incidents
        FROM patrol_teams t
        LEFT JOIN users u ON u.id = t.team_lead_id
        WHERE t.active = 1
        ORDER BY t.team_name
        """
    ) if load_teams else []
    teams = []
    for t in teams_raw:
        on_duty = t["active_shifts"] > 0
        members = t["members"] or 3
        active_inc = t.get("active_incidents") or 0
        teams.append({
            "id": t["id"],
            "name": t["team_name"],
            "lead": t.get("lead_name") or "Unassigned",
            "members": members,
            "status": "On duty" if on_duty else "Scheduled",
            "status_class": "success" if on_duty else "warning",
            "shift": "Active shift" if on_duty else "Standby",
            "active_incidents": active_inc,
        })

    inspections_raw = query_all(
        """
        SELECT si.*, z.zone_name, t.team_name, cp.checkpoint_code, cp.checkpoint_name
        FROM safety_inspections si
        JOIN campus_zones z ON z.id = si.zone_id
        LEFT JOIN patrol_teams t ON t.id = si.team_id
        LEFT JOIN checkpoints cp ON cp.id = si.checkpoint_id
        ORDER BY si.scheduled_date DESC
        """
    ) if load_inspections else []
    inspections = []
    for r in inspections_raw:
        label, css = _title_status(r["status"])
        if r["status"] == "overdue":
            label, css = "Follow-up", "danger"
        inspections.append({
            "id": r["id"],
            "code": r["inspection_code"],
            "zone": r["zone_name"],
            "type": r.get("notes") or "General safety",
            "due": r["scheduled_date"].strftime("%B %d") if r.get("scheduled_date") else "",
            "status": label,
            "status_class": css,
            "icon": "fa-wrench" if css == "danger" else "fa-clipboard-check",
            "team_id": r.get("team_id"),
            "team_name": r.get("team_name"),
            "checkpoint_id": r.get("checkpoint_id"),
            "checkpoint_code": r.get("checkpoint_code"),
            "checkpoint_name": r.get("checkpoint_name"),
        })

    actions = []
    for item in (query_all(
        "SELECT * FROM first_aid_inventory WHERE status IN ('low_stock','expired','out_of_stock') ORDER BY quantity ASC LIMIT 20"
    ) if load_actions else []):
        actions.append({
            "title": f"Restock {item['item_name']}",
            "detail": f"{item['cabinet_location']} · qty {item['quantity']}",
        })
    for risk in (query_all(
        """SELECT ra.*, z.zone_name FROM risk_assessments ra
           JOIN campus_zones z ON z.id = ra.zone_id
           WHERE ra.status IN ('open','mitigating') ORDER BY ra.risk_score DESC LIMIT 20"""
    ) if load_actions else []):
        actions.append({
            "title": risk["hazard_title"],
            "detail": f"{risk['zone_name']} · score {risk['risk_score']}",
        })

    drills = []
    for d in (query_all(
        """
        SELECT d.*, t.team_name, cp.checkpoint_code, cp.checkpoint_name, si.inspection_code
        FROM drills d
        LEFT JOIN patrol_teams t ON t.id = d.team_id
        LEFT JOIN checkpoints cp ON cp.id = d.checkpoint_id
        LEFT JOIN safety_inspections si ON si.id = d.inspection_id
        ORDER BY d.scheduled_at DESC
        """
    ) if load_drills else []):
        label, css = _title_status(d["status"])
        icon = {"fire": "fa-fire", "earthquake": "fa-house-chimney", "lockdown": "fa-lock", "medical": "fa-kit-medical"}.get(d["drill_type"], "fa-person-running")
        drills.append({
            "id": d["id"],
            "title": d["title"],
            "schedule": d["scheduled_at"].strftime("%B %d · %I:%M %p") if d.get("scheduled_at") else "",
            "type": d["drill_type"],
            "status": label,
            "status_class": css,
            "icon": icon,
            "team_id": d.get("team_id"),
            "team_name": d.get("team_name"),
            "checkpoint_id": d.get("checkpoint_id"),
            "checkpoint_code": d.get("checkpoint_code"),
            "checkpoint_name": d.get("checkpoint_name"),
            "inspection_id": d.get("inspection_id"),
            "inspection_code": d.get("inspection_code"),
        })

    plans = []
    for p in (query_all(
        """SELECT ep.*, b.building_name, z.zone_name AS assembly
           FROM evacuation_plans ep
           JOIN buildings b ON b.id = ep.building_id
           LEFT JOIN campus_zones z ON z.id = ep.primary_assembly_zone_id
           ORDER BY ep.id DESC"""
    ) if load_plans else []):
        plans.append({
            "id": p["id"],
            "building": p["building_name"],
            "floors": p["floor_label"],
            "version": f"Version {p['version_no']}",
            "assembly": p.get("assembly") or "Assembly TBD",
            "status": "Approved" if p["active"] else "Inactive",
            "status_class": "success" if p["active"] else "warning",
        })

    activity = []
    for a in (query_all(
        """SELECT al.*, u.full_name FROM audit_logs al
           LEFT JOIN users u ON u.id = al.user_id
           ORDER BY al.created_at DESC LIMIT 50"""
    ) if load_activity else []):
        when = a["created_at"]
        day = "Today" if when.date() == datetime.now().date() else when.strftime("%b %d")
        activity.append({
            "title": a["action"],
            "detail": f"{day} · {a.get('full_name') or 'System'}",
            "module": (a.get("entity_type") or "system"),
        })

    operational = _operational_metrics() if section != "visitors" else {
        "coverage": 0, "completed": 0, "planned": 0, "missed": 0,
        "active_routes": 0, "readiness": 0, "avg_response": "—",
        "covered_checkpoints": 0, "checkpoint_total": 0,
    }
    latest_route = query_one(
        """SELECT pr.*, t.team_name FROM patrol_routes pr
           LEFT JOIN patrol_teams t ON t.id = pr.team_id
           ORDER BY pr.created_at DESC LIMIT 1"""
    ) if load_route else None
    sequence = []
    route_geometry = None
    weights = []
    if latest_route:
        route_json = latest_route.get("route_json")
        if isinstance(route_json, str):
            try:
                route_json = json.loads(route_json)
            except Exception:
                route_json = {}
        route_json = route_json or {}
        route_geometry = route_json.get("geometry")
        stops = route_json.get("sequence") or []
        for stop in stops:
            sequence.append(stop)
        metrics = operational
        patrol_meta = {
            "coverage": metrics["coverage"],
            "completed": metrics["completed"],
            "planned": metrics["planned"],
            "missed": metrics["missed"],
            "active_routes": metrics["active_routes"],
            "duration": f"{max(1, int((latest_route['duration_seconds'] or 0) / 60))} min",
            "distance": f"{(latest_route['distance_meters'] or 0) / 1000:.1f} km",
            "engine": latest_route.get("routing_engine") or "OSRM",
            "assigned_team": latest_route.get("team_name"),
            "sequence": sequence,
            "geometry": route_geometry,
            "id": latest_route["id"],
        }
    else:
        # Build preview sequence from risk weights
        weights = risk_scoring.checkpoint_priority_weights() if load_route else []
        start = next((c for c in weights if c["code"] == "OSAS"), weights[0] if weights else None)
        ranked = [c for c in weights if not start or c["checkpoint_id"] != start["checkpoint_id"]][:4]
        sequence = []
        if start:
            sequence.append({"label": "START", "name": start["name"], "code": start["code"], "risk": None, "tone": None, "lat": start["lat"], "lng": start["lng"]})
        for idx, cp in enumerate(ranked, 1):
            sequence.append({"label": f"{idx:02d}", "name": f"{cp['code']} · {cp['name']}", "code": cp["code"], "risk": cp["score"], "tone": cp["tone"], "lat": cp["lat"], "lng": cp["lng"]})
        if start:
            sequence.append({"label": "END", "name": start["name"], "code": start["code"], "risk": None, "tone": None, "lat": start["lat"], "lng": start["lng"]})
        metrics = operational
        patrol_meta = {
            "coverage": metrics["coverage"],
            "completed": metrics["completed"],
            "planned": metrics["planned"],
            "missed": metrics["missed"],
            "active_routes": metrics["active_routes"],
            "duration": "—",
            "distance": "—",
            "engine": "OSRM",
            "assigned_team": teams[0]["name"] if teams else None,
            "sequence": sequence,
            "geometry": None,
            "id": None,
        }

    zones = []
    zone_incident_counts = {}
    if load_zones:
        zone_incident_counts = {
            row["zone_id"]: row["c"]
            for row in query_all(
                """SELECT zone_id, COUNT(*) AS c FROM incidents
                   WHERE status IN ('reported','verified','responding')
                   GROUP BY zone_id"""
            )
        }
    for z in (query_all(
        "SELECT * FROM campus_zones WHERE active=1 ORDER BY risk_score DESC LIMIT 5"
    ) if load_zones else []):
        score = float(z["risk_score"] or 0)
        tone = "danger" if score >= 80 else "warning" if score >= 65 else "success"
        open_inc = zone_incident_counts.get(z["id"], 0)
        zones.append({
            "name": z["zone_name"],
            "score": int(round(score)),
            "incidents": f"{open_inc} open nearby",
            "tone": tone,
            "code": z["zone_code"],
        })

    counts = {"reported": 0, "verified": 0, "responding": 0, "resolved": 0, "closed": 0}
    if load_incidents:
        for incident in all_incidents:
            counts[incident["status_key"]] = counts.get(incident["status_key"], 0) + 1
        open_incidents = sum(counts.get(status, 0) for status in ("reported", "verified", "responding"))
        high_priority = sum(1 for i in all_incidents if i.get("priority") in ("high", "critical") and i["status_key"] not in ("resolved", "closed"))
        critical_open = sum(1 for i in all_incidents if i.get("priority") == "critical" and i["status_key"] not in ("resolved", "closed"))
        active_incidents = [i for i in all_incidents if i["status_key"] in ("reported", "verified", "responding")]
    elif section in {"reports", "activity", "visitors"}:
        incident_summary = query_one("""SELECT
            SUM(CASE WHEN status='reported' THEN 1 ELSE 0 END) AS reported,
            SUM(CASE WHEN status='verified' THEN 1 ELSE 0 END) AS verified,
            SUM(CASE WHEN status='responding' THEN 1 ELSE 0 END) AS responding,
            SUM(CASE WHEN status='resolved' THEN 1 ELSE 0 END) AS resolved,
            SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed,
            SUM(CASE WHEN priority IN ('high','critical') AND status NOT IN ('resolved','closed') THEN 1 ELSE 0 END) AS high_priority,
            SUM(CASE WHEN priority='critical' AND status NOT IN ('resolved','closed') THEN 1 ELSE 0 END) AS critical_open
            FROM incidents""") or {}
        counts = {key: int(incident_summary.get(key) or 0) for key in counts}
        open_incidents = counts["reported"] + counts["verified"] + counts["responding"]
        high_priority = int(incident_summary.get("high_priority") or 0)
        critical_open = int(incident_summary.get("critical_open") or 0)
        active_incidents = []
    else:
        open_incidents = high_priority = critical_open = 0
        active_incidents = []
    guards_row = query_one("SELECT COUNT(*) AS c FROM users WHERE role IN ('guard','security_head') AND account_status='active'") if section == "teams" else {"c": 0}
    guards = int((guards_row or {}).get("c") or 0)
    readiness = operational["readiness"]
    avg_risk = round(sum(c["risk"] for c in display_checkpoints) / len(display_checkpoints), 1) if display_checkpoints else (round(sum(z["score"] for z in zones) / len(zones), 1) if zones else 0)
    team_count = len(teams) if load_teams else int(operational.get("team_total") or 0)
    on_duty_teams = sum(1 for t in teams if t["status"] == "On duty") if load_teams else int(operational.get("team_on_duty") or 0)
    standby_teams = max(0, team_count - on_duty_teams)
    inspection_total = len(inspections) if load_inspections else int(operational.get("inspection_total") or 0)
    insp_scheduled = sum(1 for i in inspections if i["status"] == "Scheduled") if load_inspections else int(operational.get("inspection_scheduled") or 0)
    insp_completed = sum(1 for i in inspections if i["status"] == "Completed") if load_inspections else int(operational.get("inspection_completed") or 0)
    insp_followups = sum(1 for i in inspections if i["status"] == "Follow-up") if load_inspections else int(operational.get("inspection_overdue") or 0)
    insp_compliance = round((insp_completed / inspection_total) * 100) if inspection_total else 0
    drill_total = len(drills) if load_drills else int(operational.get("drill_total") or 0)
    drills_upcoming = sum(1 for d in drills if d["status"] in ("Planned", "Scheduled")) if load_drills else int(operational.get("drill_upcoming") or 0)
    drills_completed = sum(1 for d in drills if d["status"] == "Completed") if load_drills else int(operational.get("drill_completed") or 0)
    drill_completion = round((drills_completed / drill_total) * 100) if drill_total else 0
    active_plans = sum(1 for p in plans if p["status"] == "Approved")
    activity_count = len(activity) if load_activity else int(operational.get("activity_total") or 0)
    activity_today = sum(1 for a in activity if str(a.get("detail", "")).startswith("Today"))
    modules_touched = len({(a.get("module") or "system").lower() for a in activity})
    actions_count = len(actions) if load_actions else int(operational.get("actions_total") or 0)

    incident_total = sum(counts.values())
    resolved_closed = counts.get("resolved", 0) + counts.get("closed", 0)

    if status_filter in counts:
        incidents = [i for i in all_incidents if i["status_key"] == status_filter]

    u = {
        "overview": url_for("safety.index"),
        "incidents": url_for("safety.incidents"),
        "inspections": url_for("safety.inspections"),
        "osas": url_for("safety.osas"),
        "emergency": url_for("safety.emergency"),
        "patrol": url_for("safety.patrol"),
        "teams": url_for("safety.teams"),
        "reports": url_for("safety.reports"),
        "activity": url_for("safety.activity"),
        "visitors": url_for("safety.visitors"),
    }

    covered_cp = int(operational.get("covered_checkpoints") or 0)
    total_cp = int(operational.get("checkpoint_total") or len(display_checkpoints) or 0)
    last_ts = _last_data_timestamp()
    last_updated_label = _relative_updated_label(last_ts)

    campus_status = _campus_security_status(
        open_incidents=open_incidents,
        high_priority=high_priority,
        critical_open=critical_open,
        missed=int(patrol_meta.get("missed") or 0),
        coverage=int(patrol_meta.get("coverage") or 0),
        readiness=readiness,
        updated_label=last_updated_label,
    )
    priority_actions = _build_priority_actions(all_incidents, patrol_meta, actions, inspections, drills, u)
    live_activity = _build_live_activity(activity, all_incidents)

    coverage_hint = (
        f"{covered_cp} / {total_cp} checkpoints scanned (7d)"
        if total_cp
        else f"{patrol_meta.get('completed', 0)} routes completed this week"
    )

    kpi_catalog = {
        "overview": [
            {"label": "Active Incidents", "value": f"{open_incidents:02d}", "hint": f"{high_priority} require immediate attention", "updated": last_updated_label, "theme": "red", "icon": "fa-triangle-exclamation", "url": u["incidents"]},
            {"label": "Patrol Coverage", "value": f"{patrol_meta['coverage']}%", "hint": coverage_hint, "updated": last_updated_label, "theme": "emerald", "icon": "fa-route", "url": u["patrol"]},
            {"label": "Personnel on Duty", "value": f"{on_duty_teams:02d}", "hint": f"{on_duty_teams} active · {standby_teams} standby", "updated": last_updated_label, "theme": "indigo", "icon": "fa-user-shield", "url": u["teams"]},
            {"label": "Campus Readiness", "value": f"{readiness}%", "hint": f"{len(actions)} actions pending · {insp_completed}/{max(inspection_total,1)} inspections done" if inspections else f"{len(actions)} actions pending", "updated": last_updated_label, "theme": "amber", "icon": "fa-shield-heart", "url": u["emergency"]},
        ],
        "incidents": [
            {"label": "Reported", "value": f"{counts.get('reported', 0):02d}", "hint": "Awaiting verification", "updated": last_updated_label, "theme": "red", "icon": "fa-bullhorn", "url": f"{u['incidents']}?status=reported"},
            {"label": "Verified", "value": f"{counts.get('verified', 0):02d}", "hint": "Ready for dispatch", "updated": last_updated_label, "theme": "amber", "icon": "fa-user-check", "url": f"{u['incidents']}?status=verified"},
            {"label": "Responding", "value": f"{counts.get('responding', 0):02d}", "hint": "Teams on scene", "updated": last_updated_label, "theme": "indigo", "icon": "fa-person-running", "url": f"{u['incidents']}?status=responding"},
            {"label": "High Priority", "value": f"{high_priority:02d}", "hint": f"{resolved_closed} resolved · avg {operational['avg_response']}", "updated": last_updated_label, "theme": "amber", "icon": "fa-bolt", "url": f"{u['incidents']}#incident-queue"},
        ],
        "inspections": [
            {"label": "Scheduled", "value": f"{insp_scheduled:02d}", "hint": "Upcoming inspections", "updated": last_updated_label, "theme": "indigo", "icon": "fa-calendar-check", "url": f"{u['inspections']}#inspection-register"},
            {"label": "Completed", "value": f"{insp_completed:02d}", "hint": f"{insp_compliance}% compliance" if inspections else "No inspections logged", "updated": last_updated_label, "theme": "emerald", "icon": "fa-clipboard-check", "url": f"{u['inspections']}#inspection-register"},
            {"label": "Follow-ups", "value": f"{insp_followups:02d}", "hint": "Overdue / corrective", "updated": last_updated_label, "theme": "amber", "icon": "fa-wrench", "url": f"{u['inspections']}#inspection-register"},
            {"label": "Open Risks", "value": f"{len(actions):02d}", "hint": "Hazards & restocks", "updated": last_updated_label, "theme": "red", "icon": "fa-triangle-exclamation", "url": f"{u['inspections']}#osas-actions"},
        ],
        "osas": [
            {"label": "Open Risks", "value": f"{len(actions):02d}", "hint": "OSAS action queue", "updated": last_updated_label, "theme": "red", "icon": "fa-triangle-exclamation", "url": f"{u['osas']}#osas-actions"},
            {"label": "Follow-ups", "value": f"{insp_followups:02d}", "hint": "Inspection corrective", "updated": last_updated_label, "theme": "amber", "icon": "fa-wrench", "url": f"{u['osas']}#inspection-register"},
            {"label": "Due Soon", "value": f"{insp_scheduled:02d}", "hint": "Scheduled inspections", "updated": last_updated_label, "theme": "indigo", "icon": "fa-calendar-check", "url": f"{u['osas']}#inspection-register"},
            {"label": "Readiness", "value": f"{readiness}%", "hint": "Campus preparedness", "updated": last_updated_label, "theme": "emerald", "icon": "fa-shield-heart", "url": u["emergency"]},
        ],
        "emergency": [
            {"label": "Upcoming Drills", "value": f"{drills_upcoming:02d}", "hint": "Planned / scheduled", "updated": last_updated_label, "theme": "indigo", "icon": "fa-calendar-days", "url": f"{u['emergency']}#drill-register"},
            {"label": "Completed Drills", "value": f"{drills_completed:02d}", "hint": f"{drill_completion}% drill rate" if drills else "No drills logged", "updated": last_updated_label, "theme": "emerald", "icon": "fa-check-double", "url": f"{u['emergency']}#drill-register"},
            {"label": "Active Plans", "value": f"{active_plans:02d}", "hint": f"{len(plans)} total on file", "updated": last_updated_label, "theme": "amber", "icon": "fa-map", "url": f"{u['emergency']}#evac-plans"},
            {"label": "Readiness", "value": f"{readiness}%", "hint": "Inspections + drills + risk", "updated": last_updated_label, "theme": "emerald", "icon": "fa-shield-heart", "url": f"{u['emergency']}#drill-register"},
        ],
        "patrol": [
            {"label": "Coverage", "value": f"{patrol_meta['coverage']}%", "hint": coverage_hint, "updated": last_updated_label, "theme": "emerald", "icon": "fa-route", "url": f"{u['patrol']}#route-performance"},
            {"label": "Completed", "value": f"{patrol_meta['completed']}", "hint": f"Of {patrol_meta['planned']} planned routes", "updated": last_updated_label, "theme": "indigo", "icon": "fa-flag-checkered", "url": f"{u['patrol']}#route-performance"},
            {"label": "Missed Scans", "value": f"{patrol_meta['missed']:02d}", "hint": "Late / invalid / missed", "updated": last_updated_label, "theme": "red", "icon": "fa-circle-xmark", "url": f"{u['patrol']}#route-performance"},
            {"label": "Active Routes", "value": f"{patrol_meta['active_routes']:02d}", "hint": patrol_meta.get("assigned_team") or patrol_meta["engine"], "updated": last_updated_label, "theme": "amber", "icon": "fa-location-arrow", "url": f"{u['patrol']}#route-board"},
        ],
        "teams": [
            {"label": "Teams", "value": f"{len(teams):02d}", "hint": "Active registry", "updated": last_updated_label, "theme": "indigo", "icon": "fa-users", "url": f"{u['teams']}#teams-panel"},
            {"label": "On Duty", "value": f"{on_duty_teams:02d}", "hint": "Active shifts now", "updated": last_updated_label, "theme": "emerald", "icon": "fa-user-check", "url": f"{u['teams']}#teams-panel"},
            {"label": "Guards", "value": f"{guards}", "hint": "Guard + security head", "updated": last_updated_label, "theme": "amber", "icon": "fa-user-shield", "url": f"{u['teams']}#teams-panel"},
            {"label": "Checkpoints", "value": f"{len(display_checkpoints)}", "hint": f"Avg risk {avg_risk} · {covered_cp} scanned (7d)", "updated": last_updated_label, "theme": "red", "icon": "fa-location-crosshairs", "url": f"{u['teams']}#checkpoints-panel"},
        ],
        "reports": [
            {"label": "Incident Records", "value": f"{incident_total:02d}", "hint": f"{open_incidents} still open", "updated": last_updated_label, "theme": "red", "icon": "fa-triangle-exclamation", "url": u["incidents"]},
            {"label": "Patrol Coverage", "value": f"{patrol_meta['coverage']}%", "hint": coverage_hint, "updated": last_updated_label, "theme": "emerald", "icon": "fa-route", "url": u["patrol"]},
            {"label": "Inspection Rate", "value": f"{insp_compliance}%", "hint": f"{insp_completed}/{inspection_total} completed" if inspection_total else "No inspections yet", "updated": last_updated_label, "theme": "amber", "icon": "fa-clipboard-check", "url": u["inspections"]},
            {"label": "Drill Completion", "value": f"{drill_completion}%", "hint": f"{drills_completed}/{drill_total} drills done" if drill_total else "No drills yet", "updated": last_updated_label, "theme": "indigo", "icon": "fa-person-running", "url": u["emergency"]},
        ],
        "activity": [
            {"label": "Recent Actions", "value": f"{len(activity):02d}", "hint": "Audit feed window", "updated": last_updated_label, "theme": "amber", "icon": "fa-list-check", "url": f"{u['activity']}#activity-feed"},
            {"label": "Today", "value": f"{activity_today:02d}", "hint": f"{modules_touched} modules touched", "updated": last_updated_label, "theme": "indigo", "icon": "fa-clock", "url": f"{u['activity']}#activity-feed"},
            {"label": "Open Incidents", "value": f"{open_incidents:02d}", "hint": "Needs response", "updated": last_updated_label, "theme": "red", "icon": "fa-triangle-exclamation", "url": u["incidents"]},
            {"label": "Active Routes", "value": f"{patrol_meta['active_routes']:02d}", "hint": "Patrol in progress", "updated": last_updated_label, "theme": "emerald", "icon": "fa-route", "url": u["patrol"]},
        ],
        "visitors": [
            {"label": "Registered Today", "value": "00", "hint": "Loading visitor register", "updated": last_updated_label, "theme": "indigo", "icon": "fa-id-badge", "url": u["visitors"]},
            {"label": "On Campus", "value": "00", "hint": "Checked in visitors", "updated": last_updated_label, "theme": "emerald", "icon": "fa-door-open", "url": u["visitors"]},
            {"label": "Expired / Revoked", "value": "00", "hint": "Invalid for gate entry", "updated": last_updated_label, "theme": "amber", "icon": "fa-ban", "url": u["visitors"]},
            {"label": "Open Incidents", "value": f"{open_incidents:02d}", "hint": "Campus security context", "updated": last_updated_label, "theme": "red", "icon": "fa-triangle-exclamation", "url": u["incidents"]},
        ],
    }

    trends = [
        {
            "title": f"{open_incidents:02d} open incidents",
            "detail": f"{high_priority} high/critical · avg response {operational['avg_response']} · {resolved_closed} resolved/closed",
        },
        {
            "title": f"Patrol coverage {patrol_meta['coverage']}%",
            "detail": coverage_hint + f" · {patrol_meta['missed']} missed scans this week",
        },
        {
            "title": f"Campus readiness {readiness}%",
            "detail": (
                f"Inspections {insp_compliance}% · drills {drill_completion}% · {actions_count} OSAS actions"
                + (f" · top risk {zones[0]['name']} ({zones[0]['score']})" if zones else "")
            ),
        },
    ]
    report_types = [
        {"key": "incidents", "label": "Incident Summary", "hint": f"{incident_total} records · {open_incidents} open"},
        {"key": "patrol", "label": "Patrol Completion", "hint": f"{patrol_meta['coverage']}% coverage · {covered_cp}/{total_cp or 0} scanned"},
        {"key": "inspections", "label": "Inspection Compliance", "hint": f"{insp_compliance}% · {inspection_total} inspections"},
        {"key": "audit", "label": "Audit Log", "hint": f"{activity_count} recent actions"},
    ]
    visitors = visitor_service.list_visitors() if section == "visitors" else []
    if section == "visitors":
        today = datetime.now().date().isoformat()
        on_campus = sum(1 for v in visitors if (v.get("status") or "") == "checked_in")
        registered_today = sum(1 for v in visitors if str(v.get("visit_date") or "")[:10] == today)
        expired = sum(1 for v in visitors if (v.get("status") or "") in ("expired", "revoked"))
        kpi_catalog["visitors"] = [
            {"label": "Registered Today", "value": f"{registered_today:02d}", "hint": f"{len(visitors)} in recent register", "updated": last_updated_label, "theme": "indigo", "icon": "fa-id-badge", "url": u["visitors"]},
            {"label": "On Campus", "value": f"{on_campus:02d}", "hint": "Checked in, not exited", "updated": last_updated_label, "theme": "emerald", "icon": "fa-door-open", "url": u["visitors"]},
            {"label": "Expired / Revoked", "value": f"{expired:02d}", "hint": "Invalid for gate entry", "updated": last_updated_label, "theme": "amber", "icon": "fa-ban", "url": u["visitors"]},
            {"label": "Open Incidents", "value": f"{open_incidents:02d}", "hint": "Campus security context", "updated": last_updated_label, "theme": "red", "icon": "fa-triangle-exclamation", "url": u["incidents"]},
        ]

    pagination = {}
    teams_options = list(teams)

    if section == "incidents":
        incidents, pagination["incidents"] = paginate_items(incidents, per_page=DEFAULT_PER_PAGE)
    if section in ("inspections", "osas"):
        inspections, pagination["inspections"] = paginate_items(inspections, per_page=DEFAULT_PER_PAGE)
        actions, pagination["actions"] = paginate_items(actions, per_page=DEFAULT_PER_PAGE, page_param="apage")
    if section == "emergency":
        drills, pagination["drills"] = paginate_items(drills, per_page=DEFAULT_PER_PAGE)
        plans, pagination["plans"] = paginate_items(plans, per_page=DEFAULT_PER_PAGE, page_param="ppage")
    if section == "patrol":
        seq, pagination["sequence"] = paginate_items(patrol_meta["sequence"], per_page=DEFAULT_PER_PAGE)
        patrol_meta = dict(patrol_meta)
        patrol_meta["sequence"] = seq
    if section == "teams":
        teams, pagination["teams"] = paginate_items(teams, per_page=DEFAULT_PER_PAGE)
        display_checkpoints, pagination["checkpoints"] = paginate_items(display_checkpoints, per_page=DEFAULT_PER_PAGE, page_param="cpage")
    if section == "activity":
        activity, pagination["activity"] = paginate_items(activity, per_page=DEFAULT_PER_PAGE)
    if section == "reports":
        trends, pagination["trends"] = paginate_items(trends, per_page=DEFAULT_PER_PAGE)
        report_types, pagination["reports"] = paginate_items(report_types, per_page=DEFAULT_PER_PAGE)
    if section == "visitors":
        visitors, pagination["visitors"] = paginate_items(visitors, per_page=DEFAULT_PER_PAGE)

    map_points = [
        {"code": c["code"], "name": c["name"], "lat": c["lat"], "lng": c["lng"], "risk": c["risk"], "tone": c["tone"], "official_risk": c.get("official_risk"), "frequency": c.get("patrol_frequency")}
        for c in all_checkpoints if c["lat"] is not None and c["lng"] is not None
    ]

    analytics = None
    risk_demo = None
    if section in ("overview", "reports"):
        analytics = _build_dashboard_analytics(
            open_incidents=open_incidents,
            counts=counts,
            high_priority=high_priority,
            patrol_meta=patrol_meta,
            operational=operational,
            readiness=readiness,
            insp_compliance=insp_compliance,
            drill_completion=drill_completion,
            zones=zones,
            on_duty_teams=on_duty_teams,
            team_count=len(teams_options),
            actions_count=actions_count,
            avg_risk=avg_risk,
        )
    if section in ("overview", "patrol"):
        top_cp = None
        if not weights:
            weights = risk_scoring.checkpoint_priority_weights()
        if weights:
            top_cp = risk_scoring.explain_checkpoint_score(weights[0]["checkpoint_id"])
        risk_demo = {
            "model": risk_scoring.risk_model_card(),
            "sample": risk_scoring.sample_risk_calculation(),
            "top_checkpoint": top_cp,
            "pipeline": risk_scoring.risk_model_card()["pipeline"],
        }

    return {
        "date_label": datetime.now().strftime("%A, %B %d, %Y"),
        "section": section,
        "kpis": kpi_catalog.get(section, kpi_catalog["overview"]),
        "zones": zones[:3],
        "incidents": incidents,
        "active_incidents": active_incidents[:6],
        "inspections": inspections,
        "actions": actions,
        "drills": drills,
        "plans": plans,
        "teams": teams,
        "teams_options": teams_options,
        "checkpoints": display_checkpoints,
        "all_checkpoints": all_checkpoints,
        "patrol": patrol_meta,
        "route": {
            "duration": patrol_meta["duration"],
            "distance": patrol_meta["distance"],
            "checkpoints": f"{len(sequence):02d} stops",
            "engine": patrol_meta["engine"],
            "assigned_team": patrol_meta.get("assigned_team"),
            "risk_reason": f"{zones[0]['name']} is prioritized (risk {zones[0]['score']})." if zones else "Risk-weighted sequence ready.",
            "routing_note": (
                f"Routed with {patrol_meta['engine']}."
                if (patrol_meta.get("engine") or "").upper() == "OSRM"
                else "Heuristic distance fallback (OSRM unavailable) — not equivalent to road-aware routing."
            ),
        },
        "readiness": {
            "score": readiness,
            "inspections": f"{len(inspections)} tracked",
            "drills": f"{len(drills):02d}",
            "open_risks": len(actions),
        },
        "activity": activity,
        "live_activity": live_activity,
        "priority_actions": priority_actions,
        "campus_status": campus_status,
        "last_updated": last_updated_label,
        "trends": trends,
        "pagination": pagination,
        "hero": {"checkpoints": len(all_checkpoints), "readiness": readiness, "avg_response": operational["avg_response"]},
        "report_types": report_types,
        "visitors": visitors,
        "analytics": analytics,
        "risk_demo": risk_demo,
        "emergency_status": {
            "normal": critical_open == 0 and open_incidents == 0,
            "label": "NORMAL" if critical_open == 0 and open_incidents == 0 else "ACTIVE RESPONSE",
            "detail": "No active emergency protocols" if critical_open == 0 and open_incidents == 0 else f"{open_incidents} open incident{'s' if open_incidents != 1 else ''} under monitoring",
        },
        "map": {
            "center": [
                current_app.config.get("CAMPUS_MAP_LAT", MV_CENTER_LAT),
                current_app.config.get("CAMPUS_MAP_LNG", MV_CENTER_LNG),
            ],
            "zoom": current_app.config.get("CAMPUS_MAP_ZOOM", 18),
            "name": current_app.config.get("CAMPUS_NAME", "BCP MV Campus"),
            "address": current_app.config.get(
                "CAMPUS_ADDRESS",
                "Lot 762, Topaz corner Sapphire Street, Millionaires Village, Barangay San Agustin, Novaliches, Quezon City",
            ),
            "points": map_points,
            "geometry": patrol_meta.get("geometry"),
            "official_routes": OFFICIAL_PATROL_ROUTES,
            "zone_frequency": OFFICIAL_ZONE_FREQUENCY,
        },
    }


def _next_code(prefix, table, column):
    row = query_one(f"SELECT {column} AS code FROM {table} WHERE {column} LIKE %s ORDER BY id DESC LIMIT 1", (f"{prefix}%",))
    if not row:
        return f"{prefix}{datetime.now().year}-001"
    try:
        num = int(str(row["code"]).split("-")[-1]) + 1
    except Exception:
        num = 1
    return f"{prefix}{datetime.now().year}-{num:03d}"


def log_incident(
    title,
    location="",
    priority="medium",
    actor="OSAS Administrator",
    user_id=None,
    incident_type="other",
    checkpoint_id=None,
    team_id=None,
):
    ensure_campus_seed()
    title = (title or "").strip()
    if not title:
        raise ValueError("Incident title is required.")
    priority = (priority or "medium").strip().lower()
    if priority not in PRIORITY_UI:
        raise ValueError("Priority must be low, medium, high or critical.")
    incident_type = (incident_type or "other").strip().lower()
    if incident_type not in INCIDENT_TYPES:
        raise ValueError("Unknown incident type.")

    # Resolve checkpoint if provided or by location match
    cp = None
    if checkpoint_id:
        try:
            cp = query_one(
                "SELECT id, zone_id, checkpoint_code, checkpoint_name FROM checkpoints WHERE id=%s OR checkpoint_code=%s LIMIT 1",
                (checkpoint_id, str(checkpoint_id).upper()),
            )
        except Exception:
            cp = None
    if not cp and location:
        cp = query_one(
            "SELECT id, zone_id, checkpoint_code, checkpoint_name FROM checkpoints WHERE checkpoint_code=%s OR checkpoint_name LIKE %s LIMIT 1",
            (location.upper().strip(), f"%{location}%"),
        )
    resolved_cp_id = cp["id"] if cp else None

    # Resolve zone: from checkpoint first, else from location
    zone = None
    if cp and cp.get("zone_id"):
        zone = query_one("SELECT id, zone_name FROM campus_zones WHERE id=%s", (cp["zone_id"],))
    if not zone and location:
        zone = query_one(
            """SELECT id, zone_name FROM campus_zones
               WHERE zone_name LIKE %s OR zone_code LIKE %s LIMIT 1""",
            (f"%{location}%", f"%{location}%"),
        )
    resolved_zone_id = zone["id"] if zone else None

    # Resolve team assignment
    resolved_team_id = None
    resolved_assigned_to = None
    team_name = None
    if team_id:
        try:
            t = query_one(
                "SELECT id, team_name, team_lead_id FROM patrol_teams WHERE id=%s OR team_name=%s LIMIT 1",
                (team_id, team_id),
            )
            if t:
                resolved_team_id = t["id"]
                resolved_assigned_to = t.get("team_lead_id")
                team_name = t["team_name"]
        except Exception:
            pass

    code = _next_code("INC-", "incidents", "incident_code")
    loc_desc = location or (f"{cp['checkpoint_name']} ({cp['checkpoint_code']})" if cp else "Campus")

    iid, _ = execute(
        """INSERT INTO incidents (incident_code, zone_id, checkpoint_id, team_id, reported_by, incident_type, title, description, priority, status, assigned_to)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'reported',%s)""",
        (code, resolved_zone_id, resolved_cp_id, resolved_team_id, user_id, incident_type, title, loc_desc, priority, resolved_assigned_to),
    )

    audit_vals = {"code": code, "title": title, "location": loc_desc}
    if resolved_team_id:
        audit_vals["team_id"] = resolved_team_id
        audit_vals["team_name"] = team_name
    if resolved_cp_id:
        audit_vals["checkpoint_id"] = resolved_cp_id
    write_audit("Incident logged", "incident", iid, new_values=audit_vals)

    msg_detail = f"{code}: {title} · {loc_desc}"
    if team_name:
        msg_detail += f" (Assigned: {team_name})"
    notify_staff("New incident reported", msg_detail, "danger", "/safety/incidents")

    if not current_app.config.get("TESTING"):
        risk_scoring.refresh_all_risk_scores()
    return code


def update_incident(
    code,
    status=None,
    notes="",
    actor="OSAS Administrator",
    user_id=None,
    actor_role=None,
    team_id=None,
    checkpoint_id=None,
):
    ensure_campus_seed()
    row = query_one(
        """
        SELECT i.*, pt.team_name, c.checkpoint_name, c.checkpoint_code
        FROM incidents i
        LEFT JOIN patrol_teams pt ON pt.id = i.team_id
        LEFT JOIN checkpoints c ON c.id = i.checkpoint_id
        WHERE i.incident_code = %s
        """,
        (code,),
    )
    if not row:
        return False

    current_status = row["status"]
    next_status = (status or current_status or "").lower()

    if next_status != current_status:
        valid, _reason = validate_incident_transition(current_status, next_status, actor_role, notes)
        if not valid:
            return False

    updates = ["status=%s"]
    params = [next_status]

    if next_status == "verified" and not row.get("verified_at"):
        updates.append("verified_at=NOW()")
    if next_status in ("resolved", "closed") and not row.get("resolved_at"):
        updates.append("resolved_at=NOW()")

    assigned_team_name = None
    if team_id is not None:
        raw_team_id = str(team_id).strip()
        if raw_team_id in ("", "0", "none", "null"):
            updates.append("team_id=NULL")
            updates.append("assigned_to=NULL")
        else:
            team_row = query_one(
                "SELECT id, team_name, team_lead_id FROM patrol_teams WHERE id=%s OR team_name=%s LIMIT 1",
                (raw_team_id, raw_team_id),
            )
            if team_row:
                updates.append("team_id=%s")
                params.append(team_row["id"])
                updates.append("assigned_to=%s")
                params.append(team_row.get("team_lead_id"))
                assigned_team_name = team_row["team_name"]

    if checkpoint_id is not None:
        raw_cp_id = str(checkpoint_id).strip()
        if raw_cp_id in ("", "0", "none", "null"):
            updates.append("checkpoint_id=NULL")
        else:
            cp_row = query_one(
                "SELECT id, zone_id, checkpoint_code, checkpoint_name FROM checkpoints WHERE id=%s OR checkpoint_code=%s LIMIT 1",
                (raw_cp_id, raw_cp_id.upper()),
            )
            if cp_row:
                updates.append("checkpoint_id=%s")
                params.append(cp_row["id"])
                if cp_row.get("zone_id"):
                    updates.append("zone_id=%s")
                    params.append(cp_row["zone_id"])

    log_notes = []
    if assigned_team_name and assigned_team_name != row.get("team_name"):
        log_notes.append(f"Assigned patrol team: {assigned_team_name}")
    if notes:
        log_notes.append(notes)

    if log_notes:
        combined_note = " · ".join(log_notes)
        updates.append("description=CONCAT(COALESCE(description, ''), %s)")
        params.append(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {combined_note}")

    params.append(code)
    execute(f"UPDATE incidents SET {', '.join(updates)} WHERE incident_code=%s", tuple(params))

    audit_old = {"status": current_status, "team_id": row.get("team_id")}
    audit_new = {"status": next_status, "notes": notes}
    if assigned_team_name:
        audit_new["assigned_team"] = assigned_team_name
    write_audit("Incident updated", "incident", row["id"], old_values=audit_old, new_values=audit_new)

    tone = "success" if next_status in ("resolved", "closed") else ("warning" if next_status == "responding" else "info")
    notif_msg = f"{row.get('title') or code} status: {next_status}"
    if assigned_team_name:
        notif_msg += f" · Assigned to {assigned_team_name}"
    notify_staff(
        f"Incident {code} Updated",
        notif_msg,
        tone,
        "/safety/incidents",
    )
    if not current_app.config.get("TESTING"):
        risk_scoring.refresh_all_risk_scores()
    return True


def assign_incident_team(code, team_id, actor="OSAS Administrator", user_id=None, actor_role=None):
    """Explicit team assignment helper for incidents."""
    return update_incident(code=code, team_id=team_id, actor=actor, user_id=user_id, actor_role=actor_role)


def create_inspection(zone, insp_type, date_label, actor="OSAS Administrator", user_id=None, team_id=None, checkpoint_id=None):
    _ensure_incident_schema()
    ensure_campus_seed()

    resolved_cp_id = None
    if checkpoint_id:
        try:
            cp_row = query_one("SELECT id, zone_id FROM checkpoints WHERE id=%s OR checkpoint_code=%s LIMIT 1", (checkpoint_id, checkpoint_id))
            if cp_row:
                resolved_cp_id = cp_row["id"]
        except Exception:
            pass

    z = query_one("SELECT id FROM campus_zones WHERE zone_name LIKE %s OR zone_code LIKE %s LIMIT 1", (f"%{zone}%", f"%{zone}%")) if zone else None
    if not z and resolved_cp_id:
        cp_zone = query_one("SELECT zone_id FROM checkpoints WHERE id=%s", (resolved_cp_id,))
        if cp_zone and cp_zone.get("zone_id"):
            z = {"id": cp_zone["zone_id"]}
    if not z:
        return None  # unknown zone: never silently file the inspection under a different zone

    resolved_team_id = None
    if team_id:
        try:
            t = query_one("SELECT id FROM patrol_teams WHERE id=%s OR team_name=%s LIMIT 1", (team_id, team_id))
            if t:
                resolved_team_id = t["id"]
        except Exception:
            pass

    last = query_one("SELECT id FROM safety_inspections ORDER BY id DESC LIMIT 1")
    code = f"INS-{datetime.now().year}-{(last['id'] if last else 0) + 1:03d}"
    iid, _ = execute(
        """INSERT INTO safety_inspections (inspection_code, zone_id, checkpoint_id, team_id, inspector_id, scheduled_date, status, notes)
           VALUES (%s,%s,%s,%s,%s,%s,'scheduled',%s)""",
        (code, z["id"] if z else 1, resolved_cp_id, resolved_team_id, user_id, date_label or datetime.now().date().isoformat(), insp_type),
    )
    from services import osas_modules_service as _mods
    _mods.create_inspection_items(iid, insp_type)
    write_audit("Inspection created", "inspection", iid, new_values={
        "code": code, "zone": zone, "checkpoint_id": resolved_cp_id, "team_id": resolved_team_id
    })
    notify_staff("Inspection scheduled", f"{code}: {insp_type} · {zone}", "info", "/safety/inspections")
    return code


def create_drill(title, drill_type, scheduled_at, actor="OSAS Administrator", user_id=None, team_id=None, checkpoint_id=None, inspection_id=None):
    _ensure_incident_schema()
    dtype = (drill_type or "other").lower()
    if dtype not in ("fire", "earthquake", "lockdown", "medical", "other"):
        dtype = "other"

    resolved_team_id = None
    if team_id:
        try:
            t = query_one("SELECT id FROM patrol_teams WHERE id=%s OR team_name=%s LIMIT 1", (team_id, team_id))
            if t:
                resolved_team_id = t["id"]
        except Exception:
            pass

    resolved_cp_id = None
    if checkpoint_id:
        try:
            cp = query_one("SELECT id FROM checkpoints WHERE id=%s OR checkpoint_code=%s LIMIT 1", (checkpoint_id, checkpoint_id))
            if cp:
                resolved_cp_id = cp["id"]
        except Exception:
            pass

    resolved_insp_id = None
    if inspection_id:
        try:
            si = query_one("SELECT id FROM safety_inspections WHERE id=%s OR inspection_code=%s LIMIT 1", (inspection_id, inspection_id))
            if si:
                resolved_insp_id = si["id"]
        except Exception:
            pass

    did, _ = execute(
        """INSERT INTO drills (drill_type, title, scheduled_at, lead_officer_id, team_id, checkpoint_id, inspection_id, status)
           VALUES (%s,%s,%s,%s,%s,%s,%s,'planned')""",
        (dtype, title, scheduled_at or datetime.now().isoformat(sep=" "), user_id, resolved_team_id, resolved_cp_id, resolved_insp_id),
    )
    write_audit("Drill scheduled", "drill", did, new_values={
        "title": title, "type": dtype, "team_id": resolved_team_id,
        "checkpoint_id": resolved_cp_id, "inspection_id": resolved_insp_id
    })


def create_plan(building, floor, assembly_zone, actor="OSAS Administrator", user_id=None, map_file=None):
    """Legacy entry point: creates an immediately-current plan version (see osas_modules_service)."""
    from services import osas_modules_service as mods

    bld = query_one("SELECT id FROM buildings WHERE building_name LIKE %s OR building_code LIKE %s LIMIT 1", (f"%{building}%", f"%{building}%"))
    zone = query_one("SELECT id FROM campus_zones WHERE zone_name LIKE %s LIMIT 1", (f"%{assembly_zone}%",))
    if not zone:
        raise mods.ModuleError("Assembly zone not found.")
    return mods.create_plan_version(
        {"building_id": bld["id"] if bld else None, "new_building": None if bld else building,
         "floor_label": floor, "assembly_zone_id": zone["id"]},
        map_file, user_id=user_id, approve=True,
    )


def generate_route(patrol_date, team_name, start_code, duration, actor="OSAS Administrator", user_id=None):
    ensure_campus_seed()
    weights = risk_scoring.checkpoint_priority_weights()
    by_code = {w["code"]: w for w in weights}
    start = by_code.get(start_code) or next((w for w in weights if w["code"] == "OSAS"), weights[0])
    others = [w for w in weights if w["checkpoint_id"] != start["checkpoint_id"] and w.get("lat") is not None]
    # Risk-prioritized visit set (top 4) then OSRM trip from start
    prioritized = sorted(others, key=lambda w: w["score"], reverse=True)[:4]
    points = [start] + prioritized
    # Roundtrip back to start for campus loops
    result = osrm_client.trip(
        [{"lat": p["lat"], "lng": p["lng"], "code": p["code"], "name": p["name"], "score": p.get("score"), "checkpoint_id": p["checkpoint_id"]} for p in points],
        roundtrip=True,
    )
    order = result.get("waypoint_order") or list(range(len(points)))
    # Deduplicate trailing return
    seen = []
    for idx in order:
        if idx < len(points):
            seen.append(points[idx])
    sequence = []
    sequence.append({"label": "START", "name": start["name"], "code": start["code"], "risk": None, "tone": None, "lat": start["lat"], "lng": start["lng"], "checkpoint_id": start["checkpoint_id"]})
    step = 1
    for p in seen:
        if p["checkpoint_id"] == start["checkpoint_id"]:
            continue
        sequence.append({
            "label": f"{step:02d}",
            "name": f"{p['code']} · {p['name']}",
            "code": p["code"],
            "risk": p.get("score"),
            "tone": p.get("tone") or ("danger" if (p.get("score") or 0) >= 80 else "warning"),
            "lat": p["lat"],
            "lng": p["lng"],
            "checkpoint_id": p["checkpoint_id"],
        })
        step += 1
    sequence.append({"label": "END", "name": start["name"], "code": start["code"], "risk": None, "tone": None, "lat": start["lat"], "lng": start["lng"], "checkpoint_id": start["checkpoint_id"]})

    # Honor requested max duration when OSRM/heuristic returns longer — keep actual OSRM values as source of truth
    distance_m = result["distance_m"]
    duration_s = result["duration_s"]
    team = query_one("SELECT id, team_name FROM patrol_teams WHERE team_name=%s", (team_name,))
    route_payload = {
        "sequence": sequence,
        "geometry": result.get("geometry"),
        "engine": result.get("engine"),
        "patrol_date": patrol_date,
        "requested_duration": duration,
        "error": result.get("error"),
        "risk_order": [p["code"] for p in prioritized],
    }
    rid, _ = execute(
        """INSERT INTO patrol_routes
           (route_name, generated_by, team_id, routing_engine, route_json, distance_meters, duration_seconds, risk_weight, status)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'assigned')""",
        (
            f"Patrol {patrol_date or datetime.now().date().isoformat()} · {team_name}",
            user_id,
            team["id"] if team else None,
            result.get("engine") or "OSRM",
            json.dumps(route_payload),
            distance_m,
            duration_s,
            prioritized[0]["score"] if prioritized else 0,
        ),
    )
    seq_no = 1
    for stop in sequence:
        if stop["label"] in ("START", "END"):
            continue
        execute(
            """INSERT INTO patrol_route_checkpoints (route_id, checkpoint_id, sequence_no, priority_score)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (route_id, checkpoint_id) DO UPDATE SET
                 sequence_no=EXCLUDED.sequence_no, priority_score=EXCLUDED.priority_score""",
            (rid, stop["checkpoint_id"], seq_no, stop.get("risk") or 0),
        )
        seq_no += 1

    write_audit("Patrol route generated", "patrol_route", rid, new_values={"team": team_name, "engine": result.get("engine"), "distance_m": distance_m})
    return {
        "id": rid,
        "assigned_team": team_name,
        "duration": f"{max(1, duration_s // 60)} min",
        "distance": f"{distance_m / 1000:.1f} km",
        "engine": result.get("engine"),
        "sequence": sequence,
        "geometry": result.get("geometry"),
    }


def save_team(team_name, team_lead, assignment, actor="OSAS Administrator", user_id=None):
    _ensure_incident_schema()
    existing = query_one("SELECT id FROM patrol_teams WHERE team_name=%s", (team_name,))
    team_lead_stripped = (team_lead or "").strip()
    lead = None
    if team_lead_stripped:
        lead = query_one(
            "SELECT id, full_name FROM users WHERE full_name LIKE %s OR username LIKE %s LIMIT 1",
            (f"%{team_lead_stripped}%", f"%{team_lead_stripped}%"),
        )
        lead_id = lead["id"] if lead else None
        display_lead = team_lead_stripped
    else:
        lead_id = None
        display_lead = "Unassigned"

    if existing:
        execute(
            "UPDATE patrol_teams SET team_lead_id=%s, lead_name=%s, active=1 WHERE id=%s",
            (lead_id, display_lead, existing["id"]),
        )
        tid = existing["id"]
    else:
        tid, _ = execute(
            "INSERT INTO patrol_teams (team_name, team_lead_id, lead_name, active) VALUES (%s,%s,%s,1)",
            (team_name, lead_id, display_lead),
        )
    if assignment and "Active" in assignment:
        execute(
            """INSERT INTO patrol_shifts (team_id, shift_date, start_time, end_time, guard_count, status)
               VALUES (%s, CURDATE(), '08:00:00', '16:00:00', 4, 'active')""",
            (tid,),
        )
    write_audit("Team saved", "patrol_team", tid, new_values={"name": team_name, "lead": display_lead})


def save_checkpoint(code, name, method, actor="OSAS Administrator", user_id=None, lat=None, lng=None):
    ensure_campus_seed()
    code = (code or "").upper().strip()
    zone = query_one("SELECT id FROM campus_zones ORDER BY id LIMIT 1")
    existing = query_one("SELECT id FROM checkpoints WHERE checkpoint_code=%s", (code,))
    token = f"tok-{code.lower()}-{secrets.token_hex(4)}"
    if existing:
        execute(
            "UPDATE checkpoints SET checkpoint_name=%s, latitude=COALESCE(%s, latitude), longitude=COALESCE(%s, longitude) WHERE id=%s",
            (name, lat, lng, existing["id"]),
        )
        cid = existing["id"]
    else:
        cid, _ = execute(
            """INSERT INTO checkpoints (checkpoint_code, checkpoint_name, zone_id, scan_token, latitude, longitude)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (code, name, zone["id"], token, lat or current_app.config.get("CAMPUS_MAP_LAT"), lng or current_app.config.get("CAMPUS_MAP_LNG")),
        )
    write_audit("Checkpoint saved", "checkpoint", cid, new_values={"code": code, "method": method})


def get_checkpoint_qr_data(checkpoint_id):
    """Return the active checkpoint's scan token for its QR label."""
    return query_one(
        """SELECT id, checkpoint_code, checkpoint_name, scan_token
           FROM checkpoints WHERE id=%s AND active=1""",
        (checkpoint_id,),
    )


def record_checkpoint_scan(token_or_code, guard_id=None, team_id=None, method="qr", notes=""):
    cp = query_one(
        "SELECT * FROM checkpoints WHERE scan_token=%s OR checkpoint_code=%s LIMIT 1",
        (token_or_code, token_or_code.upper() if token_or_code else ""),
    )
    if not cp:
        return None
    method = (method or "manual").lower()
    if method not in ("qr", "nfc", "gps", "manual"):
        method = "manual"
    duplicate = query_one(
        """SELECT id, scanned_at FROM patrol_logs
           WHERE checkpoint_id=%s AND guard_id=%s
           ORDER BY scanned_at DESC LIMIT 1""",
        (cp["id"], guard_id),
    )
    previous_scan = duplicate.get("scanned_at") if duplicate else None
    if isinstance(previous_scan, str):
        try:
            previous_scan = datetime.fromisoformat(previous_scan)
        except ValueError:
            previous_scan = None
    if previous_scan and (datetime.now() - previous_scan).total_seconds() <= 60:
        result = dict(cp)
        result["scan_result"] = "duplicate"
        result["scan_message"] = "Duplicate scan rejected within 60 seconds."
        return result
    lid, _ = execute(
        """INSERT INTO patrol_logs (checkpoint_id, team_id, guard_id, scan_method, result, notes)
           VALUES (%s,%s,%s,%s,'valid',%s)""",
        (cp["id"], team_id, guard_id, method, notes),
    )
    write_audit("Checkpoint scanned", "patrol_log", lid, new_values={"checkpoint": cp["checkpoint_code"], "method": method})
    result = dict(cp)
    result["scan_result"] = "valid"
    result["scan_message"] = "Checkpoint scan recorded."
    return result


def add_incident_attachment(incident_code, storage, filename, user_id=None):
    row = query_one("SELECT id FROM incidents WHERE incident_code=%s", (incident_code,))
    if not row:
        return None, "Incident not found."
    upload_root = current_app.config.get("UPLOAD_FOLDER", "static/uploads")
    folder = os.path.join(upload_root, "incidents")
    os.makedirs(folder, exist_ok=True)
    safe = secure_filename(filename) or "attachment.bin"
    ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
    allowed = current_app.config.get("ALLOWED_ATTACHMENT_EXTENSIONS") or set()
    if allowed and ext not in allowed:
        return None, f"File type .{ext or '?'} is not allowed."
    max_mb = float(current_app.config.get("MAX_ATTACHMENT_MB") or 5)
    # Prefer Content-Length / stream size when available
    storage.stream.seek(0, os.SEEK_END)
    size = storage.stream.tell()
    storage.stream.seek(0)
    if size > max_mb * 1024 * 1024:
        return None, f"File exceeds the {int(max_mb)} MB limit."
    unique = f"{uuid.uuid4().hex}_{safe}"
    path = os.path.join(folder, unique)
    storage.save(path)
    rel = path.replace("\\", "/")
    if not rel.startswith("static/"):
        rel = "static/" + rel.split("static/")[-1] if "static/" in rel.replace("\\", "/") else rel
    aid, _ = execute(
        """INSERT INTO incident_attachments (incident_id, file_path, file_name, uploaded_by)
           VALUES (%s,%s,%s,%s)""",
        (row["id"], rel, safe, user_id),
    )
    write_audit("Incident attachment uploaded", "incident_attachment", aid, new_values={"incident": incident_code, "file": safe})
    return rel, None


def queue_parent_notification(incident_code, recipient_name, channel, message, phone=None, email=None):
    """Queue and attempt delivery. Returns ``(notification_id, sent)``; see osas_modules_service."""
    from services import osas_modules_service as mods

    nid, status = mods.send_parent_notification(
        {"incident_code": incident_code, "recipient_name": recipient_name, "channel": channel,
         "message": message, "phone": phone, "email": email},
        user_id=None,
    )
    return nid, status == "sent"


def export_report(report_type, date_range, fmt, actor="OSAS Administrator", user_id=None):
    """Legacy entry point: writes the export to EXPORT_FOLDER and returns its relative path."""
    from services import compliance_reports

    content, _mime, filename = compliance_reports.export_report_bytes(
        report_type, fmt, preset=date_range, user_id=user_id
    )
    export_root = current_app.config.get("EXPORT_FOLDER", "static/exports")
    os.makedirs(export_root, exist_ok=True)
    path = os.path.join(export_root, filename)
    with open(path, "wb") as fh:
        fh.write(content)
    return path.replace("\\", "/")


def reset_store():
    """Test helper — no-op for MySQL backend (tests use transactions / dedicated DB)."""
    return None
