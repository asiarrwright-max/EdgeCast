---
name: Open-Meteo Historical Forecast API — single lead time constraint
description: The forecast_days param is mutually exclusive with start_date/end_date; only one effective lead time per date-range query.
---

## Rule
The Open-Meteo **Historical Forecast API** (`historical-forecast-api.open-meteo.com/v1/forecast`) has two mutually exclusive operating modes:

1. **Live forecast mode**: uses `forecast_days=N` to project N days ahead from today. Cannot be combined with `start_date`/`end_date`.
2. **Date-range mode**: uses `start_date` + `end_date`. Cannot be combined with `forecast_days` — API returns HTTP 400.

Date-range mode returns **one value per date at a fixed effective lead time** (~1–2 days ahead, empirically ~2°F MAE vs NOAA GHCND for Denver January). There is no way to retrieve multi-lead-time historical data via date ranges — each lead time would require one API call per day per city (~366 calls per city per year).

**Why:** This was discovered when every combination of `forecast_days` + `start_date`/`end_date` returned `{"error":true,"reason":"Parameter 'forecast_days' is mutually exclusive with 'start_date' and 'end_date'"}`. The V3 provider was initially designed assuming multi-lead retrieval was possible; it was not.

**How to apply:** V3 stores all Open-Meteo Historical Forecast records with `lead_time_hours=24` (nominal 1-day lead). Phase 2 will have only one lead-time bucket ("1d") from this provider. For true multi-lead coverage, a different data source (NOAA GFS archives via NOMADS/THREDDS, or a paid API) would be needed.

**Confirmed NOT reanalysis:** Jan 2024 Denver MAE vs NOAA GHCND ≈ 2°F — inconsistent with ERA5 reanalysis (<0.5°F). This is genuine GFS forecast data.
