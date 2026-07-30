---
name: V2.1 Pipeline Fixes and Analytics
description: Root causes of V2.0 losses, V2.1 fix inventory, station status table, consensus guard usage, analytics endpoints added.
---

## Root Causes (July 2026 audit)
- **Forecast at wrong location** — Open-Meteo fetched at city-centre coordinates; now uses settlement-station coords
- **Sigma too small** — V2.0 σ=1.22°F from 5 samples; V2.1 uses floor 3.5°F, MIN_SAMPLE=30, conservative prior 5.0°F

## Station Status
| City | Station | GHCND | Verified |
|------|---------|-------|----------|
| New York City | Central Park | USW00094728 | ✓ |
| Chicago | Midway (KMDW) | USW00014819 | ✓ |
| Denver | KDEN Airport | USW00003017 | ✓ |
| Oklahoma City | KOKC Will Rogers | USW00013967 | ✓ (July 2026 audit) |
| Los Angeles | KLAX (ambiguous) | USW00023174 | ✗ HIGH AMBIGUITY — USC Downtown probable |
| All others | See settlement_stations.py | — | ✗ |

**OKC July 28 miss**: 9°F error = ~7-8°F model error (radiative cooling) + ~1-2°F location offset.  
σ floor fix prevents 90+pp false edges on same-day markets.

## Analytics Endpoints Added
- `GET /api/analytics/v21/retrospective` — V2.0 → V2.1 before/after comparison
- `GET /api/analytics/v21/calibration` — confidence bucket calibration
- `GET /api/analytics/v21/readiness` — readiness stage + progress
- `GET /api/analytics/v21/stations` — all station data
- `GET /api/analytics/v21/okc-explanation` — static evidence table
- `GET /api/analytics/v21/consensus-backtest` — guard backtest (guard stays DISABLED)

## Frontend Pages
- `/performance` — added ReadinessPanel + CalibrationSection (V2.1 sections)
- `/v21-audit` — new page: retrospective comparison, station coverage, OKC evidence, consensus guard

## Bug Fixed
- `consensus_guard_backtest()` in `paper_trading_v21.py` used `t.pnl` (wrong); fixed to `t.profit_loss`

## Consensus Guard
- Default: DISABLED. Enable via `AppSetting paper_trading_v21.consensus_guard_enabled=true`.
- Backtest results shown at `/v21-audit`. Do not enable until ≥100 settled V2.1 trades show consistent positive ROI.

## Settlement Operator Storage
- DB stores settlement_operator as `"gte"` / `"lte"` (not `">"` / `"<"`)
- Frontend must check `operator === "gte"` not `">"` — important for normalCDF branch selection

## Probability Chain Math (implemented in paper-trade-detail.tsx)
- z = (threshold − forecast) / sigma
- P(YES) for gte market = 1 − Φ(z);  for lte market = Φ(z)
- normalCDF uses Abramowitz & Stegun polynomial approximation (exact to ~7 sig digits)
- Verified: computed values match stored `ec_side_probability` to 4 decimal places

## Tests Updated
- `test_verified_count_is_three` → `test_verified_count_is_four` (OKC now verified = 4 cities)
- Dallas, Houston, LA coordinate assertions updated to station coords (not city-centre)
- Full suite: 458 passed, 0 failed

## V21 Readiness Thresholds
- "Ready for Careful Evaluation": ≥250 settled trades, ≥2 city/lead-time buckets with ≥30 obs
- Currently: "Collecting Data" (V2.1 just started accumulating trades)
