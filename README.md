# OSAS Campus Safety and Security Management System

A standalone campus safety and security management system for Bestlink College of the Philippines, built from the LMS capstone template architecture: Flask, Jinja, Tailwind CSS, JavaScript, MySQL, Flask-Login, and RBAC. Library modules have been replaced by OSAS and campus-security modules.

## Modules

- Core Administration: users, roles, security teams, campus zones, buildings, checkpoints, configuration, and audit logs.
- OSAS Operations: emergency contacts, drills, evacuation plans, incidents, inspections, risk assessments, emergency roles, first-aid inventory, parent notifications, and compliance reports.
- Security Patrol Management: guard teams, shifts, QR/NFC/GPS checkpoints, patrol logs, completion verification, and incident-to-patrol linkage.
- Predictive Patrol Planning: incident and patrol history aggregation, recency-decay risk scoring, OSRM-ready routing, prioritized checkpoints, route assignment, and manual overrides.
- Incident and Emergency Response: Reported, Verified, Responding, Resolved, and Closed workflow.
- Reports and Analytics: incident trends, patrol coverage, risk heatmaps, readiness scores, compliance evidence, and audit logs.
- Workflow safeguards: sequential incident transitions, required resolution notes, role-restricted closure, and duplicate checkpoint-scan rejection.
- Explainable risk API: `GET /api/risk/checkpoints/<checkpoint_id>/explanation` for checkpoint-level scoring factors.
- Password login is followed by a short-lived, single-use email OTP when `OTP_ENABLED=1`; configure SMTP variables before enabling this in production.

## Setup

```bash
pip install -r requirements.txt
mysql -u root -p < schema.sql
python init_db.py
python app.py
```

The database name is `osas_bcp`. Demo accounts created by `init_db.py` use password `password123`.

Copy `.env.example` to `.env` for local overrides.

### Login MFA
The login flow does not create a Flask-Login session after password verification. It creates a database-backed OTP challenge first; only a valid, unexpired code completes the session. OTP hashes are stored with an HMAC derived from `SECRET_KEY`, codes are invalidated after use or too many attempts, and SMTP delivery is required in production. In development without SMTP, the code is written to the application log for local testing only.

## Verification and documentation

Run the unit and mocked-integration checks with `python -m pytest tests/ -v`. Tests that exercise MySQL-backed workflows are skipped when no test database is configured. The current role matrix, feature-status inventory, and risk-scoring specification are maintained in `docs/RBAC_ROLE_MATRIX.md`, `docs/FEATURE_STATUS.md`, and `docs/RISK_MODEL_SPEC.md`.

## Deploy

- **Railway (recommended):** [docs/RAILWAY_DEPLOY.md](docs/RAILWAY_DEPLOY.md) — Flask + MySQL in one project  
- **Render:** [docs/RENDER_DEPLOY.md](docs/RENDER_DEPLOY.md) — web on Render + external MySQL  
