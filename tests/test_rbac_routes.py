"""RBAC route protection tests for OSAS safety endpoints."""

import pytest

from app import create_app
from services.rbac import ENDPOINT_PERMISSIONS, PUBLIC_ENDPOINTS


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


def test_every_route_has_permission_or_is_public(app):
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules() if rule.endpoint}
    unregistered = [
        ep for ep in sorted(endpoints)
        if ep not in PUBLIC_ENDPOINTS and ep not in ENDPOINT_PERMISSIONS
    ]
    assert not unregistered, (
        "Routes missing from ENDPOINT_PERMISSIONS or PUBLIC_ENDPOINTS:\n"
        + "\n".join(f"  - {ep}" for ep in unregistered)
    )


def test_faculty_cannot_manage_patrol(client, app):
    with app.app_context():
        from models.database import execute
        from models.user import User
        try:
            execute(
                """INSERT INTO users (username, password_hash, email, full_name, role, campus_id, account_status)
                   VALUES ('test_faculty', %s, 'tf@test.edu', 'Test Faculty', 'faculty_staff', 'OSAS-TF-001', 'active')
                   ON DUPLICATE KEY UPDATE password_hash = VALUES(password_hash), role='faculty_staff'""",
                (User.hash_password("password123"),),
            )
        except Exception:
            pytest.skip("Database not available for integration test")

    client.post("/login", data={"username": "test_faculty", "password": "password123"})
    resp = client.get("/safety/patrol")
    assert resp.status_code == 403
