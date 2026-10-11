from flask import Blueprint, request
from flask_login import current_user, login_required

from models.database import query_all
from services.rbac import api_error, api_response, get_json, require_permission
from services import safety_service, visitor_service

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.get("/search")
@login_required
@require_permission("safety.view")
def search():
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return api_response([])
    like = f"%{query}%"
    results = []
    for row in query_all(
        """SELECT incident_code AS code, title AS name, status AS detail,
                  'Incident' AS category, '/safety/incidents' AS url
           FROM incidents
           WHERE incident_code LIKE %s OR title LIKE %s OR description LIKE %s
           ORDER BY reported_at DESC LIMIT 8""",
        (like, like, like),
    ):
        results.append(row)
    for row in query_all(
        """SELECT checkpoint_code AS code, checkpoint_name AS name,
                  'Active checkpoint' AS detail, 'Checkpoint' AS category,
                  '/safety/teams' AS url
           FROM checkpoints
           WHERE checkpoint_code LIKE %s OR checkpoint_name LIKE %s
           ORDER BY checkpoint_name LIMIT 8""",
        (like, like),
    ):
        results.append(row)
    for row in query_all(
        """SELECT route_name AS name, routing_engine AS detail,
                  'Patrol route' AS category, '/safety/patrol' AS url
           FROM patrol_routes
           WHERE route_name LIKE %s OR routing_engine LIKE %s
           ORDER BY created_at DESC LIMIT 8""",
        (like, like),
    ):
        results.append(row)
    return api_response(results[:12])


@api_bp.get("/health")
def health():
    from flask import current_app
    from models.database import query_one

    db = {
        "ok": False,
        "host": current_app.config.get("MYSQL_HOST"),
        "database": current_app.config.get("MYSQL_DATABASE"),
    }
    try:
        query_one("SELECT 1 AS ok")
        db["ok"] = True
        try:
            row = query_one("SELECT COUNT(*) AS c FROM users")
            db["users"] = int(row["c"] if row else 0)
        except Exception as exc:
            db["users_error"] = str(exc)[:180]
    except Exception as exc:
        db["error"] = str(exc)[:180]

    status = "healthy" if db.get("ok") and "users_error" not in db else "degraded"
    return api_response({"service": "osas", "status": status, "db": db})


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
    if result.get("scan_result") == "duplicate":
        return api_error(result.get("scan_message", "Duplicate scan rejected."), 409)
    return api_response(result, "Checkpoint verified", 201)


@api_bp.get("/risk/checkpoints/<int:checkpoint_id>/explanation")
@login_required
@require_permission("safety.view")
def checkpoint_risk_explanation(checkpoint_id):
    explanation = safety_service.risk_scoring.explain_checkpoint_score(checkpoint_id)
    if not explanation:
        return api_error("Checkpoint not found", 404)
    return api_response(explanation)


@api_bp.get("/visitors")
@login_required
@require_permission("visitor.view")
def visitors():
    return api_response(visitor_service.list_visitors())
