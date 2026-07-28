---
name: Kalshi price field names
description: Kalshi REST API returns bid/ask prices with a _dollars suffix, not bare names.
---

# Kalshi price field names

## The rule
Kalshi's REST API returns market prices as `yes_bid_dollars`, `yes_ask_dollars`,
`no_bid_dollars`, `no_ask_dollars` — NOT `yes_bid`, `yes_ask` etc.

Values are in decimal format (0.0–1.0), not cents.

**Why:** The original parse_market() looked for `yes_bid` (bare key), which never
existed in the API response body. All prices were stored as None. Fixed in Phase 2A
by reading `raw.get("yes_bid_dollars") or raw.get("yes_bid")` with both names for
forward-compatibility.

**How to apply:** Any code that reads raw Kalshi market dicts for prices must use
the `_dollars` suffixed keys. The `_normalise_price()` helper still applies — values
> 1 are divided by 100, but these dollar values are already 0–1 and pass through
unchanged.
