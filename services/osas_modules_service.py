"""Business logic for the OSAS Team 5 safety modules.

Modules implemented here
------------------------
1. Emergency Contact Database
2. Drill Scheduling
3. Evacuation Maps & Plans
4. Safety Inspection Checklist
5. Risk Assessment Tool
6. Parent Notification System
7. Emergency Role Assignment
8. First Aid Supplies Monitor

(Incident logging lives in ``safety_service``; compliance reports live in
``compliance_reports``.)

Design notes
------------
* Every public function validates its input and raises :class:`ModuleError`
  with a user-safe message. Routes catch it and flash the message.
* SQL is written once and runs on both PostgreSQL (production) and SQLite
  (tests), so date arithmetic is done in Python and passed as parameters.
* Every mutation writes an audit record.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import date, datetime, timedelta
from functools import wraps

from flask import current_app
from werkzeug.utils import secure_filename

from models.database import execute, get_db, query_all, query_one
from services.audit_service import write_audit
from services.notification_service import notify_patron, notify_staff

logger = logging.getLogger(__name__)


class ModuleError(ValueError):
    """Validation or workflow failure whose message is safe to show to users."""


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^[0-9+()\-\s.]{3,40}$")


def _clean(value, max_len, label, required=False):
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise ModuleError(f"{label} is required.")
    if len(text) > max_len:
        raise ModuleError(f"{label} must be {max_len} characters or fewer.")
    return text


def _int(value, label, lo, hi, default=None, required=False):
    if value is None or str(value).strip() == "":
        if required:
            raise ModuleError(f"{label} is required.")
        return default
    try:
        number = int(str(value).strip())
    except ValueError:
        raise ModuleError(f"{label} must be a whole number.") from None
    if number < lo or number > hi:
        raise ModuleError(f"{label} must be between {lo} and {hi}.")
    return number


def _opt_id(value):
    """Return an int id from a form value, or None for blank / 'none'."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("", "0", "none", "null"):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _phone(value, label="Phone", required=True):
    text = _clean(value, 40, label, required=required)
    if not text:
        return ""
    digits = re.sub(r"\D", "", text)
    if not _PHONE_RE.match(text) or not 3 <= len(digits) <= 15:
        raise ModuleError(f"{label} is not a valid phone number.")
    return text


def _email(value, label="Email", required=False):
    text = _clean(value, 120, label, required=required)
    if text and not _EMAIL_RE.match(text):
        raise ModuleError(f"{label} is not a valid email address.")
    return text


def as_datetime(value):
    """Best-effort conversion of DB values / form strings to ``datetime``."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:26] if "." in text else text, fmt)
        except ValueError:
            continue
    return None


def as_date(value):
    parsed = as_datetime(value)
    return parsed.date() if parsed else None


def _sql_ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _today():
    return datetime.now().date()


def _fmt(value, fmt="%b %d, %Y"):
    parsed = as_datetime(value)
    return parsed.strftime(fmt) if parsed else ""


def _in_clause(values):
    return ",".join(["%s"] * len(values))


def _safe_rollback():
    try:
        raw = getattr(get_db(), "_connection", None)
        if raw is not None:
            raw.rollback()
    except Exception:  # pragma: no cover - best effort only
        pass


# --------------------------------------------------------------------------- #
# Runtime schema upgrade + defaults
# --------------------------------------------------------------------------- #
MODULE_COLUMNS = {
    "drills": [
        ("completed_at", "TIMESTAMP NULL"),
        ("duration_minutes", "INT NULL"),
        ("evaluation_score", "INT NULL"),
        ("location", "VARCHAR(150) NULL"),
    ],
    "evacuation_plans": [
        ("notes", "TEXT NULL"),
        ("approved_at", "TIMESTAMP NULL"),
        ("approved_by", "INT NULL"),
        ("map_original_name", "VARCHAR(255) NULL"),
    ],
    "emergency_contacts": [
        ("priority", "INT NOT NULL DEFAULT 100"),
        ("notes", "TEXT NULL"),
    ],
    "parent_notifications": [
        ("student_name", "VARCHAR(150) NULL"),
        ("error_message", "TEXT NULL"),
        ("attempts", "INT NOT NULL DEFAULT 0"),
    ],
    "emergency_roles": [
        ("backup_user_id", "INT NULL"),
        ("assigned_at", "TIMESTAMP NULL"),
    ],
    "inspection_items": [
        ("action_done", "SMALLINT NOT NULL DEFAULT 0"),
    ],
    "safety_inspections": [
        ("summary", "TEXT NULL"),
    ],
    "first_aid_inventory": [
        ("last_restocked_at", "TIMESTAMP NULL"),
    ],
}

DEFAULT_EMERGENCY_ROLES = [
    ("Incident Commander", "Overall command of the emergency response; declares start and end of the emergency."),
    ("Deputy Incident Commander", "Assumes command when the Incident Commander is unavailable; coordinates with responders."),
    ("Evacuation Marshal", "Directs occupants to exits and assembly areas; sweeps floors to confirm they are clear."),
    ("Assembly Area Coordinator", "Manages the assembly area, keeps people together and reports status to command."),
    ("Head Counter / Roll-call", "Takes attendance at the assembly area and reports missing persons immediately."),
    ("First Aid Responder", "Provides first aid, maintains the response kit and hands over to medical services."),
    ("Fire Warden", "Checks the fire alarm panel, uses extinguishers if safe and meets the fire service."),
    ("Communications Officer", "Sends official notices, contacts emergency services and updates parents/guardians."),
    ("Security & Gate Control", "Controls gates, keeps access routes clear for responders and secures the perimeter."),
    ("Utilities Shut-off Officer", "Isolates electricity, gas and water when instructed by the Incident Commander."),
]

DEFAULT_CONTACTS = [
    # Only the nationally standardised hotline is seeded. Campus-specific numbers
    # (security desk, clinic, local fire station) must be entered by OSAS staff.
    ("National Emergency Hotline", "911", "medical", 1, "Nationwide emergency hotline (Philippines)."),
]

_SCHEMA_READY = set()


def _db_identity():
    cfg = current_app.config
    if cfg.get("TESTING"):
        return "test:" + str(cfg.get("TEST_DATABASE") or "default")
    return "pg:%s/%s" % (cfg.get("DATABASE_URL") or cfg.get("PGHOST"), cfg.get("PGDATABASE"))


def _columns(table):
    if current_app.config.get("TESTING"):
        return {row["name"] for row in query_all(f"PRAGMA table_info({table})")}
    rows = query_all(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s",
        (table,),
    )
    return {row["column_name"] for row in rows}


def ensure_module_schema():
    """Add columns introduced by these modules and seed reference data (once per DB)."""
    key = _db_identity()
    if key in _SCHEMA_READY:
        return
    try:
        for table, columns in MODULE_COLUMNS.items():
            existing = _columns(table)
            if not existing:
                continue  # table is created by schema.sql / bootstrap
            for name, ddl in columns:
                if name not in existing:
                    execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        _seed_defaults()
        _SCHEMA_READY.add(key)
    except Exception:
        _safe_rollback()
        logger.exception("OSAS module schema upgrade failed")
        raise


def _seed_defaults():
    if not query_one("SELECT id FROM emergency_roles LIMIT 1"):
        for name, responsibility in DEFAULT_EMERGENCY_ROLES:
            execute(
                "INSERT INTO emergency_roles (role_name, responsibility, active) VALUES (%s,%s,1)",
                (name, responsibility),
            )
    if not query_one("SELECT id FROM emergency_contacts LIMIT 1"):
        for name, phone, ctype, priority, notes in DEFAULT_CONTACTS:
            execute(
                """INSERT INTO emergency_contacts
                   (contact_name, organization, phone, contact_type, priority, notes, active)
                   VALUES (%s,%s,%s,%s,%s,%s,1)""",
                (name, "Government", phone, ctype, priority, notes),
            )


def module_api(fn):
    """Make sure the schema is upgraded before any module function runs."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        ensure_module_schema()
        return fn(*args, **kwargs)

    return wrapper


# --------------------------------------------------------------------------- #
# 1. Emergency Contact Database
# --------------------------------------------------------------------------- #
CONTACT_TYPES = [
    ("internal", "Campus / Internal"),
    ("fire", "Fire"),
    ("police", "Police / Security"),
    ("medical", "Medical / Ambulance"),
    ("government", "Government / LGU"),
    ("utility", "Utilities"),
    ("other", "Other"),
]
_CONTACT_TYPE_KEYS = {k for k, _ in CONTACT_TYPES}
CONTACT_TYPE_LABELS = dict(CONTACT_TYPES)


@module_api
def list_contacts(search=None, contact_type=None, include_inactive=False):
    sql = "SELECT * FROM emergency_contacts WHERE 1=1"
    params = []
    if not include_inactive:
        sql += " AND active = 1"
    if contact_type in _CONTACT_TYPE_KEYS:
        sql += " AND contact_type = %s"
        params.append(contact_type)
    term = (search or "").strip()
    if term:
        like = f"%{term}%"
        sql += " AND (contact_name LIKE %s OR organization LIKE %s OR role_title LIKE %s OR phone LIKE %s OR email LIKE %s)"
        params.extend([like] * 5)
    sql += " ORDER BY active DESC, priority ASC, contact_name ASC"
    rows = query_all(sql, params)
    for row in rows:
        row["type_label"] = CONTACT_TYPE_LABELS.get(row.get("contact_type"), "Other")
        row["tel"] = re.sub(r"[^\d+]", "", row.get("phone") or "")
    return rows


@module_api
def get_contact(contact_id):
    return query_one("SELECT * FROM emergency_contacts WHERE id=%s", (contact_id,))


