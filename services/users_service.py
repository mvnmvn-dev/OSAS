import secrets
import string

from models.database import execute, query_all, query_one
from models.user import User
from services.analytics_service import log_event
from services.rbac import ROLE_LABELS


KNOWN_AUDIT_EVENTS = [
    "role_changed",
    "account_disabled",
    "account_enabled",
    "permission_denied",
    "login_failed",
    "password_reset",
    "user_created",
    "report_exported",
]


def get_activity_event_types():
    rows = query_all(
        "SELECT DISTINCT event_type FROM analytics_log WHERE event_type IS NOT NULL ORDER BY event_type"
    )
    db_types = [row["event_type"] for row in rows if row.get("event_type")]
    return list(dict.fromkeys(KNOWN_AUDIT_EVENTS + db_types))


def list_users(role=None, account_status=None, search=None, page=None, per_page=5, **_ignored):
    where = " WHERE 1=1"
    params = []
    if role:
        where += " AND role = %s"
        params.append(role)
    if account_status:
        where += " AND account_status = %s"
        params.append(account_status)
    if search:
        like = f"%{search.strip()}%"
        where += " AND (full_name LIKE %s OR campus_id LIKE %s OR username LIKE %s OR email LIKE %s)"
        params.extend([like, like, like, like])

    select_cols = """id, username, email, full_name, role, campus_id,
                     account_status, must_change_password, created_at, profile_image"""

    if page is not None:
        total = query_one(f"SELECT COUNT(*) AS c FROM users{where}", params)["c"]
        from services.pagination import sql_page_clause

        limit_sql, limit_params = sql_page_clause(page, per_page)
        sql = f"""
            SELECT {select_cols}
            FROM users{where}
            ORDER BY full_name ASC{limit_sql}
        """
        return query_all(sql, params + limit_params), total

    sql = f"""
        SELECT {select_cols}
        FROM users{where}
        ORDER BY full_name ASC
    """
    return query_all(sql, params)


def get_user(user_id):
    return query_one(
        """SELECT id, username, email, full_name, role, campus_id,
                  account_status, must_change_password, created_at, profile_image
           FROM users WHERE id = %s""",
        (user_id,),
    )


def count_active_admins(exclude_user_id=None):
    sql = "SELECT COUNT(*) AS c FROM users WHERE role = 'admin' AND account_status = 'active'"
    params = []
    if exclude_user_id:
        sql += " AND id != %s"
        params.append(exclude_user_id)
    return query_one(sql, params)["c"]


def generate_campus_id(role):
    prefix = {
        "admin": "OSAS",
        "security_head": "SEC",
        "guard": "GRD",
        "safety_officer": "SFO",
        "faculty_staff": "FAC",
    }.get(role, "USR")
    row = query_one(
        "SELECT campus_id FROM users WHERE campus_id LIKE %s ORDER BY id DESC LIMIT 1",
        (f"{prefix}-%",),
    )
    if row:
        try:
            num = int(row["campus_id"].rsplit("-", 1)[-1]) + 1
        except ValueError:
            num = 1
    else:
        num = 1
    return f"{prefix}-{num:04d}"


def generate_temp_password(length=12):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_user(data, actor_id):
    username = data["username"].strip()
    email = data["email"].strip()
    full_name = data["full_name"].strip()
    role = data["role"]
    if role not in ROLE_LABELS:
        return False, "Invalid role."
    campus_id = (data.get("campus_id") or "").strip() or generate_campus_id(role)
    password = data.get("password") or generate_temp_password()
    must_change = 1 if data.get("must_change_password", True) else 0

    if query_one("SELECT id FROM users WHERE username = %s", (username,)):
        return False, "Username already exists."
    if query_one("SELECT id FROM users WHERE email = %s", (email,)):
        return False, "Email already exists."
    if query_one("SELECT id FROM users WHERE campus_id = %s", (campus_id,)):
        return False, "Campus ID already exists."

    user_id = execute(
        """INSERT INTO users
           (username, password_hash, email, full_name, role, campus_id,
            account_status, must_change_password)
           VALUES (%s, %s, %s, %s, %s, %s, 'active', %s)""",
        (
            username,
            User.hash_password(password),
            email,
            full_name,
            role,
            campus_id,
            must_change,
        ),
    )[0]

    log_event(
        "user_created",
        user_id=actor_id,
        entity_type="user",
        entity_id=user_id,
        metadata={"role": role, "campus_id": campus_id},
    )
    return True, {"user_id": user_id, "temp_password": password}


