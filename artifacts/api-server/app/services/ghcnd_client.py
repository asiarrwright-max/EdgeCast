"""
NOAA Climate Data Online (CDO) API client — GHCND daily observations.

Fetches official TMAX/TMIN readings from the Global Historical Climatology
Network — Daily (GHCND) dataset.  These are the same readings from which NWS
Daily Climate Reports are produced, and therefore match the values Kalshi uses
for temperature contract settlement.

Authentication
--------------
A free API token is required.  Register at:
  https://www.ncdc.noaa.gov/cdo-web/token
  (instant email delivery, no payment required)

Set the token in the NOAA_CDO_TOKEN environment variable.  If the token is
absent or the request fails, all functions return ``None`` and the caller
should fall back to ERA5 reanalysis.

Data availability
-----------------
GHCND daily data is typically available 1–2 days after the observation date.
Requesting data for a date fewer than 2 days in the past may return no result.

Unit note
---------
The CDO API returns TMAX/TMIN in degrees Fahrenheit when
``units=standard`` is specified.  Values are whole degrees (no decimal).
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_CDO_BASE = "https://www.ncdc.noaa.gov/cdo-web/api/v2/data"
_TIMEOUT = 20  # seconds


def _build_station_param(ghcnd_station_id: str) -> str:
    """Prefix bare station IDs with 'GHCND:' as required by the CDO API."""
    if ghcnd_station_id.startswith("GHCND:"):
        return ghcnd_station_id
    return f"GHCND:{ghcnd_station_id}"


async def fetch_ghcnd_daily(
    ghcnd_station_id: str,
    date_str: str,          # YYYY-MM-DD
    noaa_token: str,
) -> dict[str, float | None]:
    """
    Fetch TMAX and TMIN for *ghcnd_station_id* on *date_str*.

    Returns ``{"high": float|None, "low": float|None}``.
    Returns ``{"high": None, "low": None}`` on any error.

    Parameters
    ----------
    ghcnd_station_id:
        NOAA GHCND station identifier, e.g. ``"USW00094728"``.
        May optionally be prefixed with ``"GHCND:"``.
    date_str:
        Target date in ``YYYY-MM-DD`` format.
    noaa_token:
        CDO API token.
    """
    station_param = _build_station_param(ghcnd_station_id)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                _CDO_BASE,
                headers={"token": noaa_token},
                params={
                    "datasetid": "GHCND",
                    "stationid": station_param,
                    "startdate": date_str,
                    "enddate": date_str,
                    "datatypeid": "TMAX,TMIN",
                    "units": "standard",   # returns °F
                    "limit": 10,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 400:
            # Often means the date is too recent (data not yet published)
            logger.debug(
                "GHCND CDO 400 for station %s on %s — data may not be published yet.",
                ghcnd_station_id,
                date_str,
            )
        else:
            logger.warning(
                "GHCND CDO HTTP %d for station %s on %s: %s",
                exc.response.status_code,
                ghcnd_station_id,
                date_str,
                exc,
            )
        return {"high": None, "low": None}
    except Exception as exc:
        logger.warning(
            "GHCND CDO fetch failed for station %s on %s: %s",
            ghcnd_station_id,
            date_str,
            exc,
        )
        return {"high": None, "low": None}

    results = data.get("results", [])
    if not results:
        logger.debug(
            "GHCND CDO returned no results for station %s on %s (data not yet published or station gap).",
            ghcnd_station_id,
            date_str,
        )
        return {"high": None, "low": None}

    high: float | None = None
    low: float | None = None

    for item in results:
        dtype = item.get("datatype", "")
        raw_value = item.get("value")
        if raw_value is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue

        if dtype == "TMAX":
            high = value
        elif dtype == "TMIN":
            low = value

    if high is None and low is None:
        logger.debug(
            "GHCND CDO: no TMAX or TMIN found for station %s on %s.",
            ghcnd_station_id,
            date_str,
        )

    return {"high": high, "low": low}
