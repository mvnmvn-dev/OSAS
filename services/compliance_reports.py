"""Safety compliance reports: build, preview and export (CSV / Excel / PDF).

Every report is built as a plain dict::

    {"key", "title", "headers", "rows", "summary": [(label, value), ...],
     "period": "Nov 01, 2026 – Nov 30, 2026" | "Current snapshot"}

so the same data drives the on-screen preview and all three export formats.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta
from xml.sax.saxutils import escape

from models.database import query_all, query_one
from services import osas_modules_service as mods
from services.audit_service import write_audit
from services.osas_modules_service import ModuleError, as_date, as_datetime

# Targets used by the consolidated "Safety compliance summary". These are OSAS
# working defaults, not statutory figures; adjust them to match school policy.
TARGETS = {
    "inspection_completion": 90,   # % of inspections completed
    "drill_completion": 80,        # % of scheduled drills completed
    "notification_delivery": 90,   # % of parent notices delivered
}

REPORT_TYPES = [
    ("safety_compliance", "Safety compliance summary", "Consolidated readiness scorecard across all OSAS modules", True),
    ("incident_summary", "Incident summary", "Incidents reported in the period", True),
    ("patrol_completion", "Patrol completion", "Checkpoint scan coverage in the period", True),
    ("inspection_compliance", "Inspection compliance", "Safety inspections and checklist outcomes", True),
    ("drill_compliance", "Drill compliance", "Emergency drills scheduled and held", True),
    ("risk_register", "Risk register", "Hazards assessed in the period", True),
    ("first_aid_status", "First aid inventory status", "Current stock levels and expiry (snapshot)", False),
    ("emergency_contacts", "Emergency contact directory", "Active emergency contacts (snapshot)", False),
    ("emergency_roles", "Emergency role roster", "Role assignments and backups (snapshot)", False),
    ("parent_notifications", "Parent notification log", "Parent / guardian notices and delivery status", True),
    ("audit_log", "Audit log", "System actions recorded in the period", True),
]
REPORT_META = {key: {"label": label, "hint": hint, "ranged": ranged} for key, label, hint, ranged in REPORT_TYPES}
_LEGACY_KEYS = {
    "incident summary": "incident_summary", "incidents": "incident_summary",
    "patrol completion": "patrol_completion", "patrol": "patrol_completion",
    "inspection compliance": "inspection_compliance", "inspections": "inspection_compliance",
    "audit log": "audit_log", "audit": "audit_log",
}

RANGE_PRESETS = [
    ("7d", "Last 7 days"), ("30d", "Last 30 days"), ("90d", "Last 90 days"),
    ("semester", "Current semester"), ("custom", "Custom range"),
]
_LEGACY_RANGES = {"last 7 days": "7d", "last 30 days": "30d", "last 90 days": "90d", "current semester": "semester"}


def normalise_report_key(value):
    key = (value or "").strip()
    if key in REPORT_META:
        return key
    mapped = _LEGACY_KEYS.get(key.lower())
    if mapped:
        return mapped
    raise ModuleError("Select a valid report type.")


def current_semester(today=None):
    """Philippine academic calendar assumption: Aug–Dec, Jan–May, Jun–Jul (summer)."""
    today = today or date.today()
    if today.month >= 8:
        return date(today.year, 8, 1), date(today.year, 12, 31)
    if today.month <= 5:
        return date(today.year, 1, 1), date(today.year, 5, 31)
    return date(today.year, 6, 1), date(today.year, 7, 31)


def resolve_range(preset="30d", start=None, end=None, today=None):
    """Return ``(start_date, end_date)`` inclusive."""
    today = today or date.today()
    preset = _LEGACY_RANGES.get((preset or "").strip().lower(), (preset or "30d").strip().lower())
    if preset == "custom":
        start_d, end_d = as_date(start), as_date(end)
        if not start_d or not end_d:
            raise ModuleError("Choose both a start and an end date for a custom range.")
        if start_d > end_d:
            raise ModuleError("The start date must be on or before the end date.")
        if (end_d - start_d).days > 800:
            raise ModuleError("Custom ranges are limited to about two years.")
        return start_d, end_d
    if preset == "semester":
        return current_semester(today)
    days = {"7d": 7, "30d": 30, "90d": 90}.get(preset)
    if days is None:
        raise ModuleError("Select a valid date range.")
    return today - timedelta(days=days - 1), today


def _bounds(start, end):
    """SQL string bounds: [start 00:00:00, day-after-end 00:00:00)."""
    return start.strftime("%Y-%m-%d 00:00:00"), (end + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")


def _pct(part, whole):
    return f"{round(part / whole * 100)}%" if whole else "—"


def _d(value, fmt="%Y-%m-%d %H:%M"):
    parsed = as_datetime(value)
    return parsed.strftime(fmt) if parsed else ""


def _period(meta_ranged, start, end):
    if not meta_ranged:
        return "Current snapshot as of " + datetime.now().strftime("%b %d, %Y %I:%M %p")
    return f"{start.strftime('%b %d, %Y')} – {end.strftime('%b %d, %Y')}"


# --------------------------------------------------------------------------- #
# Individual report builders
# --------------------------------------------------------------------------- #
def _incident_summary(start, end):
    lo, hi = _bounds(start, end)
    rows = query_all(
        """SELECT i.incident_code, i.title, i.incident_type, i.priority, i.status, i.reported_at, i.resolved_at,
                  COALESCE(z.zone_name, '—') AS zone_name, COALESCE(pt.team_name, 'Unassigned') AS team_name
           FROM incidents i
           LEFT JOIN campus_zones z ON z.id = i.zone_id
           LEFT JOIN patrol_teams pt ON pt.id = i.team_id
           WHERE i.reported_at >= %s AND i.reported_at < %s
           ORDER BY i.reported_at DESC""",
        (lo, hi),
    )
    open_n = sum(1 for r in rows if r["status"] not in ("resolved", "closed"))
    high = sum(1 for r in rows if r["priority"] in ("high", "critical"))
    minutes = []
    for r in rows:
        a, b = as_datetime(r["reported_at"]), as_datetime(r["resolved_at"])
        if a and b and b >= a:
            minutes.append((b - a).total_seconds() / 60)
    return {
        "headers": ["Code", "Title", "Type", "Priority", "Status", "Zone", "Team", "Reported", "Resolved"],
        "rows": [[r["incident_code"], r["title"], (r["incident_type"] or "").replace("_", " "), r["priority"], r["status"],
                  r["zone_name"], r["team_name"], _d(r["reported_at"]), _d(r["resolved_at"])] for r in rows],
        "summary": [("Incidents reported", len(rows)), ("Still open", open_n), ("High / critical priority", high),
                    ("Resolution rate", _pct(len(rows) - open_n, len(rows))),
                    ("Avg time to resolve", f"{round(sum(minutes) / len(minutes))} min" if minutes else "—")],
    }


def _patrol_completion(start, end):
    lo, hi = _bounds(start, end)
    rows = query_all(
        """SELECT c.checkpoint_code, c.checkpoint_name, z.zone_name,
                  COUNT(l.id) AS scans,
                  COALESCE(SUM(CASE WHEN l.result = 'valid' THEN 1 ELSE 0 END), 0) AS valid_scans,
                  COALESCE(SUM(CASE WHEN l.result IN ('missed','late','invalid') THEN 1 ELSE 0 END), 0) AS flagged,
                  MAX(l.scanned_at) AS last_scan
           FROM checkpoints c
           JOIN campus_zones z ON z.id = c.zone_id
           LEFT JOIN patrol_logs l ON l.checkpoint_id = c.id AND l.scanned_at >= %s AND l.scanned_at < %s
           WHERE c.active = 1
           GROUP BY c.id, c.checkpoint_code, c.checkpoint_name, z.zone_name
           ORDER BY c.checkpoint_code""",
        (lo, hi),
    )
    covered = sum(1 for r in rows if int(r["valid_scans"]) > 0)
    total_scans = sum(int(r["scans"]) for r in rows)
    flagged = sum(int(r["flagged"]) for r in rows)
    return {
        "headers": ["Checkpoint", "Name", "Zone", "Scans", "Valid", "Flagged", "Last scan"],
        "rows": [[r["checkpoint_code"], r["checkpoint_name"], r["zone_name"], int(r["scans"]), int(r["valid_scans"]),
                  int(r["flagged"]), _d(r["last_scan"])] for r in rows],
        "summary": [("Active checkpoints", len(rows)), ("Checkpoints scanned", covered),
                    ("Checkpoint coverage", _pct(covered, len(rows))), ("Total scans", total_scans),
                    ("Flagged scans (late/missed/invalid)", flagged)],
    }


def _inspection_compliance(start, end):
    rows = query_all(
        """SELECT si.id, si.inspection_code, z.zone_name, si.notes AS insp_type, si.scheduled_date, si.status, si.overall_score,
                  (SELECT COUNT(*) FROM inspection_items i WHERE i.inspection_id = si.id AND i.result = 'pass') AS passed,
                  (SELECT COUNT(*) FROM inspection_items i WHERE i.inspection_id = si.id AND i.result = 'fail') AS failed,
                  (SELECT COUNT(*) FROM inspection_items i WHERE i.inspection_id = si.id AND i.result = 'fail'
                       AND COALESCE(i.action_done, 0) = 0) AS open_actions
           FROM safety_inspections si JOIN campus_zones z ON z.id = si.zone_id
           WHERE si.scheduled_date >= %s AND si.scheduled_date <= %s
           ORDER BY si.scheduled_date DESC""",
        (start.isoformat(), end.isoformat()),
    )
    done = [r for r in rows if r["status"] == "completed"]
    scores = [float(r["overall_score"]) for r in done if r["overall_score"] is not None]
    return {
        "headers": ["Code", "Zone", "Type", "Scheduled", "Status", "Score", "Passed", "Failed", "Open actions"],
        "rows": [[r["inspection_code"], r["zone_name"], r["insp_type"] or "", _d(r["scheduled_date"], "%Y-%m-%d"),
                  r["status"].replace("_", " "), f"{float(r['overall_score']):.0f}%" if r["overall_score"] is not None else "",
                  int(r["passed"]), int(r["failed"]), int(r["open_actions"])] for r in rows],
        "summary": [("Inspections in period", len(rows)), ("Completed", len(done)),
                    ("Completion rate", _pct(len(done), len(rows))),
                    ("Overdue", sum(1 for r in rows if r["status"] == "overdue")),
                    ("Average score", f"{round(sum(scores) / len(scores))}%" if scores else "—"),
                    ("Open corrective actions", sum(int(r["open_actions"]) for r in rows))],
    }


def _drill_compliance(start, end):
    lo, hi = _bounds(start, end)
    rows = query_all(
        """SELECT d.title, d.drill_type, d.scheduled_at, d.status, d.participant_count, d.duration_minutes,
                  d.evaluation_score, COALESCE(t.team_name, '—') AS team_name
           FROM drills d LEFT JOIN patrol_teams t ON t.id = d.team_id
           WHERE d.scheduled_at >= %s AND d.scheduled_at < %s ORDER BY d.scheduled_at DESC""",
        (lo, hi),
    )
    done = [r for r in rows if r["status"] == "completed"]
    scores = [r["evaluation_score"] for r in done if r["evaluation_score"] is not None]
    return {
        "headers": ["Drill", "Type", "Scheduled", "Status", "Team", "Participants", "Minutes", "Score"],
        "rows": [[r["title"], r["drill_type"], _d(r["scheduled_at"]), r["status"].replace("_", " "), r["team_name"],
                  int(r["participant_count"] or 0), r["duration_minutes"] if r["duration_minutes"] is not None else "",
                  r["evaluation_score"] if r["evaluation_score"] is not None else ""] for r in rows],
        "summary": [("Drills in period", len(rows)), ("Completed", len(done)),
                    ("Completion rate", _pct(len(done), len(rows))),
                    ("Cancelled", sum(1 for r in rows if r["status"] == "cancelled")),
                    ("Total participants", sum(int(r["participant_count"] or 0) for r in done)),
                    ("Average evaluation score", f"{round(sum(scores) / len(scores))}" if scores else "—")],
    }


def _risk_register(start, end):
    lo, hi = _bounds(start, end)
    rows = query_all(
        """SELECT ra.hazard_title, z.zone_name, ra.likelihood, ra.severity, ra.risk_score, ra.status,
                  ra.mitigation_plan, ra.created_at
           FROM risk_assessments ra JOIN campus_zones z ON z.id = ra.zone_id
           WHERE ra.created_at >= %s AND ra.created_at < %s ORDER BY ra.risk_score DESC""",
        (lo, hi),
    )
    levels = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    out = []
    for r in rows:
        level = mods.risk_level(r["risk_score"])[0]
        if r["status"] in ("open", "mitigating"):
            levels[level] += 1
        out.append([r["hazard_title"], r["zone_name"], int(r["likelihood"]), int(r["severity"]), int(float(r["risk_score"])),
                    level, r["status"], (r["mitigation_plan"] or "").replace("\n", " ")[:200]])
    return {
        "headers": ["Hazard", "Zone", "Likelihood", "Severity", "Score", "Level", "Status", "Mitigation"],
        "rows": out,
        "summary": [("Hazards assessed", len(rows)), ("Active critical", levels["Critical"]), ("Active high", levels["High"]),
                    ("Active medium", levels["Medium"]), ("Active low", levels["Low"]),
                    ("Resolved", sum(1 for r in rows if r["status"] == "resolved"))],
    }


def _first_aid_status():
    items = mods.list_first_aid()
    summary = mods.first_aid_summary()
    return {
        "headers": ["Item", "Cabinet", "Quantity", "Reorder level", "Expiry", "Status", "Last checked"],
        "rows": [[i["item_name"], i["cabinet_location"], int(i["quantity"]), int(i["reorder_level"]), i["expiry_label"],
                  i["status_label"], i["last_checked_label"]] for i in items],
        "summary": [("Items tracked", summary["total"]), ("In stock", summary["counts"]["available"]),
                    ("Low stock", summary["counts"]["low_stock"]), ("Out of stock", summary["counts"]["out_of_stock"]),
                    ("Expired", summary["counts"]["expired"]), ("Expiring within 30 days", summary["expiring_soon"])],
    }


def _emergency_contacts():
    rows = mods.list_contacts()
    summary = mods.contact_summary()
    return {
        "headers": ["Priority", "Name", "Organization", "Role", "Type", "Phone", "Email"],
        "rows": [[r["priority"], r["contact_name"], r.get("organization") or "", r.get("role_title") or "", r["type_label"],
                  r["phone"], r.get("email") or ""] for r in rows],
        "summary": [("Active contacts", summary["total"]),
                    ("Missing critical categories", ", ".join(summary["missing_critical"]) or "None")],
    }


def _emergency_roles():
    roles = mods.list_roles()
    cov = mods.role_coverage()
    return {
        "headers": ["Role", "Scope", "Primary", "Backup", "Responsibility"],
        "rows": [[r["role_name"], r.get("drill_title") or "Standing", r.get("assigned_name") or "UNASSIGNED",
                  r.get("backup_name") or "—", r["responsibility"]] for r in roles],
        "summary": [("Roles defined", cov["total"]), ("Assigned", cov["assigned"]), ("Unassigned", cov["unassigned"]),
                    ("Assigned without backup", cov["without_backup"]), ("Coverage", f"{cov['percent']}%")],
    }


def _parent_notifications(start, end):
    lo, hi = _bounds(start, end)
    rows = query_all(
        """SELECT pn.created_at, pn.recipient_name, pn.student_name, pn.channel, pn.status, pn.attempts, pn.sent_at,
                  COALESCE(i.incident_code, '') AS incident_code
           FROM parent_notifications pn LEFT JOIN incidents i ON i.id = pn.incident_id
           WHERE pn.created_at >= %s AND pn.created_at < %s ORDER BY pn.created_at DESC""",
        (lo, hi),
    )
    sent = sum(1 for r in rows if r["status"] == "sent")
    return {
        "headers": ["Created", "Recipient", "Student", "Channel", "Incident", "Status", "Attempts", "Sent"],
        "rows": [[_d(r["created_at"]), r["recipient_name"], r["student_name"] or "", r["channel"], r["incident_code"],
                  r["status"], int(r["attempts"] or 0), _d(r["sent_at"])] for r in rows],
        "summary": [("Notices created", len(rows)), ("Delivered", sent),
                    ("Delivery rate", _pct(sent, len(rows))),
                    ("Failed", sum(1 for r in rows if r["status"] == "failed")),
                    ("Still queued", sum(1 for r in rows if r["status"] == "queued"))],
    }


def _audit_log(start, end):
    lo, hi = _bounds(start, end)
    rows = query_all(
        """SELECT al.created_at, al.action, al.entity_type, al.entity_id, COALESCE(u.full_name, 'System') AS actor
           FROM audit_logs al LEFT JOIN users u ON u.id = al.user_id
           WHERE al.created_at >= %s AND al.created_at < %s
           ORDER BY al.created_at DESC LIMIT 5000""",
        (lo, hi),
    )
    return {
        "headers": ["When", "Action", "Entity", "Entity ID", "User"],
        "rows": [[_d(r["created_at"], "%Y-%m-%d %H:%M:%S"), r["action"], r["entity_type"] or "",
                  r["entity_id"] if r["entity_id"] is not None else "", r["actor"]] for r in rows],
        "summary": [("Actions recorded", len(rows)), ("Distinct users", len({r["actor"] for r in rows}))]
        + ([("Note", "Showing the most recent 5,000 records")] if len(rows) >= 5000 else []),
    }


def _safety_compliance(start, end):
    """Scorecard that rolls up every module against OSAS target thresholds."""
    inspections = _inspection_compliance(start, end)
    drills = _drill_compliance(start, end)
    notifications = _parent_notifications(start, end)
    s_insp = dict(inspections["summary"])
    s_drill = dict(drills["summary"])
    s_notif = dict(notifications["summary"])

    def pct_value(text):
        return int(text.rstrip("%")) if isinstance(text, str) and text.endswith("%") else None

    risk = mods.risk_summary()
    fa = mods.first_aid_summary()
    cov = mods.role_coverage()
    plans = mods.plan_coverage()
    contacts = mods.contact_summary()

    def status(ok):
        return "Meets target" if ok else "Needs attention"

    insp_rate = pct_value(s_insp["Completion rate"])
    drill_rate = pct_value(s_drill["Completion rate"])
    notif_rate = pct_value(s_notif["Delivery rate"])
    rows = [
        ["Inspections", "Completion rate", s_insp["Completion rate"], f"≥ {TARGETS['inspection_completion']}%",
         status(insp_rate is not None and insp_rate >= TARGETS["inspection_completion"])],
        ["Inspections", "Overdue inspections", s_insp["Overdue"], "0", status(s_insp["Overdue"] == 0)],
        ["Inspections", "Open corrective actions", s_insp["Open corrective actions"], "0",
         status(s_insp["Open corrective actions"] == 0)],
        ["Drills", "Completion rate", s_drill["Completion rate"], f"≥ {TARGETS['drill_completion']}%",
         status(drill_rate is not None and drill_rate >= TARGETS["drill_completion"])],
        ["Evacuation plans", "Buildings with a current plan", f"{plans['covered']} of {plans['buildings']}", "All buildings",
         status(plans["buildings"] > 0 and plans["covered"] == plans["buildings"])],
        ["Risk", "Active critical / high risks", risk["levels"]["Critical"] + risk["levels"]["High"], "0",
         status(risk["levels"]["Critical"] + risk["levels"]["High"] == 0)],
        ["First aid", "Items low, out of stock or expired", fa["needs_action"], "0", status(fa["needs_action"] == 0)],
        ["First aid", "Items expiring within 30 days", fa["expiring_soon"], "0", status(fa["expiring_soon"] == 0)],
        ["Emergency roles", "Unassigned roles", cov["unassigned"], "0", status(cov["total"] > 0 and cov["unassigned"] == 0)],
        ["Emergency contacts", "Missing fire / police / medical contact", ", ".join(contacts["missing_critical"]) or "None",
         "None", status(not contacts["missing_critical"])],
        ["Parent notifications", "Delivery rate", s_notif["Delivery rate"], f"≥ {TARGETS['notification_delivery']}%",
         status(notif_rate is None or notif_rate >= TARGETS["notification_delivery"])],
    ]
    met = sum(1 for r in rows if r[-1] == "Meets target")
    return {
        "headers": ["Area", "Measure", "Result", "Target", "Status"],
        "rows": rows,
        "summary": [("Measures meeting target", f"{met} of {len(rows)}"), ("Overall", _pct(met, len(rows)))],
    }


_BUILDERS = {
    "safety_compliance": ("ranged", _safety_compliance),
    "incident_summary": ("ranged", _incident_summary),
    "patrol_completion": ("ranged", _patrol_completion),
    "inspection_compliance": ("ranged", _inspection_compliance),
    "drill_compliance": ("ranged", _drill_compliance),
    "risk_register": ("ranged", _risk_register),
    "parent_notifications": ("ranged", _parent_notifications),
    "audit_log": ("ranged", _audit_log),
    "first_aid_status": ("snapshot", _first_aid_status),
    "emergency_contacts": ("snapshot", _emergency_contacts),
    "emergency_roles": ("snapshot", _emergency_roles),
}


@mods.module_api
def build_report(report_key, start=None, end=None, preset="30d"):
    key = normalise_report_key(report_key)
    kind, builder = _BUILDERS[key]
    start_d, end_d = (start, end) if isinstance(start, date) and isinstance(end, date) else resolve_range(preset, start, end)
    data = builder(start_d, end_d) if kind == "ranged" else builder()
    data.update(key=key, title=REPORT_META[key]["label"], period=_period(kind == "ranged", start_d, end_d),
                start=start_d, end=end_d, generated=datetime.now().strftime("%b %d, %Y %I:%M %p"))
    return data


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _safe_cell(value):
    """Neutralise spreadsheet formula injection from user-entered text."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def render_csv(report):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([report["title"]])
    writer.writerow(["Period", report["period"]])
    writer.writerow(["Generated", report["generated"]])
    writer.writerow([])
    for label, value in report["summary"]:
        writer.writerow([label, _safe_cell(value)])
    writer.writerow([])
    writer.writerow(report["headers"])
    for row in report["rows"]:
        writer.writerow([_safe_cell(c) for c in row])
    return ("﻿" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", "csv"


def render_excel(report):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = report["title"]
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"], ws["B2"] = "Period", report["period"]
    ws["A3"], ws["B3"] = "Generated", report["generated"]
    for i, (label, value) in enumerate(report["summary"], start=5):
        ws.cell(row=i, column=1, value=label).font = Font(bold=True)
        ws.cell(row=i, column=2, value=_safe_cell(value))
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 44

    data = wb.create_sheet("Data")
    data.append(report["headers"])
    fill = PatternFill("solid", fgColor="1C4D73")
    for cell in data[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill
        cell.alignment = Alignment(vertical="center")
    for row in report["rows"]:
        data.append([_safe_cell(c) for c in row])
    for idx, header in enumerate(report["headers"], start=1):
        longest = max([len(str(header))] + [len(str(r[idx - 1])) for r in report["rows"][:500]])
        data.column_dimensions[get_column_letter(idx)].width = min(max(12, longest + 2), 60)
    data.freeze_panes = "A2"
    if report["rows"]:
        data.auto_filter.ref = data.dimensions
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"


def render_pdf(report):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=styles["BodyText"], fontSize=7.5, leading=9)
    head = ParagraphStyle("head", parent=cell, textColor=colors.white, fontName="Helvetica-Bold")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter), leftMargin=0.5 * inch, rightMargin=0.5 * inch,
                            topMargin=0.6 * inch, bottomMargin=0.6 * inch, title=report["title"], author="OSAS")

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(0.5 * inch, 0.35 * inch, f"OSAS · {report['title']} · generated {report['generated']}")
        canvas.drawRightString(landscape(letter)[0] - 0.5 * inch, 0.35 * inch, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    def para(text, style=cell):
        return Paragraph(escape(str(text)), style)

    story = [Paragraph(escape(report["title"]), styles["Title"]),
             Paragraph(escape(f"Period: {report['period']}"), styles["Normal"]), Spacer(1, 10)]
    if report["summary"]:
        summary_tbl = Table([[para(k), para(v)] for k, v in report["summary"]], colWidths=[2.8 * inch, 3.2 * inch])
        summary_tbl.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
                                         ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                                         ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [summary_tbl, Spacer(1, 14)]
    if report["rows"]:
        table_data = [[para(h, head) for h in report["headers"]]] + [[para(c) for c in row] for row in report["rows"]]
        width = landscape(letter)[0] - 1.0 * inch
        tbl = Table(table_data, repeatRows=1, colWidths=[width / len(report["headers"])] * len(report["headers"]))
        tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1c4d73")),
                                 ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f6f8")]),
                                 ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
                                 ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(tbl)
    else:
        story.append(Paragraph("No records for this period.", styles["Italic"]))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue(), "application/pdf", "pdf"


_RENDERERS = {"CSV": render_csv, "EXCEL": render_excel, "XLSX": render_excel, "PDF": render_pdf}


def render_report(report, fmt):
    renderer = _RENDERERS.get((fmt or "PDF").upper())
    if not renderer:
        raise ModuleError("Select a valid export format (PDF, CSV or Excel).")
    return renderer(report)


@mods.module_api
def export_report_bytes(report_key, fmt, preset="30d", start=None, end=None, user_id=None):
    """Build + render a report and write an audit entry. Returns ``(bytes, mimetype, filename)``."""
    report = build_report(report_key, start=start, end=end, preset=preset)
    content, mimetype, ext = render_report(report, fmt)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    write_audit("Report exported", "report", None,
                new_values={"type": report["key"], "format": ext, "period": report["period"], "rows": len(report["rows"])},
                user_id=user_id)
    return content, mimetype, f"osas_{report['key']}_{stamp}.{ext}"
