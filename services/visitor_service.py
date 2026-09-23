"""Visitor registration, QR pass lifecycle, and gate audit helpers."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from flask import current_app
from models.database import execute, query_all, query_one
from services.audit_service import write_audit


def ensure_visitor_schema():
    id_column = "INTEGER PRIMARY KEY AUTOINCREMENT" if current_app.config.get("TESTING") else "INT AUTO_INCREMENT PRIMARY KEY"
    execute(
        f"""CREATE TABLE IF NOT EXISTS visitors (
            id {id_column},
            pass_code VARCHAR(80) NOT NULL UNIQUE,
            full_name VARCHAR(150) NOT NULL,
            phone VARCHAR(40),
            email VARCHAR(120),
            purpose VARCHAR(180) NOT NULL,
            host_name VARCHAR(150) NOT NULL,
            visit_date DATE NOT NULL,
            valid_until DATETIME NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'registered',
            entry_at DATETIME NULL,
            exit_at DATETIME NULL,
            registered_by INTEGER NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (registered_by) REFERENCES users(id) ON DELETE SET NULL
        )"""
    )


def _row(row):
    if not row:
        return None
    result = dict(row)
    for key in ("valid_until", "visit_date", "entry_at", "exit_at", "created_at"):
        value = result.get(key)
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat(sep=" ")
    return result


def register_visitor(full_name, purpose, host_name, visit_date, registered_by=None, phone=None, email=None):
    ensure_visitor_schema()
    pass_code = f"VIS-{datetime.now():%Y%m%d}-{secrets.token_hex(4).upper()}"
    try:
        valid_until = datetime.strptime(visit_date, "%Y-%m-%d") + timedelta(days=1)
    except (TypeError, ValueError):
        raise ValueError("visit_date must use YYYY-MM-DD format")
    visitor_id, _ = execute(
        """INSERT INTO visitors
           (pass_code, full_name, phone, email, purpose, host_name, visit_date, valid_until, registered_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (pass_code, full_name.strip(), phone, email, purpose.strip(), host_name.strip(), visit_date, valid_until, registered_by),
    )
    write_audit("Visitor pass registered", "visitor", visitor_id, {"pass_code": pass_code, "host_name": host_name}, registered_by)
    return _row(query_one("SELECT * FROM visitors WHERE id=%s", (visitor_id,)))


def list_visitors(limit=50):
    ensure_visitor_schema()
    return [_row(r) for r in query_all("SELECT * FROM visitors ORDER BY created_at DESC LIMIT %s", (int(limit),))]


def validate_pass(pass_code, actor_id=None, action="entry"):
    ensure_visitor_schema()
    visitor = query_one("SELECT * FROM visitors WHERE pass_code=%s", (pass_code.strip(),))
    if not visitor:
        return None, "Pass not found"
    now = datetime.now()
    valid_until = visitor.get("valid_until")
    if isinstance(valid_until, str):
        try:
            valid_until = datetime.fromisoformat(valid_until)
        except ValueError:
            valid_until = now
    if visitor["status"] in ("exited", "revoked"):
        return _row(visitor), f"Pass is {visitor['status']}"
    if valid_until and valid_until < now:
        execute("UPDATE visitors SET status='expired' WHERE id=%s", (visitor["id"],))
        return _row(visitor), "Pass expired"
    if action == "exit":
        if visitor["status"] != "checked_in":
            return _row(visitor), "Visitor is not checked in"
        execute("UPDATE visitors SET status='exited', exit_at=CURRENT_TIMESTAMP WHERE id=%s", (visitor["id"],))
    else:
        if visitor["status"] == "checked_in":
            return _row(visitor), "Visitor is already checked in"
        execute("UPDATE visitors SET status='checked_in', entry_at=CURRENT_TIMESTAMP WHERE id=%s", (visitor["id"],))
    write_audit(f"Visitor {action} validated", "visitor", visitor["id"], {"pass_code": pass_code, "action": action}, actor_id)
    return _row(query_one("SELECT * FROM visitors WHERE id=%s", (visitor["id"],))), "OK"
