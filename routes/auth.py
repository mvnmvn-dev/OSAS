import os

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from models.user import User
from services.analytics_service import log_event
from services.auth_service import clear_login_attempts, is_account_locked, is_safe_redirect, record_failed_login

auth_bp = Blueprint("auth", __name__)


def _login_bg_url():
    images_dir = os.path.join(current_app.static_folder, "images")
    preferred = (
        "lms login bg.jpg",
        "lms login bg.jpeg",
        "lms login bg.png",
        "lms-login-bg.jpg",
        "login-bg.jpg",
        "bcp-bg.jpg",
    )
    for name in preferred:
        path = os.path.join(images_dir, name)
        if os.path.exists(path):
            version = f"{int(os.path.getmtime(path))}-{os.path.getsize(path)}"
            return url_for("static", filename=f"images/{name}", v=version)

    if os.path.isdir(images_dir):
        for entry in sorted(os.listdir(images_dir)):
            lower = entry.lower()
            if not lower.endswith((".jpg", ".jpeg", ".png", ".webp")):
                continue
            if "login" in lower and "logo" not in lower:
                path = os.path.join(images_dir, entry)
                version = f"{int(os.path.getmtime(path))}-{os.path.getsize(path)}"
                return url_for("static", filename=f"images/{entry}", v=version)

    return url_for("static", filename="images/login-bg.jpg", v=0)


@auth_bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.get_by_username(username)

        if user and is_account_locked(user):
            error = "Account temporarily locked due to failed login attempts. Try again later."
        elif user and user.account_status != "active":
            error = "This account has been disabled. Contact the OSAS administrator."
            record_failed_login(username, user.id)
        elif user and user.check_password(password):
            clear_login_attempts(user.id)
            session.permanent = True
            login_user(user)
            if user.must_change_password:
                return redirect(url_for("auth.change_password"))
            next_page = request.args.get("next")
            if next_page and is_safe_redirect(next_page, request.host_url):
                return redirect(next_page)
            return redirect(url_for("dashboard.index"))
        else:
            record_failed_login(username, user.id if user else None)
            error = "Invalid username or password."

    return render_template(
        "login.html",
        error=error,
        login_bg_url=_login_bg_url(),
    )


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    error = None
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")

        if not current_user.check_password(current_pw):
            error = "Current password is incorrect."
        elif len(new_pw) < 8:
            error = "New password must be at least 8 characters."
        elif new_pw != confirm_pw:
            error = "New passwords do not match."
        else:
            from models.database import execute
            execute(
                "UPDATE users SET password_hash = %s, must_change_password = 0 WHERE id = %s",
                (User.hash_password(new_pw), current_user.id),
            )
            log_event("password_changed", user_id=current_user.id)
            return render_template(
                "auth/change_password.html",
                success="Password updated successfully.",
                page_title="Change Password",
            )

    return render_template(
        "auth/change_password.html",
        error=error,
        page_title="Change Password",
    )


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
