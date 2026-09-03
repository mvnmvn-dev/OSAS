# OSAS Campus Safety and Security Management System

A standalone campus safety and security management system for Bestlink College of the Philippines, built from the LMS capstone template architecture: Flask, Jinja, Tailwind CSS, JavaScript, MySQL, Flask-Login, and RBAC. Library modules have been replaced by OSAS and campus-security modules.

## Modules

- Core Administration: users, roles, security teams, campus zones, buildings, checkpoints, configuration, and audit logs.
- OSAS Operations: emergency contacts, drills, evacuation plans, incidents, inspections, risk assessments, emergency roles, first-aid inventory, parent notifications, and compliance reports.
- Security Patrol Management: guard teams, shifts, QR/NFC/GPS checkpoints, patrol logs, completion verification, and incident-to-patrol linkage.
- Predictive Patrol Planning: incident and patrol history aggregation, recency-decay risk scoring, OSRM-ready routing, prioritized checkpoints, route assignment, and manual overrides.
- Incident and Emergency Response: Reported, Verified, Responding, Resolved, and Closed workflow.
- Reports and Analytics: incident trends, patrol coverage, risk heatmaps, readiness scores, compliance evidence, and audit logs.

## Setup

```bash
pip install -r requirements.txt
mysql -u root -p < schema.sql
python init_db.py
python app.py
```

The database name is `osas_bcp`. Demo accounts created by `init_db.py` use password `password123`.
