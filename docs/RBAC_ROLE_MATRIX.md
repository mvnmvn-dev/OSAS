# LMS Capstone — RBAC Role Matrix

**Roles:** Student (patron) · Librarian Staff · Admin

Permission levels: **none** · **view own** · **view all** · **manage**

| Module | Student | Librarian Staff | Admin |
|--------|---------|-----------------|-------|
| Dashboard | view own stats | view all stats | view all stats |
| Book Search | view / reserve own | view all | view all |
| Reservations | manage own | manage all | manage all |
| Borrowing | view own / renew own | manage all (checkout/checkin) | manage all |
| Fines | view own / pay own online | view all / record payment | view all / record payment / **waive** |
| E-Books | view own / access own | manage catalog & access | manage catalog & access |
| Library Clearance | request own | manage all requests | manage all requests |
| Lost / Damaged | report own | manage all reports | manage all reports |
| Book Inventory | none | view / manage | view / manage |
| ID Verification | none | **view only** (lookup & eligibility) | view + **manage card status** |
| Reports & Analytics | none | view dashboards | view + **export system reports** |
| User Management | none | none | **manage** (create, edit, disable, reset password, assign roles) |
| Activity Log | none | none | **view** (audit trail) |
| Notifications | view own | view own | view own |

## Staff vs Admin split

**Librarian Staff** handles day-to-day library operations: checkout/check-in, fines (payment recording), reservations, lost/damaged reports, e-books, inventory, and viewing reports.

**Admin-only** capabilities:
- User account management and role assignment
- Library card status changes (verification decisions)
- Fine waivers (with required reason → audit log)
- System report export (CSV/HTML)
- Activity log / security audit access

## Account states

| State | Field | Effect |
|-------|-------|--------|
| Physical card status | `card_status` | Eligibility for borrowing (active / suspended / expired / inactive) |
| System login access | `account_status` | `active` = can log in; `disabled` = login blocked (history preserved) |

These are intentionally separate: an Admin can revoke login access without changing physical card records.

## Security controls implemented

- Global `before_request` RBAC on every registered endpoint (including API)
- IDOR checks on patron-scoped routes (clearance, fines pay-online, renew, API transactions/fines)
- Failed-login throttling (5 attempts → 5-minute lockout)
- Session idle timeout (45 minutes, configurable)
- CSRF protection on all POST forms (Flask-WTF)
- Safe redirect validation on login `next` parameter
- `SECRET_KEY` required in production (`FLASK_ENV=production`)
- Audit events logged to `analytics_log`: `role_changed`, `account_disabled`, `account_enabled`, `permission_denied`, `fine_waived`, `login_failed`, etc.
