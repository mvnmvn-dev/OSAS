import pytest

from app import create_app
from models.database import execute, query_one
from models.user import User
from services.otp_service import issue_login_otp, verify_login_otp


@pytest.fixture
def app(tmp_path, monkeypatch):
    application = create_app()
    application.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SECRET_KEY="otp-test-secret",
        TEST_DATABASE=str(tmp_path / "otp.sqlite3"),
        OTP_ENABLED=True,
    )
    delivered = []
    monkeypatch.setattr(
        "services.otp_service._send_email",
        lambda recipient, code, expires: delivered.append(code) or "test",
    )
    application.extensions["test_delivered_otps"] = delivered
    with application.app_context():
        execute(
            """INSERT INTO users
               (username, password_hash, email, full_name, role, campus_id, account_status)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            ("otp_user", User.hash_password("password123"), "otp@test.edu", "OTP User", "admin", "OTP-001", "active"),
        )
    return application


def test_otp_is_hashed_and_single_use(app):
    with app.app_context():
        user = query_one("SELECT id, email FROM users WHERE username=%s", ("otp_user",))
        challenge = issue_login_otp(user["id"], user["email"], "127.0.0.1")
        stored = query_one("SELECT code_hash FROM login_otp_challenges WHERE id=%s", (challenge["challenge_id"],))
        code = app.extensions["test_delivered_otps"][-1]
        assert stored["code_hash"] != code
        assert verify_login_otp(challenge["challenge_id"], user["id"], "123456", "127.0.0.1")[0] is False
        assert verify_login_otp(challenge["challenge_id"], user["id"], code, "127.0.0.1") == (True, "OK")
        assert verify_login_otp(challenge["challenge_id"], user["id"], code, "127.0.0.1")[0] is False


def test_otp_rejects_different_ip(app):
    with app.app_context():
        user = query_one("SELECT id, email FROM users WHERE username=%s", ("otp_user",))
        challenge = issue_login_otp(user["id"], user["email"], "127.0.0.1")
        code = app.extensions["test_delivered_otps"][-1]
        valid, message = verify_login_otp(challenge["challenge_id"], user["id"], code, "10.0.0.1")
        assert valid is False
        assert "different network" in message
