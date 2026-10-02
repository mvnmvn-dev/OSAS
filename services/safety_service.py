"""Campus safety service — MySQL-backed dashboard, CRUD, OSRM routes, exports."""
from __future__ import annotations

import json
import os
import secrets
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
    if row.get("verified_at"):
        events.append({"time": _fmt_time(row["verified_at"]), "text": "Incident verified", "at": row["verified_at"]})
    if row.get("assigned_to") and row.get("assignee_name"):
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


def ensure_campus_seed():
    """Insert baseline campus geography if the DB is empty."""
    if current_app.config.get("TESTING"):
        if not query_one("SELECT id FROM campus_zones LIMIT 1"):
            execute(
                "INSERT INTO campus_zones (zone_code, zone_name, zone_type, risk_score) VALUES (%s,%s,%s,%s)",
                ("ZA", "Zone A · Main Gate & Visitor Entrance", "gate", 90),
            )
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

    execute(
        """INSERT INTO incidents (incident_code, zone_id, reported_by, incident_type, title, description, priority, status)
           VALUES (%s,%s,%s,'security',%s,%s,'high','responding')""",
        ("INC-24091", zmap["ZA"], lead_id, "Unauthorized access attempt", "Attempted entry without ID at Main Gate · Topaz / Sapphire"),
    )
    execute(
        """INSERT INTO incidents (incident_code, zone_id, reported_by, incident_type, title, description, priority, status)
           VALUES (%s,%s,%s,'safety_hazard',%s,%s,'medium','verified')""",
        ("INC-24090", zmap["ZC"], lead_id, "Wet floor reported", "Spill on Science Laboratory Wing corridor"),
    )
    execute(
        """INSERT INTO incidents (incident_code, zone_id, reported_by, incident_type, title, description, priority, status)
           VALUES (%s,%s,%s,'medical',%s,%s,'high','resolved')""",
        ("INC-24089", zmap["ZD"], lead_id, "Medical assistance requested", "Student required first aid in Student Center lobby"),
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
    rows = query_all(
        """
        SELECT i.*, z.zone_name, u.full_name AS assignee_name
        FROM incidents i
        LEFT JOIN campus_zones z ON z.id = i.zone_id
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
        if not location:
            desc = (r.get("description") or "").split("\n")[0].strip()
            location = desc[:80] if desc else "Unspecified zone"
        out.append({
            "id": r["id"],
            "code": r["incident_code"],
            "title": r["title"],
            "location": location,
            "time": _fmt_time(r.get("reported_at")),
            "reported_full": _fmt_datetime(r.get("reported_at")),
            "status": label,
            "status_phrase": status_phrase,
            "status_key": r["status"],
            "status_class": css,
            "priority": (r.get("priority") or "medium").lower(),
            "priority_label": p_label,
            "priority_class": p_css,
            "team": r.get("assignee_name"),
            "zone_id": r.get("zone_id"),
            "timeline": _incident_timeline(r),
            "updated_ago": _relative_ago(r.get("resolved_at") or r.get("verified_at") or r.get("reported_at")),
        })
    return out


def _checkpoint_rows():
    weights = {w["checkpoint_id"]: w for w in risk_scoring.checkpoint_priority_weights()}
    rows = query_all(
        """
        SELECT c.*, z.zone_name, z.zone_code,
               (SELECT MAX(pl.scanned_at) FROM patrol_logs pl WHERE pl.checkpoint_id = c.id) AS last_scan
        FROM checkpoints c
        JOIN campus_zones z ON z.id = c.zone_id
        WHERE c.active = 1
        ORDER BY c.checkpoint_code
        """
    )
    out = []
    for r in rows:
        w = weights.get(r["id"], {})
        official_risk, official_score = OFFICIAL_CHECKPOINT_RISK.get(r["checkpoint_code"], ("Medium", 5))
        score = w.get("score") or float(r.get("risk") or 0) or 50
        tone = w.get("tone") or ("danger" if score >= 80 else "warning" if score >= 65 else "success")
        last = r.get("last_scan")
        history = _checkpoint_scan_history(r["id"])
        delayed = any(not h["ok"] for h in history[:1]) if history else False
        on_schedule = bool(last) and not delayed
        out.append({
            "id": r["id"],
            "code": r["checkpoint_code"],
            "name": r["checkpoint_name"],
            "method": "QR",
            "last_scan": last.strftime("%I:%M %p").lstrip("0") if last else "—",
            "last_scan_ago": _relative_ago(last) if last else "No scans yet",
            "risk": score,
            "official_risk": official_risk,
            "official_score": official_score,
            "patrol_frequency": OFFICIAL_ZONE_FREQUENCY.get(r.get("zone_code"), "Every 60 minutes"),
            "tone": tone,
            "lat": float(r["latitude"]) if r["latitude"] is not None else None,
            "lng": float(r["longitude"]) if r["longitude"] is not None else None,
            "scan_token": r["scan_token"],
            "map_class": {
                "ENT": "gate",
                "PG": "center",
                "OSAS": "command",
                "ITS": "science",
            }.get(r["checkpoint_code"]),
            "zone_name": r.get("zone_name"),
            "active": True,
            "patrol_status": "On Schedule" if on_schedule else ("Delayed" if delayed else "Awaiting scan"),
            "patrol_status_ok": on_schedule,
            "history": history,
            "risk_level": official_risk.upper(),
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
    """Calculate dashboard KPIs from current records instead of fixed demo values."""
    routes = query_one("""SELECT COUNT(*) AS total, SUM(status='completed') AS completed,
                                SUM(status IN ('assigned','in_progress')) AS active
                         FROM patrol_routes WHERE created_at >= NOW() - INTERVAL 7 DAY""") or {}
    scans = query_one("""SELECT COUNT(*) AS total, SUM(result='valid') AS valid,
                               SUM(result IN ('missed','late','invalid')) AS missed
                        FROM patrol_logs WHERE scanned_at >= NOW() - INTERVAL 7 DAY""") or {}
    covered = query_one(
        """SELECT COUNT(DISTINCT checkpoint_id) AS c FROM patrol_logs
           WHERE result='valid' AND scanned_at >= NOW() - INTERVAL 7 DAY"""
    ) or {}
    total_cp = query_one("SELECT COUNT(*) AS c FROM checkpoints WHERE active=1") or {}
    planned = int(routes.get("total") or 0)
    completed = int(routes.get("completed") or 0)
    scan_total = int(scans.get("total") or 0)
    covered_cp = int(covered.get("c") or 0)
    checkpoint_total = int(total_cp.get("c") or 0)
    if scan_total:
        coverage = round((int(scans.get("valid") or 0) / scan_total) * 100)
    elif planned:
        coverage = round(completed / planned * 100)
    elif checkpoint_total:
        coverage = round((covered_cp / checkpoint_total) * 100)
    else:
        coverage = 0
    inspections = query_one("SELECT COUNT(*) AS total, SUM(status='completed') AS completed FROM safety_inspections") or {}
    drills = query_one("SELECT COUNT(*) AS total, SUM(status='completed') AS completed FROM drills") or {}
    risks = query_one("SELECT AVG(risk_score) AS average FROM risk_assessments WHERE status IN ('open','mitigating')") or {}
    insp_total = int(inspections.get("total") or 0)
    drill_total = int(drills.get("total") or 0)
    # No records ⇒ 0% (not a fake 100% "perfect" score)
    insp_score = (int(inspections.get("completed") or 0) / insp_total * 100) if insp_total else 0
    drill_score = (int(drills.get("completed") or 0) / drill_total * 100) if drill_total else 0
    risk_score = float(risks.get("average") or 0)
    if insp_total or drill_total or risk_score:
        readiness = round(max(0, min(100, insp_score * .4 + drill_score * .3 + (100 - min(100, risk_score * 5)) * .3)))
    else:
        readiness = 0
    response = query_one("""SELECT AVG(TIMESTAMPDIFF(MINUTE, reported_at, COALESCE(resolved_at, NOW()))) AS minutes
                           FROM incidents WHERE reported_at >= NOW() - INTERVAL 30 DAY""") or {}
    minutes = response.get("minutes")
    return {
        "coverage": coverage,
        "completed": completed,
        "planned": planned,
        "missed": int(scans.get("missed") or 0),
        "active_routes": int(routes.get("active") or 0),
        "readiness": readiness,
        "avg_response": f"{max(0, round(float(minutes))):02d}m" if minutes is not None else "—",
        "covered_checkpoints": covered_cp,
        "checkpoint_total": checkpoint_total,
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
                  SUM(result = 'valid') AS valid,
                  SUM(result IN ('missed', 'late', 'invalid')) AS missed
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
    ensure_campus_seed()
    try:
        risk_scoring.refresh_all_risk_scores()
    except Exception:
        pass

    incidents = _incident_rows()
    all_incidents = incidents
    checkpoints = _checkpoint_rows()
    all_checkpoints = checkpoints
    display_checkpoints = [c for c in checkpoints if c.get("active", True)]

    teams_raw = query_all(
        """
        SELECT t.*, u.full_name AS lead_name,
               (SELECT COUNT(*) FROM patrol_team_members m WHERE m.team_id = t.id) AS members,
               (SELECT COUNT(*) FROM patrol_shifts s WHERE s.team_id = t.id AND s.status = 'active') AS active_shifts
        FROM patrol_teams t
        LEFT JOIN users u ON u.id = t.team_lead_id
        WHERE t.active = 1
        ORDER BY t.team_name
        """
    )
    teams = []
    for t in teams_raw:
        on_duty = t["active_shifts"] > 0
        members = t["members"] or 3
        teams.append({
            "id": t["id"],
            "name": t["team_name"],
            "lead": t.get("lead_name") or "Unassigned",
            "members": members,
            "status": "On duty" if on_duty else "Scheduled",
            "status_class": "success" if on_duty else "warning",
            "shift": "Active shift" if on_duty else "Standby",
        })

    inspections_raw = query_all(
        """
        SELECT si.*, z.zone_name
        FROM safety_inspections si
        JOIN campus_zones z ON z.id = si.zone_id
        ORDER BY si.scheduled_date DESC
        """
    )
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
        })

    actions = []
    for item in query_all(
        "SELECT * FROM first_aid_inventory WHERE status IN ('low_stock','expired') ORDER BY quantity ASC LIMIT 20"
    ):
        actions.append({
            "title": f"Restock {item['item_name']}",
            "detail": f"{item['cabinet_location']} · qty {item['quantity']}",
        })
    for risk in query_all(
        """SELECT ra.*, z.zone_name FROM risk_assessments ra
           JOIN campus_zones z ON z.id = ra.zone_id
           WHERE ra.status IN ('open','mitigating') ORDER BY ra.risk_score DESC LIMIT 20"""
    ):
        actions.append({
            "title": risk["hazard_title"],
            "detail": f"{risk['zone_name']} · score {risk['risk_score']}",
        })

    drills = []
    for d in query_all("SELECT * FROM drills ORDER BY scheduled_at DESC"):
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
        })

    plans = []
    for p in query_all(
        """SELECT ep.*, b.building_name, z.zone_name AS assembly
           FROM evacuation_plans ep
           JOIN buildings b ON b.id = ep.building_id
           LEFT JOIN campus_zones z ON z.id = ep.primary_assembly_zone_id
           ORDER BY ep.id DESC"""
    ):
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
    for a in query_all(
        """SELECT al.*, u.full_name FROM audit_logs al
           LEFT JOIN users u ON u.id = al.user_id
           ORDER BY al.created_at DESC LIMIT 50"""
    ):
        when = a["created_at"]
        day = "Today" if when.date() == datetime.now().date() else when.strftime("%b %d")
        activity.append({
            "title": a["action"],
            "detail": f"{day} · {a.get('full_name') or 'System'}",
            "module": (a.get("entity_type") or "system"),
        })

    latest_route = query_one(
        """SELECT pr.*, t.team_name FROM patrol_routes pr
           LEFT JOIN patrol_teams t ON t.id = pr.team_id
           ORDER BY pr.created_at DESC LIMIT 1"""
    )
    sequence = []
    route_geometry = None
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
        metrics = _operational_metrics()
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
        weights = risk_scoring.checkpoint_priority_weights()
        start = next((c for c in weights if c["code"] == "OSAS"), weights[0] if weights else None)
        ranked = [c for c in weights if not start or c["checkpoint_id"] != start["checkpoint_id"]][:4]
        sequence = []
        if start:
            sequence.append({"label": "START", "name": start["name"], "code": start["code"], "risk": None, "tone": None, "lat": start["lat"], "lng": start["lng"]})
        for idx, cp in enumerate(ranked, 1):
            sequence.append({"label": f"{idx:02d}", "name": f"{cp['code']} · {cp['name']}", "code": cp["code"], "risk": cp["score"], "tone": cp["tone"], "lat": cp["lat"], "lng": cp["lng"]})
        if start:
            sequence.append({"label": "END", "name": start["name"], "code": start["code"], "risk": None, "tone": None, "lat": start["lat"], "lng": start["lng"]})
        metrics = _operational_metrics()
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
    for z in query_all(
        "SELECT * FROM campus_zones WHERE active=1 ORDER BY risk_score DESC LIMIT 5"
    ):
        score = float(z["risk_score"] or 0)
        tone = "danger" if score >= 80 else "warning" if score >= 65 else "success"
        open_inc = query_one(
            "SELECT COUNT(*) AS c FROM incidents WHERE zone_id=%s AND status IN ('reported','verified','responding')",
            (z["id"],),
        )["c"]
        zones.append({
            "name": z["zone_name"],
            "score": int(round(score)),
            "incidents": f"{open_inc} open nearby",
            "tone": tone,
            "code": z["zone_code"],
        })

    open_incidents = sum(1 for i in all_incidents if i["status_key"] in ("reported", "verified", "responding"))
    high_priority = sum(1 for i in all_incidents if i.get("priority") in ("high", "critical") and i["status_key"] not in ("resolved", "closed"))
    critical_open = sum(1 for i in all_incidents if i.get("priority") == "critical" and i["status_key"] not in ("resolved", "closed"))
    active_incidents = [i for i in all_incidents if i["status_key"] in ("reported", "verified", "responding")]
    guards = query_one("SELECT COUNT(*) AS c FROM users WHERE role IN ('guard','security_head') AND account_status='active'")["c"]
    operational = _operational_metrics()
    readiness = operational["readiness"]
    avg_risk = round(sum(c["risk"] for c in display_checkpoints) / max(1, len(display_checkpoints)), 1) if display_checkpoints else 0
    on_duty_teams = sum(1 for t in teams if t["status"] == "On duty")
    standby_teams = max(0, len(teams) - on_duty_teams)
    insp_scheduled = sum(1 for i in inspections if i["status"] == "Scheduled")
    insp_completed = sum(1 for i in inspections if i["status"] == "Completed")
    insp_followups = sum(1 for i in inspections if i["status"] == "Follow-up")
    insp_compliance = round((insp_completed / len(inspections)) * 100) if inspections else 0
    drills_upcoming = sum(1 for d in drills if d["status"] in ("Planned", "Scheduled"))
    drills_completed = sum(1 for d in drills if d["status"] == "Completed")
    drill_completion = round((drills_completed / len(drills)) * 100) if drills else 0
    active_plans = sum(1 for p in plans if p["status"] == "Approved")
    activity_today = sum(1 for a in activity if str(a.get("detail", "")).startswith("Today"))
    modules_touched = len({(a.get("module") or "system").lower() for a in activity})

    counts = {"reported": 0, "verified": 0, "responding": 0, "resolved": 0, "closed": 0}
    for i in all_incidents:
        counts[i["status_key"]] = counts.get(i["status_key"], 0) + 1
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
            {"label": "Campus Readiness", "value": f"{readiness}%", "hint": f"{len(actions)} actions pending · {insp_completed}/{max(len(inspections),1)} inspections done" if inspections else f"{len(actions)} actions pending", "updated": last_updated_label, "theme": "amber", "icon": "fa-shield-heart", "url": u["emergency"]},
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
            {"label": "Incident Records", "value": f"{len(all_incidents):02d}", "hint": f"{open_incidents} still open", "updated": last_updated_label, "theme": "red", "icon": "fa-triangle-exclamation", "url": u["incidents"]},
            {"label": "Patrol Coverage", "value": f"{patrol_meta['coverage']}%", "hint": coverage_hint, "updated": last_updated_label, "theme": "emerald", "icon": "fa-route", "url": u["patrol"]},
            {"label": "Inspection Rate", "value": f"{insp_compliance}%", "hint": f"{insp_completed}/{len(inspections)} completed" if inspections else "No inspections yet", "updated": last_updated_label, "theme": "amber", "icon": "fa-clipboard-check", "url": u["inspections"]},
            {"label": "Drill Completion", "value": f"{drill_completion}%", "hint": f"{drills_completed}/{len(drills)} drills done" if drills else "No drills yet", "updated": last_updated_label, "theme": "indigo", "icon": "fa-person-running", "url": u["emergency"]},
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
                f"Inspections {insp_compliance}% · drills {drill_completion}% · {len(actions)} OSAS actions"
                + (f" · top risk {zones[0]['name']} ({zones[0]['score']})" if zones else "")
            ),
        },
    ]
    report_types = [
        {"key": "incidents", "label": "Incident Summary", "hint": f"{len(all_incidents)} records · {open_incidents} open"},
        {"key": "patrol", "label": "Patrol Completion", "hint": f"{patrol_meta['coverage']}% coverage · {covered_cp}/{total_cp or 0} scanned"},
        {"key": "inspections", "label": "Inspection Compliance", "hint": f"{insp_compliance}% · {len(inspections)} inspections"},
        {"key": "audit", "label": "Audit Log", "hint": f"{len(activity)} recent actions"},
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
            actions_count=len(actions),
            avg_risk=avg_risk,
        )
    if section in ("overview", "patrol"):
        top_cp = None
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


def log_incident(title, location, priority="medium", actor="OSAS Administrator", user_id=None, incident_type="other"):
    ensure_campus_seed()
    zone = query_one(
        """SELECT id, zone_name FROM campus_zones
           WHERE zone_name LIKE %s OR zone_code LIKE %s LIMIT 1""",
        (f"%{location}%", f"%{location}%"),
    )
    code = _next_code("INC-", "incidents", "incident_code")
    iid, _ = execute(
        """INSERT INTO incidents (incident_code, zone_id, reported_by, incident_type, title, description, priority, status)
           VALUES (%s,%s,%s,%s,%s,%s,%s,'reported')""",
        (code, zone["id"] if zone else None, user_id, incident_type, title, location, priority),
    )
    write_audit("Incident logged", "incident", iid, new_values={"code": code, "title": title, "location": location})
    notify_staff("New incident reported", f"{code}: {title} · {location}", "danger", "/safety/incidents")
    if not current_app.config.get("TESTING"):
        risk_scoring.refresh_all_risk_scores()
    return code


def update_incident(code, status, notes="", actor="OSAS Administrator", user_id=None):
    status = (status or "").lower()
    allowed = {"reported", "verified", "responding", "resolved", "closed"}
    if status not in allowed:
        return False
    row = query_one("SELECT * FROM incidents WHERE incident_code=%s", (code,))
    if not row:
        return False
    transitions = {
        "reported": {"reported", "verified"},
        "verified": {"verified", "responding", "resolved"},
        "responding": {"responding", "resolved"},
        "resolved": {"resolved", "closed"},
        "closed": {"closed"},
    }
    if status not in transitions.get(row["status"], set()):
        return False
    extras = ""
    params = [status]
    if status == "verified":
        extras = ", verified_at=NOW()"
    if status in ("resolved", "closed"):
        extras += ", resolved_at=NOW()"
    if notes:
        extras += ", description=CONCAT(COALESCE(description, ''), %s)"
        params.append(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {notes}")
    params.append(code)
    execute(f"UPDATE incidents SET status=%s{extras} WHERE incident_code=%s", tuple(params))
    write_audit("Incident updated", "incident", row["id"], old_values={"status": row["status"]}, new_values={"status": status, "notes": notes})
    tone = "success" if status in ("resolved", "closed") else ("warning" if status == "responding" else "info")
    notify_staff(
        f"Incident {code} → {status.replace('_', ' ').title()}",
        f"{row.get('title') or code} moved from {row['status']} to {status}",
        tone,
        "/safety/incidents",
    )
    return True


def create_inspection(zone, insp_type, date_label, actor="OSAS Administrator", user_id=None):
    ensure_campus_seed()
    z = query_one("SELECT id FROM campus_zones WHERE zone_name LIKE %s OR zone_code LIKE %s LIMIT 1", (f"%{zone}%", f"%{zone}%"))
    if not z:
        z = query_one("SELECT id FROM campus_zones ORDER BY id LIMIT 1")
    last = query_one("SELECT id FROM safety_inspections ORDER BY id DESC LIMIT 1")
    code = f"INS-{datetime.now().year}-{(last['id'] if last else 0) + 1:03d}"
    iid, _ = execute(
        """INSERT INTO safety_inspections (inspection_code, zone_id, inspector_id, scheduled_date, status, notes)
           VALUES (%s,%s,%s,%s,'scheduled',%s)""",
        (code, z["id"], user_id, date_label or datetime.now().date().isoformat(), insp_type),
    )
    write_audit("Inspection created", "inspection", iid, new_values={"code": code, "zone": zone})
    notify_staff("Inspection scheduled", f"{code}: {insp_type} · {zone}", "info", "/safety/inspections")
    return code


def create_drill(title, drill_type, scheduled_at, actor="OSAS Administrator", user_id=None):
    dtype = (drill_type or "other").lower()
    if dtype not in ("fire", "earthquake", "lockdown", "medical", "other"):
        dtype = "other"
    did, _ = execute(
        """INSERT INTO drills (drill_type, title, scheduled_at, lead_officer_id, status)
           VALUES (%s,%s,%s,%s,'planned')""",
        (dtype, title, scheduled_at or datetime.now().isoformat(sep=" "), user_id),
    )
    write_audit("Drill scheduled", "drill", did, new_values={"title": title, "type": dtype})


def create_plan(building, floor, assembly_zone, actor="OSAS Administrator", user_id=None):
    b = query_one("SELECT id FROM buildings WHERE building_name LIKE %s OR building_code LIKE %s LIMIT 1", (f"%{building}%", f"%{building}%"))
    if not b:
        bid, _ = execute(
            "INSERT INTO buildings (building_code, building_name, floors) VALUES (%s,%s,1)",
            (building[:8].upper().replace(" ", ""), building),
        )
        b = {"id": bid}
    az = query_one("SELECT id FROM campus_zones WHERE zone_name LIKE %s LIMIT 1", (f"%{assembly_zone}%",))
    pid, _ = execute(
        """INSERT INTO evacuation_plans (building_id, floor_label, plan_name, primary_assembly_zone_id, version_no, created_by)
           VALUES (%s,%s,%s,%s,1,%s)""",
        (b["id"], floor, f"{building} Evacuation", az["id"] if az else None, user_id),
    )
    write_audit("Evacuation plan added", "evacuation_plan", pid, new_values={"building": building})


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
               ON DUPLICATE KEY UPDATE sequence_no=VALUES(sequence_no), priority_score=VALUES(priority_score)""",
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
    existing = query_one("SELECT id FROM patrol_teams WHERE team_name=%s", (team_name,))
    lead = query_one("SELECT id FROM users WHERE full_name LIKE %s OR username LIKE %s LIMIT 1", (f"%{team_lead}%", f"%{team_lead}%"))
    lead_id = lead["id"] if lead else user_id
    if existing:
        execute("UPDATE patrol_teams SET team_lead_id=%s, active=1 WHERE id=%s", (lead_id, existing["id"]))
        tid = existing["id"]
    else:
        tid, _ = execute("INSERT INTO patrol_teams (team_name, team_lead_id, active) VALUES (%s,%s,1)", (team_name, lead_id))
    if assignment and "Active" in assignment:
        execute(
            """INSERT INTO patrol_shifts (team_id, shift_date, start_time, end_time, guard_count, status)
               VALUES (%s, CURDATE(), '08:00:00', '16:00:00', 4, 'active')""",
            (tid,),
        )
    write_audit("Team saved", "patrol_team", tid, new_values={"name": team_name, "lead": team_lead})


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
    lid, _ = execute(
        """INSERT INTO patrol_logs (checkpoint_id, team_id, guard_id, scan_method, result, notes)
           VALUES (%s,%s,%s,%s,'valid',%s)""",
        (cp["id"], team_id, guard_id, method, notes),
    )
    write_audit("Checkpoint scanned", "patrol_log", lid, new_values={"checkpoint": cp["checkpoint_code"], "method": method})
    return cp


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
    row = query_one("SELECT id FROM incidents WHERE incident_code=%s", (incident_code,))
    channel = (channel or "email").lower()
    if channel not in ("sms", "email", "push"):
        channel = "email"
    nid, _ = execute(
        """INSERT INTO parent_notifications
           (incident_id, recipient_name, recipient_phone, recipient_email, channel, message, status)
           VALUES (%s,%s,%s,%s,%s,%s,'queued')""",
        (row["id"] if row else None, recipient_name, phone, email, channel, message),
    )
    # Attempt delivery — mark sent when provider configured, else remain queued
    sent = False
    if channel == "email" and current_app.config.get("SMTP_HOST"):
        sent = _send_email(email, f"OSAS alert · {incident_code}", message)
    elif channel == "sms" and current_app.config.get("SMS_PROVIDER_URL"):
        sent = _send_sms(phone, message)
    if sent:
        execute("UPDATE parent_notifications SET status='sent', sent_at=NOW() WHERE id=%s", (nid,))
    write_audit("Parent notification queued", "parent_notification", nid, new_values={"channel": channel, "sent": sent})
    return nid, sent


def _send_email(to_addr, subject, body):
    if not to_addr:
        return False
    try:
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = current_app.config.get("SMTP_FROM")
        msg["To"] = to_addr
        msg.set_content(body)
        with smtplib.SMTP(current_app.config["SMTP_HOST"], current_app.config["SMTP_PORT"], timeout=10) as smtp:
            smtp.starttls()
            if current_app.config.get("SMTP_USER"):
                smtp.login(current_app.config["SMTP_USER"], current_app.config["SMTP_PASSWORD"])
            smtp.send_message(msg)
        return True
    except Exception:
        return False


def _send_sms(phone, message):
    if not phone:
        return False
    try:
        import requests
        resp = requests.post(
            current_app.config["SMS_PROVIDER_URL"],
            json={"to": phone, "message": message},
            headers={"Authorization": f"Bearer {current_app.config.get('SMS_API_KEY', '')}"},
            timeout=8,
        )
        return resp.status_code < 300
    except Exception:
        return False


def export_report(report_type, date_range, fmt, actor="OSAS Administrator", user_id=None):
    ensure_campus_seed()
    export_root = current_app.config.get("EXPORT_FOLDER", "static/exports")
    os.makedirs(export_root, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_type = (report_type or "report").lower().replace(" ", "_")
    fmt = (fmt or "CSV").upper()

    rows = []
    headers = []
    if "incident" in safe_type:
        headers = ["Code", "Title", "Status", "Priority", "Reported"]
        for r in query_all("SELECT incident_code, title, status, priority, reported_at FROM incidents ORDER BY reported_at DESC"):
            rows.append([r["incident_code"], r["title"], r["status"], r["priority"], str(r["reported_at"])])
    elif "patrol" in safe_type:
        headers = ["Route", "Engine", "Distance_m", "Duration_s", "Status"]
        for r in query_all("SELECT route_name, routing_engine, distance_meters, duration_seconds, status FROM patrol_routes ORDER BY created_at DESC"):
            rows.append([r["route_name"], r["routing_engine"], r["distance_meters"], r["duration_seconds"], r["status"]])
    elif "inspection" in safe_type:
        headers = ["Code", "Zone", "Date", "Status"]
        for r in query_all(
            """SELECT si.inspection_code, z.zone_name, si.scheduled_date, si.status
               FROM safety_inspections si JOIN campus_zones z ON z.id = si.zone_id ORDER BY si.scheduled_date DESC"""
        ):
            rows.append([r["inspection_code"], r["zone_name"], str(r["scheduled_date"]), r["status"]])
    else:
        headers = ["When", "Action", "Entity", "User"]
        for r in query_all(
            """SELECT al.created_at, al.action, al.entity_type, u.full_name
               FROM audit_logs al LEFT JOIN users u ON u.id = al.user_id
               ORDER BY al.created_at DESC LIMIT 200"""
        ):
            rows.append([str(r["created_at"]), r["action"], r.get("entity_type") or "", r.get("full_name") or ""])

    if fmt == "EXCEL":
        path = os.path.join(export_root, f"{safe_type}_{stamp}.xlsx")
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Report"
        ws.append(headers)
        for row in rows:
            ws.append(row)
        wb.save(path)
    elif fmt == "PDF":
        path = os.path.join(export_root, f"{safe_type}_{stamp}.pdf")
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        c = canvas.Canvas(path, pagesize=letter)
        width, height = letter
        y = height - 50
        c.setFont("Helvetica-Bold", 14)
        c.drawString(40, y, f"OSAS {report_type} ({date_range})")
        y -= 30
        c.setFont("Helvetica", 9)
        c.drawString(40, y, " | ".join(headers))
        y -= 16
        for row in rows[:60]:
            c.drawString(40, y, " | ".join(str(x)[:24] for x in row))
            y -= 12
            if y < 40:
                c.showPage()
                y = height - 40
                c.setFont("Helvetica", 9)
        c.save()
    else:
        path = os.path.join(export_root, f"{safe_type}_{stamp}.csv")
        import csv
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(headers)
            writer.writerows(rows)

    rel = path.replace("\\", "/")
    write_audit("Report exported", "report", None, new_values={"type": report_type, "format": fmt, "path": rel})
    return rel


def reset_store():
    """Test helper — no-op for MySQL backend (tests use transactions / dedicated DB)."""
    return None
