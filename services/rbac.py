from functools import wraps
from flask import abort, jsonify, request
from flask_login import current_user

ROLE_PERMISSIONS = {
    'admin': {'dashboard.view','safety.view','safety.manage','patrol.view','patrol.manage','visitor.view','visitor.manage','reports.view','reports.manage','users.manage','audit.view','profile.manage_own'},
    'security_head': {'dashboard.view','safety.view','safety.manage','patrol.view','patrol.manage','visitor.view','visitor.manage','reports.view','profile.manage_own'},
    'guard': {'dashboard.view','safety.view','patrol.view','patrol.manage','visitor.view','visitor.manage','profile.manage_own'},
    'safety_officer': {'dashboard.view','safety.view','safety.manage','visitor.view','reports.view','reports.manage','profile.manage_own'},
    'faculty_staff': {'dashboard.view','safety.view','visitor.view','profile.manage_own'},
}
ROLE_LABELS = {'admin':'Administrator','security_head':'Security Head','guard':'Security Guard','safety_officer':'Safety Officer','faculty_staff':'Faculty / Staff'}
PUBLIC_ENDPOINTS = frozenset({'auth.login','auth.logout','auth.index','auth.change_password','static','api.health'})
ENDPOINT_PERMISSIONS = {
    'dashboard.index':'dashboard.view', 'safety.index':'safety.view', 'safety.osas':'safety.view', 'safety.log_incident':'safety.manage',
    'safety.patrol':'patrol.view', 'safety.teams':'patrol.view', 'safety.incidents':'safety.view', 'safety.inspections':'safety.view', 'safety.emergency':'safety.view', 'safety.reports':'reports.view', 'safety.activity':'reports.view',
    'safety.create_inspection':'safety.manage', 'safety.create_drill':'safety.manage', 'safety.create_plan':'safety.manage',
    'safety.generate_route':'patrol.manage', 'safety.save_team':'patrol.manage', 'safety.save_checkpoint':'patrol.manage',
    'safety.export_report':'reports.manage', 'safety.update_incident':'safety.manage', 'safety.visitors':'visitor.view', 'safety.register_visitor':'visitor.manage', 'safety.validate_visitor':'visitor.manage',
    'safety.attach_incident_file':'safety.manage', 'safety.notify_parent':'safety.manage', 'safety.scan_checkpoint':'patrol.manage',
    'notification_read':'profile.manage_own', 'notifications_read_all':'profile.manage_own',
    'profile.index':'profile.manage_own', 'profile.update':'profile.manage_own', 'profile.upload_photo':'profile.manage_own', 'profile.remove_photo':'profile.manage_own',
    'users.index':'users.manage', 'users.create':'users.manage', 'users.update':'users.manage', 'users.reset_password':'users.manage',
    'api.campus_map':'safety.view', 'api.readiness':'safety.view', 'api.patrol_scan':'patrol.manage', 'api.visitors':'visitor.view',
}
NAV_SECTIONS = [
    {'label':'Command','items':[
        {'label':'Dashboard','endpoint':'safety.index','icon':'fa-gauge-high','permission':'dashboard.view'},
    ]},
    {'label':'Incident Response','items':[
        {'label':'Incidents','endpoint':'safety.incidents','icon':'fa-triangle-exclamation','permission':'safety.view'},
        {'label':'Emergency Plans','endpoint':'safety.emergency','icon':'fa-person-running','permission':'safety.view'},
    ]},
    {'label':'Patrol Operations','items':[
        {'label':'Patrol Planning','endpoint':'safety.patrol','icon':'fa-route','permission':'patrol.view'},
        {'label':'Teams & Checkpoints','endpoint':'safety.teams','icon':'fa-location-crosshairs','permission':'patrol.view'},
    ]},
    {'label':'Safety & Compliance','items':[
        {'label':'Inspections','endpoint':'safety.inspections','icon':'fa-clipboard-check','permission':'safety.view'},
        {'label':'Visitors & Gate','endpoint':'safety.visitors','icon':'fa-id-card','permission':'visitor.view'},
        {'label':'OSAS Operations','endpoint':'safety.osas','icon':'fa-shield-halved','permission':'safety.view'},
    ]},
    {'label':'Intelligence','items':[
        {'label':'Reports & Analytics','endpoint':'safety.reports','icon':'fa-chart-line','permission':'reports.view'},
        {'label':'Activity Log','endpoint':'safety.activity','icon':'fa-list-check','permission':'reports.view'},
    ]},
    {'label':'Administration','items':[
        {'label':'User Management','endpoint':'users.index','icon':'fa-users-gear','permission':'users.manage'},
    ]},
]
STAFF_TOOL_ENDPOINTS = frozenset({'safety.index','safety.incidents','safety.inspections','safety.emergency','safety.patrol','safety.teams','safety.reports','safety.activity'})

def has_permission(role, permission): return not permission or permission in ROLE_PERMISSIONS.get(role, set())
def get_nav_for_role(role): return [{'label':s['label'],'nav_items':[i for i in s['items'] if has_permission(role,i['permission'])]} for s in NAV_SECTIONS if any(has_permission(role,i['permission']) for i in s['items'])]
def get_role_label(role): return ROLE_LABELS.get(role, role or 'Unknown')
def check_endpoint_access(endpoint): return (not current_user.is_authenticated) or endpoint in PUBLIC_ENDPOINTS or (endpoint in ENDPOINT_PERMISSIONS and has_permission(current_user.role, ENDPOINT_PERMISSIONS[endpoint]))
def get_forbidden_context(endpoint=None): return 'This page requires permissions your role does not have. Contact an Administrator if you need access.'
def require_permission(permission):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated: abort(401)
            if not has_permission(current_user.role, permission): abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator
def require_roles(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles: abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator
def api_response(data=None, message='OK', status=200): return jsonify({'success': status < 400, 'message': message, 'data': data}), status
def api_error(message, status=400): return api_response(None, message, status)
def get_json(): return request.get_json(silent=True) or {}
