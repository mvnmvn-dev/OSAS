"""Routes for the OSAS Team 5 safety modules (contacts, drills, plans, inspections, risks,
parent notifications, emergency roles, first aid and compliance reports)."""
from flask import Blueprint, Response, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from models.database import query_all
from services import compliance_reports as reports_svc
from services import osas_modules_service as svc
from services.osas_modules_service import ModuleError
from services.pagination import paginate_items
from services.rbac import require_permission

modules_bp = Blueprint("modules", __name__, url_prefix="/modules")

PER_PAGE = 10


@modules_bp.before_request
def _prepare():
    # Upgrade schema / seed reference data before any module page or action runs.
    if current_user.is_authenticated:
        svc.ensure_module_schema()


def _uid():
    return getattr(current_user, "id", None)


def _lookups():
    """Shared select-box data."""
    return {
        "teams": query_all("SELECT id, team_name FROM patrol_teams WHERE active=1 ORDER BY team_name"),
        "checkpoints": query_all("SELECT id, checkpoint_code, checkpoint_name FROM checkpoints WHERE active=1 ORDER BY checkpoint_code"),
        "zones": svc.list_zones(),
    }


def _act(action, success, endpoint, **redirect_args):
    """Run ``action()``; flash ``success`` (string or callable of the result) or the validation error."""
    try:
        result = action()
        flash(success(result) if callable(success) else success, "success")
    except ModuleError as exc:
        flash(str(exc), "error")
    return redirect(url_for(endpoint, **redirect_args))


def _page(template, title, subtitle, **context):
    return render_template(template, page_title=title, page_subtitle_text=subtitle,
                           breadcrumbs=[(title, None)], **context)


# --------------------------------------------------------------------------- #
# Emergency contacts
# --------------------------------------------------------------------------- #
@modules_bp.route("/contacts")
@login_required
@require_permission("safety.view")
def contacts():
    search = request.args.get("q", "").strip()
    ctype = request.args.get("type", "")
    show_all = request.args.get("inactive") == "1"
    rows = svc.list_contacts(search, ctype, include_inactive=show_all)
    items, pagination = paginate_items(rows, per_page=PER_PAGE)
    return _page("modules/contacts.html", "Emergency Contacts",
                 "Searchable directory of internal, government and responder numbers for emergencies.",
                 contacts=items, pagination=pagination, summary=svc.contact_summary(), types=svc.CONTACT_TYPES,
                 q=search, ctype=ctype, show_all=show_all)


@modules_bp.route("/contacts", methods=["POST"])
@login_required
@require_permission("safety.manage")
def contact_save():
    contact_id = request.form.get("id", type=int)
    return _act(lambda: svc.save_contact(request.form, contact_id, _uid()),
                "Contact updated." if contact_id else "Contact added.", "modules.contacts")


@modules_bp.route("/contacts/<int:contact_id>/toggle", methods=["POST"])
@login_required
@require_permission("safety.manage")
def contact_toggle(contact_id):
    active = request.form.get("active") == "1"
    return _act(lambda: svc.set_contact_active(contact_id, active, _uid()),
                "Contact activated." if active else "Contact deactivated.", "modules.contacts")


@modules_bp.route("/contacts/<int:contact_id>/delete", methods=["POST"])
@login_required
@require_permission("safety.manage")
def contact_delete(contact_id):
    return _act(lambda: svc.delete_contact(contact_id, _uid()), "Contact deleted.", "modules.contacts")


# --------------------------------------------------------------------------- #
# Drill scheduling
# --------------------------------------------------------------------------- #
@modules_bp.route("/drills")
@login_required
@require_permission("safety.view")
def drills():
    status = request.args.get("status", "")
    dtype = request.args.get("type", "")
    rows = svc.list_drills(status or None, dtype or None)
    items, pagination = paginate_items(rows, per_page=PER_PAGE)
    return _page("modules/drills.html", "Drill Scheduling",
                 "Plan, run and evaluate emergency drills with participation records.",
                 drills=items, pagination=pagination, summary=svc.drill_summary(), types=svc.DRILL_TYPES,
                 status=status, dtype=dtype, statuses=svc.DRILL_STATUS_LABELS, **_lookups())