@module_api
def save_contact(data, contact_id=None, user_id=None):
    name = _clean(data.get("contact_name"), 150, "Contact name", required=True)
    phone = _phone(data.get("phone"))
    email = _email(data.get("email"))
    organization = _clean(data.get("organization"), 150, "Organization")
    role_title = _clean(data.get("role_title"), 100, "Role / title")
    ctype = (data.get("contact_type") or "internal").strip().lower()
    if ctype not in _CONTACT_TYPE_KEYS:
        raise ModuleError("Select a valid contact type.")
    priority = _int(data.get("priority"), "Priority", 1, 999, default=100)
    notes = _clean(data.get("notes"), 1000, "Notes")

    dup = query_one(
        "SELECT id FROM emergency_contacts WHERE LOWER(contact_name)=LOWER(%s) AND phone=%s AND id <> %s",
        (name, phone, contact_id or 0),
    )
    if dup:
        raise ModuleError("A contact with the same name and phone number already exists.")

    values = (name, organization or None, role_title or None, phone, email or None, ctype, priority, notes or None)
    if contact_id:
        if not get_contact(contact_id):
            raise ModuleError("Contact not found.")
        execute(
            """UPDATE emergency_contacts SET contact_name=%s, organization=%s, role_title=%s, phone=%s,
               email=%s, contact_type=%s, priority=%s, notes=%s WHERE id=%s""",
            values + (contact_id,),
        )
        write_audit("Emergency contact updated", "emergency_contact", contact_id, new_values={"name": name, "type": ctype}, user_id=user_id)
        return contact_id
    new_id, _ = execute(
        """INSERT INTO emergency_contacts
           (contact_name, organization, role_title, phone, email, contact_type, priority, notes, active)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1)""",
        values,
    )
    write_audit("Emergency contact added", "emergency_contact", new_id, new_values={"name": name, "type": ctype}, user_id=user_id)
    return new_id


@module_api
def set_contact_active(contact_id, active, user_id=None):
    row = get_contact(contact_id)
    if not row:
        raise ModuleError("Contact not found.")
    execute("UPDATE emergency_contacts SET active=%s WHERE id=%s", (1 if active else 0, contact_id))
    write_audit("Emergency contact " + ("activated" if active else "deactivated"), "emergency_contact", contact_id,
                new_values={"name": row["contact_name"]}, user_id=user_id)


@module_api
def delete_contact(contact_id, user_id=None):
    row = get_contact(contact_id)
    if not row:
        raise ModuleError("Contact not found.")
    execute("DELETE FROM emergency_contacts WHERE id=%s", (contact_id,))
    write_audit("Emergency contact deleted", "emergency_contact", contact_id,
                old_values={"name": row["contact_name"], "type": row["contact_type"]}, user_id=user_id)


@module_api
def contact_summary():
    rows = query_all("SELECT contact_type, COUNT(*) AS c FROM emergency_contacts WHERE active=1 GROUP BY contact_type")
    counts = {r["contact_type"]: int(r["c"]) for r in rows}
    return {
        "total": sum(counts.values()),
        "by_type": counts,
        "missing_critical": [CONTACT_TYPE_LABELS[t] for t in ("fire", "police", "medical") if not counts.get(t)],
    }


# --------------------------------------------------------------------------- #
# 2. Drill Scheduling
# --------------------------------------------------------------------------- #
DRILL_TYPES = [
    ("fire", "Fire evacuation"),
    ("earthquake", "Earthquake (duck, cover, hold)"),
    ("lockdown", "Lockdown / security threat"),
    ("medical", "Medical emergency"),
    ("other", "Other"),
]
DRILL_TYPE_LABELS = dict(DRILL_TYPES)
DRILL_STATUS_LABELS = {
    "planned": "Planned", "in_progress": "In progress", "completed": "Completed", "cancelled": "Cancelled",
}
DRILL_STATUS_CLASS = {"planned": "info", "in_progress": "warning", "completed": "success", "cancelled": "danger"}
DRILL_TRANSITIONS = {
    "planned": {"in_progress", "completed", "cancelled"},
    "in_progress": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": {"planned"},
}
DRILL_CONFLICT_WINDOW = timedelta(minutes=60)


def _drill_status(raw):
    status = (raw or "planned").lower()
    return "planned" if status == "scheduled" else status


def _decorate_drill(row):
    status = _drill_status(row.get("status"))
    when = as_datetime(row.get("scheduled_at"))
    row["status_key"] = status
    row["status_label"] = DRILL_STATUS_LABELS.get(status, status.title())
    row["status_class"] = DRILL_STATUS_CLASS.get(status, "warning")
    row["type_label"] = DRILL_TYPE_LABELS.get(row.get("drill_type"), "Other")
    row["scheduled_dt"] = when
    row["scheduled_label"] = _fmt(when, "%b %d, %Y · %I:%M %p")
    row["scheduled_input"] = when.strftime("%Y-%m-%dT%H:%M") if when else ""
    row["is_overdue"] = bool(when and status == "planned" and when < datetime.now())
    row["next_statuses"] = sorted(DRILL_TRANSITIONS.get(status, set()))
    return row


_DRILL_SELECT = """
    SELECT d.*, t.team_name, cp.checkpoint_code, cp.checkpoint_name, u.full_name AS lead_name
    FROM drills d
    LEFT JOIN patrol_teams t ON t.id = d.team_id
    LEFT JOIN checkpoints cp ON cp.id = d.checkpoint_id
    LEFT JOIN users u ON u.id = d.lead_officer_id
"""


@module_api
def list_drills(status=None, drill_type=None, upcoming_only=False):
    sql = _DRILL_SELECT + " WHERE 1=1"
    params = []
    if status == "planned":
        sql += " AND d.status IN ('planned','scheduled')"
    elif status in DRILL_STATUS_LABELS:
        sql += " AND d.status = %s"
        params.append(status)
    if drill_type in DRILL_TYPE_LABELS:
        sql += " AND d.drill_type = %s"
        params.append(drill_type)
    sql += " ORDER BY d.scheduled_at DESC"
    rows = [_decorate_drill(r) for r in query_all(sql, params)]
    if upcoming_only:
        now = datetime.now()
        rows = [r for r in rows if r["status_key"] in ("planned", "in_progress") and r["scheduled_dt"] and r["scheduled_dt"] >= now]
    return rows


@module_api
def get_drill(drill_id):
    row = query_one(_DRILL_SELECT + " WHERE d.id = %s", (drill_id,))
    return _decorate_drill(row) if row else None


@module_api
def drill_summary():
    rows = list_drills()
    now = datetime.now()
    total = len(rows)
    completed = [r for r in rows if r["status_key"] == "completed"]
    scores = [r["evaluation_score"] for r in completed if r.get("evaluation_score") is not None]
    upcoming = [r for r in rows if r["status_key"] == "planned" and r["scheduled_dt"] and r["scheduled_dt"] >= now]
    return {
        "total": total,
        "completed": len(completed),
        "planned": len([r for r in rows if r["status_key"] == "planned"]),
        "overdue": len([r for r in rows if r["is_overdue"]]),
        "completion_rate": round(len(completed) / total * 100) if total else 0,
        "participants": sum(int(r.get("participant_count") or 0) for r in completed),
        "avg_score": round(sum(scores) / len(scores)) if scores else None,
        "next": min(upcoming, key=lambda r: r["scheduled_dt"]) if upcoming else None,
    }


def _check_drill_conflict(when, team_id, exclude_id=None):
    if not team_id:
        return
    rows = query_all(
        "SELECT id, title, scheduled_at, status FROM drills WHERE team_id=%s AND status IN ('planned','scheduled','in_progress')",
        (team_id,),
    )
    for row in rows:
        if exclude_id and int(row["id"]) == int(exclude_id):
            continue
        other = as_datetime(row["scheduled_at"])
        if other and abs(other - when) < DRILL_CONFLICT_WINDOW:
            raise ModuleError(
                f"That team already has “{row['title']}” scheduled at {_fmt(other, '%b %d, %I:%M %p')} (within 60 minutes)."
            )


def _drill_fields(data):
    title = _clean(data.get("title"), 180, "Drill title", required=True)
    dtype = (data.get("drill_type") or data.get("type") or "fire").strip().lower()
    if dtype not in DRILL_TYPE_LABELS:
        raise ModuleError("Select a valid drill type.")
    when = as_datetime(data.get("scheduled_at"))
    if not when:
        raise ModuleError("A valid date and time is required for the drill.")
    team_id = _opt_id(data.get("team_id"))
    if team_id and not query_one("SELECT id FROM patrol_teams WHERE id=%s", (team_id,)):
        raise ModuleError("Selected team does not exist.")
    checkpoint_id = _opt_id(data.get("checkpoint_id"))
    if checkpoint_id and not query_one("SELECT id FROM checkpoints WHERE id=%s", (checkpoint_id,)):
        raise ModuleError("Selected checkpoint does not exist.")
    location = _clean(data.get("location"), 150, "Location")
    notes = _clean(data.get("notes"), 2000, "Notes")
    return title, dtype, when, team_id, checkpoint_id, location, notes


@module_api
def schedule_drill(data, user_id=None):
    title, dtype, when, team_id, checkpoint_id, location, notes = _drill_fields(data)
    _check_drill_conflict(when, team_id)
    lead_id = _opt_id(data.get("lead_officer_id")) or user_id
    drill_id, _ = execute(
        """INSERT INTO drills (drill_type, title, scheduled_at, lead_officer_id, team_id, checkpoint_id,
                               status, participant_count, location, notes)
           VALUES (%s,%s,%s,%s,%s,%s,'planned',0,%s,%s)""",
        (dtype, title, _sql_ts(when), lead_id, team_id, checkpoint_id, location or None, notes or None),
    )
    write_audit("Drill scheduled", "drill", drill_id, new_values={"title": title, "type": dtype, "at": _sql_ts(when)}, user_id=user_id)
    notify_staff("Drill scheduled", f"{title} · {_fmt(when, '%b %d, %I:%M %p')}", "info", "/modules/drills")
    return drill_id


