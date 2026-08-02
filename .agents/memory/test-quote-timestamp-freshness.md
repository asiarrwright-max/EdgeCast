---
name: test-quote-timestamp-freshness
description: Test fixtures that use a hardcoded datetime for quote_timestamp will silently break the next day because strategy_comparison._is_quote_fresh rejects quotes older than 4 hours.
---

**Rule:** Never hardcode a specific date for `quote_timestamp` in test fixtures. Use `datetime.now(timezone.utc)` at module level.

**Why:** `_is_quote_fresh(quote_ts, now)` returns `(now - quote_ts).total_seconds() < _STALE_QUOTE_SECONDS` where `_STALE_QUOTE_SECONDS = 4 * 3600`. A fixture with a date from the prior day passes all filtering checks except staleness, causing `_best_bet_today` to silently return `has_bet: False` rather than raising an error. Tests that assert `has_bet is False` continue to pass, masking the regression.

**How to apply:** In any test file that exercises `_best_bet_today` or anything that calls `_eligible()` in strategy_comparison.py, define the timestamp constant as:

```python
NOW = datetime.now(timezone.utc)
```

not as:

```python
NOW = datetime(2026, 8, 1, 14, 0, 0, tzinfo=timezone.utc)  # WRONG — stale after Aug 1
```