@modules_bp.route("/drills", methods=["POST"])
@login_required
@require_permission("safety.manage")
def drill_save():
    drill_id = request.form.get("id", type=int)
    if drill_id:
        return _act(lambda: svc.update_drill(drill_id, request.form, _uid()), "Drill updated.", "modules.drills")
    return _act(lambda: svc.schedule_drill(request.form, _uid()), "Drill scheduled.", "modules.drills")


@modules_bp.route("/drills/<int:drill_id>/status", methods=["POST"])
@login_required
@require_permission("safety.manage")
def drill_status(drill_id):
    new_status = request.form.get("status", "")
    return _act(lambda: svc.transition_drill(drill_id, new_status, request.form, _uid()),
                f"Drill marked {new_status.replace('_', ' ')}.", "modules.drills")


@modules_bp.route("/drills/<int:drill_id>/delete", methods=["POST"])
@login_required
@require_permission("safety.manage")
def drill_delete(drill_id):
    return _act(lambda: svc.delete_drill(drill_id, _uid()), "Drill deleted.", "modules.drills")


# --------------------------------------------------------------------------- #
# Evacuation maps & plans
# --------------------------------------------------------------------------- #
@modules_bp.route("/plans")
@login_required
@require_permission("safety.view")
def plans():
    building_id = request.args.get("building", type=int)
    rows = svc.list_plans(building_id)
    items, pagination = paginate_items(rows, per_page=PER_PAGE)
    return _page("modules/plans.html", "Evacuation Maps & Plans",
                 "Versioned evacuation plans with floor maps, assembly areas and an approval step.",
                 plans=items, pagination=pagination, coverage=svc.plan_coverage(), buildings=svc.list_buildings(),
                 assembly_zones=svc.list_zones(assembly_only=True) or svc.list_zones(), building_id=building_id)


@modules_bp.route("/plans", methods=["POST"])
@login_required
@require_permission("safety.manage")
def plan_save():
    return _act(lambda: svc.create_plan_version(request.form, request.files.get("map_file"), _uid()),
                "Plan version saved. It becomes current once approved.", "modules.plans")


@modules_bp.route("/plans/<int:plan_id>/map")
@login_required
@require_permission("safety.view")
def plan_map(plan_id):
    found = svc.plan_map_file(plan_id)
    if not found:
        abort(404)
    path, name, mimetype = found
    response = send_file(path, mimetype=mimetype, download_name=name,
                         as_attachment=request.args.get("download") == "1", conditional=True)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    return response


@modules_bp.route("/plans/<int:plan_id>/approve", methods=["POST"])
@login_required
@require_permission("safety.manage")
def plan_approve(plan_id):
    return _act(lambda: svc.approve_plan(plan_id, _uid()), "Plan approved and set as current.", "modules.plans")


@modules_bp.route("/plans/<int:plan_id>/delete", methods=["POST"])
@login_required
@require_permission("safety.manage")
def plan_delete(plan_id):
    return _act(lambda: svc.delete_plan(plan_id, _uid()), "Plan version deleted.", "modules.plans")


# --------------------------------------------------------------------------- #
# Safety inspection checklist
# --------------------------------------------------------------------------- #
@modules_bp.route("/inspections")
@login_required
@require_permission("safety.view")
def inspections():
    status = request.args.get("status", "")
    rows = svc.list_inspections(status or None)
    items, pagination = paginate_items(rows, per_page=PER_PAGE)
    return _page("modules/inspections.html", "Inspection Checklists",
                 "Schedule inspections, work through the checklist, and track corrective actions to closure.",
                 inspections=items, pagination=pagination, types=svc.INSPECTION_TYPES, status=status,
                 users=svc.list_assignable_users(), statuses=svc.INSPECTION_STATUS_CLASS, **_lookups())