@module_api
def update_drill(drill_id, data, user_id=None):
    drill = get_drill(drill_id)
    if not drill:
        raise ModuleError("Drill not found.")
    if drill["status_key"] not in ("planned", "cancelled"):
        raise ModuleError("Only planned drills can be edited.")
    title, dtype, when, team_id, checkpoint_id, location, notes = _drill_fields(data)
    _check_drill_conflict(when, team_id, exclude_id=drill_id)
    execute(
        """UPDATE drills SET title=%s, drill_type=%s, scheduled_at=%s, team_id=%s, checkpoint_id=%s,
           location=%s, notes=%s WHERE id=%s""",
        (title, dtype, _sql_ts(when), team_id, checkpoint_id, location or None, notes or None, drill_id),
    )
    write_audit("Drill updated", "drill", drill_id,
                old_values={"title": drill["title"], "at": drill["scheduled_label"]},
                new_values={"title": title, "at": _sql_ts(when)}, user_id=user_id)


@module_api
def transition_drill(drill_id, new_status, data=None, user_id=None):
    data = data or {}
    drill = get_drill(drill_id)
    if not drill:
        raise ModuleError("Drill not found.")
    new_status = (new_status or "").lower()
    if new_status not in DRILL_TRANSITIONS.get(drill["status_key"], set()):
        raise ModuleError(f"A {drill['status_label'].lower()} drill cannot be marked {new_status.replace('_', ' ')}.")

    updates, params = ["status=%s"], [new_status]
    note = _clean(data.get("notes"), 2000, "Notes")
    if new_status == "completed":
        participants = _int(data.get("participant_count"), "Participant count", 0, 100000, required=True)
        duration = _int(data.get("duration_minutes"), "Duration (minutes)", 1, 1440)
        score = _int(data.get("evaluation_score"), "Evaluation score", 0, 100)
        updates += ["participant_count=%s", "duration_minutes=%s", "evaluation_score=%s", "completed_at=%s"]
        params += [participants, duration, score, _sql_ts(datetime.now())]
    elif new_status == "cancelled":
        if not note:
            raise ModuleError("A reason is required to cancel a drill.")
    if note:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        merged = ((drill.get("notes") or "") + f"\n[{stamp}] {new_status.replace('_', ' ').title()}: {note}").strip()
        updates.append("notes=%s")
        params.append(merged)
    params.append(drill_id)
    execute(f"UPDATE drills SET {', '.join(updates)} WHERE id=%s", params)
    write_audit("Drill " + new_status.replace("_", " "), "drill", drill_id,
                old_values={"status": drill["status_key"]}, new_values={"status": new_status}, user_id=user_id)
    tone = "success" if new_status == "completed" else "info"
    notify_staff(f"Drill {new_status.replace('_', ' ')}", drill["title"], tone, "/modules/drills")


@module_api
def delete_drill(drill_id, user_id=None):
    drill = get_drill(drill_id)
    if not drill:
        raise ModuleError("Drill not found.")
    if drill["status_key"] not in ("planned", "cancelled"):
        raise ModuleError("Completed or in-progress drills are kept as compliance records and cannot be deleted.")
    execute("UPDATE emergency_roles SET drill_id=NULL WHERE drill_id=%s", (drill_id,))
    execute("DELETE FROM drills WHERE id=%s", (drill_id,))
    write_audit("Drill deleted", "drill", drill_id, old_values={"title": drill["title"]}, user_id=user_id)


# --------------------------------------------------------------------------- #
# 3. Evacuation Maps & Plans
# --------------------------------------------------------------------------- #
PLAN_MAP_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "webp"}
PLAN_MIME = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


