# OSAS Capstone Defense Guide

## Accurate system explanation

> The system centralizes campus safety and security operations. It manages incidents, inspections, emergency preparedness, visitors, patrol teams, checkpoints, and security records. Its predictive patrol component analyzes incident severity, incident recency, and patrol exceptions to calculate risk and prioritize checkpoints. Prioritized locations feed patrol route generation (OSRM when available, heuristic fallback otherwise). Checkpoint scans and patrol activity are recorded for monitoring, auditing, and reporting.

## Predictive model — what to say

**Correct description**

> A risk-based predictive patrol planning mechanism using a weighted heuristic model with recency decay and patrol-history factors.

**If asked “What AI algorithm are you using?”**

> The predictive component currently uses a weighted heuristic risk-scoring model with exponential time decay instead of a trained machine-learning model. Historical incidents, severity, recency, and patrol exceptions contribute to the risk score, which prioritizes checkpoints and generates patrol routes.

Do **not** claim machine learning unless a trained model is implemented.

### Formula (transparent)

| Factor | Weight / rule |
|--------|----------------|
| Low | 1.0 |
| Medium | 2.0 |
| High | 3.5 |
| Critical | 5.0 |
| Missed / late / invalid patrol | +1.5 × decay |
| Half-life | 7 days |
| Window | 30 days |
| Saturation | `score = 100 × (1 − e^(−raw/8))`, clamped 5–99 |

Pipeline shown in **Patrol Operations → Predictive Patrol Model**.

## Recommended demonstration scenario

1. Report a **Critical** incident near Gate 2 (`G2`).
2. Open **Patrol Operations** and show the risk model panel (weights + sample calculation + live top checkpoint).
3. Generate a patrol route — high-risk checkpoints should appear early in the sequence.
4. Assign the route to a team.
5. Record a checkpoint scan (token / checkpoint code; method is a logged tag).
6. Advance the incident: Reported → Verified → Responding → Resolved → Closed (timeline in the incident modal).
7. Open **Activity Log** (read-only audit).
8. Open **Reports & Analytics** / dashboard analytics.

## Feature accuracy notes

| Feature | Actual state |
|---------|----------------|
| QR / token scan | Implemented via code/token entry |
| NFC / GPS | Method tags stored in `patrol_logs`; no hardware NFC/GPS capture in the UI |
| OSRM routing | Used when `OSRM_BASE_URL` is reachable |
| Routing fallback | Distance/heuristic — not equivalent to road-aware OSRM |
| Parent notifications | Queued; delivery requires SMTP/SMS config |
| Demo passwords | Development only (`SHOW_DEMO_CREDENTIALS`) |

## Security hardening checklist

- [ ] `FLASK_ENV=production`, `FLASK_DEBUG=0`, strong `SECRET_KEY`
- [ ] HTTPS → `SESSION_COOKIE_SECURE=1`
- [ ] Demo credentials disabled / rotated
- [ ] Attachment type + size limits verified
- [ ] Role demos: faculty cannot manage patrol; admin can
- [ ] `pytest` clean run (see `docs/TEST_RESULTS.md`)
