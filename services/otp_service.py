"""Short-lived, single-use OTP challenges for authentication."""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import hashlib
import hmac
import secrets
import smtplib

import requests

from flask import current_app

from models.database import execute, query_one


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "").replace(" ", "T"))
        except ValueError:
            return None
    return None


def _hash_code(code):
    key = str(current_app.config["SECRET_KEY"]).encode("utf-8")
    return hmac.new(key, str(code).encode("ascii"), hashlib.sha256).hexdigest()


def _safe_email(email):
    value = (email or "").strip()
    return value if "@" in value and len(value) <= 120 else ""


def _send_email(recipient, code, expires_minutes):
    body = (
        "Your OSAS sign-in verification code is %s. It expires in %d minutes and can be used only once.\n\n"
        "If you did not attempt to sign in, contact the OSAS administrator." % (code, expires_minutes)
    )

    api_key = current_app.config.get("RESEND_API_KEY")
    if api_key:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": current_app.config.get("RESEND_FROM"),
                "to": [recipient],
                "subject": "Your OSAS verification code",
                "text": body,
            },
            timeout=10,
        )
        if resp.status_code >= 300:
            current_app.logger.error("Resend failed %s: %s", resp.status_code, resp.text)
            raise RuntimeError("Resend email delivery failed")
        return "email"

    host = current_app.config.get("SMTP_HOST")
    if not host:
        if current_app.config.get("FLASK_ENV") == "production":
            raise RuntimeError("RESEND_API_KEY or SMTP_HOST is required for OTP delivery in production")
        # Development has no implicit bypass: the code is only available in logs.
        current_app.logger.warning("Development OTP for %s: %s", recipient, code)
        return "development_log"

    message = EmailMessage()
    message["Subject"] = "Your OSAS verification code"
    message["From"] = current_app.config.get("SMTP_FROM") or current_app.config.get("SMTP_USER")
    message["To"] = recipient
    message.set_content(body)
    port = int(current_app.config.get("SMTP_PORT", 587))
    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.ehlo()
        if port != 25:
            smtp.starttls()
            smtp.ehlo()
        username = current_app.config.get("SMTP_USER")
        if username:
            smtp.login(username, current_app.config.get("SMTP_PASSWORD", ""))
        smtp.send_message(message)
    return "email"


def issue_login_otp(user_id, email, requested_ip=None, force=False):
    """Create, persist, and deliver a login OTP without storing the plaintext code."""
    ttl = int(current_app.config.get("OTP_EXPIRY_MINUTES", 5))
    resend_seconds = int(current_app.config.get("OTP_RESEND_SECONDS", 30))
    recent = query_one(
        """SELECT id, created_at FROM login_otp_challenges
           WHERE user_id=%s AND purpose='login' AND consumed_at IS NULL
             AND created_at >= %s ORDER BY id DESC LIMIT 1""",
        (user_id, _now() - timedelta(seconds=resend_seconds)),
    )
    if recent and not force:
        return {"ok": False, "reason": "cooldown", "retry_after": resend_seconds}

    # Invalidate older outstanding challenges before issuing a new one.
    execute(
        """UPDATE login_otp_challenges SET consumed_at=%s
           WHERE user_id=%s AND purpose='login' AND consumed_at IS NULL""",
        (_now(), user_id),
    )
    code = f"{secrets.randbelow(1000000):06d}"
    challenge_id, _ = execute(
        """INSERT INTO login_otp_challenges
           (user_id, code_hash, purpose, expires_at, requested_ip)
           VALUES (%s,%s,'login',%s,%s)""",
        (user_id, _hash_code(code), _now() + timedelta(minutes=ttl), requested_ip),
    )
    recipient = _safe_email(email)
    if not recipient:
        execute("UPDATE login_otp_challenges SET consumed_at=%s WHERE id=%s", (_now(), challenge_id))
        raise RuntimeError("No valid email address is configured for OTP delivery")
    try:
        channel = _send_email(recipient, code, ttl)
    except Exception:
        execute("UPDATE login_otp_challenges SET consumed_at=%s WHERE id=%s", (_now(), challenge_id))
        raise
    return {"ok": True, "challenge_id": challenge_id, "channel": channel, "expires_minutes": ttl}


def verify_login_otp(challenge_id, user_id, code, requested_ip=None):
    """Verify an OTP with bounded attempts, expiry, IP binding, and one-time use."""
    row = query_one(
        """SELECT * FROM login_otp_challenges
           WHERE id=%s AND user_id=%s AND purpose='login'""",
        (challenge_id, user_id),
    )
    if not row:
        return False, "That verification code is invalid or expired."
    now = _now()
    expires_at = _as_datetime(row.get("expires_at"))
    if row.get("consumed_at") or (expires_at and expires_at <= now):
        return False, "That verification code is invalid or expired."
    max_attempts = int(current_app.config.get("OTP_MAX_ATTEMPTS", 5))
    if int(row.get("attempts") or 0) >= max_attempts:
        execute("UPDATE login_otp_challenges SET consumed_at=%s WHERE id=%s", (now, challenge_id))
        return False, "Too many incorrect verification attempts. Request a new code."
    stored_ip = row.get("requested_ip")
    if current_app.config.get("OTP_BIND_IP") and stored_ip and requested_ip and stored_ip != requested_ip:
        return False, "This verification request came from a different network. Request a new code."
    execute("UPDATE login_otp_challenges SET attempts=attempts+1 WHERE id=%s", (challenge_id,))
    supplied = str(code or "").strip()
    if not (len(supplied) == 6 and supplied.isdigit() and hmac.compare_digest(_hash_code(supplied), row["code_hash"])):
        if int(row.get("attempts") or 0) + 1 >= max_attempts:
            execute("UPDATE login_otp_challenges SET consumed_at=%s WHERE id=%s", (now, challenge_id))
        return False, "That verification code is invalid or expired."
    execute("UPDATE login_otp_challenges SET consumed_at=%s WHERE id=%s", (now, challenge_id))
    return True, "OK"