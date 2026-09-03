"""Unit tests for decay-based risk scoring (no DB required)."""
from datetime import datetime, timedelta

from services.risk_scoring import SEVERITY, compute_raw_incident_score, decay_weight


def test_decay_weight_half_life():
    assert abs(decay_weight(0) - 1.0) < 1e-9
    assert abs(decay_weight(7) - 0.5) < 1e-6
    assert decay_weight(14) < decay_weight(7)


def test_critical_outweighs_low_same_age():
    now = datetime(2026, 9, 3, 12, 0, 0)
    low = [{"priority": "low", "reported_at": now - timedelta(days=1)}]
    critical = [{"priority": "critical", "reported_at": now - timedelta(days=1)}]
    assert compute_raw_incident_score(critical, now) > compute_raw_incident_score(low, now)


def test_recent_incident_scores_higher_than_old():
    now = datetime(2026, 9, 3, 12, 0, 0)
    recent = [{"priority": "high", "reported_at": now - timedelta(hours=6)}]
    old = [{"priority": "high", "reported_at": now - timedelta(days=20)}]
    assert compute_raw_incident_score(recent, now) > compute_raw_incident_score(old, now)


def test_severity_map_complete():
    for key in ("low", "medium", "high", "critical"):
        assert key in SEVERITY
        assert SEVERITY[key] > 0
