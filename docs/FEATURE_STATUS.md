# OSAS Feature Status

This inventory keeps the defense and deployment description aligned with the current source code.

## Implemented

- Role-based access control for dashboard, safety, patrol, visitor, reports, and administration routes.
- Failed-login throttling and account lockout.
- Incident creation, audit logging, and server-side state transitions.
- Resolution notes required before an incident reaches `Resolved`.
- Administrator/security-head authorization required to close incidents.
- Patrol checkpoint token/code validation.
- Server-side duplicate checkpoint-scan rejection within a 60-second window for the same guard and checkpoint.
- Weighted heuristic risk score with exponential recency decay and explainable contributions.
- OSRM route integration with a labeled heuristic fallback.
- Visitor pass lifecycle: registered → checked in → exited, with expiry and audit events.
- Safety inspections, emergency drills, evacuation-plan records, reports, and in-app notifications.
- Protected API for checkpoint risk-score explanations.

## Team 5 modules (added in this revision)

All pages live under `/modules/*`; logic is in `services/osas_modules_service.py` and `services/compliance_reports.py`.

| Module | Page | What it does |
|---|---|---|
| Emergency Contact Database | `/modules/contacts` | CRUD, type/priority, search, validation, duplicate check, deactivate, missing-category warning |
| Drill Scheduling | `/modules/drills` | Schedule/edit, team double-booking check, planned→in progress→completed/cancelled workflow, participants/duration/score |
| Evacuation Maps & Plans | `/modules/plans` | Versioned plans, map upload (PDF/PNG/JPG/WEBP, magic-byte checked, stored outside `static/`, served behind login), approval step, building coverage |
| Safety Inspection Checklist | `/modules/inspections` | Checklist generated per inspection type, pass/fail/N-A with findings, corrective-action tracking, scoring, completion, auto-overdue, failed items open risk assessments |
| Risk Assessment Tool | `/modules/risks` | Likelihood x severity scoring, 5x5 matrix, status workflow with required justification |
| Parent Notification System | `/modules/notifications` | Validated send, queued/sent/failed status with error and attempt count, retry, message templates, audit |
| Compliance Reports | `/modules/reports` | 11 report types, real date-range filtering, on-screen preview, PDF/Excel/CSV export (all rows, CSV-injection safe) |
| Emergency Role Assignment | `/modules/roles` | Standing or per-drill roles, primary + backup, coverage stats, in-app notice to assignee |
| First Aid Supplies Monitor | `/modules/first-aid` | Stock use/restock with reasons, automatic low/out/expired status, expiry warning, check log, staff alerts |

Also changed: legacy `create_plan`, `create_inspection`, `queue_parent_notification` and `export_report` now delegate to the new services; incident creation validates priority/type; inspections no longer fall back to an arbitrary zone.

## Partial or dependent on configuration

- Parent notifications: delivery needs `RESEND_API_KEY` or `SMTP_HOST` (email) and `SMS_PROVIDER_URL` (SMS); without them notices stay `queued` with an explanatory message. Push is not implemented.
- Compliance-summary targets (90% inspections, 80% drills, 90% delivery) are working defaults in `services/compliance_reports.py`, not statutory values; academic semester is assumed Aug-Dec / Jan-May / Jun-Jul.
- Incident attachments are validated and stored on the application filesystem; managed hosting may use ephemeral disks.
- QR/token scanning is implemented through code/token input. NFC and GPS are stored as method labels only; no hardware integration is present.
- OSRM route quality depends on a reachable campus routing service and suitable map data.
- Unit tests and mocked routing tests run against SQLite without a live PostgreSQL instance.
- Dashboard analytics reflect available database records and should not be treated as evidence of improved campus safety without a separate evaluation.

## Future or research work

- Historical holdout evaluation and fixed-route baseline comparison for the risk model.
- Requirements traceability matrix and representative usability study.
- Checkpoint assignment validation against active route/shift membership.
- Durable object storage and private authorization for sensitive attachments.
- Backup, restoration, and disaster-recovery drills.
- Production security review, including secret rotation, HTTPS verification, and upload access testing.
