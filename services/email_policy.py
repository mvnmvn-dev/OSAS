"""Email address conflict rules used by user and profile management."""

from models.database import query_one


def email_conflicts(email, role, exclude_user_id=None):
    """Return whether email is used incompatibly with the requested account role.

    Admin accounts may share one mailbox for OTP testing. Other roles retain the
    existing one-account-per-email behavior, including conflicts with admins.
    """
    sql = "SELECT id FROM users WHERE LOWER(email) = LOWER(%s)"
    params = [email]
    if role == "admin":
        sql += " AND role <> 'admin'"
    if exclude_user_id is not None:
        sql += " AND id <> %s"
        params.append(exclude_user_id)
    sql += " LIMIT 1"
    return bool(query_one(sql, params))
