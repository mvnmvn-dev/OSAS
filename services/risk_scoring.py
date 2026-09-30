"""Predictive risk scoring from incidents and patrol logs with exponential decay."""
from __future__ import annotations

import math
from datetime import datetime

from models.database import execute, query_all

# Priority → severity weight
SEVERITY = {"low": 1.0, "medium": 2.0, "high": 3.5, "critical": 5.0}
# Half-life in days for incident influence
HALF_LIFE_DAYS = 7.0
LAMBDA = math.log(2) / HALF_LIFE_DAYS
MISSED_PATROL_WEIGHT = 1.5
WINDOW_DAYS = 30


def _days_ago(ts, now=None):
    now = now or datetime.now()
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    return max(0.0, (now - ts).total_seconds() / 86400.0)


def score_for_checkpoint(checkpoint_id, now=None):
    """Compute a 0–100 risk score for one checkpoint from linked zone incidents + patrol misses."""
    now = now or datetime.now()
    incidents = query_all(
        """
        SELECT i.priority, i.reported_at
        FROM incidents i
        JOIN checkpoints c ON c.zone_id = i.zone_id
        WHERE c.id = %s
          AND i.reported_at >= (NOW() - INTERVAL %s DAY)
          AND i.status NOT IN ('closed')
        """,
        (checkpoint_id, WINDOW_DAYS),
    )
    raw = 0.0
    for row in incidents:
        severity = SEVERITY.get((row.get("priority") or "medium").lower(), 2.0)
        raw += severity * math.exp(-LAMBDA * _days_ago(row["reported_at"], now))

    misses = query_all(
        """
        SELECT scanned_at
        FROM patrol_logs
        WHERE checkpoint_id = %s
          AND result IN ('missed', 'late', 'invalid')
          AND scanned_at >= (NOW() - INTERVAL %s DAY)
        """,
        (checkpoint_id, WINDOW_DAYS),
    )
    for row in misses:
        raw += MISSED_PATROL_WEIGHT * math.exp(-LAMBDA * _days_ago(row["scanned_at"], now))

    # Soft saturate toward 100
    score = 100.0 * (1.0 - math.exp(-raw / 8.0))
    return round(min(99.0, max(5.0, score)), 1)


def score_for_zone(zone_id, now=None):
    now = now or datetime.now()
    incidents = query_all(
        """
        SELECT priority, reported_at
        FROM incidents
        WHERE zone_id = %s
          AND reported_at >= (NOW() - INTERVAL %s DAY)
          AND status NOT IN ('closed')
        """,
        (zone_id, WINDOW_DAYS),
    )
    raw = 0.0
    for row in incidents:
        severity = SEVERITY.get((row.get("priority") or "medium").lower(), 2.0)
        raw += severity * math.exp(-LAMBDA * _days_ago(row["reported_at"], now))
    score = 100.0 * (1.0 - math.exp(-raw / 8.0))
    return round(min(99.0, max(5.0, score)), 1)


def refresh_all_risk_scores(now=None):
    """Recompute checkpoint-driven zone risk scores and persist to campus_zones."""
    now = now or datetime.now()
    zones = query_all("SELECT id FROM campus_zones WHERE active = 1")
    updated = []
    for zone in zones:
        cps = query_all("SELECT id FROM checkpoints WHERE zone_id = %s AND active = 1", (zone["id"],))
        if cps:
            scores = [score_for_checkpoint(cp["id"], now) for cp in cps]
            zone_score = round(sum(scores) / len(scores), 2)
        else:
            zone_score = score_for_zone(zone["id"], now)
        execute("UPDATE campus_zones SET risk_score = %s WHERE id = %s", (zone_score, zone["id"]))
        updated.append({"zone_id": zone["id"], "risk_score": zone_score})
    return updated


