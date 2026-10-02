"""In-app notifications + live operational alerts for the header dropdown."""
from datetime import datetime, timedelta

from flask import url_for

from models.database import execute, query_all, query_one


def create_notification(user_id, title, message, ntype="info", link=None):
    nid = execute(
        """INSERT INTO notifications (user_id, title, message, ntype, link)
           VALUES (%s, %s, %s, %s, %s)""",
        (user_id, title, message, ntype, link),
    )[0]
    return nid


def get_user_notifications(user_id, limit=20, unread_only=False):
    limit = max(1, min(int(limit or 20), 100))
    sql = "SELECT * FROM notifications WHERE user_id = %s"
    params = [user_id]
    if unread_only:
        sql += " AND is_read = 0"
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    return query_all(sql, params)


def get_unread_count(user_id):
    row = query_one(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id = %s AND is_read = 0",
        (user_id,),
    )
    return int(row["c"] if row else 0)


def mark_read(notification_id, user_id):
    execute(
        "UPDATE notifications SET is_read = 1 WHERE id = %s AND user_id = %s",
        (notification_id, user_id),
    )
    return get_unread_count(user_id)


def mark_all_read(user_id):
    execute(
        "UPDATE notifications SET is_read = 1 WHERE user_id = %s AND is_read = 0",
        (user_id,),
    )
    return 0


def notify_patron(user_id, title, message, ntype="info", link=None):
    if user_id:
        create_notification(user_id, title, message, ntype, link)


def notify_staff(title, message, ntype="info", link=None):
    staff = query_all(
        """SELECT id FROM users
           WHERE role IN ('admin', 'security_head', 'safety_officer')
             AND account_status='active'"""
    )
    for row in staff:
        create_notification(row["id"], title, message, ntype, link)


def _relative_time(value):
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", ""))
        except ValueError:
            return value
    now = datetime.now()
    if getattr(value, "tzinfo", None):
        value = value.replace(tzinfo=None)
    delta = now - value
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "Just now"
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        mins = seconds // 60
        return f"{mins}m ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h ago"
    if seconds < 86400 * 7:
        days = seconds // 86400
        return f"{days}d ago"
    return value.strftime("%b %d, %Y")


def _icon_for_type(ntype):
    return {
        "success": "check",
        "warning": "exclamation",
        "danger": "triangle-exclamation",
        "info": "info",
    }.get((ntype or "info").lower(), "info")


def enrich_notification(row):
    created = row.get("created_at")
    ntype = (row.get("ntype") or "info").lower()
    return {
        "id": row.get("id"),
        "title": row.get("title") or "Notification",
        "message": row.get("message") or "",
        "ntype": ntype,
        "link": row.get("link") or "#",
        "is_read": bool(row.get("is_read")),
        "created_at": created,
        "created_iso": created.isoformat() if hasattr(created, "isoformat") else (created or ""),
        "relative_time": _relative_time(created),
        "icon": _icon_for_type(ntype),
        "source": "inbox",
    }


