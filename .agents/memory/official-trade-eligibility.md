---
name: Official Trade Eligibility Engine
description: Architecture of the V2.2/V3 hardening pass — how trades are classified OFFICIAL vs RESEARCH_ONLY, where the code lives, and what changed behaviourally.
---

## Rule

All new paper trades (V2.2 and V3) are stamped with `eligibility_status = 'OFFICIAL' | 'RESEARCH_ONLY'`
and `eligibility_reason` (reason-code string or NULL) at creation time.
Old trades pre-hardening have `eligibility_status = NULL` — treated as legacy; governed by `is_executable` alone.

## Guard summary (app/services/eligibility.py)

| Guard | Reason code | Hard-stop or eligibility |
|-------|-------------|--------------------------|
| G1 hourly_threshold | hourly_temperature_not_approved | eligibility |
| G2 same-day local tz | same_day_not_approved | eligibility |
| G3 < 120 min to settlement | cutoff_unverified_or_too_close | eligibility |
| G4 entry < $0.20 | entry_price_below_official_floor | eligibility |
| G5 edge ≥ 50pp | extreme_edge_requires_validation | eligibility |
| G6 correlated bracket | correlated_outcome_limit | BATCH-level (apply_correlated_limit) |
| G7 station unverified (NWS) | settlement_station_unverified | eligibility |
| G8 stale/missing quote | missing_or_stale_executable_quote | eligibility |
| DC nws_settlement=False | — | HARD STOP (no row created) |

## Behavioural changes from previous system

- `_check_station_verified` no longer hard-stops on `verified=False`; only `nws_settlement=False` hard-stops.
  V3 renamed to `_check_station_nws`.
- Quote freshness is no longer a hard-stop in V2.2 or V3; stale quotes produce RESEARCH_ONLY rows.
- `is_executable = True` only when `eligibility_status = 'OFFICIAL'` (and old quantity check passes).
- `RESEARCH_ONLY` trades are still created and stored; excluded from official metrics by filtering `eligibility_status`.

## Two-pass batch design (paper_trading_v22.py, v3_paper_trading.py)

Phase 1 — evaluate all candidates (no DB writes).
Phase 2 — `apply_correlated_limit(official_decisions)` mutates dicts in-place.
Phase 3 — write all trade rows.

**Why:** Guard 6 (correlated exposure) requires knowing all OFFICIAL candidates for a (city, date, variable)
group before any row is written, so the correlated-limit ranking can pick the best one.

## New DB columns (both paper_trades and v3_paper_trades)

- `eligibility_status VARCHAR(20)` — OFFICIAL | RESEARCH_ONLY | NULL (legacy)
- `eligibility_reason VARCHAR(60)` — reason code or NULL
- `quote_age_seconds FLOAT` — seconds since quote was fetched at decision time

## New API endpoint

`GET /api/paper-trades/best-bet-today` — returns best OFFICIAL open trade (highest EV = edge/price),
or `{"available": false, "message": "No official-quality paper bet is available right now."}`.
Requires auth. Queries UNION of paper_trades + v3_paper_trades.

## Test file

`tests/test_eligibility.py` — 40 tests covering all 8 guards, guard priority ordering,
correlated-limit batch logic, and research exclusion from official counts.
`tests/test_v3_paper_trading.py` updated to reflect new unverified-station and stale-quote behaviours.

## How to apply

- When reading current-experiment metrics, filter: `eligibility_status = 'OFFICIAL'` for new trades,
  `eligibility_status IS NULL AND is_executable = TRUE` for pre-hardening legacy trades.
- Never re-classify existing NULL trades retroactively without explicit user approval.
