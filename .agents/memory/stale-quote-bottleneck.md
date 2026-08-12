---
name: Stale Quote Bottleneck Root Cause
description: Why 34 RESEARCH_ONLY trades per batch are rejected for stale/missing quotes — expired markets with 10-day-old quotes from Kalshi API
---

# Stale Quote Bottleneck Root Cause

## Symptom
~34 candidates per batch receive `eligibility_reason = missing_or_stale_executable_quote`.
DB shows RESEARCH_ONLY trades with `quote_age_seconds ≈ 860,637` (9.96 days) or `≈ 998,508` (11.5 days).

## Root Cause
The Kalshi API returns markets with **past settlement dates** in its active markets list. These are markets that expired July 30–Aug 1 but are still returned as "active" (likely pending Kalshi's internal settlement confirmation).

When the collection pipeline sees these:
- `target_settlement_date` = July 30–Aug 1, 2026
- `quote_timestamp` = July 30, 2026 (the last day the market was actively trading)
- `created_at` = Aug 9, 2026 (the collection run that picked them up)

The 300s freshness gate correctly rejects them (`quote_age_seconds >> 300`), classifying them `RESEARCH_ONLY`. But RESEARCH_ONLY rows are still **created** for them, polluting the DB and analytics.

## What Is NOT The Problem
- The 300s gate is not broken or miscalibrated
- Collection cadence (3h) is not the cause
- Field parsing is correct

## Fix (not yet implemented)
In the collection pipeline or paper trading runner, skip markets where `target_settlement_date` is more than ~24h in the past at the time of evaluation. These are closed markets that will never produce fresh quotes.

**Key file:** `artifacts/api-server/app/services/collector.py` — filter before paper trading phase
**Alternatively:** In `paper_trading_v22.py` run function, skip candidates with settlement dates in the past.

**Why:** Any filter > 300s on settlement_date cutoff is safe because those markets cannot possibly produce fresh quotes (markets close before settlement), so they'd always be RESEARCH_ONLY anyway.
