"""
V3 Analytics API — Phase 1 + Phase 2
======================================
Phase 1 endpoints:
  GET  /analytics/v3/flags            — current V3 feature flag states
  GET  /analytics/v3/ingestion-audit  — per-city ingestion summary
  POST /analytics/v3/run-ingestion    — trigger historical ingestion (admin)

Phase 2 endpoints (gated on v3.validation_enabled):
  POST /analytics/v3/compute-error-stats   — build V3ErrorStats from historical records
  GET  /analytics/v3/error-stats           — view computed V3ErrorStats rows
  GET  /analytics/v3/walk-forward-report   — run walk-forward validation and return report

Phase 3 endpoints will be added to this router when Phase 3 is implemented.
This file is additive — no existing router is modified.
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
    V3ErrorStats,
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


# ===========================================================================
# Phase 2: Error stats + walk-forward validation
# ===========================================================================

# ---------------------------------------------------------------------------
# POST /analytics/v3/compute-error-stats
# ---------------------------------------------------------------------------

@router.post("/analytics/v3/compute-error-stats")
async def compute_error_stats(
    body: dict | None = None,
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Build V3ErrorStats from V3HistoricalRecord rows.
    Gated on v3.validation_enabled feature flag.

    Optional body params:
    - hist_weight:    float in [0,1]; hist_weight + forward_weight must = 1.0
    - forward_weight: float in [0,1] (default 0.0 in Phase 2)
    - shrinkage_k:    float (default 30.0)

    Deletes all existing V3ErrorStats rows for the current preload_version
    and inserts fresh rows.  Safe to call multiple times — idempotent.
    """
    from app.services.v3_flags import get_v3_flag
    from app.services.v3_error_stats import V3StatsConfig, compute_v3_error_stats

    if not await get_v3_flag(session, "v3.validation_enabled"):
        raise HTTPException(
            status_code=403,
            detail=(
                "v3.validation_enabled is false.  Set it to 'true' in "
                "app_settings to enable Phase 2 endpoints."
            ),
        )

    params = body or {}
    try:
        hist_w    = float(params.get("hist_weight",    1.0))
        fwd_w     = float(params.get("forward_weight", 0.0))
        shrink_k  = float(params.get("shrinkage_k",    30.0))
        cfg = V3StatsConfig(
            hist_weight=hist_w,
            forward_weight=fwd_w,
            shrinkage_k=shrink_k,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid config: {exc}") from exc

    try:
        result = await compute_v3_error_stats(session, config=cfg)
    except Exception as exc:
        logger.exception("[V3 ErrorStats] Unhandled error in compute-error-stats")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result


# ---------------------------------------------------------------------------
# GET /analytics/v3/error-stats
# ---------------------------------------------------------------------------

@router.get("/analytics/v3/error-stats")
async def get_error_stats(
    city: str | None = None,
    model: str | None = None,
    fallback_level: int | None = None,
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Return computed V3ErrorStats rows.  Optional query params:
    - city:           filter by city name (or '__global__')
    - model:          filter by model (e.g. 'GFS')
    - fallback_level: filter by fallback level (0–4)

    Returns rows sorted by fallback_level ASC, city, season.
    """
    from app.services.v3_flags import get_v3_flag

    if not await get_v3_flag(session, "v3.validation_enabled"):
        raise HTTPException(
            status_code=403,
            detail="v3.validation_enabled is false.",
        )

    stmt = select(V3ErrorStats).order_by(
        V3ErrorStats.fallback_level,
        V3ErrorStats.city,
        V3ErrorStats.season,
        V3ErrorStats.lead_time_bucket,
    )
    if city is not None:
        stmt = stmt.where(V3ErrorStats.city == city)
    if model is not None:
        stmt = stmt.where(V3ErrorStats.model == model)
    if fallback_level is not None:
        stmt = stmt.where(V3ErrorStats.fallback_level == fallback_level)

    result = await session.execute(stmt)
    rows = result.scalars().all()

    def _row_dict(r: V3ErrorStats) -> dict:
        return {
            "id":               r.id,
            "preload_version":  r.preload_version,
            "city":             r.city,
            "model":            r.model,
            "lead_time_bucket": r.lead_time_bucket,
            "season":           r.season,
            "fallback_level":   r.fallback_level,
            "raw_sample_size":  r.raw_sample_size,
            "effective_n":      r.effective_n,
            "bias":             r.bias,
            "sigma_raw":        r.sigma_raw,
            "sigma_shrunk":     r.sigma_shrunk,
            "mae":              r.mae,
            "rmse":             r.rmse,
            "last_computed_at": r.last_computed_at.isoformat() if r.last_computed_at else None,
        }

    return {
        "rows": [_row_dict(r) for r in rows],
        "total": len(rows),
        "note": (
            "sigma_shrunk is the value used in predictions; it incorporates "
            "partial pooling toward the parent level and is always >= 3.5°F. "
            "fallback_level 0 = most specific (city+model+lead+season), "
            "4 = global conservative prior."
        ),
    }


# ---------------------------------------------------------------------------
# GET /analytics/v3/walk-forward-report
# ---------------------------------------------------------------------------

@router.get("/analytics/v3/walk-forward-report")
async def get_walk_forward_report(
    include_records: bool = False,
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Run and return the V3 walk-forward validation report.
    Gated on v3.validation_enabled.

    Loads all 'ok' V3HistoricalRecord rows, runs the walk-forward validator
    entirely in memory, and returns the full report.  Expensive on large
    datasets — results are not cached; call once and save the output.

    Query params:
    - include_records: bool (default false) — include per-record detail
      in the response.  Can be large (732+ rows per report run).
    """
    from app.services.v3_flags import get_v3_flag
    from app.services.v3_walkforward import run_walk_forward_validation

    if not await get_v3_flag(session, "v3.validation_enabled"):
        raise HTTPException(
            status_code=403,
            detail="v3.validation_enabled is false.",
        )

    # Load all ok records
    result = await session.execute(
        select(V3HistoricalRecord).where(
            V3HistoricalRecord.quality_status == "ok",
            V3HistoricalRecord.signed_error.is_not(None),
            V3HistoricalRecord.forecast_tmax_f.is_not(None),
            V3HistoricalRecord.observed_tmax_f.is_not(None),
        ).order_by(V3HistoricalRecord.target_date)
    )
    records = result.scalars().all()

    try:
        report = run_walk_forward_validation(records)
    except Exception as exc:
        logger.exception("[V3 WalkForward] Unhandled error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    def _summary_dict(s) -> dict:
        return {
            "label":                  s.label,
            "n":                      s.n,
            "mae_raw":                s.mae_raw,
            "mae_adj":                s.mae_adj,
            "mae_delta":              s.mae_delta,
            "mae_ci95":               list(s.mae_ci95),
            "rmse_raw":               s.rmse_raw,
            "rmse_adj":               s.rmse_adj,
            "mean_error_raw":         s.mean_error_raw,
            "mean_error_adj":         s.mean_error_adj,
            "sigma_coverage_68pct":   s.sigma_coverage_68pct,
            "sigma_coverage_95pct":   s.sigma_coverage_95pct,
            "crps_raw":               s.crps_raw,
            "crps_adj":               s.crps_adj,
            "brier_score":            s.brier_score,
            "preload_hurt_n":         s.preload_hurt_n,
            "preload_hurt_pct":       s.preload_hurt_pct,
            "fallback_dist":          {str(k): v for k, v in s.fallback_dist.items()},
        }

    def _outlier_dict(o) -> dict:
        return {
            "target_date":   o.target_date,
            "city":          o.city,
            "season":        o.season,
            "forecast_f":    o.forecast_f,
            "observed_f":    o.observed_f,
            "raw_error":     o.raw_error,
            "adj_error":     o.adj_error,
            "bias_used":     o.bias_used,
            "sigma_used":    o.sigma_used,
            "z_score_raw":   o.z_score_raw,
            "preload_hurt":  o.preload_hurt,
        }

    def _cal_dict(c) -> dict:
        return {
            "bucket_lo":  c.bucket_lo,
            "bucket_hi":  c.bucket_hi,
            "count":      c.count,
            "empirical":  c.empirical,
            "mean_prob":  c.mean_prob,
        }

    response = {
        "generated_at":         report.generated_at,
        "config":               report.config,
        "total_records":        report.total_records,
        "train_cutoff_n":       report.train_cutoff_n,
        "test_n":               report.test_n,
        "verdict":              report.verdict,
        "verdict_note":         report.verdict_note,
        "overall":              _summary_dict(report.overall),
        "by_city":              {k: _summary_dict(v) for k, v in report.by_city.items()},
        "by_season":            {k: _summary_dict(v) for k, v in report.by_season.items()},
        "calibration":          [_cal_dict(c) for c in report.calibration],
        "outliers":             [_outlier_dict(o) for o in report.outliers],
        "preload_hurt_examples":[_outlier_dict(o) for o in report.preload_hurt_examples],
        "note": (
            "MAE delta < 0 means improvement; MAE delta > 0 means the preload "
            "made accuracy worse.  CRPS is the Continuous Ranked Probability Score "
            "(lower is better).  Brier score is for binary event P(T >= raw_forecast)."
        ),
    }

    if include_records:
        response["records"] = report.records

    return response
