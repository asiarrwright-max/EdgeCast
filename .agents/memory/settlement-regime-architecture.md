---
name: Settlement Regime Architecture
description: How settlement_regime is stamped on trades and what the transition from NWS to Weather Company means for EdgeCast
---

# Settlement Regime Architecture

## Core Rule
Regime is determined by `target_settlement_date` (not creation date, not collection date).
- `LEGACY_NWS`: settlement date < 2026-08-14
- `WEATHER_COMPANY`: settlement date ≥ 2026-08-14

**Why:** The contract's own settlement rules govern which authority publishes the final result. A trade evaluated on Aug 12 for a contract settling Aug 14 is `WEATHER_COMPANY` even though it was analyzed under NWS tooling.

## Implementation
- `services/settlement_regime.py` — authoritative module, `infer_settlement_regime()`, constants
- `WEATHER_COMPANY_TRANSITION_DATE = date(2026, 8, 14)` — do NOT change this
- Stamped at trade creation in `paper_trading.py`, `paper_trading_v21.py`, `paper_trading_v22.py`
- Stamped retroactively at settlement in `settlement.py` (backfills NULL rows)
- `outcome_verified` set to `True` when Kalshi confirms settlement (Kalshi is authoritative)

## DB Schema
`paper_trades.settlement_regime VARCHAR(20)` — NULL on pre-migration rows (treat as LEGACY_NWS)
`paper_trades.outcome_verified BOOLEAN` — NULL at creation, True on Kalshi settlement

**How to apply:** Before any calibration query, optionally filter `settlement_regime = 'LEGACY_NWS'` to exclude WEATHER_COMPANY trades from NWS-calibrated models (and vice versa) until methodology equivalence is confirmed.

## Unresolved (as of 2026-08-12)
- Exact rounding rules for The Weather Company temperatures
- Station/location mapping changes (if any)
- Systematic NWS vs Weather Company differences — must be checked post-Aug-14

## Mission Control Readiness Split
`forward-test-status` endpoint: `officialSettledCount = V2.3 + V3 only` (NOT V2.2).
V2.2 is in `byStrategy.v22` as historical reference. V2.3 is in `byStrategy.v23` as current.
