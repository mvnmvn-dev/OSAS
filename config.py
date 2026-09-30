import os
from datetime import timedelta
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


def _parse_mysql_url(url):
    """Support MYSQL_URL / DATABASE_URL style: mysql://user:pass@host:3306/dbname"""
    if not url:
        return {}
    parsed = urlparse(url)
    if parsed.scheme not in ("mysql", "mysql2", "mysql+mysqlconnector"):
        return {}
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": parsed.username or "root",
        "password": parsed.password or "",
        "database": (parsed.path or "/osas_bcp").lstrip("/") or "osas_bcp",
    }


class Config:
    FLASK_ENV = os.getenv("FLASK_ENV", "development")
    DEBUG = os.getenv("FLASK_DEBUG", "0" if FLASK_ENV == "production" else "1").lower() in ("1", "true", "yes")

    SECRET_KEY = os.getenv("SECRET_KEY")
    if not SECRET_KEY:
        if FLASK_ENV == "production":
            raise RuntimeError("SECRET_KEY environment variable must be set in production.")
        SECRET_KEY = "bcp-osas-dev-secret-key-change-in-production"

    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.getenv("SESSION_LIFETIME_MINUTES", "45")))
    MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
    LOGIN_LOCKOUT_MINUTES = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "5"))
    WTF_CSRF_TIME_LIMIT = None

    # Session cookie hardening
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE = FLASK_ENV == "production" or os.getenv("SESSION_COOKIE_SECURE", "0").lower() in ("1", "true", "yes")
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = SESSION_COOKIE_SAMESITE
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE

    # Prefer discrete MYSQL_* vars; fall back to MYSQL_URL / DATABASE_URL (common on hosts)
    _url_cfg = _parse_mysql_url(os.getenv("MYSQL_URL") or os.getenv("DATABASE_URL"))
    MYSQL_HOST = os.getenv("MYSQL_HOST", _url_cfg.get("host", "localhost"))
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", str(_url_cfg.get("port", 3306))))
    MYSQL_USER = os.getenv("MYSQL_USER", _url_cfg.get("user", "root"))
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", _url_cfg.get("password", ""))
    MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", _url_cfg.get("database", "osas_bcp"))
    MYSQL_SSL = os.getenv("MYSQL_SSL", "1" if FLASK_ENV == "production" else "0").lower() in ("1", "true", "yes")

    MAX_PROFILE_IMAGE_MB = int(os.getenv("MAX_PROFILE_IMAGE_MB", "2"))
    MAX_ATTACHMENT_MB = int(os.getenv("MAX_ATTACHMENT_MB", "5"))
    ALLOWED_ATTACHMENT_EXTENSIONS = {
        e.strip().lower().lstrip(".")
        for e in os.getenv(
            "ALLOWED_ATTACHMENT_EXTENSIONS",
            "pdf,png,jpg,jpeg,gif,webp,doc,docx,txt,csv,xlsx",
        ).split(",")
        if e.strip()
    }

    # Self-hosted OSRM (Table/Route/Trip). Falls back to local heuristic if unreachable.
    OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "http://127.0.0.1:5001").rstrip("/")
    OSRM_PROFILE = os.getenv("OSRM_PROFILE", "foot")
    OSRM_TIMEOUT_SECONDS = float(os.getenv("OSRM_TIMEOUT_SECONDS", "8"))

    # Map defaults — BCP Millionaires Village (MV) Campus, Novaliches, Quezon City
    # Lot 762, Topaz cor. Sapphire St., Brgy. San Agustin
    CAMPUS_MAP_LAT = float(os.getenv("CAMPUS_MAP_LAT", "14.72632"))
    CAMPUS_MAP_LNG = float(os.getenv("CAMPUS_MAP_LNG", "121.03601"))
    CAMPUS_MAP_ZOOM = int(os.getenv("CAMPUS_MAP_ZOOM", "18"))
    CAMPUS_NAME = os.getenv("CAMPUS_NAME", "BCP MV Campus")
    CAMPUS_ADDRESS = os.getenv(
        "CAMPUS_ADDRESS",
        "Lot 762, Topaz corner Sapphire Street, Millionaires Village, Barangay San Agustin, Novaliches, Quezon City",
    )
    # Optional notification channels (queued when unset)
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM = os.getenv("SMTP_FROM", "osas@bcp.edu.ph")
    SMS_PROVIDER_URL = os.getenv("SMS_PROVIDER_URL", "")
    SMS_API_KEY = os.getenv("SMS_API_KEY", "")

    # Render free disk is ephemeral — files survive until the service restarts/redeploys
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "static/uploads")
    EXPORT_FOLDER = os.getenv("EXPORT_FOLDER", "static/exports")

    # Demo credentials are for local/dev only — never present as production secrets
    SHOW_DEMO_CREDENTIALS = FLASK_ENV != "production" and os.getenv("SHOW_DEMO_CREDENTIALS", "1").lower() in ("1", "true", "yes")
