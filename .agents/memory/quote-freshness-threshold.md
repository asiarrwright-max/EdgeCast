---
name: Quote freshness threshold
description: OFFICIAL_STALE_QUOTE_SECONDS changed from 4h to 300s; test fixtures must stay within it
---

## Rule
`OFFICIAL_STALE_QUOTE_SECONDS = 300` (5 minutes). The staleness check is `age > 300` (strictly greater than), so exactly 300 s passes, 301 s fails.

## Why
5-minute freshness matches Kalshi quote staleness in a real-time trading context. The old 4-hour limit was dangerously lax.

## How to apply
- Any test fixture that creates a "fresh" quote timestamp must use `timedelta(seconds=60)` (or similar < 300 s), never `timedelta(minutes=30)` — that's stale under the new threshold.
- Boundary tests for exactly 300 s must anchor both `now` and the quote timestamp to the **same** `_now_utc()` call to avoid clock-advance flakiness (`age = 300.001 > 300`).
- Pattern: `now = _now_utc(); ts = now - timedelta(seconds=300); assess_trade_eligibility(..., quote_timestamp=ts, now=now)`
