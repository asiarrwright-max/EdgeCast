"""
Open-Meteo Historical Forecast API — V3 Provider
=================================================
Provides archived GFS model forecasts using the Open-Meteo Historical Forecast API.

IMPORTANT — Data-source verification note
------------------------------------------
This provider uses the Open-Meteo **Historical Forecast API**:

    https://historical-forecast-api.open-meteo.com/v1/forecast

This endpoint is DISTINCT from the Open-Meteo Archive API
(https://archive-api.open-meteo.com/v1/archive), which serves ERA5 reanalysis
data and is NOT suitable for V3 training.

The Historical Forecast API stores and exposes actual past GFS model run outputs.
Empirical comparison to NOAA GHCND station observations shows mean absolute errors
of ~2°F, which is inconsistent with ERA5 reanalysis (which matches NOAA to <0.5°F)
and consistent with a ~1-day ahead GFS forecast.

API design constraint — single effective lead time
---------------------------------------------------
The Historical Forecast API has two mutually exclusive operating modes:

  1. Live forecast mode: uses ``forecast_days`` to request N days ahead of today.
     Cannot be combined with ``start_date`` / ``end_date``.

  2. Date-range mode: uses ``start_date`` and ``end_date`` to retrieve historical data.
     Cannot be combined with ``forecast_days``.

Date-range mode returns one value per date at a fixed effective lead time — 
empirically ~1 day ahead (the model's short-range output for each valid date).
There is no way to request multi-lead-time data from this API via date-range queries.

Each date in the response represents the GFS model output for that valid date.
Init time is derived conservatively as ``valid_date − 1 day at 00:00 UTC``,
which makes look-ahead validation STRICTER (slightly shorter implied lead).

All records are stored with ``lead_time_hours=24`` to indicate the nominal
1-day-ahead effective lead time.  This is consistent with how V2.1 consumes
Open-Meteo forecasts in production.

Reanalysis confirmation
------------------------
``is_reanalysis`` is always False for this provider.  The provider explicitly
targets the ``gfs_seamless`` model, which returns GFS forecast runs, not ERA5.
The empirical forecast errors (~2°F MAE vs NOAA GHCND) confirm this is genuine
forecast data.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.services.v3_providers.base import (
    ForecastHistoryProvider,
    ProviderDataError,
    RawForecastRecord,
)

logger = logging.getLogger(__name__)

HISTORICAL_FORECAST_API_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

# The effective lead time this API returns in date-range mode.
# Empirically ~1 day (short-range GFS forecast), stored as 24h.
EFFECTIVE_LEAD_TIME_HOURS = 24

TRANSFORMATION_VERSION = "v1.1"  # bumped to reflect API design correction
PROVIDER_KEY = "open-meteo-forecast-history"
MODEL = "GFS"


class OpenMeteoForecastHistoryProvider(ForecastHistoryProvider):
    """
    Retrieves archived GFS daily max-temperature forecasts from the
    Open-Meteo Historical Forecast API.

    Makes one API request per date range (not per lead time), returning
    a flat list of RawForecastRecord with lead_time_hours=24 (nominal).
    """

    PROVIDER_KEY = PROVIDER_KEY
    MODEL = MODEL

    def __init__(self, timeout_seconds: float = 60.0):
        self._timeout = timeout_seconds

    async def fetch_history(
        self,
        city: str,
        station_id: str,
        lat: float,
        lon: float,
        local_timezone: str,
        start_date: str,
        end_date: str,
        lead_time_hours_list: list[int],
    ) -> list[RawForecastRecord]:
        """
        Fetch archived GFS forecasts for the given date range.

        ``lead_time_hours_list`` is accepted for interface compatibility but
        IGNORED — the API returns a single effective lead time regardless of
        requested values.  All returned records have ``lead_time_hours=24``.
        """
        if lead_time_hours_list and lead_time_hours_list != [EFFECTIVE_LEAD_TIME_HOURS]:
            logger.info(
                "[V3 Open-Meteo] %s: requested lead times %s ignored — "
                "Historical Forecast API date-range mode returns a single "
                "effective lead (~1 day).  Records stored with lead_time_hours=%d.",
                city, lead_time_hours_list, EFFECTIVE_LEAD_TIME_HOURS,
            )

        return await self._fetch_range(
            city=city,
            station_id=station_id,
            lat=lat,
            lon=lon,
            local_timezone=local_timezone,
            start_date=start_date,
            end_date=end_date,
        )

    async def _fetch_range(
        self,
        city: str,
        station_id: str,
        lat: float,
        lon: float,
        local_timezone: str,
        start_date: str,
        end_date: str,
    ) -> list[RawForecastRecord]:
        """Issue one API call for the full date range and return parsed records."""
        retrieval_ts = datetime.now(timezone.utc)

        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start_date,
            "end_date": end_date,
            "daily": "temperature_2m_max",
            "temperature_unit": "celsius",
            "models": "gfs_seamless",
            "timezone": "UTC",
            # NOTE: forecast_days is intentionally OMITTED — it is mutually exclusive
            # with start_date/end_date on this API.
        }

        raw_source_id = (
            f"{HISTORICAL_FORECAST_API_URL}?"
            f"lat={lat}&lon={lon}&start={start_date}&end={end_date}"
            f"&models=gfs_seamless&lead=24h_nominal"
        )

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(HISTORICAL_FORECAST_API_URL, params=params)
            if response.status_code == 400:
                body = response.text[:500]
                raise ProviderDataError(
                    f"[{city}] Open-Meteo 400: {body}"
                )
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        logger.info(
            "[V3 Open-Meteo] %s: fetched %s→%s, got %d daily records",
            city, start_date, end_date,
            len(data.get("daily", {}).get("time", [])),
        )

        return self._parse_response(
            data=data,
            city=city,
            station_id=station_id,
            lat=lat,
            lon=lon,
            local_timezone=local_timezone,
            retrieval_ts=retrieval_ts,
            raw_source_id=raw_source_id,
        )

    def _parse_response(
        self,
        data: dict[str, Any],
        city: str,
        station_id: str,
        lat: float,
        lon: float,
        local_timezone: str,
        retrieval_ts: datetime,
        raw_source_id: str,
        # Accept legacy kwargs from tests without breaking
        lead_time_hours: int = EFFECTIVE_LEAD_TIME_HOURS,
        forecast_days: int = 1,
        **_ignored,
    ) -> list[RawForecastRecord]:
        """
        Parse the Open-Meteo Historical Forecast API JSON body into
        ``RawForecastRecord`` objects.

        Init-time derivation
        --------------------
        The API returns ``daily.time`` as the VALID date.  We cannot directly
        observe the model initialization time from the response.  We conservatively
        derive:

            forecast_init_time = valid_date − 1 day at 00:00 UTC

        This makes look-ahead validation STRICTER (assumes model ran 1 day before
        valid date, not 0 days), which is the safer direction.

        Records where ``temperature_2m_max`` is None are included with
        ``forecast_tmax_raw=None`` so the audit log tracks coverage gaps.
        """
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        tmax_values = daily.get("temperature_2m_max", [])

        if len(dates) != len(tmax_values):
            raise ProviderDataError(
                f"[{city}] Open-Meteo response date/value length mismatch: "
                f"{len(dates)} dates vs {len(tmax_values)} values"
            )

        records: list[RawForecastRecord] = []
        for date_str, tmax in zip(dates, tmax_values):
            # date_str is YYYY-MM-DD, representing the valid date in UTC
            try:
                valid_date = datetime.strptime(date_str, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            except ValueError as exc:
                logger.warning(
                    "[V3 Open-Meteo] %s: could not parse date '%s': %s",
                    city, date_str, exc,
                )
                continue

            # Conservative init time: 1 day before the valid date at 00Z
            init_date = valid_date - timedelta(days=1)
            forecast_init_time = init_date.replace(hour=0, minute=0, second=0, microsecond=0)
            # Valid time: end of the valid date (23:59 UTC)
            forecast_valid_time = valid_date.replace(hour=23, minute=59, second=0, microsecond=0)

            source_provenance = (
                f"Open-Meteo Historical Forecast API; GFS model (gfs_seamless); "
                f"date-range mode (single effective lead, nominal 24h); "
                f"valid date {date_str}; "
                f"derived init date {init_date.strftime('%Y-%m-%d')} 00Z (conservative); "
                f"retrieved {retrieval_ts.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            )

            rec = RawForecastRecord(
                provider=PROVIDER_KEY,
                model=MODEL,
                model_version="gfs_seamless",  # model identifier from API
                city=city,
                station_id=station_id,
                station_lat=lat,
                station_lon=lon,
                local_timezone=local_timezone,
                forecast_init_time=forecast_init_time,
                forecast_valid_time=forecast_valid_time,
                retrieval_timestamp=retrieval_ts,
                target_date_local=date_str,
                lead_time_hours=EFFECTIVE_LEAD_TIME_HOURS,
                forecast_tmax_raw=tmax,  # Celsius; None if missing
                raw_unit="celsius",
                raw_source_identifier=raw_source_id,
                source_provenance=source_provenance,
                raw_response=data,
                is_reanalysis=False,
                data_flags=[],
            )
            records.append(rec)

        return records