def _private_folder(*parts):
    root = current_app.config.get("PRIVATE_UPLOAD_FOLDER") or os.path.join(current_app.instance_path, "private_uploads")
    path = os.path.join(root, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def _sniff_extension(head):
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def _store_plan_map(storage):
    """Validate (extension, size, magic bytes) and save a plan map. Returns (stored_name, original_name)."""
    original = secure_filename(storage.filename or "")
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in PLAN_MAP_EXTENSIONS:
        raise ModuleError("Plan maps must be PDF, PNG, JPG or WEBP files.")
    storage.stream.seek(0, os.SEEK_END)
    size = storage.stream.tell()
    storage.stream.seek(0)
    limit_mb = float(current_app.config.get("MAX_PLAN_MAP_MB") or 10)
    if size == 0:
        raise ModuleError("The uploaded map file is empty.")
    if size > limit_mb * 1024 * 1024:
        raise ModuleError(f"The map file exceeds the {int(limit_mb)} MB limit.")
    sniffed = _sniff_extension(storage.stream.read(16))
    storage.stream.seek(0)
    if sniffed is None or (sniffed != ("jpg" if ext in ("jpg", "jpeg") else ext)):
        raise ModuleError("The file content does not match its extension.")
    stored = f"{uuid.uuid4().hex}.{sniffed}"
    storage.save(os.path.join(_private_folder("plans"), stored))
    return stored, original


def _delete_plan_file(stored_name):
    if not stored_name:
        return
    try:
        os.remove(os.path.join(_private_folder("plans"), os.path.basename(stored_name)))
    except OSError:
        pass


def _plan_status(row):
    if row.get("active"):
        return "Current", "success"
    if row.get("approved_at"):
        return "Superseded", "warning"
    return "Pending approval", "info"


@module_api
def list_buildings():
    return query_all("SELECT id, building_code, building_name, floors FROM buildings WHERE active=1 ORDER BY building_name")


@module_api
def list_zones(assembly_only=False):
    sql = "SELECT id, zone_code, zone_name, zone_type FROM campus_zones WHERE active=1"
    if assembly_only:
        sql += " AND zone_type = 'assembly_area'"
    return query_all(sql + " ORDER BY zone_name")


@module_api
def list_plans(building_id=None, show_archived=True):
    sql = """SELECT ep.*, b.building_name, b.building_code, z.zone_name AS assembly_zone, u.full_name AS created_by_name
             FROM evacuation_plans ep
             JOIN buildings b ON b.id = ep.building_id
             LEFT JOIN campus_zones z ON z.id = ep.primary_assembly_zone_id
             LEFT JOIN users u ON u.id = ep.created_by
             WHERE 1=1"""
    params = []
    if building_id:
        sql += " AND ep.building_id = %s"
        params.append(building_id)
    if not show_archived:
        sql += " AND (ep.active = 1 OR ep.approved_at IS NULL)"
    sql += " ORDER BY b.building_name, ep.floor_label, ep.version_no DESC"
    rows = query_all(sql, params)
    for row in rows:
        row["status_label"], row["status_class"] = _plan_status(row)
        row["has_map"] = bool(row.get("map_file_path"))
    return rows


@module_api
def get_plan(plan_id):
    row = query_one(
        """SELECT ep.*, b.building_name FROM evacuation_plans ep JOIN buildings b ON b.id = ep.building_id WHERE ep.id=%s""",
        (plan_id,),
    )
    return row


def _unique_building_code(name):
    base = re.sub(r"[^A-Z0-9]", "", name.upper())[:10] or "BLDG"
    code, n = base, 1
    while query_one("SELECT id FROM buildings WHERE building_code=%s", (code,)):
        n += 1
        code = f"{base[:8]}{n}"
    return code


@module_api
def create_plan_version(data, map_file=None, user_id=None, approve=False):
    building_id = _opt_id(data.get("building_id"))
    new_name = _clean(data.get("new_building"), 150, "Building name")
    if building_id:
        building = query_one("SELECT id, building_name FROM buildings WHERE id=%s", (building_id,))
        if not building:
            raise ModuleError("Selected building does not exist.")
    elif new_name:
        building = query_one("SELECT id, building_name FROM buildings WHERE LOWER(building_name)=LOWER(%s)", (new_name,))
        if not building:
            bid, _ = execute(
                "INSERT INTO buildings (building_code, building_name, floors) VALUES (%s,%s,1)",
                (_unique_building_code(new_name), new_name),
            )
            building = {"id": bid, "building_name": new_name}
    else:
        raise ModuleError("Select a building or enter a new building name.")

    floor = _clean(data.get("floor_label"), 40, "Floor / area", required=True)
    zone_id = _opt_id(data.get("assembly_zone_id"))
    if not zone_id:
        raise ModuleError("Select the primary assembly area for this plan.")
    if not query_one("SELECT id FROM campus_zones WHERE id=%s", (zone_id,)):
        raise ModuleError("Selected assembly area does not exist.")
    plan_name = _clean(data.get("plan_name"), 180, "Plan name") or f"{building['building_name']} · {floor} Evacuation Plan"
    notes = _clean(data.get("notes"), 2000, "Evacuation notes")

    stored = original = None
    if map_file is not None and getattr(map_file, "filename", ""):
        stored, original = _store_plan_map(map_file)

    last = query_one(
        "SELECT MAX(version_no) AS v FROM evacuation_plans WHERE building_id=%s AND LOWER(floor_label)=LOWER(%s)",
        (building["id"], floor),
    )
    version = int((last or {}).get("v") or 0) + 1
    try:
        plan_id, _ = execute(
            """INSERT INTO evacuation_plans (building_id, floor_label, plan_name, map_file_path, map_original_name,
                   primary_assembly_zone_id, version_no, active, created_by, notes)
               VALUES (%s,%s,%s,%s,%s,%s,%s,0,%s,%s)""",
            (building["id"], floor, plan_name, stored, original, zone_id, version, user_id, notes or None),
        )
    except Exception:
        _delete_plan_file(stored)
        raise
    write_audit("Evacuation plan version created", "evacuation_plan", plan_id,
                new_values={"building": building["building_name"], "floor": floor, "version": version, "has_map": bool(stored)},
                user_id=user_id)
    if approve:
        approve_plan(plan_id, user_id=user_id)
    else:
        notify_staff("Evacuation plan awaiting approval", f"{plan_name} v{version}", "info", "/modules/plans")
    return plan_id


@module_api
def approve_plan(plan_id, user_id=None):
    plan = get_plan(plan_id)
    if not plan:
        raise ModuleError("Plan not found.")
    if plan.get("active"):
        raise ModuleError("This plan version is already current.")
    execute(
        "UPDATE evacuation_plans SET active=0 WHERE building_id=%s AND LOWER(floor_label)=LOWER(%s) AND id <> %s AND active=1",
        (plan["building_id"], plan["floor_label"], plan_id),
    )
    execute(
        "UPDATE evacuation_plans SET active=1, approved_at=%s, approved_by=%s WHERE id=%s",
        (_sql_ts(datetime.now()), user_id, plan_id),
    )
    write_audit("Evacuation plan approved", "evacuation_plan", plan_id,
                new_values={"version": plan["version_no"], "building": plan["building_name"]}, user_id=user_id)


@module_api
def delete_plan(plan_id, user_id=None):
    plan = get_plan(plan_id)
    if not plan:
        raise ModuleError("Plan not found.")
    if plan.get("active"):
        raise ModuleError("The current plan cannot be deleted. Approve a newer version first.")
    execute("DELETE FROM evacuation_plans WHERE id=%s", (plan_id,))
    _delete_plan_file(plan.get("map_file_path"))
    write_audit("Evacuation plan deleted", "evacuation_plan", plan_id,
                old_values={"building": plan["building_name"], "version": plan["version_no"]}, user_id=user_id)


@module_api
def plan_map_file(plan_id):
    """Return (absolute_path, download_name, mimetype) or None."""
    plan = get_plan(plan_id)
    if not plan or not plan.get("map_file_path"):
        return None
    stored = os.path.basename(plan["map_file_path"])
    path = os.path.join(_private_folder("plans"), stored)
    if not os.path.isfile(path):
        return None
    ext = stored.rsplit(".", 1)[-1].lower()
    return path, plan.get("map_original_name") or stored, PLAN_MIME.get(ext, "application/octet-stream")


@module_api
def plan_coverage():
    """How many active buildings have a current (approved) plan."""
    buildings = query_one("SELECT COUNT(*) AS c FROM buildings WHERE active=1") or {"c": 0}
    covered = query_one(
        "SELECT COUNT(DISTINCT building_id) AS c FROM evacuation_plans WHERE active=1"
    ) or {"c": 0}
    pending = query_one("SELECT COUNT(*) AS c FROM evacuation_plans WHERE active=0 AND approved_at IS NULL") or {"c": 0}
    total, have = int(buildings["c"]), int(covered["c"])
    return {"buildings": total, "covered": have, "pending": int(pending["c"]), "percent": round(have / total * 100) if total else 0}


# --------------------------------------------------------------------------- #
# 4. Safety Inspection Checklist
# --------------------------------------------------------------------------- #
CHECKLIST_TEMPLATES = {
    "Fire safety": [
        ("Fire extinguishers present, charged and accessible", "Equipment"),
        ("Extinguisher inspection tags are current", "Equipment"),
        ("Fire alarm panel and pull stations functional", "Alarm systems"),
        ("Smoke / heat detectors operational", "Alarm systems"),
        ("Emergency exits unlocked and unobstructed", "Egress"),
        ("Exit signs and emergency lighting working", "Egress"),
        ("Fire hose reels / sprinklers accessible", "Equipment"),
        ("Evacuation plan posted at exits and corridors", "Documentation"),
        ("Fire doors close and latch properly", "Egress"),
    ],
    "Access control": [
        ("Gates, locks and barriers functional", "Physical security"),
        ("CCTV cameras operating and recording", "Surveillance"),
        ("Perimeter fence and gates intact", "Physical security"),
        ("Perimeter and entrance lighting working", "Physical security"),
        ("Visitor log / pass procedure followed", "Procedures"),
        ("Guard post staffed and logbook up to date", "Procedures"),
        ("ID verification performed at entrances", "Procedures"),
        ("Restricted rooms (labs, server room) locked", "Physical security"),
    ],
    "First aid and supplies": [
        ("First-aid kit present and accessible", "Supplies"),
        ("Kit contents complete against the inventory list", "Supplies"),
        ("No expired medical supplies", "Supplies"),
        ("Eye-wash station / AED available and serviceable", "Equipment"),
        ("Emergency contact list posted near the kit", "Documentation"),
        ("Trained first aider identified for the area", "Personnel"),
        ("Clinic / stretcher access route is clear", "Egress"),
    ],
    "General safety": [
        ("Walkways free of spills, clutter and trip hazards", "Housekeeping"),
        ("Stair handrails secure and steps in good condition", "Structure"),
        ("Electrical panels accessible and covered", "Electrical"),
        ("No exposed wiring or overloaded outlets", "Electrical"),
        ("Adequate ventilation and lighting", "Environment"),
        ("Hazardous materials stored and labelled correctly", "Hazardous materials"),
        ("Assembly-area signage visible", "Documentation"),
        ("Emergency numbers displayed", "Documentation"),
    ],
}
INSPECTION_TYPES = list(CHECKLIST_TEMPLATES)
ITEM_RESULTS = ("pending", "pass", "fail", "na")
INSPECTION_STATUS_CLASS = {
    "scheduled": "info", "in_progress": "warning", "completed": "success", "overdue": "danger", "cancelled": "danger",
}


def _template_for(insp_type):
    for key in CHECKLIST_TEMPLATES:
        if key.lower() == (insp_type or "").strip().lower():
            return key, CHECKLIST_TEMPLATES[key]
    return "General safety", CHECKLIST_TEMPLATES["General safety"]


@module_api
def create_inspection_items(inspection_id, insp_type):
    """Seed the checklist for an inspection (no-op when it already has items)."""
    if query_one("SELECT id FROM inspection_items WHERE inspection_id=%s LIMIT 1", (inspection_id,)):
        return 0
    _, items = _template_for(insp_type)
    for name, category in items:
        execute(
            "INSERT INTO inspection_items (inspection_id, item_name, category, result) VALUES (%s,%s,%s,'pending')",
            (inspection_id, name, category),
        )
    return len(items)


def _next_inspection_code():
    year = datetime.now().year
    prefix = f"INS-{year}-"
    rows = query_all("SELECT inspection_code FROM safety_inspections WHERE inspection_code LIKE %s", (prefix + "%",))
    highest = 0
    for row in rows:
        try:
            highest = max(highest, int(str(row["inspection_code"]).rsplit("-", 1)[-1]))
        except ValueError:
            continue
    return f"{prefix}{highest + 1:03d}"


@module_api
def schedule_inspection(data, user_id=None):
    zone_id = _opt_id(data.get("zone_id"))
    if not zone_id or not query_one("SELECT id FROM campus_zones WHERE id=%s", (zone_id,)):
        raise ModuleError("Select a valid campus zone.")
    when = as_date(data.get("scheduled_date"))
    if not when:
        raise ModuleError("A valid scheduled date is required.")
    template_key, _ = _template_for(data.get("inspection_type"))
    team_id = _opt_id(data.get("team_id"))
    if team_id and not query_one("SELECT id FROM patrol_teams WHERE id=%s", (team_id,)):
        raise ModuleError("Selected team does not exist.")
    checkpoint_id = _opt_id(data.get("checkpoint_id"))
    if checkpoint_id and not query_one("SELECT id FROM checkpoints WHERE id=%s", (checkpoint_id,)):
        raise ModuleError("Selected checkpoint does not exist.")
    inspector_id = _opt_id(data.get("inspector_id")) or user_id
    code = _next_inspection_code()
    inspection_id, _ = execute(
        """INSERT INTO safety_inspections (inspection_code, zone_id, checkpoint_id, team_id, inspector_id,
                                           scheduled_date, status, notes)
           VALUES (%s,%s,%s,%s,%s,%s,'scheduled',%s)""",
        (code, zone_id, checkpoint_id, team_id, inspector_id, when.isoformat(), template_key),
    )
    create_inspection_items(inspection_id, template_key)
    write_audit("Inspection scheduled", "inspection", inspection_id,
                new_values={"code": code, "type": template_key, "date": when.isoformat()}, user_id=user_id)
    notify_staff("Inspection scheduled", f"{code}: {template_key}", "info", "/modules/inspections")
    return inspection_id


@module_api
def refresh_overdue_inspections():
    """Flag open inspections whose date has passed. Returns number changed."""
    _, changed = execute(
        "UPDATE safety_inspections SET status='overdue' WHERE status IN ('scheduled','in_progress') AND scheduled_date < %s",
        (_today().isoformat(),),
    )
    return changed or 0


_INSPECTION_SELECT = """
    SELECT si.*, z.zone_name, t.team_name, u.full_name AS inspector_name,
           (SELECT COUNT(*) FROM inspection_items i WHERE i.inspection_id = si.id) AS item_total,
           (SELECT COUNT(*) FROM inspection_items i WHERE i.inspection_id = si.id AND i.result = 'pass') AS item_pass,
           (SELECT COUNT(*) FROM inspection_items i WHERE i.inspection_id = si.id AND i.result = 'fail') AS item_fail,
           (SELECT COUNT(*) FROM inspection_items i WHERE i.inspection_id = si.id AND i.result = 'pending') AS item_pending,
           (SELECT COUNT(*) FROM inspection_items i WHERE i.inspection_id = si.id AND i.result = 'fail'
                AND COALESCE(i.action_done, 0) = 0) AS open_actions
    FROM safety_inspections si
    JOIN campus_zones z ON z.id = si.zone_id
    LEFT JOIN patrol_teams t ON t.id = si.team_id
    LEFT JOIN users u ON u.id = si.inspector_id
"""


def _decorate_inspection(row):
    row["status_class"] = INSPECTION_STATUS_CLASS.get(row.get("status"), "warning")
    row["status_label"] = (row.get("status") or "").replace("_", " ").title()
    row["type_label"] = row.get("notes") or "General safety"
    row["scheduled_label"] = _fmt(row.get("scheduled_date"))
    row["progress"] = round((int(row["item_total"]) - int(row["item_pending"])) / int(row["item_total"]) * 100) if int(row.get("item_total") or 0) else 0
    for key in ("item_total", "item_pass", "item_fail", "item_pending", "open_actions"):
        row[key] = int(row.get(key) or 0)
    return row


@module_api
def list_inspections(status=None, zone_id=None):
    refresh_overdue_inspections()
    sql = _INSPECTION_SELECT + " WHERE 1=1"
    params = []
    if status in INSPECTION_STATUS_CLASS:
        sql += " AND si.status = %s"
        params.append(status)
    if zone_id:
        sql += " AND si.zone_id = %s"
        params.append(zone_id)
    sql += " ORDER BY si.scheduled_date DESC, si.id DESC"
    return [_decorate_inspection(r) for r in query_all(sql, params)]


@module_api
def get_inspection(inspection_ref):
    """Fetch by numeric id or inspection code, with checklist items grouped by category."""
    refresh_overdue_inspections()
    ref = str(inspection_ref)
    row = query_one(_INSPECTION_SELECT + " WHERE si.id = %s" if ref.isdigit() else _INSPECTION_SELECT + " WHERE si.inspection_code = %s",
                    (int(ref) if ref.isdigit() else ref,))
    if not row:
        return None
    _decorate_inspection(row)
    if not row["item_total"] and row["status"] not in ("completed", "cancelled"):
        create_inspection_items(row["id"], row.get("notes"))
        row = _decorate_inspection(query_one(_INSPECTION_SELECT + " WHERE si.id = %s", (row["id"],)))
    items = query_all("SELECT * FROM inspection_items WHERE inspection_id=%s ORDER BY category, id", (row["id"],))
    groups = {}
    for item in items:
        item["action_done"] = bool(item.get("action_done"))
        groups.setdefault(item.get("category") or "General", []).append(item)
    row["items"] = items
    row["groups"] = groups
    row["editable"] = row["status"] not in ("completed", "cancelled")
    return row


def _get_item(inspection_id, item_id):
    item = query_one("SELECT * FROM inspection_items WHERE id=%s AND inspection_id=%s", (item_id, inspection_id))
    if not item:
        raise ModuleError("Checklist item not found.")
    return item


def _require_editable(inspection_id):
    insp = query_one("SELECT id, status, inspection_code FROM safety_inspections WHERE id=%s", (inspection_id,))
    if not insp:
        raise ModuleError("Inspection not found.")
    if insp["status"] in ("completed", "cancelled"):
        raise ModuleError("This inspection is closed and can no longer be edited.")
    return insp


@module_api
def record_item_result(inspection_id, item_id, result, finding="", corrective_action="", user_id=None):
    insp = _require_editable(inspection_id)
    item = _get_item(inspection_id, item_id)
    result = (result or "").lower()
    if result not in ITEM_RESULTS:
        raise ModuleError("Invalid checklist result.")
    finding = _clean(finding, 1000, "Finding")
    action = _clean(corrective_action, 1000, "Corrective action")
    if result == "fail" and not finding:
        raise ModuleError("Describe the finding for any failed item.")
    if result != "fail":
        finding = finding if result == "na" else ""
        action = ""
    execute(
        "UPDATE inspection_items SET result=%s, finding=%s, corrective_action=%s, action_done=0 WHERE id=%s",
        (result, finding or None, action or None, item_id),
    )
    if insp["status"] == "scheduled":
        execute("UPDATE safety_inspections SET status='in_progress' WHERE id=%s", (inspection_id,))
    write_audit("Inspection item recorded", "inspection_item", item_id,
                old_values={"result": item["result"]}, new_values={"result": result, "inspection": insp["inspection_code"]},
                user_id=user_id)


@module_api
def add_inspection_item(inspection_id, name, category="Custom", user_id=None):
    _require_editable(inspection_id)
    name = _clean(name, 180, "Item description", required=True)
    category = _clean(category, 100, "Category") or "Custom"
    item_id, _ = execute(
        "INSERT INTO inspection_items (inspection_id, item_name, category, result) VALUES (%s,%s,%s,'pending')",
        (inspection_id, name, category),
    )
    write_audit("Inspection item added", "inspection_item", item_id, new_values={"name": name}, user_id=user_id)
    return item_id


@module_api
def delete_inspection_item(inspection_id, item_id, user_id=None):
    _require_editable(inspection_id)
    item = _get_item(inspection_id, item_id)
    execute("DELETE FROM inspection_items WHERE id=%s", (item_id,))
    write_audit("Inspection item removed", "inspection_item", item_id, old_values={"name": item["item_name"]}, user_id=user_id)


@module_api
def set_action_done(inspection_id, item_id, done, user_id=None):
    insp = query_one("SELECT id, status FROM safety_inspections WHERE id=%s", (inspection_id,))
    if not insp:
        raise ModuleError("Inspection not found.")
    item = _get_item(inspection_id, item_id)
    if item["result"] != "fail":
        raise ModuleError("Only failed items have corrective actions.")
    execute("UPDATE inspection_items SET action_done=%s WHERE id=%s", (1 if done else 0, item_id))
    write_audit("Corrective action " + ("closed" if done else "reopened"), "inspection_item", item_id, user_id=user_id)


@module_api
def complete_inspection(inspection_id, summary="", user_id=None, raise_risks=True):
    insp = _require_editable(inspection_id)
    summary = _clean(summary, 2000, "Summary")
    full = query_one("SELECT zone_id FROM safety_inspections WHERE id=%s", (inspection_id,))
    items = query_all("SELECT * FROM inspection_items WHERE inspection_id=%s", (inspection_id,))
    if not items:
        raise ModuleError("Add at least one checklist item before completing the inspection.")
    pending = [i for i in items if i["result"] == "pending"]
    if pending:
        raise ModuleError(f"{len(pending)} checklist item(s) still need a result.")
    passed = sum(1 for i in items if i["result"] == "pass")
    failed = [i for i in items if i["result"] == "fail"]
    scored = passed + len(failed)
    score = round(passed / scored * 100, 2) if scored else 100.0
    execute(
        "UPDATE safety_inspections SET status='completed', completed_at=%s, overall_score=%s, summary=%s WHERE id=%s",
        (_sql_ts(datetime.now()), score, summary or None, inspection_id),
    )
    risks_created = 0
    if raise_risks:
        for item in failed:
            title = f"{insp['inspection_code']}: {item['item_name']}"[:180]
            if query_one(
                "SELECT id FROM risk_assessments WHERE zone_id=%s AND hazard_title=%s AND status IN ('open','mitigating')",
                (full["zone_id"], title),
            ):
                continue
            execute(
                """INSERT INTO risk_assessments (zone_id, assessed_by, hazard_title, hazard_description, likelihood,
                                                 severity, risk_score, mitigation_plan, status)
                   VALUES (%s,%s,%s,%s,3,3,9,%s,'open')""",
                (full["zone_id"], user_id, title, item.get("finding"), item.get("corrective_action")),
            )
            risks_created += 1
    write_audit("Inspection completed", "inspection", inspection_id,
                new_values={"score": score, "failed": len(failed), "risks_created": risks_created}, user_id=user_id)
    tone = "success" if not failed else "warning"
    notify_staff(f"Inspection {insp['inspection_code']} completed",
                 f"Score {score:.0f}% · {len(failed)} failed item(s)", tone, f"/modules/inspections/{inspection_id}")
    return {"score": score, "failed": len(failed), "risks_created": risks_created}


@module_api
def cancel_inspection(inspection_id, reason, user_id=None):
    insp = _require_editable(inspection_id)
    reason = _clean(reason, 500, "Reason", required=True)
    execute("UPDATE safety_inspections SET status='cancelled', summary=%s WHERE id=%s", (reason, inspection_id))
    write_audit("Inspection cancelled", "inspection", inspection_id, new_values={"code": insp["inspection_code"], "reason": reason}, user_id=user_id)


# --------------------------------------------------------------------------- #
# 5. Risk Assessment Tool
# --------------------------------------------------------------------------- #
LIKELIHOOD_LABELS = {1: "Rare", 2: "Unlikely", 3: "Possible", 4: "Likely", 5: "Almost certain"}
SEVERITY_LABELS = {1: "Negligible", 2: "Minor", 3: "Moderate", 4: "Major", 5: "Catastrophic"}
RISK_STATUSES = ("open", "mitigating", "resolved", "accepted")
RISK_TRANSITIONS = {
    "open": {"mitigating", "resolved", "accepted"},
    "mitigating": {"open", "resolved", "accepted"},
    "resolved": {"open"},
    "accepted": {"open", "mitigating"},
}


def risk_level(score):
    """Map a likelihood x severity score (1-25) to ``(label, css_class)``."""
    score = float(score or 0)
    if score >= 15:
        return "Critical", "danger"
    if score >= 10:
        return "High", "danger"
    if score >= 5:
        return "Medium", "warning"
    return "Low", "success"


def _decorate_risk(row):
    row["level"], row["level_class"] = risk_level(row.get("risk_score"))
    row["score"] = int(float(row.get("risk_score") or 0))
    row["likelihood_label"] = LIKELIHOOD_LABELS.get(int(row.get("likelihood") or 0), "")
    row["severity_label"] = SEVERITY_LABELS.get(int(row.get("severity") or 0), "")
    row["next_statuses"] = sorted(RISK_TRANSITIONS.get(row.get("status"), set()))
    row["created_label"] = _fmt(row.get("created_at"))
    row["reviewed_label"] = _fmt(row.get("reviewed_at"))
    return row


_RISK_SELECT = """
    SELECT ra.*, z.zone_name, u.full_name AS assessor_name
    FROM risk_assessments ra
    JOIN campus_zones z ON z.id = ra.zone_id
    LEFT JOIN users u ON u.id = ra.assessed_by
"""


@module_api
def list_risks(status=None, level=None, zone_id=None):
    sql = _RISK_SELECT + " WHERE 1=1"
    params = []
    if status in RISK_STATUSES:
        sql += " AND ra.status = %s"
        params.append(status)
    if zone_id:
        sql += " AND ra.zone_id = %s"
        params.append(zone_id)
    sql += " ORDER BY ra.risk_score DESC, ra.id DESC"
    rows = [_decorate_risk(r) for r in query_all(sql, params)]
    if level:
        rows = [r for r in rows if r["level"].lower() == level.lower()]
    return rows


@module_api
def get_risk(risk_id):
    row = query_one(_RISK_SELECT + " WHERE ra.id=%s", (risk_id,))
    return _decorate_risk(row) if row else None


def _risk_inputs(data):
    zone_id = _opt_id(data.get("zone_id"))
    if not zone_id or not query_one("SELECT id FROM campus_zones WHERE id=%s", (zone_id,)):
        raise ModuleError("Select a valid campus zone.")
    title = _clean(data.get("hazard_title"), 180, "Hazard title", required=True)
    description = _clean(data.get("hazard_description"), 2000, "Hazard description")
    likelihood = _int(data.get("likelihood"), "Likelihood", 1, 5, required=True)
    severity = _int(data.get("severity"), "Severity", 1, 5, required=True)
    mitigation = _clean(data.get("mitigation_plan"), 2000, "Mitigation plan")
    return zone_id, title, description, likelihood, severity, mitigation


@module_api
def create_risk(data, user_id=None):
    zone_id, title, description, likelihood, severity, mitigation = _risk_inputs(data)
    score = likelihood * severity
    risk_id, _ = execute(
        """INSERT INTO risk_assessments (zone_id, assessed_by, hazard_title, hazard_description, likelihood,
                                         severity, risk_score, mitigation_plan, status)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'open')""",
        (zone_id, user_id, title, description or None, likelihood, severity, score, mitigation or None),
    )
    level, _css = risk_level(score)
    write_audit("Risk assessment created", "risk_assessment", risk_id,
                new_values={"title": title, "score": score, "level": level}, user_id=user_id)
    if score >= 15:
        notify_staff("Critical risk identified", f"{title} · score {score}", "danger", "/modules/risks")
    return risk_id


@module_api
def update_risk(risk_id, data, user_id=None):
    risk = get_risk(risk_id)
    if not risk:
        raise ModuleError("Risk assessment not found.")
    zone_id, title, description, likelihood, severity, mitigation = _risk_inputs(data)
    score = likelihood * severity
    execute(
        """UPDATE risk_assessments SET zone_id=%s, hazard_title=%s, hazard_description=%s, likelihood=%s,
           severity=%s, risk_score=%s, mitigation_plan=%s WHERE id=%s""",
        (zone_id, title, description or None, likelihood, severity, score, mitigation or None, risk_id),
    )
    write_audit("Risk assessment updated", "risk_assessment", risk_id,
                old_values={"score": risk["score"]}, new_values={"score": score}, user_id=user_id)
    if score >= 15 > risk["score"]:
        notify_staff("Critical risk identified", f"{title} · score {score}", "danger", "/modules/risks")


@module_api
def set_risk_status(risk_id, new_status, note="", user_id=None):
    risk = get_risk(risk_id)
    if not risk:
        raise ModuleError("Risk assessment not found.")
    new_status = (new_status or "").lower()
    if new_status not in RISK_TRANSITIONS.get(risk["status"], set()):
        raise ModuleError(f"A {risk['status']} risk cannot be moved to {new_status or 'that status'}.")
    note = _clean(note, 1000, "Note")
    if new_status in ("mitigating", "resolved") and not (risk.get("mitigation_plan") or note):
        raise ModuleError("Record a mitigation plan or note before moving this risk forward.")
    if new_status == "accepted" and not note:
        raise ModuleError("A justification is required to accept a risk.")
    plan = risk.get("mitigation_plan") or ""
    if note:
        plan = (plan + f"\n[{datetime.now().strftime('%Y-%m-%d')}] {new_status.title()}: {note}").strip()
    reviewed = _sql_ts(datetime.now()) if new_status in ("resolved", "accepted", "mitigating") else None
    execute(
        "UPDATE risk_assessments SET status=%s, mitigation_plan=%s, reviewed_at=COALESCE(%s, reviewed_at) WHERE id=%s",
        (new_status, plan or None, reviewed, risk_id),
    )
    write_audit("Risk status changed", "risk_assessment", risk_id,
                old_values={"status": risk["status"]}, new_values={"status": new_status}, user_id=user_id)


@module_api
def risk_matrix():
    """5x5 grid (rows = likelihood 5..1, columns = severity 1..5) of open/mitigating risks."""
    rows = query_all(
        "SELECT likelihood, severity, COUNT(*) AS c FROM risk_assessments WHERE status IN ('open','mitigating') GROUP BY likelihood, severity"
    )
    counts = {(int(r["likelihood"]), int(r["severity"])): int(r["c"]) for r in rows}
    grid = []
    for likelihood in range(5, 0, -1):
        grid.append([
            {"likelihood": likelihood, "severity": s, "count": counts.get((likelihood, s), 0),
             "level": risk_level(likelihood * s)[0].lower()}
            for s in range(1, 6)
        ])
    return grid


@module_api
def risk_summary():
    rows = list_risks()
    active = [r for r in rows if r["status"] in ("open", "mitigating")]
    levels = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    for r in active:
        levels[r["level"]] += 1
    return {"total": len(rows), "active": len(active), "levels": levels,
            "resolved": len([r for r in rows if r["status"] == "resolved"]),
            "accepted": len([r for r in rows if r["status"] == "accepted"])}


# --------------------------------------------------------------------------- #
# 6. Parent Notification System
# --------------------------------------------------------------------------- #
NOTIFICATION_CHANNELS = [("email", "Email"), ("sms", "SMS"), ("push", "Push (not yet available)")]
NOTIFICATION_STATUS_CLASS = {"queued": "warning", "sent": "success", "failed": "danger"}
MESSAGE_TEMPLATES = {
    "general": "Good day. This is the BCP Office of Student Affairs and Services. We are informing you about an incident "
               "involving {student} ({incident}). Please contact the OSAS office for details.",
    "medical": "Good day. {student} received first-aid attention on campus ({incident}) and is being looked after by our "
               "staff. Please contact OSAS as soon as you receive this message.",
    "safety": "Good day. A safety incident occurred on campus ({incident}). {student} is safe. We will share updates as they "
              "become available.",
    "dismissal": "Good day. Because of an emergency on campus ({incident}), classes are being dismissed early. Please "
                 "arrange to fetch {student} at the school gate.",
    "drill": "Good day. The campus will hold an emergency drill. Please do not be alarmed; no action is required.",
}
_MAX_SMS_LENGTH = 480


def _email_provider():
    cfg = current_app.config
    if cfg.get("RESEND_API_KEY"):
        return "resend"
    if cfg.get("SMTP_HOST"):
        return "smtp"
    return None


def _deliver_email(to_addr, subject, body):
    import requests  # local import: only needed when sending

    cfg = current_app.config
    if cfg.get("RESEND_API_KEY"):
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {cfg['RESEND_API_KEY']}"},
            json={"from": cfg.get("RESEND_FROM"), "to": [to_addr], "subject": subject, "text": body},
            timeout=10,
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"Email provider rejected the message (HTTP {resp.status_code}).")
        return
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.get("SMTP_FROM") or cfg.get("SMTP_USER")
    msg["To"] = to_addr
    msg.set_content(body)
    port = int(cfg.get("SMTP_PORT", 587))
    with smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=10) as smtp:
        smtp.ehlo()
        if port != 25:
            smtp.starttls()
            smtp.ehlo()
        if cfg.get("SMTP_USER"):
            smtp.login(cfg["SMTP_USER"], cfg.get("SMTP_PASSWORD", ""))
        smtp.send_message(msg)


