"""
V3 Analytics API — Phase 1
===========================
Phase 1 endpoints:
  GET  /analytics/v3/ingestion-audit  — per-city ingestion summary
  POST /analytics/v3/run-ingestion    — trigger historical ingestion (admin)
  GET  /analytics/v3/flags            — current V3 feature flag states

Phase 2 and Phase 3 endpoints will be added to this router when those phases
are implemented.  This file is additive — no existing router is modified.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import AppSetting
from app.models_v3 import (
    V3_FLAG_DEFAULTS,
    V3HistoricalRecord,
    V3IngestionLog,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["V3 Analytics"])


# ---------------------------------------------------------------------------
# GET /analytics/v3/flags — feature flag status
# ---------------------------------------------------------------------------

@router.get("/analytics/v3/flags")
async def get_v3_flags(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Return the current value of all V3 feature flags."""
    from sqlalchemy import select
    result = await session.execute(
        select(AppSetting).where(
            AppSetting.key.in_(list(V3_FLAG_DEFAULTS.keys()))
        )
    )
    rows = {r.key: r.value for r in result.scalars().all()}

    flags: dict[str, Any] = {}
    for key, default in V3_FLAG_DEFAULTS.items():
        raw = rows.get(key, default)
        flags[key] = {
            "value": raw,
            "enabled": (raw or "").lower() in ("true", "1", "yes"),
            "default": default,
        }

    return {
        "flags": flags,
        "note": (
            "Set a flag to 'true' via the app_settings table to enable "
            "the corresponding V3 capability.  All flags default to 'false'."
        ),
    }


# ---------------------------------------------------------------------------
# GET /analytics/v3/ingestion-audit — per-city ingestion summary
# ---------------------------------------------------------------------------