def checkpoint_priority_weights():
    """Return [{checkpoint_id, code, name, lat, lng, score}] sorted by score desc."""
    rows = query_all(
        """
        SELECT c.id, c.checkpoint_code, c.checkpoint_name, c.latitude, c.longitude, c.zone_id,
               z.risk_score AS zone_risk
        FROM checkpoints c
        JOIN campus_zones z ON z.id = c.zone_id
        WHERE c.active = 1
        """
    )
    result = []
    for row in rows:
        score = score_for_checkpoint(row["id"])
        result.append({
            "checkpoint_id": row["id"],
            "code": row["checkpoint_code"],
            "name": row["checkpoint_name"],
            "lat": float(row["latitude"]) if row["latitude"] is not None else None,
            "lng": float(row["longitude"]) if row["longitude"] is not None else None,
            "zone_id": row["zone_id"],
            "score": score,
            "tone": "danger" if score >= 80 else "warning" if score >= 65 else "success",
        })
    result.sort(key=lambda r: r["score"], reverse=True)
    return result


def decay_weight(days_ago, half_life_days=HALF_LIFE_DAYS):
    """Pure function for unit tests."""
    return math.exp(-(math.log(2) / half_life_days) * days_ago)


def compute_raw_incident_score(incidents, now=None):
    """Pure helper: incidents = [{priority, reported_at}, ...]."""
    now = now or datetime.now()
    raw = 0.0
    for row in incidents:
        severity = SEVERITY.get((row.get("priority") or "medium").lower(), 2.0)
        raw += severity * math.exp(-LAMBDA * _days_ago(row["reported_at"], now))
    return raw


def explain_checkpoint_score(checkpoint_id, now=None, limit=5):
    """
    Transparent breakdown for defense demos.
    Returns contributions that feed the weighted heuristic risk score.
    """
    now = now or datetime.now()
    cp = query_all(
        """SELECT c.id, c.checkpoint_code, c.checkpoint_name, z.zone_name
           FROM checkpoints c JOIN campus_zones z ON z.id = c.zone_id
           WHERE c.id = %s""",
        (checkpoint_id,),
    )
    if not cp:
        return None
    cp = cp[0]
    contributions = []
    raw = 0.0

    incidents = query_all(
        """
        SELECT i.priority, i.reported_at, i.title, i.incident_code
        FROM incidents i
        JOIN checkpoints c ON c.zone_id = i.zone_id
        WHERE c.id = %s
          AND i.reported_at >= (NOW() - INTERVAL %s DAY)
          AND i.status NOT IN ('closed')
        ORDER BY i.reported_at DESC
        """,
        (checkpoint_id, WINDOW_DAYS),
    )
    for row in incidents:
        priority = (row.get("priority") or "medium").lower()
        severity = SEVERITY.get(priority, 2.0)
        days = _days_ago(row["reported_at"], now)
        decay = math.exp(-LAMBDA * days)
        term = severity * decay
        raw += term
        contributions.append({
            "kind": "incident",
            "label": row.get("title") or row.get("incident_code") or "Incident",
            "priority": priority.title(),
            "severity_weight": severity,
            "days_ago": round(days, 2),
            "decay": round(decay, 3),
            "contribution": round(term, 3),
        })

    misses = query_all(
        """
        SELECT scanned_at, result
        FROM patrol_logs
        WHERE checkpoint_id = %s
          AND result IN ('missed', 'late', 'invalid')
          AND scanned_at >= (NOW() - INTERVAL %s DAY)
        ORDER BY scanned_at DESC
        """,
        (checkpoint_id, WINDOW_DAYS),
    )
    for row in misses:
        days = _days_ago(row["scanned_at"], now)
        decay = math.exp(-LAMBDA * days)
        term = MISSED_PATROL_WEIGHT * decay
        raw += term
        contributions.append({
            "kind": "patrol_exception",
            "label": f"Patrol {row.get('result')}",
            "priority": (row.get("result") or "missed").title(),
            "severity_weight": MISSED_PATROL_WEIGHT,
            "days_ago": round(days, 2),
            "decay": round(decay, 3),
            "contribution": round(term, 3),
        })

    score = round(min(99.0, max(5.0, 100.0 * (1.0 - math.exp(-raw / 8.0)))), 1)
    contributions.sort(key=lambda c: c["contribution"], reverse=True)
    return {
        "checkpoint_id": cp["id"],
        "code": cp["checkpoint_code"],
        "name": cp["checkpoint_name"],
        "zone": cp.get("zone_name"),
        "raw": round(raw, 3),
        "score": score,
        "contributions": contributions[:limit],
        "model": {
            "type": "weighted_heuristic",
            "description": "Weighted heuristic risk-scoring with exponential recency decay (not a trained ML model).",
            "severity_weights": dict(SEVERITY),
            "half_life_days": HALF_LIFE_DAYS,
            "missed_patrol_weight": MISSED_PATROL_WEIGHT,
            "window_days": WINDOW_DAYS,
            "saturation": "score = 100 * (1 - e^(-raw/8)), clamped 5–99",
        },
    }


