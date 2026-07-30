---
name: V2.1 Pipeline Fixes
description: Root causes and fixes from the July 2026 EdgeCast pipeline audit; sigma governance, station coordinates, quote freshness, V2.1 strategy structure.
---

## Root Causes Fixed (July 2026 Audit)

**Root cause 1 — Sigma too small (critical)**
- V2.0 with MIN_SAMPLE=5 accepted 5-sample σ=1.22°F, which is 3-6× smaller than real forecast errors (3-10°F).
- Fix: MIN_SAMPLE raised to 30. SIGMA_FLOOR=3.5°F added (daily); SIGMA_FLOOR_HOURLY=2.0°F. SIGMA_CEILING=15.0°F.
- Conservative prior added: _conservative_prior(lead) replaces V1 fixed table when DB has < MIN_SAMPLE samples. Prior values are 2x larger than V1 table (e.g. 5.0°F at lead 0-1d vs V1's 2.5°F).

**Root cause 2 — City-centre coordinates (critical)**
- SERIES_TO_CITY and CITY_COORDS used downtown lat/lon for all cities.
- Fix: All 32 SERIES_TO_CITY entries and 13 CITY_COORDS entries updated to settlement-station coordinates (airports, Central Park, etc).
- OKC: downtown (35.47, -97.52) → KOKC airport (35.39, -97.60). 7-mile offset contributes ~2°F; most of the 9°F error was Open-Meteo model error.
- LAX: downtown (34.05, -118.24) → KLAX airport (33.94, -118.39). USC Downtown may be Kalshi's actual station — keep LAX unverified until confirmed.

**Root cause 3 — No station verification guard**
- V2.0 traded all cities regardless of whether the settlement station mapping was confirmed.
- Fix: V2.1 skips (not excludes) any market for an unverified city.

**Root cause 4 — Stale quotes & execution realism**
- 24h stale-quote threshold was too permissive.
- Fix in V2.1: STALE_QUOTE_SECONDS=14400 (4h). Trades with >50 qty at <$0.10 flagged is_executable=False.

## Station Status After Audit

| City | Station | Verified | Notes |
|------|---------|----------|-------|
| New York City | Central Park (40.78, -73.97) | ✓ | |
| Chicago | Midway KMDW (41.79, -87.75) | ✓ | |
| Denver | KDEN airport (39.86, -104.67) | ✓ | 25 mi east of downtown |
| Oklahoma City | KOKC Will Rogers (35.39, -97.60) | ✓ | Verified July 2026 |
| Los Angeles | KLAX airport (33.94, -118.39) | ✗ | USC Downtown may be actual station |
| All others | Airport ICAO | ✗ | Pending contract PDF confirmation |

V2.1 will only trade NYC, Chicago, Denver, Oklahoma City until others are verified.

## New Files / Changes

- `paper_trading_v21.py` — new strategy, imports from paper_trading_v2.py where shared
- `settlement_stations.py` — OKC set verified=True with audit notes; LAX notes updated
- `probability_engine_v2.py` — MIN_SAMPLE, SIGMA_FLOOR, SIGMA_CEILING, _conservative_prior()
- `kalshi.py` — SERIES_TO_CITY and CITY_COORDS updated to station coords
- `models.py` — PaperTrade: added quote_bid, quote_ask, quote_timestamp, est_available_qty, is_executable, station_verified, station_lat, station_lon
- `database.py` — migration adds 8 new paper_trades columns
- `collector.py` — Step 5e runs run_paper_trading_v21() after v2.0
- `tests/test_v21_fixes.py` — 27 targeted validation tests (all pass)

## Consensus Guard

Built into V2.1 but disabled by default. Enable via AppSetting:
  `paper_trading_v21.consensus_guard_enabled = true`
Threshold: 85% consensus against our direction → skip.
Backtest via: `await consensus_guard_backtest(session)`.

**Why:** The guard avoids trading against 95-99% consensus markets (all OKC/LAX losses had 90%+ consensus against EdgeCast). Disabled by default until backtest data confirms it doesn't sacrifice too many wins.

## get_verified_station() Bug

The existing `get_verified_station(city)` function in settlement_stations.py has a documentation/implementation mismatch — the docstring says it returns None for unverified stations, but the code returns `s` regardless (`return s if (...) else s`). V2.1 uses `get_station(city).verified` directly instead of relying on this function.
