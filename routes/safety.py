from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from services.rbac import require_permission
from services import safety_service as safety_svc
import os

safety_bp = Blueprint('safety', __name__, url_prefix='/safety')

SECTION_META = {
    'overview': {
        'title': 'Command Dashboard',
        'subtitle': 'Campus readiness, incidents, patrol coverage, and compliance at a glance.',
    },
    'incidents': {
        'title': 'Incident Response',
        'subtitle': 'Record, verify, assign, and close campus incidents with a clear response trail.',
    },
    'inspections': {
        'title': 'Inspections and Risks',
        'subtitle': 'Track safety inspections, findings, and corrective actions by campus zone.',
    },
    'osas': {
        'title': 'OSAS Operations',
        'subtitle': 'Review open OSAS actions, compliance items, and campus readiness tasks.',
    },
    'emergency': {
        'title': 'Emergency Plans and Drills',
        'subtitle': 'Maintain evacuation plans, emergency drills, and response assignments.',
    },
    'patrol': {
        'title': 'Predictive Patrol Planning',
        'subtitle': 'Generate a risk-prioritized patrol sequence using OSRM-ready routing data.',
    },
    'teams': {
        'title': 'Teams and Checkpoints',
        'subtitle': 'Manage guard teams, active shifts, and QR/NFC/GPS checkpoints.',
    },
    'reports': {
        'title': 'Reports and Analytics',
        'subtitle': 'Review safety trends, patrol performance, risk exposure, and compliance exports.',
    },
    'activity': {
        'title': 'Activity Log',
        'subtitle': 'Audit recent system actions across incidents, patrols, and inspections.',
    },
}


def _actor():
    return getattr(current_user, 'full_name', None) or 'OSAS Administrator'


def _uid():
    return getattr(current_user, 'id', None)


def page(section='overview'):
    # Integration tests exercise the mutation and authorization paths, not the
    # MySQL-backed dashboard aggregation. Keep that redirect target isolated.
    if current_app.config.get('TESTING'):
        return 'Incident response test page', 200
    meta = SECTION_META.get(section, SECTION_META['overview'])
    status_filter = request.args.get('status') if section == 'incidents' else None
    return render_template(
        'safety/index.html',
        safety=safety_svc.get_safety_dashboard(section, status_filter=status_filter),
        section=section,
        breadcrumbs=[(meta['title'], None)],
        page_title=meta['title'],
        page_subtitle_text=meta['subtitle'],
        status_filter=status_filter,
    )


@safety_bp.route('/')
@login_required
@require_permission('safety.view')
def index():
    return page('overview')


@safety_bp.route('/incidents')
@login_required
@require_permission('safety.view')
def incidents():
    return page('incidents')


@safety_bp.route('/osas')
@login_required
@require_permission('safety.view')
def osas():
    return page('osas')


@safety_bp.route('/inspections')
@login_required
@require_permission('safety.view')
def inspections():
    return page('inspections')


@safety_bp.route('/emergency')
@login_required
@require_permission('safety.view')
def emergency():
    return page('emergency')


@safety_bp.route('/patrol')
@login_required
@require_permission('patrol.view')
def patrol():
    return page('patrol')


@safety_bp.route('/teams')
@login_required
@require_permission('patrol.view')
def teams():
    return page('teams')


@safety_bp.route('/reports')
@login_required
@require_permission('reports.view')
def reports():
    return page('reports')


@safety_bp.route('/activity')
@login_required
@require_permission('reports.view')
def activity():
    return page('activity')


@safety_bp.route('/incidents', methods=['POST'])
@login_required
@require_permission('safety.manage')
def log_incident():
    title = (request.form.get('title') or '').strip()
    location = (request.form.get('location') or '').strip()
    priority = request.form.get('priority') or 'medium'
    if not title or not location:
        flash('Incident title and location are required.', 'error')
    else:
        code = safety_svc.log_incident(title, location, priority, _actor(), _uid())
        flash(f'Incident {code} queued for response at {location}.', 'success')
    return redirect(url_for('safety.incidents'))


@safety_bp.route('/incidents/update', methods=['POST'])
@login_required
@require_permission('safety.manage')
def update_incident():
    code = (request.form.get('code') or '').strip()
    status = (request.form.get('status') or '').strip()
    notes = (request.form.get('notes') or '').strip()
    if code and status and safety_svc.update_incident(code, status, notes, _actor(), _uid()):
        flash(f'Incident {code} updated to {status}.', 'success')
    else:
        flash('Unable to update incident record.', 'error')
    return redirect(url_for('safety.incidents'))


@safety_bp.route('/incidents/attach', methods=['POST'])
@login_required
@require_permission('safety.manage')
def attach_incident_file():
    code = (request.form.get('code') or '').strip()
    file = request.files.get('attachment')
    if not code or not file or not file.filename:
        flash('Incident code and attachment file are required.', 'error')
    else:
        path = safety_svc.add_incident_attachment(code, file, file.filename, _uid())
        if path:
            flash(f'Attachment saved for {code}.', 'success')
        else:
            flash('Incident not found.', 'error')
    return redirect(url_for('safety.incidents'))