def build_ops_alerts(limit=6):
    """Live campus signals shown above persisted inbox items (not stored rows)."""
    alerts = []
    critical = query_all(
        """SELECT i.incident_code, i.title, i.priority, i.reported_at,
                  COALESCE(z.zone_name, 'Campus') AS location_text
           FROM incidents i
           LEFT JOIN campus_zones z ON z.id = i.zone_id
           WHERE i.status IN ('reported','verified','responding')
             AND LOWER(i.priority) IN ('critical','high')
           ORDER BY FIELD(LOWER(i.priority),'critical','high'), i.reported_at DESC
           LIMIT %s""",
        (limit,),
    ) or []

    for row in critical:
        prio = (row.get("priority") or "high").lower()
        loc = row.get("location_text") or "Campus"
        alerts.append({
            "id": f"ops-inc-{row.get('incident_code')}",
            "title": f"{prio.title()} incident open",
            "message": f"{row.get('incident_code')}: {row.get('title')} · {loc}",
            "ntype": "danger" if prio == "critical" else "warning",
            "link": url_for("safety.incidents"),
            "is_read": False,
            "relative_time": _relative_time(row.get("reported_at")),
            "created_iso": "",
            "icon": _icon_for_type("danger" if prio == "critical" else "warning"),
            "source": "live",
            "tag": "Incidents",
        })

    missed = query_one(
        """SELECT COUNT(*) AS c FROM patrol_logs
           WHERE result IN ('missed','late','invalid')
             AND scanned_at >= NOW() - INTERVAL 24 HOUR"""
    ) or {}
    missed_n = int(missed.get("c") or 0)
    if missed_n:
        alerts.append({
            "id": "ops-patrol-missed",
            "title": "Patrol exceptions (24h)",
            "message": f"{missed_n} missed, late, or invalid checkpoint scan{'s' if missed_n != 1 else ''}",
            "ntype": "warning",
            "link": url_for("safety.patrol"),
            "is_read": False,
            "relative_time": "Last 24h",
            "created_iso": "",
            "icon": "route",
            "source": "live",
            "tag": "Patrol",
        })

    overdue = query_one(
        """SELECT COUNT(*) AS c FROM safety_inspections
           WHERE status IN ('overdue','scheduled')
             AND scheduled_date < CURDATE()"""
    ) or {}
    overdue_n = int(overdue.get("c") or 0)
    if overdue_n:
        alerts.append({
            "id": "ops-insp-overdue",
            "title": "Overdue inspections",
            "message": f"{overdue_n} inspection{'s' if overdue_n != 1 else ''} past scheduled date",
            "ntype": "warning",
            "link": url_for("safety.inspections"),
            "is_read": False,
            "relative_time": "Needs action",
            "created_iso": "",
            "icon": "clipboard-check",
            "source": "live",
            "tag": "Inspections",
        })

    soon = datetime.now() + timedelta(days=7)
    drills = query_one(
        """SELECT COUNT(*) AS c FROM drills
           WHERE status IN ('planned','scheduled')
             AND scheduled_at IS NOT NULL
             AND scheduled_at <= %s""",
        (soon,),
    ) or {}
    drill_n = int(drills.get("c") or 0)
    if drill_n:
        alerts.append({
            "id": "ops-drill-soon",
            "title": "Drills due within 7 days",
            "message": f"{drill_n} planned drill{'s' if drill_n != 1 else ''} on the emergency calendar",
            "ntype": "info",
            "link": url_for("safety.emergency"),
            "is_read": False,
            "relative_time": "Upcoming",
            "created_iso": "",
            "icon": "calendar-days",
            "source": "live",
            "tag": "Emergency",
        })

    return alerts[:limit]


def build_header_status(ops_alerts=None):
    """Compact header system-status derived from live alerts + DB ping."""
    db_ok = True
    try:
        query_one("SELECT 1 AS ok")
    except Exception:
        db_ok = False

    alerts = ops_alerts if ops_alerts is not None else []
    danger = sum(1 for a in alerts if a.get("ntype") == "danger")
    warn = sum(1 for a in alerts if a.get("ntype") == "warning")

    if not db_ok:
        return {
            "tone": "critical",
            "label": "Database Offline",
            "rows": [
                ("Application", "Degraded", "warn"),
                ("Database", "Unavailable", "crit"),
                ("Notifications", "Paused", "warn"),
                ("QR / Patrol Scan", "Unknown", "warn"),
            ],
        }
    if danger:
        return {
            "tone": "critical",
            "label": "Security Alert",
            "rows": [
                ("Application", "Operational", ""),
                ("Database", "Operational", ""),
                ("Live alerts", f"{danger} critical", "crit"),
                ("QR / Patrol Scan", "Operational", ""),
            ],
        }
    if warn:
        return {
            "tone": "attention",
            "label": "Attention Required",
            "rows": [
                ("Application", "Operational", ""),
                ("Database", "Operational", ""),
                ("Live alerts", f"{warn} needing review", "warn"),
                ("QR / Patrol Scan", "Operational", ""),
            ],
        }
    return {
        "tone": "operational",
        "label": "System Operational",
        "rows": [
            ("Application", "Operational", ""),
            ("Database", "Operational", ""),
            ("Notifications", "Operational", ""),
            ("QR / Patrol Scan", "Operational", ""),
        ],
    }


def get_notification_panel(user_id, limit=20):
    raw = get_user_notifications(user_id, limit=limit)
    items = [enrich_notification(row) for row in raw]
    unread = get_unread_count(user_id)
    try:
        ops_alerts = build_ops_alerts()
    except Exception:
        ops_alerts = []
    return {
        "items": items,
        "unread_count": unread,
        "ops_alerts": ops_alerts,
        "ops_alert_count": len(ops_alerts),
        "total_attention": unread + len(ops_alerts),
        "header_status": build_header_status(ops_alerts),
    }
