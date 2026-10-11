# OSAS Risk-Scoring Model Specification

## Scope and terminology

The predictive patrol component is a **relative risk prioritization score**, not a calibrated probability and not a trained machine-learning model. It ranks active checkpoints for patrol planning using recent incident and patrol-exception records.

## Inputs and constants

| Input | Rule |
|---|---|
| Incident priority | low = 1.0, medium = 2.0, high = 3.5, critical = 5.0 |
| Incident recency | Exponential decay with a 7-day half-life |
| Patrol exceptions | Missed, late, and invalid scans contribute 1.5 before decay |
| Lookback window | 30 days |
| Output range | Clamped to 5.0–99.0 |

For a record that is `d` days old:

```text
 decay(d) = exp(-(ln(2) / 7) × d)
```

The raw checkpoint score is:

```text
 raw = Σ(incident_severity × decay(age))
       + Σ(1.5 × decay(age)) for patrol exceptions
```

The displayed score is:

```text
 score = clamp(100 × (1 - exp(-raw / 8)), 5, 99)
```

Closed incidents are excluded. Checkpoint incidents are linked through the checkpoint's campus zone.

## Worked example

A critical incident 0.5 days old and one late scan 1 day old produce:

1. Incident term = `5.0 × decay(0.5)`
2. Patrol term = `1.5 × decay(1.0)`
3. Add both terms to obtain `raw`
4. Apply the saturation formula to obtain the displayed score

The implementation exposes this calculation through the patrol UI and through the protected endpoint:

```text
GET /api/risk/checkpoints/<checkpoint_id>/explanation
```

## Route behavior

The highest-ranked active checkpoints with coordinates are passed to OSRM Trip when available. If OSRM is unavailable, the application uses a nearest-neighbor heuristic and labels the result `heuristic`; this fallback is not equivalent to road-aware routing.

## Limitations and evaluation plan

- Weights are design assumptions, not statistically learned parameters.
- A high score means higher prioritization under the formula, not a guaranteed incident.
- Missing, delayed, or inconsistently classified records can change rankings.
- The model should be evaluated on a historical training period and a separate later period.
- Defense evidence should report high-risk checkpoint coverage, subsequent incident concentration, missed-checkpoint rate, patrol completion, route distance/time, and comparison with a fixed-route baseline.
- Potential bias from reporting patterns and uneven checkpoint coverage must be discussed before making safety-effectiveness claims.
