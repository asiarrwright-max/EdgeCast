---
name: Bet Watch architecture
description: How the Bet Watch read-only decision-support layer is structured and key design decisions.
---

# Bet Watch Architecture

## Data source
Reads exclusively from `paper_trades WHERE strategy_version='v2.3' AND created_at > NOW()-48h`.
No new predictions, no new DB writes — purely observational over the FTB scan output.

## Watch status mapping (pure function `_watch_status`)
Order matters — evaluated top-to-bottom:
1. `v2_excluded` reason → AVOID / STALE
2. price ≤ $0.01 → AVOID / STALE
3. quote age > 2 hours → AVOID / STALE (even for OFFICIAL rows — display-only, doesn't change DB)
4. no timestamp + stale-quote reason → AVOID / STALE
5. eligibility_status == OFFICIAL → OFFICIAL-ELIGIBLE
6. `correlated_outcome_limit` → NEAR OFFICIAL
7. `missing_or_stale_executable_quote` + age < 15 min → NEAR OFFICIAL; else PRELIMINARY
8. station/cutoff/extreme-edge → WATCHING
9. price-floor/hourly/same-day → PRELIMINARY

**Why:** AVOID/STALE must be checked before OFFICIAL so stale data never shows as actionable.

## Ranking
Primary sort: `_STATUS_ORDER` (OFFICIAL-ELIGIBLE=0 … AVOID/STALE=4).
Secondary: composite score = `(edge_pp * freshness_factor * liquidity_factor) + status_bonus + station_bonus`.

**Why:** pure edge ranking is gamed by illiquid/stale markets; composite score prefers actually actionable opportunities.

## Best opportunity filter
Must pass all three: watch_status != AVOID/STALE, edge >= 3.0pp, kalshi_price is not None.
kalshi_price=None check prevents fabricating a recommendation from missing price data.

## Files
- Router: `artifacts/api-server/app/routers/bet_watch.py`
- Tests: `artifacts/api-server/tests/test_bet_watch.py` (58 tests, 9 spec requirements)
- API client: `lib/api-client-react/src/bet-watch.ts`
- Frontend page: `artifacts/edgecast/src/pages/bet-watch.tsx`
- Nav: `artifacts/edgecast/src/components/layout.tsx` (Eye icon, /bet-watch first in nav)
- Route: `artifacts/edgecast/src/App.tsx`
- Audit section: `BetWatchAuditSection` in `artifacts/edgecast/src/pages/audit-validation.tsx`
- Router registered in `artifacts/api-server/main.py`

## Safety guarantees (always in response)
`trading_state_modified: false` and `ftb_untouched: true` — hardcoded, never computed.
