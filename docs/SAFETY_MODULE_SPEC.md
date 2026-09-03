# Safety and Security Campus Management Extension

## Purpose

This extension adapts the existing Bestlink College Library Management System capstone template into the user’s proposed Safety and Security Campus Management System. It intentionally follows the same Flask, Jinja, Tailwind CSS, JavaScript, MySQL, RBAC, services, routes, templates, and REST API conventions already present in the archive.

## Template Alignment

| Existing LMS convention | Safety system implementation |
|---|---|
| `app.py` blueprint registration | `routes/safety.py` registered as `safety_bp` |
| `services/` business logic | `services/safety_service.py` provides the dashboard contract |
| `templates/base.html` shell | `templates/safety/index.html` extends the same shell |
| `services/rbac.py` permissions | `safety.view` and `safety.manage` for staff/admin roles |
| `schema.sql` normalized MySQL schema | Safety zones, incidents, patrol logs, and patrol routes |
| Existing card, table, modal, and status CSS | Safety-specific additions appended to `static/css/custom.css` |

## Predictive Patrol Planning Design

The first implementation presents the operational contract for the technical centerpiece. Historical incident frequency, priority, recency, and patrol completion can be aggregated by zone and checkpoint. A risk score can then be computed using a weighted decay function. The resulting priority order can be sent to OSRM’s Table, Route, or Trip endpoint over a custom campus pathway extract. The resulting route should be persisted in `patrol_routes` with its engine, distance, duration, and checkpoint sequence.

## Recommended Build Sequence

1. Seed campus zones, buildings, gates, checkpoints, and emergency roles in MySQL.
2. Implement incident CRUD and the `Reported → Verified → Responding → Resolved` workflow.
3. Add patrol log capture through QR/NFC checkpoint scans.
4. Implement the Python risk-scoring service with recency decay and severity weighting.
5. Connect the route generator to a self-hosted OSRM instance using a custom BCP pathway map.
6. Add scheduled drills, safety inspections, first-aid inventory, parent notifications, and PDF/Excel compliance reports.
