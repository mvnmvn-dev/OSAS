from datetime import datetime, timedelta

from flask import current_app

from models.database import execute, query_one
from services.analytics_service import log_event


def is_account_locked(user_row):
    locked_until = user_row.get("locked_until") if isinstance(user_row, dict) else getattr(user_row, "locked_until", None)
    if locked_until and locked_until > datetime.now():
        return True
    return False


def record_failed_login(username, user_id=None):
    if not user_id:
        log_event("login_failed", metadata={"username": username, "reason": "unknown_user"})
        return

    attempts = (user_row := query_one("SELECT failed_login_attempts FROM users WHERE id = %s", (user_id,)))
    if not attempts:
        return

    new_count = (attempts["failed_login_attempts"] or 0) + 1
    max_attempts = current_app.config.get("MAX_LOGIN_ATTEMPTS", 5)
    lock_minutes = current_app.config.get("LOGIN_LOCKOUT_MINUTES", 5)

    if new_count >= max_attempts:
        locked_until = datetime.now() + timedelta(minutes=lock_minutes)
        execute(
            "UPDATE users SET failed_login_attempts = %s, locked_until = %s WHERE id = %s",
            (new_count, locked_until, user_id),
        )
        log_event(
            "login_failed",
            user_id=user_id,
            metadata={"username": username, "reason": "account_locked", "attempts": new_count},
        )
    else:
        execute("UPDATE users SET failed_login_attempts = %s WHERE id = %s", (new_count, user_id))
        log_event(
            "login_failed",
            user_id=user_id,
            metadata={"username": username, "reason": "bad_password", "attempts": new_count},
        )


def clear_login_attempts(user_id):
    execute(
        "UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE id = %s",
        (user_id,),
    )


def is_safe_redirect(target, host_url):
    if not target:
        return False
    from urllib.parse import urlparse

    ref_url = urlparse(host_url)
    test_url = urlparse(target)
    if test_url.scheme or test_url.netloc:
        return ref_url.netloc == test_url.netloc and ref_url.scheme == test_url.scheme
    return target.startswith("/")
