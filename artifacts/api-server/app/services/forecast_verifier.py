"""
Forecast Verifier — Phase v2 (GHCND edition).

For every settled paper trade, fetches the actual observed temperature and
stores it alongside the original forecast.  The error is then aggregated into
ForecastErrorStats for use by the v2 probability engine.

Observation source priority
----------------------------
1. **NOAA GHCND via CDO API** (preferred)
   The same official NWS Daily Climate Report values that Kalshi uses for
   settlement.  Requires NOAA_CDO_TOKEN env var (free registration).
   Used when:
     - The city has an entry in settlement_stations.SETTLEMENT_STATIONS, AND
     - A NOAA_CDO_TOKEN is configured.
   ``source_label`` is set to:
     - ``'ghcnd_observation'``            — station is verified (confirmed from contract PDF)
     - ``'ghcnd_observation_unverified'`` — station is probable but not yet verified

2. **Open-Meteo ERA5 reanalysis** (fallback)
   Used when NOAA token is absent, CDO fetch fails, or the city has no
   station entry.  ERA5 is a retrospective reanalysis, NOT a station reading;
   it can differ from the NWS Daily Climate Report by 1–4°F on individual days.
   ``source_label`` is set to ``'era5_reanalysis'``.

Existing rows
-------------
ForecastVerification rows written before this version have
``source_label='open_meteo_historical'``.  They are retained as-is.  The
verifier skips rows that already have ``actual_value`` populated (idempotent).

Both functions are idempotent and safe to re-run.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ForecastVerification, ForecastErrorStats, PaperTrade, WeatherLocation
from app.services.settlement_stations import get_station
from app.services.ghcnd_client import fetch_ghcnd_daily

logger = logging.getLogger(__name__)

_MIN_SAMPLE_FOR_GLOBAL = 3   # minimum distinct cities before writing a global row
_ERA5_API = "https://archive-api.open-meteo.com/v1/archive"

# source_label values written by this module
SRC_GHCND_VERIFIED   = "ghcnd_observation"             # confirmed settlement station
SRC_GHCND_UNVERIFIED = "ghcnd_observation_unverified"  # probable station, not confirmed
SRC_ERA5             = "era5_reanalysis"               # Open-Meteo ERA5 fallback
SRC_LEGACY           = "open_meteo_historical"         # rows from before this version


def _season(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "fall"


def _lead_bucket(days: int | None) -> str:
    if days is None:
        return ">7d"
    if days <= 1:
        return "0-1d"
    if days <= 3:
        return "2-3d"
    if days <= 7:
        return "4-7d"
    return ">7d"


async def _fetch_era5_temps(
    lat: float,
    lon: float,
    date_str: str,
) -> dict[str, float | None]:
    """
    Fallback: fetch daily high/low from the Open-Meteo ERA5 archive.

    Returns {"high": float|None, "low": float|None}.
    This is ERA5 reanalysis data, NOT an official station reading.
    """
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                _ERA5_API,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "start_date": date_str,
                    "end_date": date_str,
                    "daily": "temperature_2m_max,temperature_2m_min",
                    "timezone": "auto",
                    "temperature_unit": "fahrenheit",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        daily = data.get("daily", {})
        highs = daily.get("temperature_2m_max", [])
        lows = daily.get("temperature_2m_min", [])
        high = float(highs[0]) if highs and highs[0] is not None else None
        low = float(lows[0]) if lows and lows[0] is not None else None
        return {"high": high, "low": low}
    except Exception as exc:
        logger.warning(
            "ERA5 archive fetch failed for %s (%s, %s): %s", date_str, lat, lon, exc
        )
        return {"high": None, "low": None}


async def _fetch_observation(
    city: str,
    lat: float,
    lon: float,
    date_str: str,
    noaa_token: str,
) -> tuple[dict[str, float | None], str, str | None]:
    """
    Fetch a temperature observation for *city* on *date_str*.

    Resolution order:
      1. GHCND CDO API (if station entry exists and NOAA token is configured)
      2. ERA5 reanalysis fallback

    Returns
    -------
    (temps, source_label, ghcnd_station_id)
      - temps            ``{"high": float|None, "low": float|None}``
      - source_label     one of the SRC_* constants
      - ghcnd_station_id GHCND station ID used, or None if ERA5 was used
    """
    station = get_station(city)

    if station is not None and noaa_token:
        temps = await fetch_ghcnd_daily(station.ghcnd_station_id, date_str, noaa_token)
        if temps["high"] is not None or temps["low"] is not None:
            src = SRC_GHCND_VERIFIED if station.verified else SRC_GHCND_UNVERIFIED
            if not station.verified:
                logger.debug(
                    "GHCND data fetched for unverified station %s (%s): %s. "
                    "Assumption: %s",
                    station.ghcnd_station_id,
                    city,
                    date_str,
                    station.notes or "see settlement_stations.py for details",
                )
            return temps, src, station.ghcnd_station_id

        # GHCND returned nothing (data not yet published, station gap, etc.)
        # Fall through to ERA5.
        logger.info(
            "GHCND CDO returned no data for %s station %s on %s — "
            "falling back to ERA5 reanalysis.",
            city,
            station.ghcnd_station_id,
            date_str,
        )

    elif station is not None and not noaa_token:
        logger.debug(
            "NOAA_CDO_TOKEN not set — using ERA5 fallback for %s on %s. "
            "Set NOAA_CDO_TOKEN to use official GHCND station %s.",
            city,
            date_str,
            station.ghcnd_station_id,
        )
    else:
        logger.debug(
            "No settlement station entry for city '%s' — using ERA5 fallback for %s.",
            city,
            date_str,
        )

    # ERA5 fallback uses city-centre coordinates from WeatherLocation table
    temps = await _fetch_era5_temps(lat, lon, date_str)
    return temps, SRC_ERA5, None


async def fetch_and_store_verifications(session: AsyncSession) -> dict[str, int]:
    """
    For every settled paper trade, ensure a ForecastVerification row exists
    and is populated with the actual observed temperature.

    Idempotent: skips rows that already have actual_value populated.

    Returns: {"created": int, "updated": int, "skipped": int, "errors": int}
    """
    stats = {"created": 0, "updated": 0, "skipped": 0, "errors": 0}
    settings = get_settings()
    noaa_token = settings.noaa_cdo_token.strip()

    if not noaa_token:
        logger.warning(
            "NOAA_CDO_TOKEN is not set.  Verification will use ERA5 reanalysis "
            "for all cities.  ERA5 values differ from official NWS station readings "
            "by 1–4°F on individual days.  Register for a free token at "
            "https://www.ncdc.noaa.gov/cdo-web/token and set NOAA_CDO_TOKEN."
        )

    # All settled trades (v1 and v2)
    q = await session.execute(
        select(PaperTrade).where(
            PaperTrade.kalshi_result.in_(["yes", "no"]),
            PaperTrade.target_settlement_date.is_not(None),
            PaperTrade.city.is_not(None),
            PaperTrade.weather_variable.is_not(None),
        )
    )
    settled_trades = q.scalars().all()

    if not settled_trades:
        logger.info("Forecast verifier: no settled trades to process.")
        return stats

    # Build city → (lat, lon) from WeatherLocation (city-centre coords, ERA5 fallback)
    locs_q = await session.execute(select(WeatherLocation))
    loc_map: dict[str, tuple[float, float]] = {
        loc.city: (loc.latitude, loc.longitude)
        for loc in locs_q.scalars().all()
    }

    # Collect unique (city, weather_variable, target_date) tuples
    from app.models import PredictionSnapshot
    seen: dict[tuple[str, str, str], dict] = {}
    for trade in settled_trades:
        key = (trade.city, trade.weather_variable, trade.target_settlement_date)
        if key not in seen:
            seen[key] = {
                "snapshot_id": trade.snapshot_id,
                "forecast_value": None,
                "lead_time_days": trade.lead_time_days,
            }

    # Load snapshot forecast values
    snap_ids = [v["snapshot_id"] for v in seen.values() if v["snapshot_id"] is not None]
    if snap_ids:
        snaps_q = await session.execute(
            select(PredictionSnapshot).where(PredictionSnapshot.id.in_(snap_ids))
        )
        snap_map = {s.id: s for s in snaps_q.scalars().all()}
        for key, meta in seen.items():
            sid = meta["snapshot_id"]
            if sid and sid in snap_map:
                snap = snap_map[sid]
                meta["forecast_value"] = snap.forecast_value

    # Check existing verification rows
    existing_q = await session.execute(
        select(
            ForecastVerification.city,
            ForecastVerification.weather_variable,
            ForecastVerification.target_date,
            ForecastVerification.actual_value,
        )
    )
    existing: dict[tuple[str, str, str], float | None] = {}
    for row in existing_q:
        existing[(row.city, row.weather_variable, row.target_date)] = row.actual_value

    # Process each unique (city, variable, date)
    for (city, var, date_str), meta in seen.items():
        loc = loc_map.get(city)
        if loc is None:
            logger.warning("No WeatherLocation entry for city '%s' — skipping verification.", city)
            stats["skipped"] += 1
            continue

        already_exists = (city, var, date_str) in existing
        already_verified = already_exists and existing[(city, var, date_str)] is not None

        if already_verified:
            stats["skipped"] += 1
            continue

        try:
            temps, source_label, ghcnd_station_id = await _fetch_observation(
                city, loc[0], loc[1], date_str, noaa_token
            )
            actual = temps.get(var)  # "high" or "low"

            # Parse date for month/season
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                month = dt.month
                season = _season(month)
            except ValueError:
                month = None
                season = None

            if already_exists:
                # Update existing stub row
                upd_q = await session.execute(
                    select(ForecastVerification).where(
                        ForecastVerification.city == city,
                        ForecastVerification.weather_variable == var,
                        ForecastVerification.target_date == date_str,
                    )
                )
                row = upd_q.scalar_one_or_none()
                if row and actual is not None:
                    row.actual_value = actual
                    row.forecast_error = (
                        round(actual - row.forecast_value, 4)
                        if row.forecast_value is not None
                        else None
                    )
                    row.source_label = source_label
                    row.ghcnd_station_id = ghcnd_station_id
                    stats["updated"] += 1
            else:
                # Create new row
                fv = meta.get("forecast_value")
                ferr = round(actual - fv, 4) if (actual is not None and fv is not None) else None
                row = ForecastVerification(
                    snapshot_id=meta.get("snapshot_id"),
                    city=city,
                    weather_variable=var,
                    target_date=date_str,
                    target_hour=None,
                    forecast_value=fv,
                    lead_time_days=meta.get("lead_time_days"),
                    actual_value=actual,
                    forecast_error=ferr,
                    source_label=source_label if actual is not None else None,
                    ghcnd_station_id=ghcnd_station_id,
                    month=month,
                    season=season,
                )
                session.add(row)
                stats["created"] += 1

        except Exception as exc:
            logger.warning(
                "Verification error for %s/%s/%s: %s", city, var, date_str, exc
            )
            stats["errors"] += 1

    await session.commit()
    logger.info(
        "Forecast verifier: %d created, %d updated, %d skipped, %d errors.",
        stats["created"], stats["updated"], stats["skipped"], stats["errors"],
    )
    return stats


def _compute_stats(errors: list[float]) -> dict[str, float]:
    n = len(errors)
    mean_e = statistics.mean(errors)
    median_e = statistics.median(errors)
    mae = statistics.mean(abs(e) for e in errors)
    std = statistics.stdev(errors) if n >= 2 else 0.0
    return {"mean": mean_e, "median": median_e, "mae": mae, "std": std}


async def recompute_error_stats(session: AsyncSession) -> dict[str, int]:
    """
    Aggregate ForecastVerification rows into ForecastErrorStats.

    Groups:
      - (city, variable, lead_bucket, month=None)  — city-level all-season
      - (city="__global__", variable, lead_bucket, month=None) — global fallback

    All source_label values are included (ghcnd_observation, era5_reanalysis,
    and the legacy 'open_meteo_historical').  Callers can inspect the
    source_label distribution via the /audit endpoints.

    Upserts via delete + re-insert per group.
    Returns: {"groups_computed": int}
    """
    stats = {"groups_computed": 0}

    q = await session.execute(
        select(ForecastVerification).where(
            ForecastVerification.actual_value.is_not(None),
            ForecastVerification.forecast_value.is_not(None),
        )
    )
    all_verifs = q.scalars().all()

    if not all_verifs:
        logger.info("recompute_error_stats: no verified rows yet.")
        return stats

    from collections import defaultdict
    groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    global_groups: dict[tuple[str, str], list[float]] = defaultdict(list)

    for v in all_verifs:
        if v.forecast_error is None:
            continue
        lb = _lead_bucket(v.lead_time_days)
        groups[(v.city, v.weather_variable, lb)].append(v.forecast_error)
        global_groups[(v.weather_variable, lb)].append(v.forecast_error)

    now = datetime.now(timezone.utc)

    # Delete and re-insert all stats
    existing_q = await session.execute(select(ForecastErrorStats))
    for row in existing_q.scalars().all():
        await session.delete(row)
    await session.flush()

    # City-level rows
    for (city, var, lb), errors in groups.items():
        s = _compute_stats(errors)
        row = ForecastErrorStats(
            city=city,
            weather_variable=var,
            lead_time_bucket=lb,
            month=None,
            mean_error=round(s["mean"], 4),
            median_error=round(s["median"], 4),
            mae=round(s["mae"], 4),
            std_dev=round(s["std"], 4),
            sample_size=len(errors),
            fallback_level="city",
            last_computed_at=now,
        )
        session.add(row)
        stats["groups_computed"] += 1

    # Global fallback rows
    city_counts: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (city, var, lb) in groups:
        city_counts[(var, lb)].add(city)

    for (var, lb), errors in global_groups.items():
        n_cities = len(city_counts.get((var, lb), set()))
        if n_cities < _MIN_SAMPLE_FOR_GLOBAL:
            continue
        s = _compute_stats(errors)
        row = ForecastErrorStats(
            city="__global__",
            weather_variable=var,
            lead_time_bucket=lb,
            month=None,
            mean_error=round(s["mean"], 4),
            median_error=round(s["median"], 4),
            mae=round(s["mae"], 4),
            std_dev=round(s["std"], 4),
            sample_size=len(errors),
            fallback_level="global",
            last_computed_at=now,
        )
        session.add(row)
        stats["groups_computed"] += 1

    await session.commit()
    logger.info("recompute_error_stats: %d groups written.", stats["groups_computed"])
    return stats