@modules_bp.route("/inspections", methods=["POST"])
@login_required
@require_permission("safety.manage")
def inspection_schedule():
    try:
        inspection_id = svc.schedule_inspection(request.form, _uid())
    except ModuleError as exc:
        flash(str(exc), "error")
        return redirect(url_for("modules.inspections"))
    flash("Inspection scheduled with its checklist.", "success")
    return redirect(url_for("modules.inspection_detail", inspection_id=inspection_id))


@modules_bp.route("/inspections/<int:inspection_id>")
@login_required
@require_permission("safety.view")
def inspection_detail(inspection_id):
    inspection = svc.get_inspection(inspection_id)
    if not inspection:
        abort(404)
    return _page("modules/inspection_detail.html", f"Inspection {inspection['inspection_code']}",
                 f"{inspection['type_label']} · {inspection['zone_name']}", inspection=inspection)


@modules_bp.route("/inspections/<int:inspection_id>/items/<int:item_id>", methods=["POST"])
@login_required
@require_permission("safety.manage")
def inspection_item_result(inspection_id, item_id):
    return _act(lambda: svc.record_item_result(inspection_id, item_id, request.form.get("result"),
                                               request.form.get("finding"), request.form.get("corrective_action"), _uid()),
                "Checklist item recorded.", "modules.inspection_detail", inspection_id=inspection_id)


@modules_bp.route("/inspections/<int:inspection_id>/items", methods=["POST"])
@login_required
@require_permission("safety.manage")
def inspection_item_add(inspection_id):
    return _act(lambda: svc.add_inspection_item(inspection_id, request.form.get("item_name"),
                                                request.form.get("category") or "Custom", _uid()),
                "Checklist item added.", "modules.inspection_detail", inspection_id=inspection_id)


@modules_bp.route("/inspections/<int:inspection_id>/items/<int:item_id>/delete", methods=["POST"])
@login_required
@require_permission("safety.manage")
def inspection_item_delete(inspection_id, item_id):
    return _act(lambda: svc.delete_inspection_item(inspection_id, item_id, _uid()),
                "Checklist item removed.", "modules.inspection_detail", inspection_id=inspection_id)


@modules_bp.route("/inspections/<int:inspection_id>/items/<int:item_id>/action", methods=["POST"])
@login_required
@require_permission("safety.manage")
def inspection_action_toggle(inspection_id, item_id):
    done = request.form.get("done") == "1"
    return _act(lambda: svc.set_action_done(inspection_id, item_id, done, _uid()),
                "Corrective action closed." if done else "Corrective action reopened.",
                "modules.inspection_detail", inspection_id=inspection_id)


@modules_bp.route("/inspections/<int:inspection_id>/complete", methods=["POST"])
@login_required
@require_permission("safety.manage")
def inspection_complete(inspection_id):
    def msg(result):
        text = f"Inspection completed with a score of {result['score']:.0f}%."
        if result["risks_created"]:
            text += f" {result['risks_created']} risk assessment(s) were opened for failed items."
        return text

    return _act(lambda: svc.complete_inspection(inspection_id, request.form.get("summary"), _uid()),
                msg, "modules.inspection_detail", inspection_id=inspection_id)


@modules_bp.route("/inspections/<int:inspection_id>/cancel", methods=["POST"])
@login_required
@require_permission("safety.manage")
def inspection_cancel(inspection_id):
    return _act(lambda: svc.cancel_inspection(inspection_id, request.form.get("reason"), _uid()),
                "Inspection cancelled.", "modules.inspections")


# --------------------------------------------------------------------------- #
# Risk assessment
# --------------------------------------------------------------------------- #
@modules_bp.route("/risks")
@login_required
@require_permission("safety.view")
def risks():
    status = request.args.get("status", "")
    level = request.args.get("level", "")
    rows = svc.list_risks(status or None, level or None)
    items, pagination = paginate_items(rows, per_page=PER_PAGE)
    return _page("modules/risks.html", "Risk Assessment",
                 "Score hazards by likelihood × severity, assign mitigation and track them to closure.",
                 risks=items, pagination=pagination, matrix=svc.risk_matrix(), summary=svc.risk_summary(),
                 zones=svc.list_zones(), status=status, level=level, statuses=svc.RISK_STATUSES,
                 likelihoods=svc.LIKELIHOOD_LABELS, severities=svc.SEVERITY_LABELS)


