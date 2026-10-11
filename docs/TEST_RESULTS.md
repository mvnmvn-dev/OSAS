# OSAS Test Results Summary

This document records the latest verified baseline and the commands required to reproduce it. It is not a claim that the suite was rerun in every environment.

```bash
pip install -r requirements.txt
pytest tests/ -v
```

## Run summary

| Metric | Value |
|--------|--------|
| Date | 2026-09-30 |
| Result | **19 passed (historical baseline)** |
| Duration | ~2.3s |
| Failures | 0 |

## By category

| Test Category | Result |
|---------------|--------|
| Risk Scoring | PASS (6) |
| Patrol Routing / OSRM | PASS (4) |
| Official GIS Pack | PASS (2) |
| RBAC | PASS (2) |
| Incident Workflow | PASS (3) |
| User Management | PASS (2) |

## Notes

- Integration tests that touch MySQL require a configured local database (`MYSQL_*` in `.env`).
- OSRM tests use mocked HTTP / heuristic fallback and do not require a live OSRM container.
- Four additional pure workflow-rule tests are now included in `tests/test_workflow_rules.py`; rerun the command below in a Python-enabled environment before reporting a new total.
- Only report results as current if the same suite still passes on the defense machine.

## Commands used

```text
py -m pytest tests/ -v --tb=short
```
