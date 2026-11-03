"""Tests for the OSAS Team 5 modules (SQLite test database)."""
import io
from datetime import datetime, timedelta

import pytest

from app import create_app
from models.user import User
from services import compliance_reports as reports
from services import osas_modules_service as svc
from services.osas_modules_service import ModuleError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def app(tmp_path):
    application = create_app()
    application.config.update(
        TESTING=True, WTF_CSRF_ENABLED=False, SECRET_KEY="test",
        TEST_DATABASE=str(tmp_path / "t.sqlite3"),
        PRIVATE_UPLOAD_FOLDER=str(tmp_path / "private"),
        SMTP_HOST="", RESEND_API_KEY="", SMS_PROVIDER_URL="",
    )
    return application


@pytest.fixture
def ctx(app):
    with app.app_context():
        from models.database import execute
        from services.safety_service import ensure_campus_seed
        ensure_campus_seed()
        svc.ensure_module_schema()
        execute("INSERT INTO users (username,password_hash,email,full_name,role,campus_id) VALUES ('adm',%s,'a@x.edu','Admin One','admin','C1')",
                (User.hash_password("pw"),))
        execute("INSERT INTO users (username,password_hash,email,full_name,role,campus_id) VALUES ('fac',%s,'f@x.edu','Faculty One','faculty_staff','C2')",
                (User.hash_password("pw"),))
        yield app


def _login(client, who="adm"):
    client.post("/login", data={"username": who, "password": "pw"}, follow_redirects=True)


# ---------------------------------------------------------------- contacts
def test_contacts_validation_and_crud(ctx):
    assert svc.list_contacts()[0]["phone"] == "911"
    with pytest.raises(ModuleError):
        svc.save_contact({"contact_name": "X", "phone": "abc"})
    with pytest.raises(ModuleError):
        svc.save_contact({"contact_name": "X", "phone": "123456", "email": "bad"})
    cid = svc.save_contact({"contact_name": "Fire Stn", "phone": "(02) 8123-4567", "contact_type": "fire"})
    with pytest.raises(ModuleError):
        svc.save_contact({"contact_name": "fire stn", "phone": "(02) 8123-4567"})
    svc.save_contact({"contact_name": "Fire Station", "phone": "8123456", "contact_type": "fire"}, cid)
    assert svc.list_contacts(search="station")[0]["contact_name"] == "Fire Station"
    svc.set_contact_active(cid, False)
    assert all(c["id"] != cid for c in svc.list_contacts())
    svc.delete_contact(cid)
    assert svc.get_contact(cid) is None


# ------------------------------------------------------------------ drills
def test_drill_lifecycle_and_conflict(ctx):
    from models.database import query_one
    team = query_one("SELECT id FROM patrol_teams LIMIT 1")["id"]
    when = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    did = svc.schedule_drill({"title": "Fire drill", "drill_type": "fire", "scheduled_at": when, "team_id": team})
    with pytest.raises(ModuleError):
        svc.schedule_drill({"title": "Clash", "drill_type": "fire", "scheduled_at": when, "team_id": team})
    with pytest.raises(ModuleError):
        svc.transition_drill(did, "completed", {})  # participants required
    with pytest.raises(ModuleError):
        svc.transition_drill(did, "cancelled", {})  # reason required
    svc.transition_drill(did, "in_progress")
    svc.transition_drill(did, "completed", {"participant_count": "120", "duration_minutes": "15", "evaluation_score": "88"})
    d = svc.get_drill(did)
    assert d["status_key"] == "completed" and d["participant_count"] == 120 and d["completed_at"]
    with pytest.raises(ModuleError):
        svc.update_drill(did, {"title": "x", "scheduled_at": when})
    with pytest.raises(ModuleError):
        svc.delete_drill(did)
    assert svc.drill_summary()["completion_rate"] == 100


# ------------------------------------------------------------------- plans
def test_plan_versioning_and_map_upload(ctx):
    from werkzeug.datastructures import FileStorage
    zone = svc.list_zones()[0]["id"]
    bad = FileStorage(io.BytesIO(b"<svg onload=alert(1)>"), filename="map.png")
    with pytest.raises(ModuleError):
        svc.create_plan_version({"new_building": "Gym", "floor_label": "G", "assembly_zone_id": zone}, bad)
    good = FileStorage(io.BytesIO(PNG), filename="map.png")
    p1 = svc.create_plan_version({"new_building": "Gym", "floor_label": "G", "assembly_zone_id": zone}, good, approve=True)
    p2 = svc.create_plan_version({"new_building": "Gym", "floor_label": "g", "assembly_zone_id": zone})
    plans = {p["id"]: p for p in svc.list_plans()}
    assert plans[p1]["status_label"] == "Current" and plans[p2]["status_label"] == "Pending approval"
    assert plans[p2]["version_no"] == 2
    assert svc.plan_map_file(p1) is not None
    svc.approve_plan(p2)
    plans = {p["id"]: p for p in svc.list_plans()}
    assert plans[p1]["status_label"] == "Superseded" and plans[p2]["status_label"] == "Current"
    with pytest.raises(ModuleError):
        svc.delete_plan(p2)
    svc.delete_plan(p1)
    assert svc.plan_map_file(p1) is None


