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

## Controlled paper-trading cycle result (first live cycle)
64 V2.2 trades: 39 exec OPEN, 11 non-exec OPEN, 14 V2_EXCLUDED. 0 duplicates.
All V2.2 tickers have a matching V2.1 trade; 0 V2.2-only markets.
Non-zero V2.1/V2.2 deltas are **timing artifacts** — V2.1 entered earlier with an older quote; V2.2 re-runs today's quote. Not a formula difference (bias still 0 for both).

## 429 rate-limit safety
The 429 fires at the Kalshi all-markets cursor level (series-level scan), AFTER market fetch, BEFORE trade creation. No partial trade is ever committed. Duplicate guard in `maybe_create_paper_trade_v21` (and v22) checks for existing OPEN trade by ticker+strategy_version before INSERT — retry is fully safe.

**How to apply:** Any new strategy that modifies the probability engine must use a distinct STRATEGY_VERSION and isolated settings prefix. V2.2 is the template for this pattern.
