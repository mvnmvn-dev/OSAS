from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from models.database import execute, query_all, query_one
from models.user import User
from services.audit_service import write_audit
from services.rbac import require_permission

users_bp = Blueprint("users", __name__, url_prefix="/admin/users")

VALID_ROLES = ("admin", "security_head", "guard", "safety_officer", "faculty_staff")
VALID_STATUSES = ("active", "disabled")


def _user_rows(search="", role="", status=""):
    sql = """SELECT id, username, email, full_name, role, campus_id,
                     account_status, must_change_password, failed_login_attempts,
                     locked_until, created_at
              FROM users WHERE 1=1"""
    params = []
    if search:
        sql += " AND (username LIKE %s OR full_name LIKE %s OR email LIKE %s OR campus_id LIKE %s)"
        value = f"%{search}%"
        params.extend([value] * 4)
    if role in VALID_ROLES:
        sql += " AND role=%s"
        params.append(role)
    if status in VALID_STATUSES:
        sql += " AND account_status=%s"
        params.append(status)
    sql += " ORDER BY full_name, username"
    return query_all(sql, params)


@users_bp.get("/")
@login_required
@require_permission("users.manage")
def index():
    search = request.args.get("q", "").strip()
    role = request.args.get("role", "")
    status = request.args.get("status", "")
    return render_template(
        "users/index.html",
        users=_user_rows(search, role, status),
        search=search,
        selected_role=role,
        selected_status=status,
        roles=VALID_ROLES,
        role_labels={
            "admin": "Administrator",
            "security_head": "Security Head",
            "guard": "Security Guard",
            "safety_officer": "Safety Officer",
            "faculty_staff": "Faculty / Staff",
        },
        breadcrumbs=[("User Management", None)],
        page_title="User Management",
    )


@users_bp.post("/create")
@login_required
@require_permission("users.manage")
def create():
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip()
    full_name = request.form.get("full_name", "").strip()
    campus_id = request.form.get("campus_id", "").strip()
    role = request.form.get("role", "faculty_staff")
    password = request.form.get("password", "")

    if not all((username, email, full_name, campus_id, password)):
        flash("Username, email, full name, campus ID, and password are required.", "error")
    elif role not in VALID_ROLES:
        flash("Invalid user role.", "error")
    elif len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
    elif query_one("SELECT id FROM users WHERE username=%s OR email=%s OR campus_id=%s", (username, email, campus_id)):
        flash("Username, email, or campus ID is already in use.", "error")
    else:
        user_id, _ = execute(
            """INSERT INTO users
               (username, password_hash, email, full_name, role, campus_id, account_status, must_change_password)
               VALUES (%s,%s,%s,%s,%s,%s,'active',1)""",
            (username, User.hash_password(password), email, full_name, role, campus_id),
        )
        write_audit("User created", "user", user_id, new_values={"username": username, "role": role})
        flash(f"User {username} created. They must change their password on first login.", "success")
    return redirect(url_for("users.index"))


@users_bp.post("/<int:user_id>/update")
@login_required
@require_permission("users.manage")
def update(user_id):
    user = query_one("SELECT * FROM users WHERE id=%s", (user_id,))
    role = request.form.get("role", "")
    status = request.form.get("account_status", "")
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    campus_id = request.form.get("campus_id", "").strip()

    if not user:
        flash("User not found.", "error")
    elif role not in VALID_ROLES or status not in VALID_STATUSES:
        flash("Invalid role or account status.", "error")
    elif not all((full_name, email, campus_id)):
        flash("Full name, email, and campus ID are required.", "error")
    elif user_id == current_user.id and status != "active":
        flash("You cannot disable your own administrator account.", "error")
    elif query_one("SELECT id FROM users WHERE (email=%s OR campus_id=%s) AND id<>%s", (email, campus_id, user_id)):
        flash("Email or campus ID is already in use.", "error")
    elif user["role"] == "admin" and role != "admin" and not query_one("SELECT id FROM users WHERE role='admin' AND account_status='active' AND id<>%s LIMIT 1", (user_id,)):
        flash("At least one active administrator must remain.", "error")
    else:
        execute(
            "UPDATE users SET full_name=%s, email=%s, campus_id=%s, role=%s, account_status=%s WHERE id=%s",
            (full_name, email, campus_id, role, status, user_id),
        )
        write_audit("User updated", "user", user_id, old_values={"role": user["role"], "account_status": user["account_status"]}, new_values={"role": role, "account_status": status})
        flash("User account updated.", "success")
    return redirect(url_for("users.index"))


@users_bp.post("/<int:user_id>/reset-password")
@login_required
@require_permission("users.manage")
def reset_password(user_id):
    user = query_one("SELECT id, username FROM users WHERE id=%s", (user_id,))
    password = request.form.get("password", "")
    if not user:
        flash("User not found.", "error")
    elif len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
    else:
        execute("UPDATE users SET password_hash=%s, must_change_password=1, failed_login_attempts=0, locked_until=NULL WHERE id=%s", (User.hash_password(password), user_id))
        write_audit("User password reset", "user", user_id)
        flash(f"Password reset for {user['username']}; they must change it at next login.", "success")
    return redirect(url_for("users.index"))
