---
name: V3 same-day contract staleness
description: Same-day (target_date = today) contracts have extreme claimed edges because market prices reflect the already-observed morning temperature, not a forecast.
---

## The rule

For low-temperature contracts (KXLOWTXXX) settling on the current calendar day, the daily
minimum occurs in the early morning hours (typically 3–7am local time).  By the time the
collection job runs (midday), the market price already reflects the *known* outcome.

V3, however, still holds the prior-day GFS forecast and computes a probability based on
uncertainty.  The result is a massive claimed edge that is spurious — V3 is not predicting
the future, it is comparing a stale forecast against a settled market.

**These trades are already correctly screened by `is_executable=False`** via the 4-hour
quote-staleness check in `paper_trading_v21.py → _is_executable()`.  No new bugs; the
screening works.  But the root cause of the "extreme edge" trades (74–94pp) seen in
investigation 2 is this staleness, not a formula error.

## What is NOT a problem
- Price direction (NO uses no_ask, YES uses yes_ask) ✓
- Edge formula (ec_side_prob - side_market_price) ✓  
- No midpoint-as-price error ✓
- No inverted YES/NO confusion ✓

## What is a latent issue
V3 still enters PENDING snapshots and may attempt paper trades for same-day contracts
where the outcome is already known.  A stronger guard would skip any market where
`target_date <= today` and `settlement_variable == 'low'` (low temps occur before midday)
or where `target_date < today` for any variable.  Currently the 4-hour quote age filter
catches these at execution time but does not prevent the (wasteful) PENDING snapshot.

## Official ROI rule
Only `is_executable=True` paper trades count for official V3 ROI metrics.
Of the first 27 open V3 paper trades:
- 10 are `is_executable=True` (valid entry quote at time of paper-trade decision)
- 17 are `is_executable=False` (stale quote, excluded from official ROI)
