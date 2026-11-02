import pytest

from app import create_app
from models.database import execute, query_one
from models.user import User


@pytest.fixture
def app():
    application = create_app()
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SECRET_KEY="test-secret")
    return application


def _seed_user(app, username, role, email, campus_id):
    with app.app_context():
        execute(
            """INSERT INTO users (username, password_hash, email, full_name, role, campus_id, account_status)
               VALUES (%s,%s,%s,%s,%s,%s,'active')
               ON DUPLICATE KEY UPDATE password_hash=VALUES(password_hash), role=VALUES(role)""",
            (username, User.hash_password("password123"), email, username.title(), role, campus_id),
        )


def _login(client, username):
    return client.post("/login", data={"username": username, "password": "password123"})


@pytest.fixture
def admin_client(app, client):
    _seed_user(app, "user_admin", "admin", "user-admin@test.edu", "USER-ADMIN")
    _login(client, "user_admin")
    return client


def test_admin_can_view_and_create_users(admin_client, app):
    response = admin_client.get("/admin/users/")
    assert response.status_code == 200
    assert b"User Management" in response.data

    response = admin_client.post(
        "/admin/users/create",
        data={
            "username": "new_guard",
            "email": "new-guard@test.edu",
            "full_name": "New Guard",
            "campus_id": "NEW-GUARD",
            "role": "guard",
            "password": "temporary123",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        user = query_one("SELECT role, must_change_password FROM users WHERE username=%s", ("new_guard",))
    assert user["role"] == "guard"
    assert user["must_change_password"] == 1


def test_admin_accounts_can_share_email_for_otp_but_other_roles_cannot(admin_client, app):
    shared_email = "user-admin@test.edu"
    response = admin_client.post(
        "/admin/users/create",
        data={
            "username": "second_admin",
            "email": shared_email,
            "full_name": "Second Admin",
            "campus_id": "SECOND-ADMIN",
            "role": "admin",
            "password": "temporary123",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assert query_one("SELECT id FROM users WHERE username=%s", ("second_admin",))

    response = admin_client.post(
        "/admin/users/create",
        data={
            "username": "guard_shared_email",
            "email": shared_email,
            "full_name": "Shared Email Guard",
            "campus_id": "SHARED-GUARD",
            "role": "guard",
            "password": "temporary123",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assert query_one("SELECT id FROM users WHERE username=%s", ("guard_shared_email",)) is None


def test_non_admin_cannot_view_user_management(client, app):
    _seed_user(app, "user_faculty", "faculty_staff", "user-faculty@test.edu", "USER-FACULTY")
    _login(client, "user_faculty")
    response = client.get("/admin/users/")
    assert response.status_code == 403
