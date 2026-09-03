from datetime import datetime
import os
from flask import Flask, render_template, request
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect
from config import Config
from models.database import close_db
from models.user import User
from routes.auth import auth_bp
from routes.dashboard import dashboard_bp
from routes.safety import safety_bp
from routes.profile import profile_bp
from services.rbac import check_endpoint_access, get_forbidden_context, get_nav_for_role, get_role_label, has_permission, STAFF_TOOL_ENDPOINTS

csrf = CSRFProtect()

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config.setdefault('MYSQL_DATABASE', Config.MYSQL_DATABASE)
    os.makedirs(app.config.get('UPLOAD_FOLDER', 'static/uploads'), exist_ok=True)
    os.makedirs(app.config.get('EXPORT_FOLDER', 'static/exports'), exist_ok=True)
    os.makedirs(os.path.join(app.config.get('UPLOAD_FOLDER', 'static/uploads'), 'incidents'), exist_ok=True)
    login_manager = LoginManager(); login_manager.login_view = 'auth.login'; login_manager.init_app(app)
    csrf.init_app(app)
    @login_manager.user_loader
    def load_user(user_id): return User.get_by_id(int(user_id))
    app.teardown_appcontext(close_db)
    app.register_blueprint(auth_bp); app.register_blueprint(dashboard_bp); app.register_blueprint(safety_bp); app.register_blueprint(profile_bp)
    @app.before_request
    def enforce_rbac():
        if current_user.is_authenticated and request.endpoint and not check_endpoint_access(request.endpoint):
            from flask import abort
            abort(403)
    @app.context_processor
    def inject_globals():
        if current_user.is_authenticated:
            role = current_user.role
            return {'now':datetime.now(),'nav_sections':get_nav_for_role(role),'has_perm':lambda p:has_permission(role,p),'user_role':role,'role_label':get_role_label(role),'show_staff_banner':request.endpoint in STAFF_TOOL_ENDPOINTS,'notifications':[],'unread_count':0}
        return {'now':datetime.now(),'nav_sections':[],'has_perm':lambda p:False,'user_role':None,'role_label':None,'show_staff_banner':False,'notifications':[],'unread_count':0}
    @app.errorhandler(403)
    def forbidden(e): return render_template('errors/403.html', forbidden_message=get_forbidden_context(), attempted_endpoint=request.endpoint), 403
    @app.errorhandler(404)
    def not_found(e): return render_template('errors/404.html'), 404
    return app

app = create_app()
if __name__ == '__main__': app.run(host='0.0.0.0', port=5000, debug=True)