@router.get("/analytics/v3/ingestion-audit")
async def get_ingestion_audit(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Return a per-city summary of all V3 historical ingestion runs.

    Shows:
    - Date range of accepted records in V3HistoricalRecord
    - Records accepted / rejected
    - Rejection reason breakdown
    - Missing observation dates
    - Last successful run timestamp
    - Provider and model coverage
    """
    # Per-city record stats from V3HistoricalRecord
    hist_stats_result = await session.execute(
        select(
            V3HistoricalRecord.city,
            V3HistoricalRecord.forecast_source,
            V3HistoricalRecord.forecast_model,
            func.min(V3HistoricalRecord.target_date).label("earliest_date"),
            func.max(V3HistoricalRecord.target_date).label("latest_date"),
            func.count().label("total_records"),
            func.sum(
                (V3HistoricalRecord.quality_status == "ok").cast(
                    __import__("sqlalchemy").Integer
                )
            ).label("ok_records"),
        ).group_by(
            V3HistoricalRecord.city,
            V3HistoricalRecord.forecast_source,
            V3HistoricalRecord.forecast_model,
        )
    )
    hist_stats = hist_stats_result.all()

    # Per-city latest ingestion log
    log_result = await session.execute(
        select(
            V3IngestionLog.city,
            V3IngestionLog.provider,
            V3IngestionLog.model,
            func.max(V3IngestionLog.completed_at).label("last_run_at"),
            func.sum(V3IngestionLog.records_attempted).label("total_attempted"),
            func.sum(V3IngestionLog.records_accepted).label("total_accepted"),
            func.sum(V3IngestionLog.records_rejected).label("total_rejected"),
        ).group_by(
            V3IngestionLog.city,
            V3IngestionLog.provider,
            V3IngestionLog.model,
        )
    )
    log_stats = log_result.all()

    # Latest per-city rejection details (from most recent log entry)
    latest_logs_result = await session.execute(
        select(V3IngestionLog)
        .order_by(V3IngestionLog.completed_at.desc())
        .limit(200)
    )
    latest_logs = latest_logs_result.scalars().all()
    # Build city → latest log lookup
    city_latest: dict[str, V3IngestionLog] = {}
    for log in latest_logs:
        if log.city not in city_latest:
            city_latest[log.city] = log

    # Assemble per-city entries
    city_map: dict[str, dict] = {}

    for row in hist_stats:
        key = row.city
        if key not in city_map:
            city_map[key] = {
                "city": row.city,
                "sources": [],
                "earliest_date": row.earliest_date,
                "latest_date": row.latest_date,
                "total_ok_records": 0,
                "total_records": 0,
                "last_run_at": None,
                "total_attempted": 0,
                "total_accepted": 0,
                "total_rejected": 0,
                "rejection_breakdown": {},
                "missing_observation_count": 0,
                "api_errors": [],
                "status": "no_data",
            }
        entry = city_map[key]
        entry["sources"].append({
            "provider": row.forecast_source,
            "model": row.forecast_model,
            "records": row.total_records,
            "ok_records": row.ok_records or 0,
        })
        if (entry["earliest_date"] is None or
                (row.earliest_date and row.earliest_date < entry["earliest_date"])):
            entry["earliest_date"] = row.earliest_date
        if (entry["latest_date"] is None or
                (row.latest_date and row.latest_date > entry["latest_date"])):
            entry["latest_date"] = row.latest_date
        entry["total_ok_records"] += row.ok_records or 0
        entry["total_records"] += row.total_records

    for row in log_stats:
        key = row.city
        if key not in city_map:
            city_map[key] = {
                "city": row.city,
                "sources": [],
                "earliest_date": None,
                "latest_date": None,
                "total_ok_records": 0,
                "total_records": 0,
                "last_run_at": None,
                "total_attempted": 0,
                "total_accepted": 0,
                "total_rejected": 0,
                "rejection_breakdown": {},
                "missing_observation_count": 0,
                "api_errors": [],
                "status": "no_data",
            }
        entry = city_map[key]
        entry["total_attempted"] += row.total_attempted or 0
        entry["total_accepted"] += row.total_accepted or 0
        entry["total_rejected"] += row.total_rejected or 0
        if row.last_run_at:
            last = row.last_run_at
            if entry["last_run_at"] is None or last > entry["last_run_at"]:
                entry["last_run_at"] = last.isoformat() if hasattr(last, "isoformat") else str(last)

    for city, log in city_latest.items():
        if city in city_map:
            entry = city_map[city]
            if log.rejection_breakdown:
                for k, v in log.rejection_breakdown.items():
                    entry["rejection_breakdown"][k] = (
                        entry["rejection_breakdown"].get(k, 0) + v
                    )
            if log.missing_observation_dates:
                entry["missing_observation_count"] += len(log.missing_observation_dates)
            if log.api_errors:
                entry["api_errors"].extend(log.api_errors[:3])
            entry["status"] = log.status or "unknown"

    cities_list = sorted(city_map.values(), key=lambda x: x["city"])

    total_records = sum(c["total_records"] for c in cities_list)
    total_ok = sum(c["total_ok_records"] for c in cities_list)

    return {
        "cities": cities_list,
        "summary": {
            "cities_with_data": len([c for c in cities_list if c["total_records"] > 0]),
            "total_records": total_records,
            "total_ok_records": total_ok,
            "total_cities_audited": len(cities_list),
        },
        "note": (
            "ok_records have both forecast and observation populated and passed "
            "look-ahead validation.  pending_observation records are awaiting "
            "NOAA GHCND observations."
        ),
    }


# ---------------------------------------------------------------------------
# POST /analytics/v3/run-ingestion — trigger ingestion (admin, flag-gated)
# ---------------------------------------------------------------------------

@router.post("/analytics/v3/run-ingestion")
async def trigger_ingestion(
    body: dict | None = None,
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Trigger V3 historical ingestion.  Gated on v3.ingestion_enabled.

    Optional body params:
    - start_date: YYYY-MM-DD (default: 2 years ago)
    - end_date:   YYYY-MM-DD (default: yesterday)
    - provider:   provider key (default: "open-meteo-forecast-history")
    - cities:     list of city names (default: all active verified cities)
    - lead_times: list of lead_time_hours (default: [24,48,72,96,120,144,168])
    """
    from app.services.v3_ingestion import run_ingestion

    params = body or {}
    try:
        result = await run_ingestion(
            session=session,
            start_date=params.get("start_date"),
            end_date=params.get("end_date"),
            provider_key=params.get("provider", "open-meteo-forecast-history"),
            lead_times_hours=params.get("lead_times"),
            cities=params.get("cities"),
        )
    except Exception as exc:
        logger.exception("[V3 Ingestion] Unhandled error in run-ingestion endpoint")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result