@modules_bp.route("/risks", methods=["POST"])
@login_required
@require_permission("safety.manage")
def risk_save():
    risk_id = request.form.get("id", type=int)
    if risk_id:
        return _act(lambda: svc.update_risk(risk_id, request.form, _uid()), "Risk assessment updated.", "modules.risks")
    return _act(lambda: svc.create_risk(request.form, _uid()), "Risk assessment recorded.", "modules.risks")


@modules_bp.route("/risks/<int:risk_id>/status", methods=["POST"])
@login_required
@require_permission("safety.manage")
def risk_status(risk_id):
    new_status = request.form.get("status", "")
    return _act(lambda: svc.set_risk_status(risk_id, new_status, request.form.get("note"), _uid()),
                f"Risk marked {new_status}.", "modules.risks")


# --------------------------------------------------------------------------- #
# Parent notifications
# --------------------------------------------------------------------------- #
@modules_bp.route("/notifications")
@login_required
@require_permission("safety.manage")
def notifications():
    status = request.args.get("status", "")
    channel = request.args.get("channel", "")
    q = request.args.get("q", "").strip()
    rows = svc.list_notifications(status or None, channel or None, q)
    items, pagination = paginate_items(rows, per_page=PER_PAGE)
    return _page("modules/notifications.html", "Parent Notifications",
                 "Send incident notices to parents and guardians and track delivery.",
                 notifications=items, pagination=pagination, summary=svc.notification_summary(),
                 channels=svc.NOTIFICATION_CHANNELS, templates=svc.MESSAGE_TEMPLATES,
                 status=status, channel=channel, q=q, prefill_incident=request.args.get("incident", ""))


@modules_bp.route("/notifications", methods=["POST"])
@login_required
@require_permission("safety.manage")
def notification_send():
    try:
        _, status = svc.send_parent_notification(request.form, _uid())
    except ModuleError as exc:
        flash(str(exc), "error")
        return redirect(url_for("modules.notifications"))
    if status == "sent":
        flash("Notification sent.", "success")
    elif status == "failed":
        flash("Delivery failed. The notice is saved; use Retry once the problem is fixed.", "error")
    else:
        flash("Notification saved but not delivered: no delivery provider is configured for this channel.", "error")
    return redirect(url_for("modules.notifications"))


@modules_bp.route("/notifications/<int:notification_id>/retry", methods=["POST"])
@login_required
@require_permission("safety.manage")
def notification_retry(notification_id):
    return _act(lambda: svc.retry_notification(notification_id, _uid()),
                lambda status: f"Retry finished: {status}.", "modules.notifications")


@modules_bp.route("/notifications/retry-all", methods=["POST"])
@login_required
@require_permission("safety.manage")
def notification_retry_all():
    return _act(lambda: svc.retry_pending_notifications(),
                lambda r: f"Retried pending notices: {r['sent']} sent, {r['failed']} failed, {r['queued']} still queued.",
                "modules.notifications")


# --------------------------------------------------------------------------- #
# Emergency roles
# --------------------------------------------------------------------------- #
@modules_bp.route("/roles")
@login_required
@require_permission("safety.view")
def roles():
    drill_id = request.args.get("drill", type=int)
    rows = svc.list_roles(drill_id=drill_id)
    return _page("modules/roles.html", "Emergency Roles",
                 "Assign accountable people and backups to each emergency response role.",
                 roles=rows, coverage=svc.role_coverage(), users=svc.list_assignable_users(),
                 drills=svc.list_drills(), drill_id=drill_id)


@modules_bp.route("/roles", methods=["POST"])
@login_required
@require_permission("safety.manage")
def role_save():
    return _act(lambda: svc.create_role(request.form, _uid()), "Emergency role created.", "modules.roles")


@modules_bp.route("/roles/<int:role_id>/assign", methods=["POST"])
@login_required
@require_permission("safety.manage")
def role_assign(role_id):
    return _act(lambda: svc.assign_role(role_id, request.form.get("assigned_user_id"),
                                        request.form.get("backup_user_id"), _uid()),
                "Role assignment saved.", "modules.roles")