# ------------------------------------------------------------- inspections
def test_inspection_checklist_flow(ctx):
    zone = svc.list_zones()[0]["id"]
    iid = svc.schedule_inspection({"zone_id": zone, "inspection_type": "Fire safety", "scheduled_date": datetime.now().date().isoformat()})
    insp = svc.get_inspection(iid)
    assert insp["item_total"] == len(svc.CHECKLIST_TEMPLATES["Fire safety"])
    with pytest.raises(ModuleError):
        svc.complete_inspection(iid)  # items pending
    first = insp["items"][0]["id"]
    with pytest.raises(ModuleError):
        svc.record_item_result(iid, first, "fail", "")  # finding required
    svc.record_item_result(iid, first, "fail", "Extinguisher empty", "Recharge")
    for item in insp["items"][1:]:
        svc.record_item_result(iid, item["id"], "pass")
    assert svc.get_inspection(iid)["status"] == "in_progress"
    result = svc.complete_inspection(iid, "Done")
    assert result["failed"] == 1 and result["risks_created"] == 1
    assert round(result["score"]) == round(8 / 9 * 100)
    assert svc.list_risks()[0]["hazard_title"].endswith(insp["items"][0]["item_name"])
    with pytest.raises(ModuleError):
        svc.record_item_result(iid, first, "pass")  # closed


def test_overdue_flagging(ctx):
    zone = svc.list_zones()[0]["id"]
    iid = svc.schedule_inspection({"zone_id": zone, "inspection_type": "General safety",
                                   "scheduled_date": (datetime.now() - timedelta(days=2)).date().isoformat()})
    assert svc.get_inspection(iid)["status"] == "overdue"


# ------------------------------------------------------------------- risks
def test_risk_scoring_and_workflow(ctx):
    zone = svc.list_zones()[0]["id"]
    assert svc.risk_level(25)[0] == "Critical" and svc.risk_level(12)[0] == "High" and svc.risk_level(6)[0] == "Medium"
    with pytest.raises(ModuleError):
        svc.create_risk({"zone_id": zone, "hazard_title": "x", "likelihood": 9, "severity": 1})
    rid = svc.create_risk({"zone_id": zone, "hazard_title": "Loose railing", "likelihood": 4, "severity": 4})
    assert svc.get_risk(rid)["score"] == 16
    with pytest.raises(ModuleError):
        svc.set_risk_status(rid, "mitigating")  # no plan or note
    with pytest.raises(ModuleError):
        svc.set_risk_status(rid, "accepted")  # needs justification
    svc.set_risk_status(rid, "mitigating", "Barrier installed")
    svc.set_risk_status(rid, "resolved")
    assert svc.get_risk(rid)["status"] == "resolved"
    assert sum(c["count"] for row in svc.risk_matrix() for c in row) == 0


