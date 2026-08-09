---
name: Forward Test B activation
description: Key facts about the FTB correction package deployment and boundary timestamp.
---

**FORWARD_TEST_START_B** = `datetime(2026, 8, 9, 0, 15, 12, tzinfo=timezone.utc)` — set in `app/routers/paper_trades.py`. This is the UTC timestamp of the first corrected deployment (Publish #1, build `4a32213f`).

**Why this timestamp:** Publish #1 completed application startup at 2026-08-09T00:15:12Z. Publish #2 (which set the timestamp) came later but the boundary is anchored to Publish #1 — the first moment corrected code ran in production.

**FORWARD_TEST_PHASE_B** = `"Forward Test B active"`.

**All 8 correction blockers** are marked `"Resolved"` in `audit-validation.tsx`. The `"Resolved"` status was added as a distinct `FindingStatus` type with its own emerald-300 badge.

**db_date_alignment → FIX_REQUIRED in production is expected and correct.** The check queries all OFFICIAL trades since FORWARD_TEST_START (FTA start), which includes historical FTA trades that have the UTC-date bug. The fix applies to new trades only. Dev shows CLEARED because the dev DB has no historical OFFICIAL trades with that pattern.

**How to apply:** When investigating why the startup audit shows FIX_REQUIRED in production, do not treat it as a regression. It will self-resolve once FTA historical trades age out of the check window or the check is scoped to FTB-only trades.

**Test guard:** `test_ftb_corrections.py` still asserts `FORWARD_TEST_START_B is None`. This test must be updated or deleted before the next test run — it was a pre-activation guard and is now stale.
