"""Campus safety service — MySQL-backed dashboard, CRUD, OSRM routes, exports."""
from __future__ import annotations

import json
import os
import secrets
import uuid
from datetime import datetime
from io import BytesIO

from flask import current_app
from werkzeug.utils import secure_filename

from models.database import execute, query_all, query_one
from services.audit_service import write_audit
from services.pagination import DEFAULT_PER_PAGE, paginate_items
from services import osrm_client
from services import risk_scoring

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


def _title_status(status):
    label, css = STATUS_UI.get((status or "").lower(), ((status or "Unknown").replace("_", " ").title(), "warning"))
    return label, css


def _now_label():
    return datetime.now().strftime("%I:%M %p").lstrip("0")


def ensure_campus_seed():
    """Insert baseline campus geography if the DB is empty."""
    if query_one("SELECT id FROM buildings LIMIT 1"):
        return False

    buildings = [
        ("MAIN", "Main Building", 4, 14.73290, 121.05790),
        ("SCI", "Science Building", 3, 14.73310, 121.05840),
        ("SC", "Student Center", 2, 14.73250, 121.05870),
        ("SEC", "Security Office", 1, 14.73270, 121.05750),
    ]
    for code, name, floors, lat, lng in buildings:
        execute(
            "INSERT INTO buildings (building_code, building_name, floors, latitude, longitude) VALUES (%s,%s,%s,%s,%s)",
            (code, name, floors, lat, lng),
        )

    bmap = {r["building_code"]: r["id"] for r in query_all("SELECT id, building_code FROM buildings")}
    zones = [
        ("G2", "Gate 2 / Main Entrance", bmap["MAIN"], "gate", 87),
        ("QN", "Quadrangle North", None, "open_space", 72),
        ("S1", "Science Building Zone", bmap["SCI"], "building", 72),
        ("SCL", "Student Center Lobby", bmap["SC"], "building", 59),
        ("SECZ", "Security Office Zone", bmap["SEC"], "building", 35),
        ("AZA", "Assembly Zone A", bmap["MAIN"], "assembly_area", 20),
        ("AZB", "Assembly Zone B", bmap["SCI"], "assembly_area", 20),
    ]
    for code, name, bid, ztype, risk in zones:
        execute(
            """INSERT INTO campus_zones (zone_code, zone_name, building_id, zone_type, risk_score)
               VALUES (%s,%s,%s,%s,%s)""",
            (code, name, bid, ztype, risk),
        )

    zmap = {r["zone_code"]: r["id"] for r in query_all("SELECT id, zone_code FROM campus_zones")}
    checkpoints = [
        ("SEC", "Security Office", "SECZ", 14.73270, 121.05750),
        ("G2", "Main Entrance", "G2", 14.73295, 121.05770),
        ("QN", "Quadrangle North", "QN", 14.73320, 121.05810),
        ("S1", "Science Building", "S1", 14.73310, 121.05840),
        ("SC", "Student Center", "SCL", 14.73250, 121.05870),
    ]
    for code, name, zcode, lat, lng in checkpoints:
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
        ("INC-24091", zmap["G2"], lead_id, "Unauthorized access attempt", "Attempted entry without ID at Gate 2"),
    )
    execute(
        """INSERT INTO incidents (incident_code, zone_id, reported_by, incident_type, title, description, priority, status)
           VALUES (%s,%s,%s,'safety_hazard',%s,%s,'medium','verified')""",
        ("INC-24090", zmap["S1"], lead_id, "Wet floor reported", "Spill on Science Building 2F corridor"),
    )
    execute(
        """INSERT INTO incidents (incident_code, zone_id, reported_by, incident_type, title, description, priority, status)
           VALUES (%s,%s,%s,'medical',%s,%s,'high','resolved')""",
        ("INC-24089", zmap["SCL"], lead_id, "Medical assistance requested", "Student required first aid in lobby"),
    )

    execute(
        """INSERT INTO safety_inspections (inspection_code, zone_id, inspector_id, scheduled_date, status, notes)
           VALUES (%s,%s,%s,CURDATE() + INTERVAL 2 DAY,'scheduled',%s)""",
        ("INS-2026-031", zmap["S1"], lead_id, "Fire safety equipment"),
    )
    execute(
        """INSERT INTO safety_inspections (inspection_code, zone_id, inspector_id, scheduled_date, status, notes)
           VALUES (%s,%s,%s,CURDATE() - INTERVAL 1 DAY,'overdue',%s)""",
        ("INS-2026-030", zmap["G2"], lead_id, "Access control and lighting — corrective action open"),
    )

    execute(
        """INSERT INTO drills (drill_type, title, scheduled_at, lead_officer_id, status)
           VALUES ('fire',%s, DATE_ADD(NOW(), INTERVAL 9 DAY), %s, 'planned')""",
        ("Fire evacuation drill", lead_id),
    )
    execute(
        """INSERT INTO drills (drill_type, title, scheduled_at, lead_officer_id, status)
           VALUES ('earthquake',%s, DATE_ADD(NOW(), INTERVAL 16 DAY), %s, 'planned')""",
        ("Earthquake response drill", lead_id),
    )

    execute(
        """INSERT INTO evacuation_plans (building_id, floor_label, plan_name, primary_assembly_zone_id, version_no, created_by)
           VALUES (%s,'Floors 1–4','Main Building Evacuation',%s,3,%s)""",
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
        (zmap["S1"], lead_id),
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
        ORDER BY i.reported_at DESC
        """
    )
    out = []
    for r in rows:
        label, css = _title_status(r["status"])
        out.append({
            "id": r["id"],
            "code": r["incident_code"],
            "title": r["title"],
            "location": r.get("zone_name") or "Unspecified zone",
            "time": r["reported_at"].strftime("%I:%M %p").lstrip("0") if r.get("reported_at") else "",
            "status": label,
            "status_key": r["status"],
            "status_class": css,
            "priority": r["priority"],
            "team": r.get("assignee_name"),
            "zone_id": r.get("zone_id"),
        })
    return out


def _checkpoint_rows():
    weights = {w["checkpoint_id"]: w for w in risk_scoring.checkpoint_priority_weights()}
    rows = query_all(
        """
        SELECT c.*, z.zone_name,
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
        score = w.get("score") or float(r.get("risk") or 0) or 50
        tone = w.get("tone") or ("danger" if score >= 80 else "warning" if score >= 65 else "success")
        last = r.get("last_scan")
        out.append({
            "id": r["id"],
            "code": r["checkpoint_code"],
            "name": r["checkpoint_name"],
            "method": "QR",
            "last_scan": last.strftime("%I:%M %p").lstrip("0") if last else "—",
            "risk": score,
            "tone": tone,
            "lat": float(r["latitude"]) if r["latitude"] is not None else None,
            "lng": float(r["longitude"]) if r["longitude"] is not None else None,
            "scan_token": r["scan_token"],
            "map_class": {"G2": "gate", "S1": "science", "QN": "quad", "SC": "center"}.get(r["checkpoint_code"]),
            "zone_name": r.get("zone_name"),
        })
    return out


def get_safety_dashboard(section="overview"):
    ensure_campus_seed()
    try:
        risk_scoring.refresh_all_risk_scores()
    except Exception:
        pass

    incidents = _incident_rows()
    checkpoints = _checkpoint_rows()
    all_checkpoints = checkpoints
    display_checkpoints = [c for c in checkpoints if c["code"] != "SEC"]

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
        patrol_meta = {
            "coverage": 86,
            "completed": query_one("SELECT COUNT(*) AS c FROM patrol_routes WHERE status='completed'")["c"],
            "planned": query_one("SELECT COUNT(*) AS c FROM patrol_routes")["c"] or 1,
            "missed": query_one("SELECT COUNT(*) AS c FROM patrol_logs WHERE result='missed'")["c"],
            "active_routes": query_one("SELECT COUNT(*) AS c FROM patrol_routes WHERE status IN ('assigned','in_progress','draft')")["c"],
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
        start = next((c for c in weights if c["code"] == "SEC"), weights[0] if weights else None)
        ranked = [c for c in weights if not start or c["checkpoint_id"] != start["checkpoint_id"]][:4]
        sequence = []
        if start:
            sequence.append({"label": "START", "name": start["name"], "code": start["code"], "risk": None, "tone": None, "lat": start["lat"], "lng": start["lng"]})
        for idx, cp in enumerate(ranked, 1):
            sequence.append({"label": f"{idx:02d}", "name": f"{cp['code']} · {cp['name']}", "code": cp["code"], "risk": cp["score"], "tone": cp["tone"], "lat": cp["lat"], "lng": cp["lng"]})
        if start:
            sequence.append({"label": "END", "name": start["name"], "code": start["code"], "risk": None, "tone": None, "lat": start["lat"], "lng": start["lng"]})
        patrol_meta = {
            "coverage": 86,
            "completed": 0,
            "planned": 0,
            "missed": 0,
            "active_routes": 0,
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

    open_incidents = sum(1 for i in incidents if i["status_key"] in ("reported", "verified", "responding"))
    guards = query_one("SELECT COUNT(*) AS c FROM users WHERE role IN ('guard','security_head') AND account_status='active'")["c"]
    readiness = 91
    avg_risk = round(sum(c["risk"] for c in display_checkpoints) / max(1, len(display_checkpoints)), 1) if display_checkpoints else 0

    counts = {"reported": 0, "verified": 0, "responding": 0, "resolved": 0}
    for i in incidents:
        counts[i["status_key"]] = counts.get(i["status_key"], 0) + 1

    kpi_catalog = {
        "overview": [
            {"label": "Open Incidents", "value": f"{open_incidents:02d}", "hint": f"{counts.get('responding', 0)} responding", "theme": "red", "icon": "fa-triangle-exclamation"},
            {"label": "Patrol Coverage", "value": f"{patrol_meta['coverage']}%", "hint": f"{patrol_meta['completed']} completed routes", "theme": "emerald", "icon": "fa-route"},
            {"label": "Campus Risk Index", "value": str(avg_risk), "hint": "Decay-weighted", "theme": "amber", "icon": "fa-gauge-high"},
            {"label": "Guards on Duty", "value": f"{guards}", "hint": f"{len(teams)} teams", "theme": "indigo", "icon": "fa-user-shield"},
        ],
        "incidents": [
            {"label": "Reported", "value": f"{counts.get('reported', 0):02d}", "hint": "Awaiting verification", "theme": "red", "icon": "fa-bullhorn"},
            {"label": "Verified", "value": f"{counts.get('verified', 0):02d}", "hint": "Ready for dispatch", "theme": "amber", "icon": "fa-user-check"},
            {"label": "Responding", "value": f"{counts.get('responding', 0):02d}", "hint": "Teams on scene", "theme": "indigo", "icon": "fa-person-running"},
            {"label": "Resolved", "value": f"{counts.get('resolved', 0):02d}", "hint": "Closed or resolved", "theme": "emerald", "icon": "fa-check"},
        ],
        "inspections": [
            {"label": "Scheduled", "value": f"{sum(1 for i in inspections if i['status']=='Scheduled'):02d}", "hint": "Upcoming", "theme": "indigo", "icon": "fa-calendar-check"},
            {"label": "Register", "value": f"{len(inspections):02d}", "hint": "Total records", "theme": "emerald", "icon": "fa-clipboard-check"},
            {"label": "Follow-ups", "value": f"{sum(1 for i in inspections if i['status']=='Follow-up'):02d}", "hint": "Corrective actions", "theme": "amber", "icon": "fa-wrench"},
            {"label": "Open risks", "value": f"{len(actions):02d}", "hint": "OSAS queue", "theme": "red", "icon": "fa-triangle-exclamation"},
        ],
        "emergency": [
            {"label": "Planned drills", "value": f"{sum(1 for d in drills if d['status'] in ('Planned','Scheduled')):02d}", "hint": "Calendar", "theme": "indigo", "icon": "fa-calendar-days"},
            {"label": "Drill register", "value": f"{len(drills):02d}", "hint": "All events", "theme": "emerald", "icon": "fa-check-double"},
            {"label": "Evacuation plans", "value": f"{len(plans):02d}", "hint": "Buildings covered", "theme": "amber", "icon": "fa-map"},
            {"label": "Readiness", "value": f"{readiness}%", "hint": "Preparedness index", "theme": "emerald", "icon": "fa-users"},
        ],
        "patrol": [
            {"label": "Coverage", "value": f"{patrol_meta['coverage']}%", "hint": "This week", "theme": "emerald", "icon": "fa-route"},
            {"label": "Completed", "value": f"{patrol_meta['completed']}", "hint": f"Of {patrol_meta['planned']} routes", "theme": "indigo", "icon": "fa-flag-checkered"},
            {"label": "Missed scans", "value": f"{patrol_meta['missed']:02d}", "hint": "Patrol logs", "theme": "red", "icon": "fa-circle-xmark"},
            {"label": "Active routes", "value": f"{patrol_meta['active_routes']:02d}", "hint": patrol_meta["engine"], "theme": "amber", "icon": "fa-location-arrow"},
        ],
        "teams": [
            {"label": "Teams", "value": f"{len(teams):02d}", "hint": "Active registry", "theme": "indigo", "icon": "fa-users"},
            {"label": "Checkpoints", "value": f"{len(checkpoints)}", "hint": "QR / NFC / GPS", "theme": "amber", "icon": "fa-location-crosshairs"},
            {"label": "Guards", "value": f"{guards}", "hint": "Security roles", "theme": "emerald", "icon": "fa-user-shield"},
            {"label": "Avg risk", "value": str(avg_risk), "hint": "Checkpoint-weighted", "theme": "red", "icon": "fa-gauge-high"},
        ],
        "reports": [
            {"label": "Incidents open", "value": f"{open_incidents:02d}", "hint": "Live queue", "theme": "red", "icon": "fa-triangle-exclamation"},
            {"label": "Patrol coverage", "value": f"{patrol_meta['coverage']}%", "hint": "Target 90%", "theme": "emerald", "icon": "fa-route"},
            {"label": "Avg risk index", "value": str(avg_risk), "hint": "Decay model", "theme": "amber", "icon": "fa-gauge-high"},
            {"label": "Audit entries", "value": f"{len(activity):02d}", "hint": "Recent actions", "theme": "indigo", "icon": "fa-list-check"},
        ],
        "activity": [
            {"label": "Audit entries", "value": f"{len(activity):02d}", "hint": "Rolling feed", "theme": "amber", "icon": "fa-list-check"},
            {"label": "Open incidents", "value": f"{open_incidents:02d}", "hint": "Cross-module", "theme": "red", "icon": "fa-triangle-exclamation"},
            {"label": "Active routes", "value": f"{patrol_meta['active_routes']:02d}", "hint": "Patrol", "theme": "emerald", "icon": "fa-route"},
            {"label": "Inspections", "value": f"{len(inspections):02d}", "hint": "Register", "theme": "indigo", "icon": "fa-clipboard-check"},
        ],
    }
    kpi_catalog["osas"] = kpi_catalog["inspections"]

    trends = [
        {"title": f"Open incidents at {open_incidents:02d}", "detail": "Synced from Incident Response"},
        {"title": f"{zones[0]['name']} highest risk" if zones else "No zones", "detail": f"Score {zones[0]['score']}" if zones else ""},
        {"title": f"{len(drills)} drills on calendar", "detail": f"{len(plans)} evacuation plans on file"},
    ]
    report_types = [
        {"key": "incidents", "label": "Incident Summary", "hint": f"{len(incidents)} records"},
        {"key": "patrol", "label": "Patrol Completion", "hint": f"{patrol_meta['coverage']}% coverage"},
        {"key": "inspections", "label": "Inspection Compliance", "hint": f"{len(inspections)} inspections"},
        {"key": "audit", "label": "Audit Log", "hint": f"{len(activity)} actions"},
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

    map_points = [
        {"code": c["code"], "name": c["name"], "lat": c["lat"], "lng": c["lng"], "risk": c["risk"], "tone": c["tone"]}
        for c in all_checkpoints if c["lat"] is not None and c["lng"] is not None
    ]

    return {
        "date_label": datetime.now().strftime("%A, %B %d, %Y"),
        "section": section,
        "kpis": kpi_catalog.get(section, kpi_catalog["overview"]),
        "zones": zones[:3],
        "incidents": incidents,
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
        },
        "readiness": {
            "score": readiness,
            "inspections": f"{len(inspections)} tracked",
            "drills": f"{len(drills):02d}",
            "open_risks": len(actions),
        },
        "activity": activity,
        "trends": trends,
        "pagination": pagination,
        "hero": {"checkpoints": len(all_checkpoints), "readiness": readiness, "avg_response": "06m"},
        "report_types": report_types,
        "map": {
            "center": [current_app.config.get("CAMPUS_MAP_LAT", 14.7328), current_app.config.get("CAMPUS_MAP_LNG", 121.0582)],
            "zoom": current_app.config.get("CAMPUS_MAP_ZOOM", 17),
            "points": map_points,
            "geometry": patrol_meta.get("geometry"),
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
    # simpler yearless sequence if preferred
    last = query_one("SELECT id FROM incidents ORDER BY id DESC LIMIT 1")
    code = f"INC-{24000 + (last['id'] if last else 0) + 1}"
    iid, _ = execute(
        """INSERT INTO incidents (incident_code, zone_id, reported_by, incident_type, title, description, priority, status)
           VALUES (%s,%s,%s,%s,%s,%s,%s,'reported')""",
        (code, zone["id"] if zone else None, user_id, incident_type, title, location, priority),
    )
    write_audit("Incident logged", "incident", iid, new_values={"code": code, "title": title, "location": location})
    risk_scoring.refresh_all_risk_scores()
    return code


def update_incident(code, status, notes="", actor="OSAS Administrator", user_id=None):
    status = (status or "").lower()
    row = query_one("SELECT * FROM incidents WHERE incident_code=%s", (code,))
    if not row:
        return False
    extras = ""
    params = [status]
    if status == "verified":
        extras = ", verified_at=NOW()"
    if status in ("resolved", "closed"):
        extras += ", resolved_at=NOW()"
    params.append(code)
    execute(f"UPDATE incidents SET status=%s{extras} WHERE incident_code=%s", tuple(params))
    write_audit("Incident updated", "incident", row["id"], old_values={"status": row["status"]}, new_values={"status": status, "notes": notes})
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
    start = by_code.get(start_code) or next((w for w in weights if w["code"] == "SEC"), weights[0])
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
        return None
    upload_root = current_app.config.get("UPLOAD_FOLDER", "static/uploads")
    folder = os.path.join(upload_root, "incidents")
    os.makedirs(folder, exist_ok=True)
    safe = secure_filename(filename) or "attachment.bin"
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
    return rel


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
