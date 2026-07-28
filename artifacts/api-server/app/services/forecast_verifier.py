"""
Forecast Verifier — Phase v2.

Fetches actual observed temperatures from the Open-Meteo historical API for
cities where paper trades have settled, then computes per-city forecast error
statistics for use by the v2 probability engine.

Data limitations
----------------
- Open-Meteo /archive endpoint returns verified ERA5/ERA5-Land reanalysis data,
  NOT the original NWP model forecast for a past date.  This means we are
  measuring (reanalysis − NWP-forecast) error, not (observation − NWP-forecast).
  The reanalysis is close to observations but is not identical.

- City is the finest resolution available.  No per-station obs data is used.

- Both functions are idempotent and safe to re-run.
"""
from __future__ import annotations

import logging
import math
import statistics
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ForecastVerification, ForecastErrorStats, PaperTrade, WeatherLocation

logger = logging.getLogger(__name__)

_MIN_SAMPLE_FOR_GLOBAL = 3   # at least 3 cities before writing a global row
_HISTORICAL_API = "https://archive-api.open-meteo.com/v1/archive"


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


async def _fetch_historical_temps(
    lat: float,
    lon: float,
    date_str: str,  # YYYY-MM-DD
) -> dict[str, float | None]:
    """
    Fetch daily high and low for a single date from the Open-Meteo archive.
    Returns {"high": float|None, "low": float|None}.
    """
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                _HISTORICAL_API,
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
        logger.warning("Open-Meteo archive fetch failed for %s (%s, %s): %s", date_str, lat, lon, exc)
        return {"high": None, "low": None}


async def fetch_and_store_verifications(session: AsyncSession) -> dict[str, int]:
    """
    For every settled paper trade, ensure a ForecastVerification row exists
    and is populated with the actual observed temperature.

    Idempotent: skips rows that already have actual_value populated.

    Returns: {"created": int, "updated": int, "skipped": int, "errors": int}
    """
    stats = {"created": 0, "updated": 0, "skipped": 0, "errors": 0}

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

    # Build city → (lat, lon) mapping
    locs_q = await session.execute(select(WeatherLocation))
    loc_map: dict[str, tuple[float, float]] = {
        loc.city: (loc.latitude, loc.longitude)
        for loc in locs_q.scalars().all()
    }

    # Collect unique (city, weather_variable, target_date) tuples
    # Map to the snapshot_id + forecast_value from one representative trade
    seen: dict[tuple[str, str, str], dict] = {}
    for trade in settled_trades:
        key = (trade.city, trade.weather_variable, trade.target_settlement_date)
        if key not in seen:
            seen[key] = {
                "snapshot_id": trade.snapshot_id,
                "forecast_value": None,  # will be sourced from snapshot
                "lead_time_days": trade.lead_time_days,
            }

    # Load snapshot forecast values for these keys
    from app.models import PredictionSnapshot
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
                city, var, _date = key
                if var == "high":
                    meta["forecast_value"] = snap.forecast_value
                else:
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
            stats["skipped"] += 1
            continue

        already_exists = (city, var, date_str) in existing
        already_verified = already_exists and existing[(city, var, date_str)] is not None

        if already_verified:
            stats["skipped"] += 1
            continue

        try:
            temps = await _fetch_historical_temps(loc[0], loc[1], date_str)
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
                    row.source_label = "open_meteo_historical"
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
                    source_label="open_meteo_historical" if actual is not None else None,
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

    Upserts into ForecastErrorStats (delete + re-insert per group for simplicity).

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

    # Group by (city, variable, lead_bucket)
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

    # Delete and re-insert all non-global stats
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

    # Global fallback rows (across all cities, same variable + lead_bucket)
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
