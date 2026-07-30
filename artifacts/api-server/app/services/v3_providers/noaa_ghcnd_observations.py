"""
NOAA GHCN-Daily Observation Client — V3
========================================
Fetches official daily TMAX observations from the NOAA GHCND dataset for the
exact verified settlement station.

Why this source?
-----------------
Kalshi temperature markets settle on NWS Daily Climate Reports, which are
derived from official ASOS/AWOS station observations — the same data that NOAA
archives in the GHCN-Daily dataset.  Using GHCND for V3 observations ensures
we are training on the same truth signal that Kalshi uses to settle contracts.

We reuse the GHCND station ID from ``SettlementStation.ghcnd_station_id``,
which has been verified (for active cities) to match the Kalshi settlement
station via the ``rules_secondary`` field of the Kalshi market API.

This module is observation-only.  It has no knowledge of forecasts or paper
trading — it only fetches TMAX readings and returns them.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

GHCND_API_URL = "https://www.ncei.noaa.gov/access/services/data/v1"

# The NCEI access services API (v1) returns TMAX in full degrees — NOT tenths.
# units=metric  → full degrees Celsius (e.g. 31.7°C)
# units=standard → full degrees Fahrenheit (e.g. 89.0°F)
#
# IMPORTANT: This is different from the older CDO v1 API (ncdc.noaa.gov) and the
# raw GHCND .dly file format, which store values in tenths of degrees Celsius.
# Do NOT apply a divide-by-10 scale factor to NCEI access API responses.
#
# We request units=metric and convert C→F ourselves for full auditability.
TMAX_SCALE_FACTOR = 1.0  # kept for test compatibility; MUST be 1.0 for NCEI access API


class NoaaGhcndObservationError(Exception):
    """Raised when a NOAA GHCND fetch fails or returns unexpected data."""


async def fetch_tmax_observations(
    station_id: str,
    start_date: str,  # YYYY-MM-DD
    end_date: str,    # YYYY-MM-DD
) -> dict[str, float]:
    """
    Fetch official TMAX daily observations for a GHCND station over a date range.

    Returns
    -------
    dict[str, float]
        Mapping of YYYY-MM-DD → observed daily max temperature in Fahrenheit.
        Missing dates (station gaps, station not operating) are absent from
        the dict; callers should treat them as ``None`` (not as zero).

    Notes
    -----
    - Uses the NOAA CDO v1 API with format=json.
    - Requires NOAA_CDO_TOKEN environment variable (already available in this
      project for existing GHCND settlement verification).
    - TMAX values are returned in tenths-of-degrees Celsius; we convert to °F.
    - The API limits date ranges; very large ranges are split internally.
    """
    settings = get_settings()
    token = getattr(settings, "noaa_cdo_token", None)
    if not token:
        raise NoaaGhcndObservationError(
            "NOAA_CDO_TOKEN is not set.  Cannot fetch GHCND observations."
        )

    all_observations: dict[str, float] = {}

    # NOAA CDO API caps at 1 year per request; split longer ranges
    chunks = _split_date_range(start_date, end_date, max_days=365)

    async with httpx.AsyncClient(timeout=60.0) as client:
        for chunk_start, chunk_end in chunks:
            params = {
                "dataset": "daily-summaries",
                "stations": station_id,
                "dataTypes": "TMAX",
                "startDate": chunk_start,
                "endDate": chunk_end,
                "format": "json",
                "units": "metric",  # returns full degrees Celsius; we convert C→F ourselves
                "includeAttributes": "false",
            }
            headers = {"token": token}

            try:
                resp = await client.get(GHCND_API_URL, params=params, headers=headers)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "[V3 NOAA] HTTP %s for station %s %s–%s: %s",
                    exc.response.status_code, station_id, chunk_start, chunk_end, exc
                )
                # Don't abort the whole ingestion; return what we have so far
                continue
            except httpx.RequestError as exc:
                logger.error(
                    "[V3 NOAA] Request error for station %s: %s", station_id, exc
                )
                continue

            try:
                data: list[dict[str, Any]] = resp.json()
            except Exception:
                logger.error(
                    "[V3 NOAA] Could not parse JSON for station %s %s–%s",
                    station_id, chunk_start, chunk_end
                )
                continue

            for row in data:
                obs_date = row.get("DATE", "")[:10]  # YYYY-MM-DD
                raw_tmax = row.get("TMAX")
                if obs_date and raw_tmax is not None:
                    try:
                        # NCEI access API returns full degrees Celsius (units=metric)
                        # TMAX_SCALE_FACTOR is 1.0 — no division needed.
                        tmax_celsius = float(raw_tmax) / TMAX_SCALE_FACTOR
                        tmax_fahrenheit = celsius_to_fahrenheit(tmax_celsius)
                        all_observations[obs_date] = tmax_fahrenheit
                    except (ValueError, TypeError) as exc:
                        logger.warning(
                            "[V3 NOAA] Could not parse TMAX value %r for %s on %s: %s",
                            raw_tmax, station_id, obs_date, exc
                        )

    return all_observations


def celsius_to_fahrenheit(celsius: float) -> float:
    """
    Convert Celsius to Fahrenheit.
    Formula: F = C × (9/5) + 32
    Unit conversions are documented explicitly here and stored in V3HistoricalRecord.
    """
    return celsius * 9.0 / 5.0 + 32.0


def _split_date_range(
    start_date: str,
    end_date: str,
    max_days: int = 365,
) -> list[tuple[str, str]]:
    """
    Split a date range into chunks of at most ``max_days`` days.
    Returns list of (chunk_start, chunk_end) YYYY-MM-DD string tuples.
    """
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    chunks: list[tuple[str, str]] = []
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=max_days - 1), end)
        chunks.append((current.isoformat(), chunk_end.isoformat()))
        current = chunk_end + timedelta(days=1)
    return chunks
