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

The Historical Forecast API stores and exposes actual past model runs with
real forecast initialization times and lead times.  The ``forecast_days``
parameter controls the lead time offset: requesting ``forecast_days=3`` returns
what the model forecast 3 days ahead of the initialization date.

Verification of init time
--------------------------
The API response includes a ``utc_offset_seconds`` field but does NOT directly
include a ``forecast_init_time`` field in the standard JSON body.  We derive
``forecast_init_time`` from the daily time-index entry: each date in the
``daily.time`` array corresponds to the VALID date, and the init time is
``valid_date - forecast_days`` (i.e. the day the model was run).

This derivation is documented here and reproduced in the look-ahead validator.
Records where the derived init time cannot be verified are rejected with
``MISSING_INIT_TIME``.

Records sourced from reanalysis (ERA5) would have ``is_reanalysis=True`` and
are rejected by the look-ahead validator.  This provider never returns
reanalysis data — it explicitly requests the GFS model.

Supported lead times
---------------------
Valid ``forecast_days`` values via this API: 1 through 7.
Corresponding ``lead_time_hours``: 24, 48, 72, 96, 120, 144, 168.
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

# The Open-Meteo Historical Forecast API supports forecast_days 1-7 for GFS.
VALID_LEAD_TIMES_HOURS = {24, 48, 72, 96, 120, 144, 168}
# Maps lead_time_hours → forecast_days parameter value
LEAD_TO_FORECAST_DAYS: dict[int, int] = {h: h // 24 for h in VALID_LEAD_TIMES_HOURS}

TRANSFORMATION_VERSION = "v1.0"
PROVIDER_KEY = "open-meteo-forecast-history"
MODEL = "GFS"


class OpenMeteoForecastHistoryProvider(ForecastHistoryProvider):
    """
    Retrieves archived GFS daily max-temperature forecasts from the
    Open-Meteo Historical Forecast API.
    """

    PROVIDER_KEY = PROVIDER_KEY
    MODEL = MODEL

    def __init__(self, timeout_seconds: float = 30.0):
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
        Fetch one batch of API calls per lead-time value.

        The Open-Meteo Historical Forecast API requires a separate request per
        ``forecast_days`` value.  This method issues all requests concurrently
        using httpx and returns a flat list of ``RawForecastRecord`` objects.

        Parameters
        ----------
        lead_time_hours_list
            Lead times in hours.  Must be multiples of 24 between 24 and 168.
            Unsupported values are logged and skipped (not raised as errors) so
            that one bad lead time does not block the rest.
        """
        import asyncio

        valid_leads = [
            h for h in lead_time_hours_list if h in VALID_LEAD_TIMES_HOURS
        ]
        skipped_leads = set(lead_time_hours_list) - VALID_LEAD_TIMES_HOURS
        if skipped_leads:
            logger.warning(
                "[V3 Open-Meteo] %s: skipping unsupported lead times %s "
                "(supported: 24-168h in 24h increments)",
                city, sorted(skipped_leads)
            )

        if not valid_leads:
            raise ProviderDataError(
                f"No valid lead times for {city}: "
                f"all requested values {lead_time_hours_list} are unsupported."
            )

        tasks = [
            self._fetch_one_lead(
                city, station_id, lat, lon, local_timezone,
                start_date, end_date, lead_h
            )
            for lead_h in valid_leads
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_records: list[RawForecastRecord] = []
        for lead_h, result in zip(valid_leads, results):
            if isinstance(result, Exception):
                logger.error(
                    "[V3 Open-Meteo] %s lead=%dh: fetch failed: %s",
                    city, lead_h, result
                )
            else:
                all_records.extend(result)

        return all_records

    async def _fetch_one_lead(
        self,
        city: str,
        station_id: str,
        lat: float,
        lon: float,
        local_timezone: str,
        start_date: str,
        end_date: str,
        lead_time_hours: int,
    ) -> list[RawForecastRecord]:
        """
        Issue one API request for a single forecast_days value and parse the response.
        """
        forecast_days = LEAD_TO_FORECAST_DAYS[lead_time_hours]
        retrieval_ts = datetime.now(timezone.utc)

        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start_date,
            "end_date": end_date,
            "daily": "temperature_2m_max",
            "temperature_unit": "celsius",
            "forecast_days": forecast_days,
            "models": "gfs_seamless",
            "timezone": "UTC",
        }

        raw_source_id = (
            f"{HISTORICAL_FORECAST_API_URL}?"
            f"lat={lat}&lon={lon}&start={start_date}&end={end_date}"
            f"&forecast_days={forecast_days}&models=gfs_seamless"
        )

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(HISTORICAL_FORECAST_API_URL, params=params)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        return self._parse_response(
            data=data,
            city=city,
            station_id=station_id,
            lat=lat,
            lon=lon,
            local_timezone=local_timezone,
            lead_time_hours=lead_time_hours,
            forecast_days=forecast_days,
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
        lead_time_hours: int,
        forecast_days: int,
        retrieval_ts: datetime,
        raw_source_id: str,
    ) -> list[RawForecastRecord]:
        """
        Parse the Open-Meteo Historical Forecast API JSON body into
        ``RawForecastRecord`` objects.

        Init-time derivation
        --------------------
        The API returns ``daily.time`` entries as the VALID date of the
        forecast (i.e. the day the forecast is describing).  The model was
        initialized ``forecast_days`` days before this valid date.

        So:
            forecast_init_time (UTC midnight) = valid_date - timedelta(days=forecast_days)
            forecast_valid_time (UTC midnight) = valid_date

        This is the best approximation available from this API.  GFS runs
        are initialized at 00Z, 06Z, 12Z, and 18Z; we conservatively use 00Z
        (midnight UTC) which means our init_time estimate is the earliest
        possible, making look-ahead validation STRICTER not looser.

        Records where ``temperature_2m_max`` is None (observation gap) are
        included with ``forecast_tmax_raw=None`` so the ingestion log can
        track missing dates.
        """
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        tmax_values = daily.get("temperature_2m_max", [])
        model_reported = data.get("hourly_units", {}).get("temperature_2m", "°C")

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
                    city, date_str, exc
                )
                continue

            # Derive init time: valid_date - lead_time
            init_date = valid_date - timedelta(days=forecast_days)
            # Use 00:00 UTC — earliest possible init time for this day's GFS run.
            forecast_init_time = init_date.replace(hour=0, minute=0, second=0, microsecond=0)
            # Valid time is end-of-day for daily max: we use 23:59 UTC
            forecast_valid_time = valid_date.replace(hour=23, minute=59, second=0, microsecond=0)

            source_provenance = (
                f"Open-Meteo Historical Forecast API; GFS model (gfs_seamless); "
                f"{forecast_days}-day lead time; "
                f"valid date {date_str}; "
                f"init date {init_date.strftime('%Y-%m-%d')} 00Z (derived); "
                f"retrieved {retrieval_ts.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            )

            rec = RawForecastRecord(
                provider=PROVIDER_KEY,
                model=MODEL,
                model_version=data.get("model"),  # "gfs_seamless" or similar
                city=city,
                station_id=station_id,
                station_lat=lat,
                station_lon=lon,
                local_timezone=local_timezone,
                forecast_init_time=forecast_init_time,
                forecast_valid_time=forecast_valid_time,
                retrieval_timestamp=retrieval_ts,
                target_date_local=date_str,  # same as UTC date for daily max
                lead_time_hours=lead_time_hours,
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
