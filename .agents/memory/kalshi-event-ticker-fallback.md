---
name: Kalshi event_ticker series fallback
description: Kalshi API often omits series_ticker from response bodies; must derive it from event_ticker instead.
---

## Rule
When `series_ticker` is empty/null in a Kalshi market response, derive it from `event_ticker` by stripping the trailing date segment (`DD[A-Z]{3}DD` pattern).

**Example:** `event_ticker = "KXLOWTNYC-26JUL27"` → `series_ticker = "KXLOWTNYC"`

**Why:** The Kalshi public API frequently omits `series_ticker` in market response bodies, even when you filtered the request by series. Without this fallback, every such market fails city extraction and becomes a parse failure — despite `SERIES_TO_CITY` having a perfect match.

**How to apply:** In `extract_city()` (and any function consuming series_ticker from raw Kalshi dicts), run the fallback before looking up SERIES_TO_CITY. The regex `^\d{2}[A-Z]{3}\d{2}$` identifies date segments to strip.
