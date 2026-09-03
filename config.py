import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


class Config:
    FLASK_ENV = os.getenv("FLASK_ENV", "development")

    SECRET_KEY = os.getenv("SECRET_KEY")
    if not SECRET_KEY:
        if FLASK_ENV == "production":
            raise RuntimeError("SECRET_KEY environment variable must be set in production.")
        SECRET_KEY = "bcp-osas-dev-secret-key-change-in-production"

    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.getenv("SESSION_LIFETIME_MINUTES", "45")))
    MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
    LOGIN_LOCKOUT_MINUTES = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "5"))
    WTF_CSRF_TIME_LIMIT = None

    MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "osas_bcp")

    MAX_PROFILE_IMAGE_MB = int(os.getenv("MAX_PROFILE_IMAGE_MB", "2"))
    MAX_ATTACHMENT_MB = int(os.getenv("MAX_ATTACHMENT_MB", "5"))

    # Self-hosted OSRM (Table/Route/Trip). Falls back to local heuristic if unreachable.
    OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "http://127.0.0.1:5001").rstrip("/")
    OSRM_PROFILE = os.getenv("OSRM_PROFILE", "foot")
    OSRM_TIMEOUT_SECONDS = float(os.getenv("OSRM_TIMEOUT_SECONDS", "8"))

    # Map defaults — Bestlink College (Quezon City) approximate campus center
    CAMPUS_MAP_LAT = float(os.getenv("CAMPUS_MAP_LAT", "14.73280"))
    CAMPUS_MAP_LNG = float(os.getenv("CAMPUS_MAP_LNG", "121.05820"))
    CAMPUS_MAP_ZOOM = int(os.getenv("CAMPUS_MAP_ZOOM", "17"))

    # Optional notification channels (queued when unset)
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM = os.getenv("SMTP_FROM", "osas@bcp.edu.ph")
    SMS_PROVIDER_URL = os.getenv("SMS_PROVIDER_URL", "")
    SMS_API_KEY = os.getenv("SMS_API_KEY", "")

    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "static/uploads")
    EXPORT_FOLDER = os.getenv("EXPORT_FOLDER", "static/exports")
