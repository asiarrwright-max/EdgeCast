"""
V3 Historical Ingestion Orchestrator
======================================
Coordinates the full pipeline for loading historical forecast-vs-observation
data into ``V3HistoricalRecord``:

  1. For each active, verified city:
     a. Fetch archived model forecasts via the registered provider(s).
     b. Fetch official NOAA GHCND TMAX observations for the exact station.
     c. Run look-ahead validation on every forecast record.
     d. Write the verbatim API response to ``V3RawSourceRecord`` (immutable).
     e. Normalize (unit conversion, lead-time bucketing, season tagging).
     f. Pair the forecast with the observation, compute error metrics.
     g. Insert into ``V3HistoricalRecord`` (unique constraint prevents duplicates).
     h. Log every outcome (accepted, rejected, reason) to ``V3IngestionLog``.

  2. Never touch any V1/V2/V2.1 table.

Concurrency
-----------
Cities are processed concurrently via ``asyncio.gather`` with a semaphore
(``MAX_CONCURRENT_CITIES``) to avoid overwhelming the provider APIs.

Duplicate prevention
---------------------
The unique constraint on ``V3HistoricalRecord`` covers:
    (city, station_id, target_date, forecast_source, forecast_model,
     lead_time_hours, preload_version)
On conflict, the row is SKIPPED (not updated).  This makes ingestion runs
idempotent: re-running for the same date range adds only new records.

Feature flag gate
-----------------
``run_ingestion`` checks ``v3.ingestion_enabled`` before doing anything.
Call this function only from the admin API endpoint — not from the scheduler.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from math import isfinite

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting
from app.models_v3 import (
    CURRENT_PRELOAD_VERSION,
    TRANSFORMATION_VERSION,
    V3HistoricalRecord,
    V3IngestionLog,
    V3RawSourceRecord,
)
from app.services.city_availability import get_active_cities
from app.services.settlement_stations import SETTLEMENT_STATIONS
from app.services.v3_lookahead import RejectionReason, validate_record
from app.services.v3_providers.base import RawForecastRecord
from app.services.v3_providers.noaa_ghcnd_observations import (
    NoaaGhcndObservationError,
    fetch_tmax_observations,
)
from app.services.v3_providers.registry import get_provider_class

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_CONCURRENT_CITIES = 3
# Keep concurrency low to respect API rate limits.

DEFAULT_LEAD_TIMES_HOURS = [24, 48, 72, 96, 120, 144, 168]
# 1-day through 7-day lead times.

DEFAULT_PROVIDER_KEY = "open-meteo-forecast-history"

SEASON_MAP = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring",  4: "spring", 5: "spring",
    6: "summer",  7: "summer", 8: "summer",
    9: "fall",   10: "fall",  11: "fall",
}

LEAD_TIME_BUCKET: dict[int, str] = {
    24: "1d", 48: "2d", 72: "3d",
    96: "4d", 120: "5d", 144: "6d", 168: "7d",
}


# ── Public entry point ────────────────────────────────────────────────────────

async def run_ingestion(
    session: AsyncSession,
    *,
    start_date: str | None = None,   # YYYY-MM-DD; default = 2 years ago
    end_date: str | None = None,     # YYYY-MM-DD; default = yesterday
    provider_key: str = DEFAULT_PROVIDER_KEY,
    lead_times_hours: list[int] | None = None,
    cities: list[str] | None = None,  # None = all active verified cities
) -> dict:
    """
    Run V3 historical ingestion.

    Gated on ``v3.ingestion_enabled`` AppSetting.  Returns a summary dict.
    """
    # ── Feature flag gate ────────────────────────────────────────────────
    flag_row = await session.execute(
        select(AppSetting).where(AppSetting.key == "v3.ingestion_enabled")
    )
    flag = flag_row.scalar_one_or_none()
    if flag is None or flag.value not in ("true", "1", "yes"):
        return {
            "status": "blocked",
            "reason": (
                "v3.ingestion_enabled is false.  "
                "Set it to 'true' in app_settings to allow ingestion."
            ),
        }

    # ── Resolve defaults ─────────────────────────────────────────────────
    today = date.today()
    end = (today - timedelta(days=1)).isoformat() if end_date is None else end_date
    start = (today - timedelta(days=2 * 365)).isoformat() if start_date is None else start_date
    lead_times = lead_times_hours or DEFAULT_LEAD_TIMES_HOURS

    # ── Resolve target cities ────────────────────────────────────────────
    if cities is None:
        active = await get_active_cities(session)
        target_cities = [c for c in active if c in SETTLEMENT_STATIONS]
    else:
        target_cities = [c for c in cities if c in SETTLEMENT_STATIONS]

    if not target_cities:
        return {"status": "no_cities", "reason": "No active verified cities found."}

    run_id = str(uuid.uuid4())
    logger.info(
        "[V3 Ingestion] run_id=%s provider=%s cities=%d range=%s→%s leads=%s",
        run_id, provider_key, len(target_cities), start, end, lead_times,
    )

    # ── Run per-city concurrently ────────────────────────────────────────
    sem = asyncio.Semaphore(MAX_CONCURRENT_CITIES)

    async def process_city(city: str) -> dict:
        async with sem:
            return await _ingest_city(
                session=session,
                run_id=run_id,
                city=city,
                provider_key=provider_key,
                start_date=start,
                end_date=end,
                lead_times_hours=lead_times,
            )

    results = await asyncio.gather(
        *[process_city(c) for c in target_cities],
        return_exceptions=True,
    )

    total_accepted = total_rejected = total_attempted = 0
    city_summaries = []
    for city, result in zip(target_cities, results):
        if isinstance(result, Exception):
            logger.error("[V3 Ingestion] %s failed: %s", city, result)
            city_summaries.append({"city": city, "status": "error", "error": str(result)})
        else:
            total_attempted += result.get("records_attempted", 0)
            total_accepted  += result.get("records_accepted", 0)
            total_rejected  += result.get("records_rejected", 0)
            city_summaries.append(result)

    return {
        "status": "complete",
        "run_id": run_id,
        "provider": provider_key,
        "start_date": start,
        "end_date": end,
        "cities_processed": len(target_cities),
        "records_attempted": total_attempted,
        "records_accepted": total_accepted,
        "records_rejected": total_rejected,
        "city_summaries": city_summaries,
    }


# ── Per-city pipeline ─────────────────────────────────────────────────────────

async def _ingest_city(
    session: AsyncSession,
    run_id: str,
    city: str,
    provider_key: str,
    start_date: str,
    end_date: str,
    lead_times_hours: list[int],
) -> dict:
    station = SETTLEMENT_STATIONS.get(city)
    if station is None:
        return {"city": city, "status": "no_station", "records_attempted": 0,
                "records_accepted": 0, "records_rejected": 0}

    started = datetime.now(timezone.utc)
    log_entry = V3IngestionLog(
        run_id=run_id,
        city=city,
        provider=provider_key,
        model=_get_model_for_provider(provider_key),
        start_date=start_date,
        end_date=end_date,
        lead_times_json=lead_times_hours,
        status="running",
    )
    session.add(log_entry)
    await session.flush()

    rejection_counts: dict[str, int] = defaultdict(int)
    api_errors: list[str] = []
    missing_obs_dates: list[str] = []
    accepted = rejected = 0

    try:
        # 1. Fetch archived forecasts
        provider_cls = get_provider_class(provider_key)
        provider = provider_cls()
        raw_records: list[RawForecastRecord] = await provider.fetch_history(
            city=city,
            station_id=station.ghcnd_station_id,
            lat=station.lat,
            lon=station.lon,
            local_timezone=station.timezone,
            start_date=start_date,
            end_date=end_date,
            lead_time_hours_list=lead_times_hours,
        )

        # 2. Fetch NOAA observations for the date range
        try:
            obs_map: dict[str, float] = await fetch_tmax_observations(
                station_id=station.ghcnd_station_id,
                start_date=start_date,
                end_date=end_date,
            )
        except NoaaGhcndObservationError as exc:
            api_errors.append(f"NOAA GHCND error: {exc}")
            obs_map = {}

        # 3. Process each raw forecast record
        for raw in raw_records:
            attempt_result = await _process_one_record(
                session=session,
                raw=raw,
                station=station,
                obs_map=obs_map,
                missing_obs_dates=missing_obs_dates,
            )
            if attempt_result == "accepted":
                accepted += 1
            elif attempt_result.startswith("rejected:"):
                rejected += 1
                reason = attempt_result.split(":", 1)[1]
                rejection_counts[reason] += 1
            # "duplicate" counts neither accepted nor rejected for clarity

    except Exception as exc:
        api_errors.append(f"Provider error: {exc}")
        logger.exception("[V3 Ingestion] Unexpected error for city %s", city)

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    total_attempted = accepted + rejected

    log_entry.records_attempted = total_attempted
    log_entry.records_accepted = accepted
    log_entry.records_rejected = rejected
    log_entry.rejection_breakdown = dict(rejection_counts)
    log_entry.missing_observation_dates = list(set(missing_obs_dates))
    log_entry.api_errors = api_errors
    log_entry.status = "success" if not api_errors else "partial"
    log_entry.duration_seconds = duration
    log_entry.completed_at = datetime.now(timezone.utc)
    await session.flush()

    return {
        "city": city,
        "status": log_entry.status,
        "records_attempted": total_attempted,
        "records_accepted": accepted,
        "records_rejected": rejected,
        "rejection_breakdown": dict(rejection_counts),
        "missing_observation_count": len(set(missing_obs_dates)),
        "api_errors": api_errors,
    }


# ── Single-record processing ──────────────────────────────────────────────────

async def _process_one_record(
    session: AsyncSession,
    raw: RawForecastRecord,
    station,
    obs_map: dict[str, float],
    missing_obs_dates: list[str],
) -> str:
    """
    Validate, normalize, pair with observation, and persist one record.

    Returns one of:
      "accepted"          — inserted into V3HistoricalRecord
      "rejected:<reason>" — failed look-ahead or other hard check
      "duplicate"         — unique constraint conflict; silently skipped
    """
    # 1. Look-ahead validation (hard gate)
    validation = validate_record(raw)
    if not validation.is_valid:
        return f"rejected:{validation.rejection_reason}"

    # 2. Persist the raw source record (immutable)
    raw_rec = V3RawSourceRecord(
        provider=raw.provider,
        model=raw.model,
        model_version=raw.model_version,
        city=raw.city,
        station_id=raw.station_id,
        retrieval_timestamp=raw.retrieval_timestamp,
        raw_source_identifier=raw.raw_source_identifier,
        source_provenance=raw.source_provenance,
        raw_response=raw.raw_response if isinstance(raw.raw_response, dict) else None,
        transformation_version=TRANSFORMATION_VERSION,
    )
    session.add(raw_rec)
    await session.flush()

    # 3. Unit conversion: Celsius → Fahrenheit
    from app.services.v3_providers.noaa_ghcnd_observations import celsius_to_fahrenheit

    forecast_tmax_f: float | None = None
    unit_conversions: dict = {}
    if raw.forecast_tmax_raw is not None:
        if raw.raw_unit == "celsius":
            forecast_tmax_f = celsius_to_fahrenheit(raw.forecast_tmax_raw)
            unit_conversions["forecast_tmax"] = "celsius_to_fahrenheit"
        elif raw.raw_unit == "fahrenheit":
            forecast_tmax_f = float(raw.forecast_tmax_raw)
        elif raw.raw_unit == "kelvin":
            forecast_tmax_f = celsius_to_fahrenheit(raw.forecast_tmax_raw - 273.15)
            unit_conversions["forecast_tmax"] = "kelvin_to_fahrenheit"

    # 4. Pair with observation
    observed_tmax_f = obs_map.get(raw.target_date_local)
    if observed_tmax_f is None:
        missing_obs_dates.append(raw.target_date_local)

    # 5. Compute error metrics
    signed_error = abs_error = squared_error = None
    if forecast_tmax_f is not None and observed_tmax_f is not None:
        if isfinite(forecast_tmax_f) and isfinite(observed_tmax_f):
            signed_error  = observed_tmax_f - forecast_tmax_f
            abs_error     = abs(signed_error)
            squared_error = signed_error ** 2

    # 6. Calendar tagging
    try:
        dt = datetime.strptime(raw.target_date_local, "%Y-%m-%d")
        month = dt.month
        season = SEASON_MAP[month]
    except ValueError:
        month = None
        season = None

    # 7. Quality status
    has_missing_value = raw.forecast_tmax_raw is None
    if has_missing_value:
        quality_status = "pending_observation"
        flags = [RejectionReason.MISSING_FORECAST_VALUE]
    elif observed_tmax_f is None:
        quality_status = "pending_observation"
        flags = validation.flags
    else:
        quality_status = "ok"
        flags = validation.flags

    # 8. Lead-time bucket
    bucket = LEAD_TIME_BUCKET.get(raw.lead_time_hours, f"{raw.lead_time_hours // 24}d")

    # 9. Insert (skip on unique conflict)
    hist_rec = V3HistoricalRecord(
        preload_version=CURRENT_PRELOAD_VERSION,
        raw_source_id=raw_rec.id,
        city=raw.city,
        station_id=raw.station_id,
        station_name=station.station_name,
        station_lat=raw.station_lat,
        station_lon=raw.station_lon,
        local_timezone=raw.local_timezone,
        target_date=raw.target_date_local,
        forecast_source=raw.provider,
        forecast_model=raw.model,
        model_version=raw.model_version,
        forecast_init_time=raw.forecast_init_time,
        forecast_valid_time=raw.forecast_valid_time,
        forecast_retrieval_time=raw.retrieval_timestamp,
        lead_time_hours=raw.lead_time_hours,
        lead_time_bucket=bucket,
        forecast_tmax_f=forecast_tmax_f,
        observed_tmax_f=observed_tmax_f,
        signed_error=signed_error,
        abs_error=abs_error,
        squared_error=squared_error,
        month=month,
        season=season,
        quality_status=quality_status,
        missing_data_flags=[f.value if hasattr(f, "value") else str(f) for f in flags],
        rejection_reason=None,
        transformation_version=TRANSFORMATION_VERSION,
        unit_conversions=unit_conversions or None,
    )

    try:
        session.add(hist_rec)
        await session.flush()
        return "accepted"
    except Exception as exc:
        await session.rollback()
        # Most likely a unique constraint violation (duplicate record)
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            return "duplicate"
        logger.warning("[V3 Ingestion] Insert error for %s %s: %s", raw.city, raw.target_date_local, exc)
        return f"rejected:INSERT_ERROR"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_model_for_provider(provider_key: str) -> str:
    """Return the primary model name for a provider key without instantiating it."""
    from app.services.v3_providers.registry import get_provider_class
    try:
        return get_provider_class(provider_key).MODEL
    except KeyError:
        return "unknown"
