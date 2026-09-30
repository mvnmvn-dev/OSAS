from mysql.connector import connect
from werkzeug.security import generate_password_hash

from config import Config


def ensure_database():
    connection = connect(
        host=Config.MYSQL_HOST,
        port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
    )
    cursor = connection.cursor()
    cursor.execute("CREATE DATABASE IF NOT EXISTS osas_bcp CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    cursor.close()
    connection.close()


ensure_database()


def apply_schema():
    connection = connect(
        host=Config.MYSQL_HOST,
        port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
        database="osas_bcp",
    )
    cursor = connection.cursor()
    schema_path = __file__.replace("init_db.py", "schema.sql")
    schema_sql = open(schema_path, encoding="utf-8").read()
    for statement in schema_sql.split(";"):
        statement = statement.strip()
        if statement and not statement.upper().startswith("CREATE DATABASE") and not statement.upper().startswith("USE "):
            cursor.execute(statement)
    connection.commit()
    cursor.close()
    connection.close()


apply_schema()

from models.database import get_db  # noqa: E402
from app import app  # noqa: E402


with app.app_context():
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "INSERT IGNORE INTO users (username,password_hash,email,full_name,role,campus_id) VALUES (%s,%s,%s,%s,%s,%s)",
        ("admin", generate_password_hash("password123"), "admin@bcp.edu.ph", "OSAS Administrator", "admin", "OSAS-0001"),
    )
    cur.execute(
        "INSERT IGNORE INTO users (username,password_hash,email,full_name,role,campus_id) VALUES (%s,%s,%s,%s,%s,%s)",
        ("security.head", generate_password_hash("password123"), "security@bcp.edu.ph", "Security Head", "security_head", "OSAS-0002"),
    )
    db.commit()
    cur.close()
    from services.safety_service import ensure_campus_seed
    ensure_campus_seed()
    print("OSAS database initialized.")
    print("DEMO/DEV credentials only (not for production): admin / security.head · password123")