def _deliver_sms(phone, message):
    import requests

    resp = requests.post(
        current_app.config["SMS_PROVIDER_URL"],
        json={"to": phone, "message": message},
        headers={"Authorization": f"Bearer {current_app.config.get('SMS_API_KEY', '')}"},
        timeout=8,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"SMS provider rejected the message (HTTP {resp.status_code}).")


@module_api
def deliver_notification(notification_id):
    """Attempt delivery. Returns the resulting status: ``sent`` / ``failed`` / ``queued``."""
    row = query_one("SELECT * FROM parent_notifications WHERE id=%s", (notification_id,))
    if not row:
        raise ModuleError("Notification not found.")
    if row["status"] == "sent":
        return "sent"
    channel = row["channel"]
    status, error = "queued", None
    attempts = int(row.get("attempts") or 0)
    try:
        if channel == "email":
            if not _email_provider():
                error = "No email provider is configured (set RESEND_API_KEY or SMTP_HOST)."
            else:
                attempts += 1
                _deliver_email(row["recipient_email"], f"OSAS notice · {row.get('student_name') or 'Campus update'}", row["message"])
                status = "sent"
        elif channel == "sms":
            if not current_app.config.get("SMS_PROVIDER_URL"):
                error = "No SMS provider is configured (set SMS_PROVIDER_URL)."
            else:
                attempts += 1
                _deliver_sms(row["recipient_phone"], row["message"])
                status = "sent"
        else:
            error = "Push notifications are not available yet."
    except Exception as exc:  # delivery errors must never break the request
        status, error = "failed", str(exc)[:480] or exc.__class__.__name__
        logger.warning("Parent notification %s failed: %s", notification_id, error)
    execute(
        "UPDATE parent_notifications SET status=%s, error_message=%s, attempts=%s, sent_at=%s WHERE id=%s",
        (status, error, attempts, _sql_ts(datetime.now()) if status == "sent" else None, notification_id),
    )
    write_audit("Parent notification " + status, "parent_notification", notification_id,
                new_values={"channel": channel, "attempts": attempts}, user_id=None)
    return status


