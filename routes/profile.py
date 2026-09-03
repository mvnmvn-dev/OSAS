from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from services.profile_service import get_profile, remove_profile_image, save_profile_image, update_profile

profile_bp = Blueprint("profile", __name__, url_prefix="/profile")


@profile_bp.route("/")
@login_required
def index():
    profile = get_profile(current_user.id)
    return render_template(
        "profile/index.html",
        profile=profile,
        breadcrumbs=[("Account", None)],
        page_title="Account Settings",
    )


@profile_bp.route("/update", methods=["POST"])
@login_required
def update():
    ok, msg = update_profile(
        current_user.id,
        request.form.get("full_name"),
        request.form.get("email"),
    )
    flash(msg, "success" if ok else "error")
    return redirect(url_for("profile.index"))


@profile_bp.route("/photo", methods=["POST"])
@login_required
def upload_photo():
    ok, result = save_profile_image(current_user.id, request.files.get("profile_image"))
    flash(
        "Profile photo updated." if ok else result,
        "success" if ok else "error",
    )
    return redirect(url_for("profile.index"))


@profile_bp.route("/photo/remove", methods=["POST"])
@login_required
def remove_photo():
    ok, msg = remove_profile_image(current_user.id)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("profile.index"))
