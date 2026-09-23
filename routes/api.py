from flask import Blueprint
from flask_login import current_user, login_required

from services.rbac import api_error, api_response, get_json, require_permission
from services import safety_service, visitor_service

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.get("/health")
def health():
    return api_response({"service": "osas", "status": "healthy"})


@api_bp.get("/campus/map")
@login_required
@require_permission("safety.view")
def campus_map():
    dashboard = safety_service.get_safety_dashboard("overview")
    return api_response(dashboard["map"])


@api_bp.get("/readiness")
@login_required
@require_permission("safety.view")
def readiness():
    dashboard = safety_service.get_safety_dashboard("overview")
    return api_response(dashboard["readiness"])


@api_bp.post("/patrol/scan")
@login_required
@require_permission("patrol.manage")
def patrol_scan():
    payload = get_json()
    token = (payload.get("scan_token") or payload.get("checkpoint_code") or "").strip()
    if not token:
        return api_error("scan_token or checkpoint_code is required")
    result = safety_service.record_checkpoint_scan(
        token, guard_id=current_user.id, method=payload.get("scan_method", "qr"), notes=payload.get("notes", "")
    )
    if not result:
        return api_error("Checkpoint token or code not recognized", 404)
    return api_response(result, "Checkpoint verified", 201)


@api_bp.get("/visitors")
@login_required
@require_permission("visitor.view")
def visitors():
    return api_response(visitor_service.list_visitors())
