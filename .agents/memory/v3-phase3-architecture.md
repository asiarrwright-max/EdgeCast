---
name: V3 Phase 3 architecture
description: How V3 live predictions, paper trading & settlement are wired into the collection pipeline
---

## Pipeline flow (collection job)
5e → V2.1 paper trading
5f → `v3_predictor.run_v3_predictions()` — creates V3PredictionSnapshot per active market
5g → `v3_paper_trading.run_paper_trading_v3()` — creates V3PaperTrade from V3 snapshots
5h → finalise job

Settlement scheduler runs `v3_settlement.run_v3_settlement_job()` immediately after the V1/V2/V2.1 `run_settlement_job()` in `_settlement_loop`.

## Key constants
- V3_MODEL = "GFS", V3_LEAD_BUCKET = "1d" (only bucket in the preload)
- STRATEGY_VERSION = "v3.0" (unique constraint key for V3PaperTrade)
- Minimum edge: 10pp (same as V2.1), stake: $10

## Comparison linkage
- `run_v3_predictions()` generates a UUID per prediction
- UUID is written to both `V3PredictionSnapshot.comparison_group_id` AND (if NULL) the paired `PredictionSnapshot.comparison_group_id`
- `GET /analytics/v3/live-comparison` joins on this UUID

## Feature flags (must be 'true' in app_settings)
- `v3.predictions_enabled` — gates run_v3_predictions()
- `v3.paper_trading_enabled` — gates run_paper_trading_v3()
- Both set to 'true' as of Phase 3 delivery

## V3PredictionSnapshot new fields (Phase 3)
- `bias_applied BOOLEAN` — whether the bias gate passed for this prediction
- `bias_suppressed_reason VARCHAR(200)` — why bias was NOT applied

## What V3 trades vs skips
V3 skips: hourly_threshold contracts, unverified/non-NWS stations, markets with no active KalshiMarket
V3 allows: all verified NWS cities (uses global fallback sigma for non-Denver/OKC)

**Why:** V3's value is calibrated sigma; the global fallback (σ≈3.978°F) is still better than V2.1's fixed table for untrained cities.

## Phase 3 analytics endpoints
GET  /analytics/v3/live-predictions
GET  /analytics/v3/live-paper-trades
GET  /analytics/v3/live-comparison
POST /analytics/v3/run-v3-predictions     (manual trigger)
POST /analytics/v3/run-v3-paper-trading   (manual trigger)
POST /analytics/v3/run-v3-settlement      (manual trigger)
POST /analytics/v3/enable-predictions
POST /analytics/v3/enable-paper-trading