@module_api
def send_parent_notification(data, user_id=None):
    recipient = _clean(data.get("recipient_name"), 150, "Recipient name", required=True)
    student = _clean(data.get("student_name"), 150, "Student name")
    channel = (data.get("channel") or "email").lower()
    if channel not in {c for c, _ in NOTIFICATION_CHANNELS}:
        raise ModuleError("Select a valid channel.")
    message = _clean(data.get("message"), 1000, "Message", required=True)
    email = _email(data.get("email"), "Recipient email")
    phone = _phone(data.get("phone"), "Recipient phone", required=False)
    if channel == "email" and not email:
        raise ModuleError("A recipient email is required for email notifications.")
    if channel == "sms":
        if not phone:
            raise ModuleError("A recipient phone number is required for SMS notifications.")
        if len(message) > _MAX_SMS_LENGTH:
            raise ModuleError(f"SMS messages are limited to {_MAX_SMS_LENGTH} characters.")
    incident_code = _clean(data.get("incident_code"), 30, "Incident code")
    incident_id = None
    if incident_code:
        incident = query_one("SELECT id FROM incidents WHERE incident_code=%s", (incident_code,))
        if not incident:
            raise ModuleError(f"Incident {incident_code} was not found.")
        incident_id = incident["id"]
    nid, _ = execute(
        """INSERT INTO parent_notifications (incident_id, recipient_name, student_name, recipient_phone,
                                             recipient_email, channel, message, status, attempts)
           VALUES (%s,%s,%s,%s,%s,%s,%s,'queued',0)""",
        (incident_id, recipient, student or None, phone or None, email or None, channel, message),
    )
    write_audit("Parent notification queued", "parent_notification", nid,
                new_values={"channel": channel, "incident": incident_code or None}, user_id=user_id)
    return nid, deliver_notification(nid)


