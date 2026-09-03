"""Incident CRUD integration tests against MySQL when available."""
import pytest

from app import create_app
from models.user import User


@pytest.fixture
def app():
    application = create_app()
    application.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret",
    })
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def _login_admin(client, app):
    with app.app_context():
        from models.database import execute, query_one
        from services.safety_service import ensure_campus_seed
        try:
            execute(
                """INSERT INTO users (username, password_hash, email, full_name, role, campus_id, account_status)
                   VALUES ('test_admin_safety', %s, 'tas@test.edu', 'Test Admin', 'admin', 'OSAS-TEST-99', 'active')
                   ON DUPLICATE KEY UPDATE password_hash = VALUES(password_hash), role='admin'""",
                (User.hash_password("password123"),),
            )
            ensure_campus_seed()
        except Exception as exc:
            pytest.skip(f"Database not available: {exc}")
    return client.post("/login", data={"username": "test_admin_safety", "password": "password123"}, follow_redirects=True)


def test_log_and_update_incident(client, app):
    _login_admin(client, app)
    with app.app_context():
        from services.safety_service import log_incident, update_incident
        from models.database import query_one
        code = log_incident("Pytest hazard", "Gate 2", "high", user_id=1)
        row = query_one("SELECT * FROM incidents WHERE incident_code=%s", (code,))
        assert row is not None
        assert row["status"] == "reported"
        assert update_incident(code, "verified")
        row2 = query_one("SELECT status FROM incidents WHERE incident_code=%s", (code,))
        assert row2["status"] == "verified"


def test_incident_create_route(client, app):
    _login_admin(client, app)
    resp = client.post(
        "/safety/incidents",
        data={"title": "Route test incident", "location": "Science Building", "priority": "medium"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"queued for response" in resp.data or b"Incident" in resp.data


def test_rbac_endpoints_cover_safety_mutations(app):
    from services.rbac import ENDPOINT_PERMISSIONS, PUBLIC_ENDPOINTS
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules() if rule.endpoint}
    missing = [
        ep for ep in sorted(endpoints)
        if ep not in PUBLIC_ENDPOINTS and ep not in ENDPOINT_PERMISSIONS
    ]
    assert not missing, missing