def sample_risk_calculation(priority="critical", days_ago=0.5, missed=1, missed_days_ago=1.0):
    """
    Fixed worked example for panel walkthrough — independent of live DB state.
    Example: Critical incident 12h ago + 1 late scan 1 day ago.
    """
    severity = SEVERITY.get(priority, 5.0)
    incident_decay = decay_weight(days_ago)
    incident_term = severity * incident_decay
    miss_decay = decay_weight(missed_days_ago)
    miss_term = (MISSED_PATROL_WEIGHT * miss_decay) if missed else 0.0
    raw = incident_term + miss_term
    score = round(min(99.0, max(5.0, 100.0 * (1.0 - math.exp(-raw / 8.0)))), 1)
    return {
        "title": "Sample calculation · Gate-area checkpoint",
        "steps": [
            {
                "label": f"{priority.title()} incident ({days_ago} days ago)",
                "detail": f"severity {severity} x decay {incident_decay:.3f} = {incident_term:.3f}",
            },
            {
                "label": f"Late/missed patrol ({missed_days_ago} days ago)" if missed else "No patrol exception",
                "detail": f"weight {MISSED_PATROL_WEIGHT} x decay {miss_decay:.3f} = {miss_term:.3f}" if missed else "—",
            },
            {
                "label": "Raw risk sum",
                "detail": f"{raw:.3f}",
            },
            {
                "label": "Saturated score (0-100)",
                "detail": f"100 * (1 - e^(-{raw:.3f}/8)) -> {score}",
            },
        ],
        "score": score,
        "raw": round(raw, 3),
        "note": "This is a transparent heuristic example for defense — not a trained machine-learning prediction.",
    }


def risk_model_card():
    """Static model metadata for UI / documentation."""
    return {
        "name": "Predictive patrol risk model",
        "type": "Weighted heuristic with exponential recency decay",
        "not_ml": True,
        "pipeline": [
            "Incident severity",
            "Incident recency (7-day half-life)",
            "Patrol exceptions (missed / late / invalid)",
            "Risk score",
            "Checkpoint prioritization",
            "Patrol route generation",
        ],
        "severity_weights": [
            {"label": "Low", "value": SEVERITY["low"]},
            {"label": "Medium", "value": SEVERITY["medium"]},
            {"label": "High", "value": SEVERITY["high"]},
            {"label": "Critical", "value": SEVERITY["critical"]},
        ],
        "half_life_days": HALF_LIFE_DAYS,
        "window_days": WINDOW_DAYS,
        "missed_patrol_weight": MISSED_PATROL_WEIGHT,
        "defense_blurb": (
            "The predictive component uses a weighted heuristic risk-scoring model with exponential "
            "time decay instead of a trained machine-learning model. Historical incidents, severity, "
            "recency, and patrol exceptions contribute to the risk score, which prioritizes checkpoints "
            "and feeds patrol route generation."
        ),
    }
