# V3 Phase 1 Deliverables

**Date completed:** 2026-07-30  
**Status:** ✅ Ready for review

---

## What Phase 1 Delivers

V3 Phase 1 builds the foundation for the Historical Preload strategy: schema, data pipeline, and audit UI. Nothing here changes V1, V2, or V2.1 in any way.

---

## Files Added

### Backend — Models

| File | Description |
|------|-------------|
| `app/models_v3.py` | Six SQLAlchemy V3 tables (all `v3_` prefix); `V3_FLAG_DEFAULTS`; `CURRENT_PRELOAD_VERSION = "v3.0"` |

**V3 tables:**
- `v3_raw_source_records` — immutable verbatim API responses (never modified after insert)
- `v3_historical_records` — normalized forecast + observation pairs; unique constraint prevents duplicates on `(city, station_id, target_date, forecast_source, forecast_model, lead_time_hours, preload_version)`
- `v3_error_stats` — per-city, per-lead-time bias/sigma stats (Phase 2 writes these)
- `v3_prediction_snapshots` — Phase 3 parallel predictions
- `v3_paper_trades` — Phase 3 paper trades
- `v3_ingestion_logs` — per-city per-run audit trail

### Backend — Provider Abstraction

| File | Description |
|------|-------------|
| `app/services/v3_providers/__init__.py` | Package stub |
| `app/services/v3_providers/base.py` | `ForecastHistoryProvider` ABC; `RawForecastRecord` dataclass; `ProviderDataError` |
| `app/services/v3_providers/registry.py` | `get_provider_class()`, `get_all_provider_keys()` |
| `app/services/v3_providers/open_meteo_forecast_history.py` | Open-Meteo Historical Forecast API (`historical-forecast-api.open-meteo.com`); GFS model; derives `forecast_init_time` = `valid_date − forecast_days` at 00Z |
| `app/services/v3_providers/noaa_ghcnd_observations.py` | NOAA CDO v1 GHCND TMAX observations; tenths-of-°C → °F; splits long date ranges into ≤365-day chunks |

### Backend — Services

| File | Description |
|------|-------------|
| `app/services/v3_lookahead.py` | Look-ahead validator; 5 hard rules + 1 soft flag; `LookaheadResult`; `RejectionReason` enum |
| `app/services/v3_ingestion.py` | Full orchestrator; per-city concurrency with semaphore; raw record storage → validation → normalization → observation pairing → `V3HistoricalRecord` insert; idempotent |
| `app/services/v3_flags.py` | `ensure_v3_feature_flags()` (idempotent seed); `get_v3_flag()` |

### Backend — Router

| File | Description |
|------|-------------|
| `app/routers/v3_analytics.py` | `GET /api/analytics/v3/flags`; `GET /api/analytics/v3/ingestion-audit`; `POST /api/analytics/v3/run-ingestion` |

### Backend — Modified (additive only)

| File | Change |
|------|--------|
| `app/models.py` | `import app.models_v3` (one line; registers V3 tables with `Base.metadata`) |
| `app/database.py` | `ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS comparison_group_id VARCHAR(36)`; calls `ensure_v3_feature_flags()` from `init_db()` |
| `main.py` | `app.include_router(v3_analytics.router, prefix="/api")` |

### Frontend

| File | Change |
|------|--------|
| `artifacts/edgecast/src/pages/v21-audit.tsx` | New `V3DataSection` component appended to the Strategy Audit page; shows feature flag states + per-city ingestion status table |
| `lib/api-client-react/src/v3-analytics.ts` | `V3FlagsData`, `V3Flag`, `V3CityAuditEntry`, `V3IngestionAuditData` types; `useGetV3Flags()`, `useGetV3IngestionAudit()` hooks |
| `lib/api-client-react/src/index.ts` | `export * from "./v3-analytics"` |

---

## Schema Details

### `v3_historical_records` (core table)

```
city                  VARCHAR
station_id            VARCHAR       — GHCND station (e.g. USW00023062)
target_date           DATE
forecast_source       VARCHAR       — provider key
forecast_model        VARCHAR       — "GFS"
lead_time_hours       INTEGER
preload_version       VARCHAR       — "v3.0"

forecast_init_time    TIMESTAMPTZ   — derived: valid_date − lead_days at 00Z
forecast_valid_time   TIMESTAMPTZ
forecast_tmax_f       FLOAT         — Fahrenheit (null if provider returned null)

observed_tmax_f       FLOAT         — NOAA GHCND (null until observation available)
error_f               FLOAT         — observed − forecast (null until both present)
abs_error_f           FLOAT
season                VARCHAR       — winter/spring/summer/fall

quality_status        VARCHAR       — "ok" | "lookahead_rejected" | "pending_observation"
rejection_reason      VARCHAR       — RejectionReason value if rejected
raw_source_id         INTEGER       — soft reference to v3_raw_source_records.id

UNIQUE (city, station_id, target_date, forecast_source, forecast_model, lead_time_hours, preload_version)
```

### Feature Flags (all default `false`)

