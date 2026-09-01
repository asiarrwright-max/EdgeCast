# Settled V3 Accuracy Lab evidence status

Status: **DATA_BLOCKED — no measured production result is asserted here.**

The prior one-row OFFICIAL run is rejected as an incomplete source. This branch
requires either direct read-only access to the production PostgreSQL database,
or a complete export plus a manifest whose `complete_settled_v3_count` equals
the export row count. The complete population is every row returned by:

```sql
SELECT *
FROM v3_paper_trades
WHERE status = 'SETTLED'
ORDER BY target_settlement_date, id;
```

The required source is the production `v3_paper_trades` table (or a CSV/JSON
export of that query) with, at minimum: `id`, `market_ticker`, `city`,
`weather_variable`, `contract_type`, `target_settlement_date`,
`strategy_version`, `direction`, `ec_yes_probability`, `ec_side_probability`,
`side_market_price`, `lead_time_days`, `historical_sigma`, `final_sigma`,
`station_verified`, `stake`, `profit_loss`, `status`, `outcome`, and
`eligibility_status`. No `DATABASE_URL` or complete export is available in the
current Codex environment, so exact N, win rate, Brier, calibration, cohort
weaknesses, holdout results, and a V3.1 recommendation cannot be truthfully
reported yet.

## #43 / PR #45 blocker

The retrospective raw stale/missing-quote cohort exists in
`v3_paper_trades`, but the exact “otherwise eligible except quote” count cannot
be certified without the complete production rows and contemporaneous guard
fields (`decision_timestamp`, `market_close_timestamp`,
`settlement_timezone`, `side_market_price`, `edge_pct_points`, and
`station_verified`). Correlated-exposure state is not persisted on the trade,
so that guard is not exactly reconstructable from this table alone; its
decision-time guard result or exposure snapshot/export is additionally needed.

There can be no production JIT observations until PR #45's instrumentation is
deployed. `main` has no `v3_jit_quote_audits` table/model or write path. After
deployment, the exact JIT source is a read-only export of
`v3_jit_quote_audits`, including outcome, selected-side asks, market status,
latency, quote age, `other_guards_pass`, and failure reason. Historical rows
must not be backfilled or reclassified.

## Reproduction

From `artifacts/api-server`, run:

```bash
PYTHONPATH=. python scripts/run_v3_settled_bakeoff.py
```

or use `--input complete.csv --manifest manifest.json`. The runner writes the
full JSON report, main-cohort CSV, untouched-holdout JSON, and holdout-results
CSV. It exits with `BLOCKED_INCOMPLETE_SOURCE` when completeness cannot be
proved.
