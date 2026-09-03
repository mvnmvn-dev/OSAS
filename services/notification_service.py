from models.database import execute, query_all, query_one


def create_notification(user_id, title, message, ntype="info", link=None):
    nid = execute(
        """INSERT INTO notifications (user_id, title, message, ntype, link)
           VALUES (%s, %s, %s, %s, %s)""",
        (user_id, title, message, ntype, link),
    )[0]
    return nid


def get_user_notifications(user_id, limit=20, unread_only=False):
    sql = "SELECT * FROM notifications WHERE user_id = %s"
    params = [user_id]
    if unread_only:
        sql += " AND is_read = 0"
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    return query_all(sql, params)


def get_unread_count(user_id):
    row = query_one(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id = %s AND is_read = 0",
        (user_id,),
    )
    return row["c"] if row else 0


def mark_read(notification_id, user_id):
    execute(
        "UPDATE notifications SET is_read = 1 WHERE id = %s AND user_id = %s",
        (notification_id, user_id),
    )


def mark_all_read(user_id):
    execute(
        "UPDATE notifications SET is_read = 1 WHERE user_id = %s AND is_read = 0",
        (user_id,),
    )


def notify_patron(user_id, title, message, ntype="info", link=None):
    if user_id:
        create_notification(user_id, title, message, ntype, link)


def notify_staff(title, message, ntype="info", link=None):
    staff = query_all("SELECT id FROM users WHERE role IN ('staff', 'admin')")
    for row in staff:
        create_notification(row["id"], title, message, ntype, link)