# ----------------------------------------------------------- notifications
def test_notifications_status_and_retry(ctx, monkeypatch):
    with pytest.raises(ModuleError):
        svc.send_parent_notification({"recipient_name": "P", "channel": "email", "message": "hi"})
    nid, status = svc.send_parent_notification({"recipient_name": "Parent", "channel": "email", "email": "p@x.com", "message": "hi"})
    assert status == "queued"  # no provider configured
    ctx.config["SMTP_HOST"] = "smtp.test"
    monkeypatch.setattr(svc, "_deliver_email", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert svc.retry_notification(nid) == "failed"
    row = svc.list_notifications()[0]
    assert row["status"] == "failed" and "boom" in row["error_message"] and row["attempts"] == 1
    monkeypatch.setattr(svc, "_deliver_email", lambda *a, **k: None)
    assert svc.retry_notification(nid) == "sent"
    with pytest.raises(ModuleError):
        svc.retry_notification(nid)
    assert svc.notification_summary()["delivery_rate"] == 100


# ------------------------------------------------------------------- roles
def test_roles_assignment(ctx):
    from models.database import query_one
    uid = query_one("SELECT id FROM users WHERE username='adm'")["id"]
    fac = query_one("SELECT id FROM users WHERE username='fac'")["id"]
    roles = svc.list_roles()
    assert len(roles) >= 10 and svc.role_coverage()["assigned"] == 0
    rid = roles[0]["id"]
    with pytest.raises(ModuleError):
        svc.assign_role(rid, uid, uid)
    with pytest.raises(ModuleError):
        svc.assign_role(rid, None, fac)
    with pytest.raises(ModuleError):
        svc.assign_role(rid, 9999)
    svc.assign_role(rid, uid, fac)
    assert svc.role_coverage()["assigned"] == 1
    with pytest.raises(ModuleError):
        svc.create_role({"role_name": roles[0]["role_name"], "responsibility": "dup"})


# ---------------------------------------------------------------- first aid
def test_first_aid_stock_logic(ctx):
    assert svc.compute_stock_status(0, 2, None) == "out_of_stock"
    assert svc.compute_stock_status(2, 2, None) == "low_stock"
    assert svc.compute_stock_status(5, 2, "2000-01-01") == "expired"
    iid = svc.save_first_aid_item({"item_name": "Bandages", "cabinet_location": "Clinic", "quantity": "10", "reorder_level": "4"})
    with pytest.raises(ModuleError):
        svc.save_first_aid_item({"item_name": "bandages", "cabinet_location": "clinic", "quantity": "1"})
    with pytest.raises(ModuleError):
        svc.adjust_stock(iid, -11)
    svc.adjust_stock(iid, -7, "Incident")
    item = [i for i in svc.list_first_aid() if i["id"] == iid][0]
    assert item["quantity"] == 3 and item["status"] == "low_stock"
    svc.adjust_stock(iid, 20, "Restock", new_expiry="2099-01-01")
    item = [i for i in svc.list_first_aid() if i["id"] == iid][0]
    assert item["status"] == "available" and item["last_restocked_at"]


# ----------------------------------------------------------------- reports
def test_reports_all_types_and_formats(ctx):
    for key, *_ in reports.REPORT_TYPES:
        rep = reports.build_report(key, preset="30d")
        assert rep["headers"] and isinstance(rep["rows"], list) and rep["summary"]
        for fmt, magic in (("csv", b"\xef\xbb\xbf"), ("excel", b"PK"), ("pdf", b"%PDF")):
            data, _mime, _ext = reports.render_report(rep, fmt)
            assert data.startswith(magic)


def test_report_range_validation_and_formula_escape(ctx):
    with pytest.raises(ModuleError):
        reports.resolve_range("custom", "2026-02-01", "2026-01-01")
    with pytest.raises(ModuleError):
        reports.resolve_range("custom", None, None)
    svc.save_contact({"contact_name": "=HYPERLINK(\"http://x\")", "phone": "12345"})
    rep = reports.build_report("emergency_contacts")
    data, _, _ = reports.render_csv(rep)
    assert b"'=HYPERLINK" in data


def test_report_date_range_is_applied(ctx):
    from models.database import execute
    execute("INSERT INTO incidents (incident_code,title,reported_at) VALUES ('OLD-1','Old','2020-01-01 10:00:00')")
    execute("INSERT INTO incidents (incident_code,title,reported_at) VALUES ('NEW-1','New',%s)", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
    codes = [r[0] for r in reports.build_report("incident_summary", preset="7d")["rows"]]
    assert "NEW-1" in codes and "OLD-1" not in codes
    codes = [r[0] for r in reports.build_report("incident_summary", preset="custom", start="2019-12-01", end="2020-02-01")["rows"]]
    assert codes == ["OLD-1"]


# ------------------------------------------------------------- HTTP / RBAC
PAGES = ["contacts", "drills", "plans", "inspections", "risks", "notifications", "roles", "first-aid", "reports"]


def test_pages_render_for_admin(ctx):
    client = ctx.test_client()
    _login(client)
    for page in PAGES:
        resp = client.get(f"/modules/{page}")
        assert resp.status_code == 200, page
    assert client.get("/modules/reports/download?report=safety_compliance&format=pdf").data.startswith(b"%PDF")


def test_rbac_blocks_faculty_writes_and_notifications(ctx):
    client = ctx.test_client()
    _login(client, "fac")
    assert client.get("/modules/contacts").status_code == 200
    assert client.post("/modules/contacts", data={"contact_name": "A", "phone": "12345"}).status_code == 403
    assert client.get("/modules/notifications").status_code == 403
    assert client.get("/modules/reports").status_code == 403
    assert client.post("/modules/first-aid", data={}).status_code == 403


def test_http_inspection_and_validation_flash(ctx):
    client = ctx.test_client()
    _login(client)
    resp = client.post("/modules/contacts", data={"contact_name": "", "phone": "1"}, follow_redirects=True)
    assert b"Contact name is required" in resp.data
    zone = svc.list_zones()[0]["id"]
    resp = client.post("/modules/inspections", data={"zone_id": zone, "inspection_type": "Fire safety",
                                                     "scheduled_date": "2030-01-01"}, follow_redirects=True)
    assert resp.status_code == 200 and b"Fire safety" in resp.data