@module_api
def retry_notification(notification_id, user_id=None):
    row = query_one("SELECT status FROM parent_notifications WHERE id=%s", (notification_id,))
    if not row:
        raise ModuleError("Notification not found.")
    if row["status"] == "sent":
        raise ModuleError("This notification has already been sent.")
    return deliver_notification(notification_id)


@module_api
def retry_pending_notifications():
    ids = [r["id"] for r in query_all("SELECT id FROM parent_notifications WHERE status IN ('queued','failed') ORDER BY id")]
    results = {"sent": 0, "failed": 0, "queued": 0}
    for nid in ids:
        results[deliver_notification(nid)] += 1
    return results


@module_api
def list_notifications(status=None, channel=None, search=None):
    sql = """SELECT pn.*, i.incident_code, i.title AS incident_title
             FROM parent_notifications pn LEFT JOIN incidents i ON i.id = pn.incident_id WHERE 1=1"""
    params = []
    if status in NOTIFICATION_STATUS_CLASS:
        sql += " AND pn.status = %s"
        params.append(status)
    if channel in {c for c, _ in NOTIFICATION_CHANNELS}:
        sql += " AND pn.channel = %s"
        params.append(channel)
    term = (search or "").strip()
    if term:
        like = f"%{term}%"
        sql += " AND (pn.recipient_name LIKE %s OR pn.student_name LIKE %s OR i.incident_code LIKE %s)"
        params.extend([like] * 3)
    sql += " ORDER BY pn.id DESC"
    rows = query_all(sql, params)
    for row in rows:
        row["status_class"] = NOTIFICATION_STATUS_CLASS.get(row["status"], "warning")
        row["created_label"] = _fmt(row.get("created_at"), "%b %d, %Y %I:%M %p")
        row["sent_label"] = _fmt(row.get("sent_at"), "%b %d, %Y %I:%M %p")
    return rows


@module_api
def notification_summary():
    rows = query_all("SELECT status, COUNT(*) AS c FROM parent_notifications GROUP BY status")
    counts = {r["status"]: int(r["c"]) for r in rows}
    total = sum(counts.values())
    return {"total": total, "sent": counts.get("sent", 0), "queued": counts.get("queued", 0),
            "failed": counts.get("failed", 0),
            "delivery_rate": round(counts.get("sent", 0) / total * 100) if total else 0,
            "email_ready": _email_provider() is not None,
            "sms_ready": bool(current_app.config.get("SMS_PROVIDER_URL"))}


# --------------------------------------------------------------------------- #
# 7. Emergency Role Assignment
# --------------------------------------------------------------------------- #
_ROLE_SELECT = """
    SELECT er.*, u.full_name AS assigned_name, u.role AS assigned_role, u.email AS assigned_email,
           b.full_name AS backup_name, d.title AS drill_title
    FROM emergency_roles er
    LEFT JOIN users u ON u.id = er.assigned_user_id
    LEFT JOIN users b ON b.id = er.backup_user_id
    LEFT JOIN drills d ON d.id = er.drill_id
"""


@module_api
def list_roles(drill_id=None, scope=None):
    sql = _ROLE_SELECT + " WHERE er.active = 1"
    params = []
    if drill_id:
        sql += " AND er.drill_id = %s"
        params.append(drill_id)
    elif scope == "standing":
        sql += " AND er.drill_id IS NULL"
    sql += " ORDER BY er.drill_id IS NOT NULL, er.role_name"
    rows = query_all(sql, params)
    for row in rows:
        row["is_assigned"] = bool(row.get("assigned_user_id"))
        row["assigned_label"] = _fmt(row.get("assigned_at"))
    return rows


@module_api
def list_assignable_users():
    return query_all(
        "SELECT id, full_name, role, campus_id FROM users WHERE account_status='active' ORDER BY full_name"
    )


def _validate_user(user_id, label):
    if user_id is None:
        return None
    user = query_one("SELECT id, full_name, account_status FROM users WHERE id=%s", (user_id,))
    if not user or user["account_status"] != "active":
        raise ModuleError(f"{label} must be an active user.")
    return user


@module_api
def create_role(data, user_id=None):
    name = _clean(data.get("role_name"), 120, "Role name", required=True)
    responsibility = _clean(data.get("responsibility"), 1000, "Responsibility", required=True)
    drill_id = _opt_id(data.get("drill_id"))
    if drill_id and not query_one("SELECT id FROM drills WHERE id=%s", (drill_id,)):
        raise ModuleError("Selected drill does not exist.")
    if query_one("SELECT id FROM emergency_roles WHERE LOWER(role_name)=LOWER(%s) AND active=1 AND COALESCE(drill_id,0)=%s",
                 (name, drill_id or 0)):
        raise ModuleError("That role already exists for the selected scope.")
    role_id, _ = execute(
        "INSERT INTO emergency_roles (role_name, responsibility, drill_id, active) VALUES (%s,%s,%s,1)",
        (name, responsibility, drill_id),
    )
    write_audit("Emergency role created", "emergency_role", role_id, new_values={"name": name, "drill_id": drill_id}, user_id=user_id)
    return role_id


@module_api
def assign_role(role_id, assigned_user_id, backup_user_id=None, user_id=None):
    role = query_one("SELECT * FROM emergency_roles WHERE id=%s AND active=1", (role_id,))
    if not role:
        raise ModuleError("Role not found.")
    primary = _opt_id(assigned_user_id)
    backup = _opt_id(backup_user_id)
    if primary is None and backup is not None:
        raise ModuleError("Assign a primary person before choosing a backup.")
    if primary is not None and primary == backup:
        raise ModuleError("The backup must be a different person from the primary assignee.")
    primary_user = _validate_user(primary, "Primary assignee")
    _validate_user(backup, "Backup assignee")
    execute(
        "UPDATE emergency_roles SET assigned_user_id=%s, backup_user_id=%s, assigned_at=%s WHERE id=%s",
        (primary, backup, _sql_ts(datetime.now()) if primary else None, role_id),
    )
    write_audit("Emergency role " + ("assigned" if primary else "unassigned"), "emergency_role", role_id,
                old_values={"assigned_user_id": role.get("assigned_user_id")},
                new_values={"assigned_user_id": primary, "backup_user_id": backup}, user_id=user_id)
    if primary_user and primary != role.get("assigned_user_id"):
        notify_patron(primary, "Emergency role assigned", f"You are the {role['role_name']}. {role['responsibility']}", "info", "/modules/roles")
    if backup and backup != role.get("backup_user_id"):
        notify_patron(backup, "Emergency backup role", f"You are the backup {role['role_name']}.", "info", "/modules/roles")


@module_api
def delete_role(role_id, user_id=None):
    role = query_one("SELECT * FROM emergency_roles WHERE id=%s", (role_id,))
    if not role:
        raise ModuleError("Role not found.")
    execute("DELETE FROM emergency_roles WHERE id=%s", (role_id,))
    write_audit("Emergency role deleted", "emergency_role", role_id, old_values={"name": role["role_name"]}, user_id=user_id)


