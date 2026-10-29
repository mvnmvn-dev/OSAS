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


def test_log_incident_with_checkpoint_and_team(client, app):
    _login_admin(client, app)
    with app.app_context():
        from services.safety_service import log_incident, update_incident, assign_incident_team
        from models.database import query_one
        team = query_one("SELECT id, team_name FROM patrol_teams LIMIT 1")
        cp = query_one("SELECT id, checkpoint_code FROM checkpoints LIMIT 1")
        team_id = team["id"] if team else None
        cp_id = cp["id"] if cp else None

        code = log_incident(
            title="Integrated hazard",
            location="Main Gate",
            priority="high",
            user_id=1,
            checkpoint_id=cp_id,
            team_id=team_id,
        )
        row = query_one("SELECT * FROM incidents WHERE incident_code=%s", (code,))
        assert row is not None
        if team_id:
            assert row["team_id"] == team_id
        if cp_id:
            assert row["checkpoint_id"] == cp_id

        # Admin assigns / changes team
        assert assign_incident_team(code, team_id, actor_role="admin")
        row_after = query_one("SELECT * FROM incidents WHERE incident_code=%s", (code,))
        assert row_after["team_id"] == team_id


def test_incident_create_route_with_team_and_checkpoint(client, app):
    _login_admin(client, app)
    resp = client.post(
        "/safety/incidents",
        data={
            "title": "Route integrated incident",
            "location": "Science Building",
            "priority": "high",
            "incident_type": "security",
            "checkpoint_id": "1",
            "team_id": "1",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"queued for response" in resp.data or b"Incident" in resp.data


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
