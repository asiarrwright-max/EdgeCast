# V3 Lead-Time Precision — Findings and Phase 2 Implications

## Summary

The Open-Meteo Historical Forecast API (date-range mode) does not supply model
initialization timestamps. All records ingested from this source have
`init_time_source = "derived_prior_day_00z"` and `lead_time_bucket = "1d"`.
Short-range lead-time buckets (0–6h, 6–12h, 12–18h, 18–24h, 24–36h, 36–48h)
cannot be computed from this source and are reserved for future providers.

This is sufficient for the current V3 scope because Kalshi daily high/low
temperature markets normally open on the previous day — matching the ~1-day
effective lead of the archived GFS data.

---

## API Investigation

The `historical-forecast-api.open-meteo.com/v1/forecast` endpoint was probed
live on 2026-07-30 with the following tests:

### Modes

| Parameters | Result |
|-----------|--------|
| `start_date + end_date` | ✅ Works — returns one TMAX value per date |
| `forecast_days` (no date range) | ✅ Works — returns N days from "today" |
| `start_date + end_date + forecast_days` | ❌ HTTP 400: *"Parameter 'forecast_days' is mutually exclusive with 'start_date' and 'end_date'"* |

The two modes are hard walls, not defaults to override.

### API response keys (date-range mode)

```
['daily', 'daily_units', 'elevation', 'generationtime_ms',
 'latitude', 'longitude', 'timezone', 'timezone_abbreviation', 'utc_offset_seconds']
```

No model run timestamp. No 00Z/06Z/12Z/18Z identifier. No `model` field
(the API docs say `gfs_seamless` blends multiple GFS runs). `daily.time` entries
are `YYYY-MM-DD` strings — no time-of-day component.

`generationtime_ms` is server-side API response latency, **not** a model
initialization timestamp.

### Confirmation the data is genuine GFS forecast (not ERA5 reanalysis)

Denver, January 2024 — GFS vs NOAA GHCND station USW00003017:

| Date | GFS °F | NOAA °F | Error |
|------|--------|---------|-------|
| Jan 1 | 53.4 | 55.0 | −1.6 |
| Jan 2 | 48.2 | 48.9 | −0.7 |
| Jan 3 | 45.1 | 46.9 | −1.8 |
| Jan 4 | 35.6 | 33.1 | +2.5 |
| Jan 5 | 38.8 | 42.1 | −3.2 |

MAE ≈ 2.0°F. ERA5 reanalysis would match NOAA to within ~0.5°F. These errors
are consistent with a ~1-day-ahead GFS short-range forecast, confirming the
data is not reanalysis and passes the V3 lookahead validator's Rule 3.

---

## What "derived_prior_day_00z" means

For each valid date D, the provider sets:

- `forecast_init_time = D − 1 day at 00:00 UTC`  (conservative)
- `forecast_valid_time = D at 23:59:00 UTC`
- `lead_time_hours = 24` (nominal)
- `lead_time_bucket = "1d"`

The **computed lead** from these timestamps is ~48 hours (from Dec 31 00:00 to
Jan 1 23:59 UTC), not 24 hours. The nominal `lead_time_hours = 24` is the
provider's best estimate of when the model began producing useful output for
that valid date — approximately one calendar day before.

The init-time derivation is **intentionally conservative** (errs toward
rejecting look-ahead): by placing init_time 1 day before valid_time at 00Z,
the look-ahead validator applies a stricter constraint than the actual GFS
run schedule would require.

The `init_time_derived` flag in `missing_data_flags` marks every record from
this provider to surface this in audits.

---

## Short-range bucket scheme (for future API-provided timestamps)

When a future provider sets `init_time_source = "api_provided"`, the ingestion
engine computes `(forecast_valid_time − forecast_init_time).total_seconds() / 3600`
and assigns a short-range bucket:

| Exact lead (hours) | Bucket |
|--------------------|--------|
| < 6 | `0-6h` |
| 6 – 12 | `6-12h` |
| 12 – 18 | `12-18h` |
| 18 – 24 | `18-24h` |
| 24 – 36 | `24-36h` |
| 36 – 48 | `36-48h` |
| 48 – 72 | `2d` (broad fallback) |
| 72 – 96 | `3d` |
| … | … |

Phase 2 will group records by `lead_time_bucket` for bias and sigma estimation.
If a short-range bucket has fewer records than `MIN_SAMPLE` (defined in Phase 2),
it is merged into the next broader group before computing statistics.

The motivation: a prediction made at 02:00 local (shortly after a Kalshi market
opens) carries different uncertainty than one made at 18:00 local after 16 hours
of weather observations have already been published and the GFS has assimilated
new data. Short-range buckets let Phase 2 distinguish these cases.

### Practical implication for Kalshi market timing

Kalshi daily high/low markets typically open the prior day, meaning the relevant
forecast lead when EdgeCast trades is in the **18–36h** range. The current 1-day
bucket covers this range conservatively. Short-range granularity would be most
useful if EdgeCast ever trades intraday (same-day markets), which is not in scope
for V3.0.

---

## What would be needed for exact lead times

To obtain model run timestamps at sub-daily precision for historical GFS data:

1. **NOAA NOMADS/THREDDS GFS archives** — provides GRIB2 files with explicit
   model run times (e.g. `gfs.20240101/00/atmos/gfs.t00z.pgrb2.1p00.f024`).
   The `f024` component gives the exact forecast hour. Requires GRIB parsing
   (`cfgrib` or `wgrib2`).

2. **AWS Open Data GFS archive** — same files mirrored at
   `s3://noaa-gfs-bdp-pds/gfs.YYYYMMDD/HH/atmos/`.

3. **A paid weather API** (e.g. Weather.com Enterprise, Tomorrow.io) that
   explicitly exposes model run times alongside historical forecast data.

Implementing option 1 or 2 would allow populating `init_time_source = "api_provided"`
and computing exact short-range buckets. The `_compute_lead_bucket()` function in
`v3_ingestion.py` and the `SHORT_RANGE_BUCKETS` constant are already in place to
handle this without any schema changes.

---

## Future expansion scope

Multi-day lead times (3d–7d) are documented here as a possible future expansion
**only if** EdgeCast later trades Kalshi markets that are listed farther in advance.
As of V3.0, Kalshi daily temperature markets open 1 day before settlement; the
1-day lead is the only operationally relevant lead for this strategy.

---

## Schema fields added in this refinement

| Field | Table | Type | Meaning |
|-------|-------|------|---------|
| `init_time_source` | `v3_historical_records` | `VARCHAR(30)` | How `forecast_init_time` was determined |

Values:
- `"derived_prior_day_00z"` — all current Open-Meteo records
- `"api_provided"` — reserved for future providers with explicit run timestamps

The `lead_time_bucket` column already existed and now formally supports both
the daily scheme and the short-range scheme. Current records use `"1d"` only.