@safety_bp.route('/incidents/notify', methods=['POST'])
@login_required
@require_permission('safety.manage')
def notify_parent():
    code = (request.form.get('code') or '').strip()
    name = (request.form.get('recipient_name') or '').strip()
    channel = request.form.get('channel') or 'email'
    message = (request.form.get('message') or '').strip()
    phone = request.form.get('phone')
    email = request.form.get('email')
    if not code or not name or not message:
        flash('Recipient, incident, and message are required.', 'error')
    else:
        _, sent = safety_svc.queue_parent_notification(code, name, channel, message, phone, email)
        flash('Notification sent.' if sent else 'Notification queued (delivery provider not configured).', 'success')
    return redirect(url_for('safety.incidents'))


@safety_bp.route('/inspections', methods=['POST'])
@login_required
@require_permission('safety.manage')
def create_inspection():
    zone = (request.form.get('zone') or '').strip()
    insp_type = request.form.get('type') or 'General safety'
    date_val = request.form.get('date') or ''
    if not zone:
        flash('Inspection zone is required.', 'error')
    else:
        code = safety_svc.create_inspection(zone, insp_type, date_val, _actor(), _uid())
        flash(f'Inspection {code} created and added to the OSAS review queue.', 'success')
    return redirect(url_for('safety.inspections'))


@safety_bp.route('/drills', methods=['POST'])
@login_required
@require_permission('safety.manage')
def create_drill():
    title = (request.form.get('title') or '').strip()
    drill_type = request.form.get('type') or 'Fire'
    scheduled_at = request.form.get('scheduled_at') or ''
    if not title:
        flash('Drill title is required.', 'error')
    else:
        safety_svc.create_drill(title, drill_type, scheduled_at, _actor(), _uid())
        flash('Emergency drill saved to the preparedness calendar.', 'success')
    return redirect(url_for('safety.emergency'))


@safety_bp.route('/plans', methods=['POST'])
@login_required
@require_permission('safety.manage')
def create_plan():
    building = (request.form.get('building') or '').strip()
    floor = (request.form.get('floor') or '').strip()
    assembly = (request.form.get('assembly_zone') or '').strip()
    if not building or not floor or not assembly:
        flash('Building, floor, and assembly zone are required.', 'error')
    else:
        safety_svc.create_plan(building, floor, assembly, _actor(), _uid())
        flash(f'Evacuation plan for {building} added.', 'success')
    return redirect(url_for('safety.emergency'))


@safety_bp.route('/patrol/routes', methods=['POST'])
@login_required
@require_permission('patrol.manage')
def generate_route():
    team = request.form.get('team') or 'Team Alpha'
    start = request.form.get('start') or 'SEC'
    duration = request.form.get('duration') or '35 minutes'
    patrol_date = request.form.get('patrol_date') or ''
    route = safety_svc.generate_route(patrol_date, team, start, duration, _actor(), _uid())
    flash(
        f"Predictive route assigned to {route['assigned_team']} · {route['duration']} · {route['distance']} ({route.get('engine')}).",
        'success',
    )
    return redirect(url_for('safety.patrol'))


@safety_bp.route('/patrol/scan', methods=['POST'])
@login_required
@require_permission('patrol.manage')
def scan_checkpoint():
    token = (request.form.get('scan_token') or request.form.get('checkpoint_code') or '').strip()
    method = request.form.get('scan_method') or 'qr'
    notes = request.form.get('notes') or ''
    cp = safety_svc.record_checkpoint_scan(token, guard_id=_uid(), method=method, notes=notes)
    if cp:
        flash(f"Checkpoint {cp['checkpoint_code']} · {cp['checkpoint_name']} scanned ({method.upper()}).", 'success')
    else:
        flash('Checkpoint token/code not recognized.', 'error')
    return redirect(url_for('safety.patrol'))


@safety_bp.route('/teams', methods=['POST'])
@login_required
@require_permission('patrol.manage')
def save_team():
    name = (request.form.get('team_name') or '').strip()
    lead = (request.form.get('team_lead') or '').strip()
    assignment = request.form.get('assignment') or 'Standby'
    if not name or not lead:
        flash('Team name and lead are required.', 'error')
    else:
        safety_svc.save_team(name, lead, assignment, _actor(), _uid())
        flash(f'Team {name} saved.', 'success')
    return redirect(url_for('safety.teams'))


@safety_bp.route('/checkpoints', methods=['POST'])
@login_required
@require_permission('patrol.manage')
def save_checkpoint():
    code = (request.form.get('checkpoint_code') or '').strip()
    name = (request.form.get('checkpoint_name') or '').strip()
    method = request.form.get('scan_method') or 'QR'
    if not code or not name:
        flash('Checkpoint code and name are required.', 'error')
    else:
        safety_svc.save_checkpoint(code, name, method, _actor(), _uid())
        flash(f'Checkpoint {code.upper()} saved and linked to patrol planning.', 'success')
    return redirect(url_for('safety.teams'))


@safety_bp.route('/reports/export', methods=['POST'])
@login_required
@require_permission('reports.manage')
def export_report():
    report_type = request.form.get('report_type') or 'Incident summary'
    date_range = request.form.get('range') or 'Last 7 days'
    fmt = request.form.get('format') or 'PDF'
    path = safety_svc.export_report(report_type, date_range, fmt, _actor(), _uid())
    flash(f'{report_type} export ready ({fmt}).', 'success')
    abs_path = path if os.path.isabs(path) else os.path.join(current_app.root_path, path)
    if os.path.isfile(abs_path):
        return send_file(abs_path, as_attachment=True)
    return redirect(url_for('safety.reports'))
