---
name: Guard 3 market close timestamp
description: Guard 3 uses actual Kalshi market close_time, not target_settlement_date_str; boundary is <= 120 min
---

## Rule
Guard 3 evaluates `market_close_timestamp` (Kalshi's actual `close_time` field), not `target_settlement_date_str`.
- `market_close_timestamp=None` → RESEARCH_ONLY (`REASON_CUTOFF`)
- `seconds_to_close <= 7200` (≤ 120 min) → RESEARCH_ONLY
- `seconds_to_close > 7200` (> 120 min) → passes Guard 3

`target_settlement_date_str` is used **only** for Guard 2 (same-day local-timezone check).

## Why
`target_settlement_date_str` is settlement time in local NWS timezone, not market close time. A market can stop accepting orders well before the weather event settles. Using `close_time` is the only reliable way to avoid entering trades after the market has closed.

## How to apply
- Pass `market_close_timestamp=market.close_time` to `assess_trade_eligibility` from both V2.2 and V3 pipelines.
- A naive (no tzinfo) `market_close_timestamp` is accepted and treated as UTC.
- Test fixtures for Guard 3 must set `market_close_timestamp` explicitly; the default `_build_base_kwargs()` includes one 2 days out.
- DST and timezone offsets do not affect the cutoff check — it's pure UTC arithmetic.