@modules_bp.route("/roles/<int:role_id>/delete", methods=["POST"])
@login_required
@require_permission("safety.manage")
def role_delete(role_id):
    return _act(lambda: svc.delete_role(role_id, _uid()), "Role deleted.", "modules.roles")


# --------------------------------------------------------------------------- #
# First aid supplies
# --------------------------------------------------------------------------- #
@modules_bp.route("/first-aid")
@login_required
@require_permission("safety.view")
def first_aid():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    location = request.args.get("location", "")
    rows = svc.list_first_aid(q, status or None, location or None)
    items, pagination = paginate_items(rows, per_page=PER_PAGE)
    return _page("modules/first_aid.html", "First Aid Supplies",
                 "Monitor cabinet stock, reorder levels and expiry dates.",
                 items=items, pagination=pagination, summary=svc.first_aid_summary(), locations=svc.first_aid_locations(),
                 q=q, status=status, location=location, statuses=svc.FIRST_AID_STATUS_LABELS)


@modules_bp.route("/first-aid", methods=["POST"])
@login_required
@require_permission("safety.manage")
def first_aid_save():
    item_id = request.form.get("id", type=int)
    return _act(lambda: svc.save_first_aid_item(request.form, item_id, _uid()),
                "Item updated." if item_id else "Item added.", "modules.first_aid")


@modules_bp.route("/first-aid/<int:item_id>/adjust", methods=["POST"])
@login_required
@require_permission("safety.manage")
def first_aid_adjust(item_id):
    direction = -1 if request.form.get("direction") == "use" else 1
    try:
        amount = abs(int(request.form.get("amount", "0")))
    except ValueError:
        amount = 0
    return _act(lambda: svc.adjust_stock(item_id, direction * amount, request.form.get("reason"),
                                         request.form.get("expiry_date"), _uid()),
                lambda qty: f"Stock updated. {qty} now on hand.", "modules.first_aid")


@modules_bp.route("/first-aid/<int:item_id>/check", methods=["POST"])
@login_required
@require_permission("safety.manage")
def first_aid_check(item_id):
    return _act(lambda: svc.mark_first_aid_checked(item_id, _uid()), "Item marked as checked.", "modules.first_aid")


@modules_bp.route("/first-aid/<int:item_id>/delete", methods=["POST"])
@login_required
@require_permission("safety.manage")
def first_aid_delete(item_id):
    return _act(lambda: svc.delete_first_aid_item(item_id, _uid()), "Item deleted.", "modules.first_aid")


# --------------------------------------------------------------------------- #
# Safety compliance reports
# --------------------------------------------------------------------------- #
@modules_bp.route("/reports")
@login_required
@require_permission("reports.view")
def reports():
    key = request.args.get("report", "safety_compliance")
    preset = request.args.get("range", "30d")
    start, end = request.args.get("start"), request.args.get("end")
    report, error = None, None
    try:
        report = reports_svc.build_report(key, start=start, end=end, preset=preset)
    except ModuleError as exc:
        error = str(exc)
    shown_rows = report["rows"][:200] if report else []
    return _page("modules/reports.html", "Safety Compliance Reports",
                 "Preview any report on screen, then export it as PDF, Excel or CSV.",
                 report=report, shown_rows=shown_rows, error=error, report_types=reports_svc.REPORT_TYPES,
                 presets=reports_svc.RANGE_PRESETS, key=key, preset=preset, start=start or "", end=end or "")


@modules_bp.route("/reports/download")
@login_required
@require_permission("reports.manage")
def report_download():
    try:
        content, mimetype, filename = reports_svc.export_report_bytes(
            request.args.get("report", "safety_compliance"), request.args.get("format", "pdf"),
            preset=request.args.get("range", "30d"), start=request.args.get("start"), end=request.args.get("end"),
            user_id=_uid())
    except ModuleError as exc:
        flash(str(exc), "error")
        return redirect(url_for("modules.reports"))
    response = Response(content, mimetype=mimetype)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
