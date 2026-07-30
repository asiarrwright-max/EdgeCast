---
name: V2.2 isolation architecture
description: How V2.2 is isolated from V2.1, what it shares, and where the sign fix lives
---

## The one changed line
`probability_engine_v22.run_analysis_v22`: `mu = forecast_value + bias` (was `- bias` in V2.1).
**Why:** mean_error = mean(actual−forecast); positive → GFS under-forecasts → mu should rise, not fall.

## What V2.2 reuses vs overrides
- Reuses: all helpers (`_bias_v2`, `_sigma_v2`, `_calibration_adj_v2`, all guards) imported from v21/v2.
- Overrides: one mu line, bias_note direction label, explanation prefix "[v2.2]".
- Paper trades go into `paper_trades` table with `strategy_version="v2.2"` (not a new table).

## Feature flags
`v2.2.predictions_enabled` and `v2.2.paper_trading_enabled` — both default "false", seeded in `ensure_v22_feature_flags()` which is called at startup in `app/database.py`.

## Shared comparison identifier
V2.1 and V2.2 both reference `prediction_snapshots` via `paper_trades.snapshot_id`.
`prediction_snapshots.comparison_group_id` is written by V3 predictor.
Cross-strategy join: `paper_trades → snapshot_id → prediction_snapshots.comparison_group_id = v3_paper_trades.comparison_group_id`.
No new DB column needed.

## Collector step
Step 5e_b in `collector.py` — runs V2.2 paper trading after V2.1 (step 5e), before V3 predictions (step 5f). Non-fatal exception wrapper; logs if flag disabled.

## V3 analytics sections
`_compute_v3_trade_sections()` and helpers (`_fee_estimate`, `_v3_brier_score`) are module-level exports in `v3_analytics.py` — tested without DB using duck-typed mock trades.
Official ROI uses only `is_executable=True`. Non-executable and observation-only are separate buckets.

**How to apply:** Any new strategy that modifies the probability engine must use a distinct STRATEGY_VERSION and isolated settings prefix. V2.2 is the template for this pattern.