| Key | Controls |
|-----|----------|
| `v3.ingestion_enabled` | Historical ingestion pipeline |
| `v3.validation_enabled` | Walk-forward validation (Phase 2) |
| `v3.predictions_enabled` | Live parallel predictions (Phase 3) |
| `v3.paper_trading_enabled` | Paper trading on V3 predictions (Phase 3) |

---

## Look-Ahead Validation Rules

| Rule | Hard/Soft | Condition |
|------|-----------|-----------|
| `MISSING_INIT_TIME` | Hard | `forecast_init_time` is None |
| `FUTURE_INIT_TIME` | Hard | `init_time > retrieval_time` |
| `REANALYSIS_NOT_ALLOWED` | Hard | `is_reanalysis=True` (ERA5, MERRA-2, etc.) |
| `LOOKAHEAD_VIOLATION` | Hard | `init_time > valid_time − lead_hours + 12h tolerance` |
| `VALID_TIME_INCONSISTENCY` | Hard | `valid_time < init_time` |
| `MISSING_FORECAST_VALUE` | **Soft** | `forecast_tmax_raw` is None — stored, not rejected |

---

## Data Source Decision

**Using:** `historical-forecast-api.open-meteo.com/v1/forecast` (actual past GFS model runs)  
**Not using:** `archive-api.open-meteo.com` (ERA5 reanalysis — would be rejected by Rule 3)

This distinction is enforced at the provider level: `OpenMeteoForecastHistoryProvider` sets `is_reanalysis=False` and uses the correct domain. Any future provider returning ERA5 must set `is_reanalysis=True` and will be rejected by the validator before any record is stored.

---

## Test Results

| Suite | Tests | Status |
|-------|-------|--------|
| `test_v3_lookahead.py` | 19 | ✅ all pass |
| `test_v3_ingestion.py` | 30 | ✅ all pass |
| `test_v21_regression.py` | 9 | ✅ all pass |
| Full suite | **525** | ✅ all pass (was 476 before Phase 1) |

**V2.1 regression tests** (in `test_v21_regression.py`) confirm:
- `decide_trade_v21` YES trade behavior unchanged (test_snapshot_1)
- `decide_trade_v21` edge-below-threshold skip unchanged (test_snapshot_2)
- Stale quote skip unchanged (test_snapshot_3)
- Unverified city skip unchanged (test_snapshot_4)
- Washington DC non-NWS skip unchanged (test_snapshot_5)
- All V2.1 constants (`STALE_QUOTE_SECONDS`, `NON_EXECUTABLE_MAX_QTY`, `NON_EXECUTABLE_MAX_PRICE`, `CONSENSUS_GUARD_THRESHOLD`, `STRATEGY_VERSION`) are unchanged after importing all V3 modules

---

## V2.1 Isolation Guarantee

V3 code is additive only. The following were explicitly tested:
- V3 table names all start with `v3_`; no existing table is referenced
- `ForecastErrorStats` and `V3ErrorStats` are separate classes on separate tables
- `PaperTrade` and `V3PaperTrade` are separate classes on separate tables
- `PredictionSnapshot` and `V3PredictionSnapshot` are separate classes on separate tables
- All four V3 feature flags default to `false`; importing V3 modules does not activate anything
- `init_db()` calls `ensure_v3_feature_flags()` which is fully idempotent (no-op if flags already exist)

---

## Current Status of V3 Tables

As of Phase 1 completion, all V3 tables are **created and empty**. Ingestion has not run because `v3.ingestion_enabled = false`.

The V3 Data section at the bottom of the Strategy Audit page shows:
- Feature flag table (all false)
- "No ingestion data yet" placeholder with instructions

---

## Known Limitations (Phase 1)

1. **No data yet.** Ingestion has not been triggered. The audit UI shows an empty state.
2. **Open-Meteo only.** GEFS and ECMWF are registered as comments in the provider registry; their implementations are placeholder.
3. **No bias/sigma model.** `V3ErrorStats` exists but is never written — that is Phase 2's job.
4. **Lead-time derivation is conservative.** `forecast_init_time = valid_date − forecast_days` at 00Z. For GFS runs initialized at 06Z, 12Z, or 18Z, this over-estimates the lead by 0–18h. This is intentional: it errs on the side of rejecting look-ahead rather than allowing it.
5. **NOAA CDO rate limits.** The CDO v1 API has low rate limits. The ingestion pipeline uses per-city sequential requests for NOAA (concurrent for Open-Meteo). For a 2-year, 24-city ingestion run, expect ~15–30 minutes.

---

## Recommendation to Proceed to Phase 2

Phase 1 is complete. The recommendation is to:

1. Enable `v3.ingestion_enabled = true` via the app_settings table
2. Trigger a test ingestion for 1–2 cities via `POST /api/analytics/v3/run-ingestion` with `{"cities": ["Denver", "Oklahoma City"], "start_date": "2024-01-01", "end_date": "2024-12-31"}`
3. Verify accepted/rejected counts and quality_status distribution in the audit UI
4. Run the full 2-year ingestion across all verified cities
5. Approve Phase 2 when satisfied with data quality

Phase 2 task (#42) is `PENDING` with `blockedBy: PARENT` until this review is complete.
