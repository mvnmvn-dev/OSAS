from datetime import datetime
import logging
import os
from flask import Flask, jsonify, render_template, request
from flask_login import LoginManager, current_user, login_required
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from config import Config
from models.database import close_db
from models.user import User
from routes.auth import auth_bp
from routes.dashboard import dashboard_bp
from routes.safety import safety_bp
from routes.profile import profile_bp
from routes.users import users_bp
from routes.api import api_bp
from services.rbac import check_endpoint_access, get_forbidden_context, get_nav_for_role, get_role_label, has_permission
from services.notification_service import get_notification_panel, mark_all_read, mark_read

csrf = CSRFProtect()
logger = logging.getLogger(__name__)

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config.setdefault('MYSQL_DATABASE', Config.MYSQL_DATABASE)
    # Trust Render / reverse-proxy HTTPS headers so secure cookies work
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    os.makedirs(app.config.get('UPLOAD_FOLDER', 'static/uploads'), exist_ok=True)
    os.makedirs(app.config.get('EXPORT_FOLDER', 'static/exports'), exist_ok=True)
    os.makedirs(os.path.join(app.config.get('UPLOAD_FOLDER', 'static/uploads'), 'incidents'), exist_ok=True)
    os.makedirs(os.path.join(app.config.get('UPLOAD_FOLDER', 'static/uploads'), 'avatars'), exist_ok=True)
    login_manager = LoginManager(); login_manager.login_view = 'auth.login'; login_manager.init_app(app)
    csrf.init_app(app)
    @login_manager.user_loader
    def load_user(user_id): return User.get_by_id(int(user_id))
    app.teardown_appcontext(close_db)
    app.register_blueprint(auth_bp); app.register_blueprint(dashboard_bp); app.register_blueprint(safety_bp); app.register_blueprint(profile_bp); app.register_blueprint(users_bp); app.register_blueprint(api_bp)
    @app.before_request
    def enforce_rbac():
        if current_user.is_authenticated and request.endpoint and not check_endpoint_access(request.endpoint):
            from flask import abort
            abort(403)
    @app.context_processor
    def inject_globals():
        if current_user.is_authenticated:
            role = current_user.role
            try:
                panel = get_notification_panel(current_user.id)
            except Exception:
                panel = {
                    "items": [],
                    "unread_count": 0,
                    "ops_alerts": [],
                    "ops_alert_count": 0,
                    "total_attention": 0,
                    "header_status": {
                        "tone": "operational",
                        "label": "System Operational",
                        "rows": [
                            ("Application", "Operational", ""),
                            ("Database", "Operational", ""),
                            ("Notifications", "Operational", ""),
                            ("QR / Patrol Scan", "Operational", ""),
                        ],
                    },
                }
            return {
                "now": datetime.now(),
                "nav_sections": get_nav_for_role(role),
                "has_perm": lambda p: has_permission(role, p),
                "user_role": role,
                "role_label": get_role_label(role),
                "show_staff_banner": False,
                "notifications": panel["items"],
                "unread_count": panel["unread_count"],
                "ops_alerts": panel["ops_alerts"],
                "ops_alert_count": panel["ops_alert_count"],
                "notif_attention": panel["total_attention"],
                "header_status": panel["header_status"],
            }
        return {
            "now": datetime.now(),
            "nav_sections": [],
            "has_perm": lambda p: False,
            "user_role": None,
            "role_label": None,
            "show_staff_banner": False,
            "notifications": [],
            "unread_count": 0,
            "ops_alerts": [],
            "ops_alert_count": 0,
            "notif_attention": 0,
            "header_status": None,
        }
    @app.errorhandler(403)
    def forbidden(e): return render_template('errors/403.html', forbidden_message=get_forbidden_context(), attempted_endpoint=request.endpoint), 403
    @app.errorhandler(404)
    def not_found(e): return render_template('errors/404.html'), 404
    @app.post('/notifications/<int:notification_id>/read')
    @login_required
    def notification_read(notification_id):
        unread = mark_read(notification_id, current_user.id)
        return jsonify({"ok": True, "data": {"unread_count": unread}})
    @app.post('/notifications/read-all')
    @login_required
    def notifications_read_all():
        mark_all_read(current_user.id)
        return jsonify({"ok": True, "data": {"unread_count": 0}})
    return app

app = create_app()

# Ensure schema/seed exist when MySQL is reachable (Railway private network).
if os.getenv("AUTO_DB_BOOTSTRAP", "1").lower() in ("1", "true", "yes") and not app.config.get("TESTING"):
    try:
        from scripts.railway_bootstrap import bootstrap_with_app
        bootstrap_with_app(app)
    except Exception:
        logger.exception(
            "DB bootstrap failed (host=%s db=%s). Login will fail until MySQL vars are fixed.",
            app.config.get("MYSQL_HOST"),
            app.config.get("MYSQL_DATABASE"),
        )
if __name__ == '__main__':
    # Prefer FLASK_DEBUG / Config.DEBUG. Never force debug in production.
    debug = bool(app.config.get('DEBUG')) and app.config.get('FLASK_ENV') != 'production'
    host = os.getenv('FLASK_HOST', '127.0.0.1' if not debug else '0.0.0.0')
    port = int(os.getenv('PORT') or os.getenv('FLASK_PORT', '5000'))
    app.run(host=host, port=port, debug=debug)