@module_api
def role_coverage():
    roles = list_roles()
    assigned = [r for r in roles if r["is_assigned"]]
    no_backup = [r for r in assigned if not r.get("backup_user_id")]
    per_user = {}
    for r in assigned:
        per_user.setdefault(r["assigned_user_id"], []).append(r["role_name"])
    overloaded = {uid: names for uid, names in per_user.items() if len(names) >= 3}
    return {"total": len(roles), "assigned": len(assigned), "unassigned": len(roles) - len(assigned),
            "without_backup": len(no_backup), "overloaded": len(overloaded),
            "percent": round(len(assigned) / len(roles) * 100) if roles else 0}


# --------------------------------------------------------------------------- #
# 8. First Aid Supplies Monitor
# --------------------------------------------------------------------------- #
EXPIRY_WARNING_DAYS = 30
FIRST_AID_STATUS_LABELS = {
    "available": ("In stock", "success"),
    "low_stock": ("Low stock", "warning"),
    "out_of_stock": ("Out of stock", "danger"),
    "expired": ("Expired", "danger"),
}
_STATUS_SEVERITY = {"available": 0, "low_stock": 1, "out_of_stock": 2, "expired": 2}


def compute_stock_status(quantity, reorder_level, expiry, today=None):
    today = today or _today()
    expiry_date = as_date(expiry)
    if expiry_date and expiry_date < today:
        return "expired"
    if int(quantity or 0) <= 0:
        return "out_of_stock"
    if int(quantity or 0) <= int(reorder_level or 0):
        return "low_stock"
    return "available"


def _decorate_item(row, today=None):
    today = today or _today()
    expiry = as_date(row.get("expiry_date"))
    status = compute_stock_status(row.get("quantity"), row.get("reorder_level"), expiry, today)
    row["computed_status"] = status
    row["status_label"], row["status_class"] = FIRST_AID_STATUS_LABELS[status]
    row["expiry_label"] = _fmt(expiry)
    row["days_to_expiry"] = (expiry - today).days if expiry else None
    row["expiring_soon"] = bool(expiry and 0 <= (expiry - today).days <= EXPIRY_WARNING_DAYS)
    row["last_checked_label"] = _fmt(row.get("last_checked_at"))
    checked = as_datetime(row.get("last_checked_at"))
    row["check_overdue"] = not checked or (datetime.now() - checked).days > 30
    return row


@module_api
def refresh_first_aid_statuses(notify=True):
    """Recompute stored statuses from quantity/expiry; alert staff on newly worsened items."""
    today = _today()
    worsened = []
    for row in query_all("SELECT id, item_name, cabinet_location, quantity, reorder_level, expiry_date, status FROM first_aid_inventory"):
        new = compute_stock_status(row["quantity"], row["reorder_level"], row["expiry_date"], today)
        if new != row["status"]:
            execute("UPDATE first_aid_inventory SET status=%s WHERE id=%s", (new, row["id"]))
            if _STATUS_SEVERITY[new] > _STATUS_SEVERITY.get(row["status"], 0):
                worsened.append((row, new))
    if notify:
        for row, new in worsened:
            notify_staff(f"First aid: {FIRST_AID_STATUS_LABELS[new][0]}",
                         f"{row['item_name']} · {row['cabinet_location']}", "warning", "/modules/first-aid")
    return len(worsened)


@module_api
def list_first_aid(search=None, status=None, location=None):
    refresh_first_aid_statuses()
    sql = "SELECT f.*, u.full_name AS updated_by_name FROM first_aid_inventory f LEFT JOIN users u ON u.id = f.updated_by WHERE 1=1"
    params = []
    if status in FIRST_AID_STATUS_LABELS:
        sql += " AND f.status = %s"
        params.append(status)
    if location:
        sql += " AND f.cabinet_location = %s"
        params.append(location)
    term = (search or "").strip()
    if term:
        sql += " AND (f.item_name LIKE %s OR f.cabinet_location LIKE %s)"
        params.extend([f"%{term}%"] * 2)
    sql += " ORDER BY f.cabinet_location, f.item_name"
    today = _today()
    return [_decorate_item(r, today) for r in query_all(sql, params)]


@module_api
def first_aid_locations():
    return [r["cabinet_location"] for r in query_all("SELECT DISTINCT cabinet_location FROM first_aid_inventory ORDER BY cabinet_location")]


@module_api
def first_aid_summary():
    items = list_first_aid()
    counts = {k: 0 for k in FIRST_AID_STATUS_LABELS}
    for item in items:
        counts[item["computed_status"]] += 1
    return {"total": len(items), "counts": counts,
            "expiring_soon": len([i for i in items if i["expiring_soon"] and i["computed_status"] != "expired"]),
            "check_overdue": len([i for i in items if i["check_overdue"]]),
            "needs_action": counts["low_stock"] + counts["out_of_stock"] + counts["expired"]}


def _first_aid_fields(data):
    name = _clean(data.get("item_name"), 150, "Item name", required=True)
    location = _clean(data.get("cabinet_location"), 150, "Cabinet location", required=True)
    quantity = _int(data.get("quantity"), "Quantity", 0, 1000000, required=True)
    reorder = _int(data.get("reorder_level"), "Reorder level", 0, 1000000, default=1)
    expiry = None
    if (data.get("expiry_date") or "").strip():
        expiry = as_date(data.get("expiry_date"))
        if not expiry:
            raise ModuleError("Expiry date is not valid.")
    return name, location, quantity, reorder, expiry


@module_api
def save_first_aid_item(data, item_id=None, user_id=None):
    name, location, quantity, reorder, expiry = _first_aid_fields(data)
    status = compute_stock_status(quantity, reorder, expiry)
    if query_one("SELECT id FROM first_aid_inventory WHERE LOWER(item_name)=LOWER(%s) AND LOWER(cabinet_location)=LOWER(%s) AND id <> %s",
                 (name, location, item_id or 0)):
        raise ModuleError("That item already exists in this cabinet. Adjust its stock instead.")
    expiry_value = expiry.isoformat() if expiry else None
    if item_id:
        if not query_one("SELECT id FROM first_aid_inventory WHERE id=%s", (item_id,)):
            raise ModuleError("Item not found.")
        execute(
            """UPDATE first_aid_inventory SET item_name=%s, cabinet_location=%s, quantity=%s, reorder_level=%s,
               expiry_date=%s, status=%s, updated_by=%s WHERE id=%s""",
            (name, location, quantity, reorder, expiry_value, status, user_id, item_id),
        )
        write_audit("First aid item updated", "first_aid_item", item_id, new_values={"name": name, "quantity": quantity}, user_id=user_id)
        return item_id
    new_id, _ = execute(
        """INSERT INTO first_aid_inventory (item_name, cabinet_location, quantity, reorder_level, expiry_date, status,
                                            updated_by, last_checked_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (name, location, quantity, reorder, expiry_value, status, user_id, _sql_ts(datetime.now())),
    )
    write_audit("First aid item added", "first_aid_item", new_id, new_values={"name": name, "quantity": quantity}, user_id=user_id)
    return new_id


@module_api
def adjust_stock(item_id, delta, reason="", new_expiry=None, user_id=None):
    row = query_one("SELECT * FROM first_aid_inventory WHERE id=%s", (item_id,))
    if not row:
        raise ModuleError("Item not found.")
    delta = _int(delta, "Quantity change", -1000000, 1000000, required=True)
    if delta == 0:
        raise ModuleError("Enter a non-zero quantity change.")
    reason = _clean(reason, 200, "Reason")
    new_qty = int(row["quantity"]) + delta
    if new_qty < 0:
        raise ModuleError(f"Only {row['quantity']} in stock; cannot remove {abs(delta)}.")
    expiry = as_date(row.get("expiry_date"))
    restocked = delta > 0
    if restocked and (new_expiry or "").strip():
        expiry = as_date(new_expiry)
        if not expiry:
            raise ModuleError("Expiry date is not valid.")
    status = compute_stock_status(new_qty, row["reorder_level"], expiry)
    now = _sql_ts(datetime.now())
    execute(
        """UPDATE first_aid_inventory SET quantity=%s, status=%s, expiry_date=%s, last_checked_at=%s,
           last_restocked_at=COALESCE(%s, last_restocked_at), updated_by=%s WHERE id=%s""",
        (new_qty, status, expiry.isoformat() if expiry else None, now, now if restocked else None, user_id, item_id),
    )
    write_audit("First aid stock " + ("restocked" if restocked else "used"), "first_aid_item", item_id,
                old_values={"quantity": row["quantity"]},
                new_values={"quantity": new_qty, "change": delta, "reason": reason or None}, user_id=user_id)
    if _STATUS_SEVERITY[status] > _STATUS_SEVERITY.get(row["status"], 0):
        notify_staff(f"First aid: {FIRST_AID_STATUS_LABELS[status][0]}", f"{row['item_name']} · {row['cabinet_location']}",
                     "warning", "/modules/first-aid")
    return new_qty


@module_api
def mark_first_aid_checked(item_id, user_id=None):
    if not query_one("SELECT id FROM first_aid_inventory WHERE id=%s", (item_id,)):
        raise ModuleError("Item not found.")
    execute("UPDATE first_aid_inventory SET last_checked_at=%s, updated_by=%s WHERE id=%s",
            (_sql_ts(datetime.now()), user_id, item_id))
    write_audit("First aid item checked", "first_aid_item", item_id, user_id=user_id)


@module_api
def delete_first_aid_item(item_id, user_id=None):
    row = query_one("SELECT * FROM first_aid_inventory WHERE id=%s", (item_id,))
    if not row:
        raise ModuleError("Item not found.")
    execute("DELETE FROM first_aid_inventory WHERE id=%s", (item_id,))
    write_audit("First aid item deleted", "first_aid_item", item_id,
                old_values={"name": row["item_name"], "location": row["cabinet_location"]}, user_id=user_id)
