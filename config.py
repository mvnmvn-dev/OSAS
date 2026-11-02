import os
from datetime import timedelta
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

load_dotenv()


def _parse_database_url(url):
    """Parse DATABASE_URL. PostgreSQL is required (postgres:// or postgresql://)."""
    if not url:
        return {}
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").split("+")[0].lower()
    if scheme in ("mysql", "mysql2"):
        raise RuntimeError(
            "OSAS now uses PostgreSQL. Set DATABASE_URL to a postgres:// connection string (Render Postgres)."
        )
    if scheme not in ("postgres", "postgresql"):
        return {}
    dsn = url
    if dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://") :]
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": unquote(parsed.username) if parsed.username else "postgres",
        "password": unquote(parsed.password) if parsed.password else "",
        "database": (parsed.path or "/osas_bcp").lstrip("/") or "osas_bcp",
        "dsn": dsn,
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
    _smtp_host = os.getenv("SMTP_HOST", "")
    _otp_default = "0" if FLASK_ENV == "production" and not _smtp_host else "1"
    OTP_ENABLED = os.getenv("OTP_ENABLED", _otp_default).lower() in ("1", "true", "yes")
    OTP_EXPIRY_MINUTES = int(os.getenv("OTP_EXPIRY_MINUTES", "5"))
    OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
    OTP_RESEND_SECONDS = int(os.getenv("OTP_RESEND_SECONDS", "30"))
    WTF_CSRF_TIME_LIMIT = None

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE = FLASK_ENV == "production" or os.getenv("SESSION_COOKIE_SECURE", "0").lower() in ("1", "true", "yes")
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = SESSION_COOKIE_SAMESITE
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE

    _url_cfg = _parse_database_url(
        os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or os.getenv("POSTGRESQL_URL")
    )
    DATABASE_URL = _url_cfg.get("dsn") or os.getenv("DATABASE_URL") or ""
    PGHOST = os.getenv("PGHOST") or os.getenv("POSTGRES_HOST") or _url_cfg.get("host", "localhost")
    PGPORT = int(os.getenv("PGPORT") or os.getenv("POSTGRES_PORT") or str(_url_cfg.get("port", 5432)))
    PGUSER = os.getenv("PGUSER") or os.getenv("POSTGRES_USER") or _url_cfg.get("user", "postgres")
    PGPASSWORD = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD") or _url_cfg.get("password", "")
    PGDATABASE = os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB") or _url_cfg.get("database", "osas_bcp")
    PGSSL = os.getenv("PGSSL", "1" if FLASK_ENV == "production" else "0").lower() in ("1", "true", "yes")

    # Aliases used by older bootstrap/health logs
    MYSQL_HOST = PGHOST
    MYSQL_PORT = PGPORT
    MYSQL_USER = PGUSER
    MYSQL_PASSWORD = PGPASSWORD
    MYSQL_DATABASE = PGDATABASE
    MYSQL_SSL = PGSSL

    SEED_USERNAME = os.getenv("SEED_USERNAME", "software.engineer")
    SEED_PASSWORD = os.getenv("SEED_PASSWORD", "Engineer@OSAS2026")
    SEED_EMAIL = os.getenv("SEED_EMAIL", "engineer@bcp.edu.ph")
    SEED_FULL_NAME = os.getenv("SEED_FULL_NAME", "Software Engineer")
    SEED_ROLE = os.getenv("SEED_ROLE", "admin")
    SEED_CAMPUS_ID = os.getenv("SEED_CAMPUS_ID", "OSAS-ENG-0001")

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

    OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "http://127.0.0.1:5001").rstrip("/")
    OSRM_PROFILE = os.getenv("OSRM_PROFILE", "foot")
    OSRM_TIMEOUT_SECONDS = float(os.getenv("OSRM_TIMEOUT_SECONDS", "8"))

    CAMPUS_MAP_LAT = float(os.getenv("CAMPUS_MAP_LAT", "14.72632"))
    CAMPUS_MAP_LNG = float(os.getenv("CAMPUS_MAP_LNG", "121.03601"))
    CAMPUS_MAP_ZOOM = int(os.getenv("CAMPUS_MAP_ZOOM", "18"))
    CAMPUS_NAME = os.getenv("CAMPUS_NAME", "BCP MV Campus")
    CAMPUS_ADDRESS = os.getenv(
        "CAMPUS_ADDRESS",
        "Lot 762, Topaz corner Sapphire Street, Millionaires Village, Barangay San Agustin, Novaliches, Quezon City",
    )
    SMTP_HOST = _smtp_host
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM = os.getenv("SMTP_FROM", "osas@bcp.edu.ph")
    SMS_PROVIDER_URL = os.getenv("SMS_PROVIDER_URL", "")
    SMS_API_KEY = os.getenv("SMS_API_KEY", "")

    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "static/uploads")
    EXPORT_FOLDER = os.getenv("EXPORT_FOLDER", "static/exports")

    SHOW_DEMO_CREDENTIALS = FLASK_ENV != "production" and os.getenv("SHOW_DEMO_CREDENTIALS", "1").lower() in ("1", "true", "yes")