def update_user(user_id, data, actor_id, reason=None):
    existing = get_user(user_id)
    if not existing:
        return False, "User not found."

    new_role = data.get("role", existing["role"])
    new_status = data.get("account_status", existing.get("account_status", "active"))
    new_campus = (data.get("campus_id") or existing.get("campus_id") or "").strip()

    if new_role not in ROLE_LABELS:
        return False, "Invalid role."

    if existing["role"] == "admin" and new_role != "admin":
        if user_id == actor_id:
            return False, "You cannot remove your own admin role."
        if count_active_admins(exclude_user_id=user_id) == 0:
            return False, "Cannot demote the last active admin."

    if existing.get("account_status", "active") == "active" and new_status == "disabled":
        if user_id == actor_id:
            return False, "You cannot deactivate your own account."
        if existing["role"] == "admin" and count_active_admins(exclude_user_id=user_id) == 0:
            return False, "Cannot deactivate the last active admin."

    if new_campus != existing.get("campus_id"):
        clash = query_one("SELECT id FROM users WHERE campus_id = %s AND id != %s", (new_campus, user_id))
        if clash:
            return False, "Campus ID already exists."

    execute(
        """UPDATE users SET full_name = %s, email = %s, role = %s, campus_id = %s,
           account_status = %s WHERE id = %s""",
        (
            data.get("full_name", existing["full_name"]).strip(),
            data.get("email", existing["email"]).strip(),
            new_role,
            new_campus,
            new_status,
            user_id,
        ),
    )

    if existing["role"] != new_role:
        log_event(
            "role_changed",
            user_id=actor_id,
            entity_type="user",
            entity_id=user_id,
            metadata={
                "from_role": existing["role"],
                "to_role": new_role,
                "reason": reason,
                "target_name": existing["full_name"],
            },
        )

    if existing.get("account_status", "active") != new_status:
        event = "account_disabled" if new_status == "disabled" else "account_enabled"
        log_event(
            event,
            user_id=actor_id,
            entity_type="user",
            entity_id=user_id,
            metadata={"reason": reason, "target_name": existing["full_name"]},
        )

    return True, "User updated."


def set_account_status(user_id, status, actor_id, reason=None):
    return update_user(
        user_id,
        {"account_status": status, **(get_user(user_id) or {})},
        actor_id,
        reason=reason,
    )


def reset_password(user_id, actor_id, password=None):
    user = get_user(user_id)
    if not user:
        return False, "User not found."
    temp = password or generate_temp_password()
    execute(
        "UPDATE users SET password_hash = %s, must_change_password = 1, failed_login_attempts = 0, locked_until = NULL WHERE id = %s",
        (User.hash_password(temp), user_id),
    )
    log_event(
        "password_reset",
        user_id=actor_id,
        entity_type="user",
        entity_id=user_id,
        metadata={"target_name": user["full_name"]},
    )
    return True, temp


def list_activity_log(event_type=None, actor=None, page=None, per_page=5):
    where = " WHERE 1=1"
    params = []
    if event_type:
        where += " AND a.event_type = %s"
        params.append(event_type)
    if actor:
        like = f"%{actor.strip()}%"
        where += " AND (u.full_name LIKE %s OR u.username LIKE %s)"
        params.extend([like, like])

    if page is not None:
        total = query_one(
            f"""SELECT COUNT(*) AS c
                FROM analytics_log a
                LEFT JOIN users u ON u.id = a.user_id{where}""",
            params,
        )["c"]
        from services.pagination import sql_page_clause

        limit_sql, limit_params = sql_page_clause(page, per_page)
        sql = f"""
            SELECT a.*, u.full_name AS actor_name, u.username AS actor_username
            FROM analytics_log a
            LEFT JOIN users u ON u.id = a.user_id{where}
            ORDER BY a.created_at DESC{limit_sql}
        """
        rows = query_all(sql, params + limit_params)
    else:
        sql = f"""
            SELECT a.*, u.full_name AS actor_name, u.username AS actor_username
            FROM analytics_log a
            LEFT JOIN users u ON u.id = a.user_id{where}
            ORDER BY a.created_at DESC
        """
        rows = query_all(sql, params)
        total = len(rows)

    for row in rows:
        if row.get("metadata") and isinstance(row["metadata"], str):
            import json
            try:
                row["metadata"] = json.loads(row["metadata"])
            except json.JSONDecodeError:
                row["metadata"] = {}
    if page is not None:
        return rows, total
    return rows
