import os
import secrets
from pathlib import Path

from flask import current_app
from werkzeug.utils import secure_filename

from models.database import execute, query_one
from models.user import User

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def get_profile(user_id):
    return query_one(
        """SELECT id, username, email, full_name, role, library_id, barcode,
                  card_status, card_expiry, account_status, profile_image, created_at
           FROM users WHERE id = %s""",
        (user_id,),
    )


def _avatars_dir():
    path = Path(current_app.static_folder) / "uploads" / "avatars"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _delete_image_file(relative_path):
    if not relative_path:
        return
    filepath = Path(current_app.static_folder) / relative_path.replace("\\", "/")
    if filepath.is_file():
        try:
            filepath.unlink()
        except OSError:
            pass


def update_profile(user_id, full_name, email):
    full_name = (full_name or "").strip()
    email = (email or "").strip().lower()

    if not full_name:
        return False, "Full name is required."
    if not email or "@" not in email:
        return False, "A valid email is required."

    existing = query_one("SELECT id FROM users WHERE email = %s AND id != %s", (email, user_id))
    if existing:
        return False, "That email is already in use."

    execute(
        "UPDATE users SET full_name = %s, email = %s WHERE id = %s",
        (full_name, email, user_id),
    )
    return True, "Profile updated successfully."


def save_profile_image(user_id, file_storage):
    if not file_storage or not file_storage.filename:
        return False, "No image selected."

    filename = secure_filename(file_storage.filename)
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return False, "Use JPG, PNG, WEBP, or GIF images only."

    max_bytes = current_app.config.get("MAX_PROFILE_IMAGE_MB", 2) * 1024 * 1024
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > max_bytes:
        return False, f"Image must be under {current_app.config.get('MAX_PROFILE_IMAGE_MB', 2)} MB."

    row = query_one("SELECT profile_image FROM users WHERE id = %s", (user_id,))
    if row and row.get("profile_image"):
        _delete_image_file(row["profile_image"])

    token = secrets.token_hex(4)
    stored_name = f"{user_id}_{token}{ext}"
    avatars_dir = _avatars_dir()
    filepath = avatars_dir / stored_name
    file_storage.save(filepath)

    relative = f"uploads/avatars/{stored_name}"
    execute("UPDATE users SET profile_image = %s WHERE id = %s", (relative, user_id))
    return True, relative


def remove_profile_image(user_id):
    row = query_one("SELECT profile_image FROM users WHERE id = %s", (user_id,))
    if not row or not row.get("profile_image"):
        return False, "No profile photo to remove."

    _delete_image_file(row["profile_image"])
    execute("UPDATE users SET profile_image = NULL WHERE id = %s", (user_id,))
    return True, "Profile photo removed."
