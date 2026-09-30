# OSAS Test Results Summary

Generated from a local run of the project test suite.

```bash
pip install -r requirements.txt
pytest tests/ -v
```

## Run summary

| Metric | Value |
|--------|--------|
| Date | 2026-09-30 |
| Result | **19 passed** |
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
- Only report these results if the same suite still passes on the defense machine.

## Commands used

```text
py -m pytest tests/ -v --tb=short
```
