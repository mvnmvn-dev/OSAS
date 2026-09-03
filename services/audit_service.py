"""Audit log helpers for create/update actions across OSAS modules."""
from __future__ import annotations

import json

from flask import has_request_context, request
from flask_login import current_user

from models.database import execute


def write_audit(action, entity_type=None, entity_id=None, old_values=None, new_values=None, user_id=None):
    if user_id is None and has_request_context() and getattr(current_user, "is_authenticated", False):
        user_id = current_user.id
    ip_address = None
    if has_request_context():
        ip_address = request.headers.get("X-Forwarded-For", request.remote_addr)
    execute(
        """
        INSERT INTO audit_logs (user_id, action, entity_type, entity_id, old_values, new_values, ip_address)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user_id,
            action,
            entity_type,
            entity_id,
            json.dumps(old_values) if old_values is not None else None,
            json.dumps(new_values) if new_values is not None else None,
            ip_address,
        ),
    )
