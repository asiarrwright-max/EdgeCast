---
name: V3 Lookahead Validator Rule Ordering
description: Rule 4 (LOOKAHEAD_VIOLATION) fires before Rule 5 (VALID_TIME_INCONSISTENCY); the two are not independent.
---

When `forecast_valid_time < forecast_init_time`, the lookahead formula computes `max_allowed_init = valid_time - lead + tolerance`, which is even earlier than `valid_time`. Since `init_time > valid_time > max_allowed_init`, Rule 4 fires and Rule 5 is never reached.

**Why:** The validator short-circuits on the first failing rule. Rule 4 subsumes Rule 5 for this configuration.

**How to apply:** Tests asserting VALID_TIME_INCONSISTENCY must accept either `LOOKAHEAD_VIOLATION` or `VALID_TIME_INCONSISTENCY` as the rejection reason when `valid_time < init_time`, OR must set `retrieval_timestamp` far in the future and choose values that pass Rule 4 but fail Rule 5 (hard to do — easier to just accept both).
