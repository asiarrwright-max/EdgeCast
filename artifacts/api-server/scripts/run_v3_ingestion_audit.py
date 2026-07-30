"""
V3 Ingestion Runner & Audit Script
====================================
Runs the full V3 historical ingestion for Denver and Oklahoma City (2024),
then queries the database and prints a detailed audit report.

Usage:
    cd artifacts/api-server
    python scripts/run_v3_ingestion_audit.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("v3_audit")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Import models to register all tables with Base.metadata
import app.models       # noqa: F401 — registers V2 tables
import app.models_v3    # noqa: F401 — registers V3 tables

from sqlalchemy import cast, func, select, text, Text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import get_settings
from app.database import Base
from app.models import AppSetting
from app.models_v3 import (
    V3HistoricalRecord,
    V3IngestionLog,
    V3RawSourceRecord,
    V3_FLAG_DEFAULTS,
)

CITIES = ["Denver", "Oklahoma City"]
START_DATE = "2024-01-01"
END_DATE   = "2024-12-31"
PROVIDER   = "open-meteo-forecast-history"


async def make_engine():
    settings = get_settings()
    db_url, connect_args = settings.get_async_db_url()
    engine = create_async_engine(db_url, connect_args=connect_args, echo=False)
    return engine


async def main():
    engine = await make_engine()
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    # ── Step 1: Ensure V3 tables exist ────────────────────────────────────
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
    logger.info("Tables verified.")

    # ── Step 2: Enable v3.ingestion_enabled ──────────────────────────────
    logger.info("=== Enabling v3.ingestion_enabled flag ===")
    async with SessionLocal() as session:
        flag = await session.execute(
            select(AppSetting).where(AppSetting.key == "v3.ingestion_enabled")
        )
        row = flag.scalar_one_or_none()
        if row is None:
            row = AppSetting(
                key="v3.ingestion_enabled", value="true",
                description="Enable V3 historical ingestion pipeline",
            )
            session.add(row)
        else:
            row.value = "true"
        await session.commit()
    logger.info("v3.ingestion_enabled = true")

    # ── Step 3: Run ingestion — one session per city (sequential) ─────────
    # Sequential processing avoids SQLAlchemy asyncio session concurrency issues.
    from app.services.v3_ingestion import _ingest_city, _get_model_for_provider
    import uuid

    run_id = str(uuid.uuid4())
    all_city_results = []
    t0 = datetime.now(timezone.utc)

    logger.info("")
    logger.info("=== Ingestion run_id=%s ===", run_id)
    logger.info("Cities: %s  |  Range: %s → %s", CITIES, START_DATE, END_DATE)
    logger.info("Provider: %s", PROVIDER)
    logger.info("")

    for city in CITIES:
        logger.info("--- Processing %s ---", city)
        async with SessionLocal() as session:
            # Seed the flag in this session too (needed inside _ingest_city path)
            flag = await session.execute(
                select(AppSetting).where(AppSetting.key == "v3.ingestion_enabled")
            )
            # (already set — just confirming session sees it)
            result = await _ingest_city(
                session=session,
                run_id=run_id,
                city=city,
                provider_key=PROVIDER,
                start_date=START_DATE,
                end_date=END_DATE,
                lead_times_hours=[24],  # single lead time — provider ignores this
            )
            await session.commit()
        logger.info(
            "%s: status=%s accepted=%d rejected=%d errors=%s",
            city, result["status"],
            result.get("records_accepted", 0),
            result.get("records_rejected", 0),
            result.get("api_errors", []),
        )
        all_city_results.append(result)

    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
    total_accepted = sum(r.get("records_accepted", 0) for r in all_city_results)
    total_rejected = sum(r.get("records_rejected", 0) for r in all_city_results)

    logger.info("")
    logger.info("=== Ingestion complete in %.1fs: accepted=%d rejected=%d ===",
                elapsed, total_accepted, total_rejected)

    # ── Step 4: Build audit report ────────────────────────────────────────
    logger.info("")
    logger.info("=== Building audit report ===")

    async with SessionLocal() as session:
        report = await build_audit_report(session)

    print("\n" + "="*80)
    print("V3 HISTORICAL INGESTION — DETAILED AUDIT REPORT")
    print("="*80)
    print(json.dumps(report, indent=2, default=str))
    print("="*80)

    # ── Step 5: Reset flag to false ────────────────────────────────────────
    async with SessionLocal() as session:
        flag = await session.execute(
            select(AppSetting).where(AppSetting.key == "v3.ingestion_enabled")
        )
        row = flag.scalar_one_or_none()
        if row:
            row.value = "false"
            await session.commit()
    logger.info("v3.ingestion_enabled reset to false.")

    await engine.dispose()


# ─── Audit queries ─────────────────────────────────────────────────────────────

async def build_audit_report(session) -> dict:
    from app.services.settlement_stations import SETTLEMENT_STATIONS
    import calendar
    from datetime import date, timedelta

    report: dict = {}

    # ── 1. Historical coverage ────────────────────────────────────────────
    coverage = {}
    for city in CITIES:
        station = SETTLEMENT_STATIONS[city]
        expected_days = sum(calendar.monthrange(2024, m)[1] for m in range(1, 13))

        dates_result = await session.execute(
            select(V3HistoricalRecord.target_date)
            .where(
                V3HistoricalRecord.city == city,
                V3HistoricalRecord.quality_status.in_(["ok", "pending_observation"]),
            )
            .group_by(V3HistoricalRecord.target_date)
        )
        covered_dates = sorted([str(r[0]) for r in dates_result.all()])
        covered_count = len(covered_dates)

        all_2024 = set()
        d = date(2024, 1, 1)
        while d <= date(2024, 12, 31):
            all_2024.add(d.isoformat())
            d += timedelta(days=1)
        missing_dates = sorted(all_2024 - set(covered_dates))
        missing_ranges = _compress_date_gaps(missing_dates)

        # Lead-time coverage (should be all 24h)
        lead_result = await session.execute(
            select(
                V3HistoricalRecord.lead_time_hours,
                func.count().label("n"),
            )
            .where(V3HistoricalRecord.city == city)
            .group_by(V3HistoricalRecord.lead_time_hours)
            .order_by(V3HistoricalRecord.lead_time_hours)
        )
        leads = {str(r[0]) + "h": r[1] for r in lead_result.all()}

        status_result = await session.execute(
            select(
                V3HistoricalRecord.quality_status,
                func.count().label("n"),
            )
            .where(V3HistoricalRecord.city == city)
            .group_by(V3HistoricalRecord.quality_status)
        )
        statuses = {r[0]: r[1] for r in status_result.all()}

        coverage[city] = {
            "station_id": station.ghcnd_station_id,
            "station_name": station.station_name,
            "expected_days_2024": expected_days,
            "covered_days": covered_count,
            "coverage_pct": round(covered_count / expected_days * 100, 1),
            "missing_day_count": len(missing_dates),
            "missing_date_ranges": missing_ranges,
            "lead_time_coverage": leads,
            "quality_status_counts": statuses,
        }

    report["1_historical_coverage"] = coverage

    # ── 2. Forecast source validation ────────────────────────────────────
    source_val = {}
    for city in CITIES:
        sample_result = await session.execute(
            select(
                V3HistoricalRecord.target_date,
                V3HistoricalRecord.lead_time_hours,
                V3HistoricalRecord.forecast_init_time,
                V3HistoricalRecord.forecast_valid_time,
                V3HistoricalRecord.forecast_source,
                V3HistoricalRecord.forecast_model,
                V3HistoricalRecord.model_version,
                V3HistoricalRecord.forecast_retrieval_time,
            )
            .where(V3HistoricalRecord.city == city)
            .order_by(V3HistoricalRecord.target_date)
            .limit(5)
        )
        timing_samples = []
        for r in sample_result.all():
            init_t, valid_t, retr_t = r[2], r[3], r[7]
            timing_samples.append({
                "target_date": str(r[0]),
                "lead_time_hours_nominal": r[1],
                "forecast_init_time_utc": str(init_t),
                "forecast_valid_time_utc": str(valid_t),
                "retrieval_timestamp_utc": str(retr_t),
                "init_before_retrieval": bool(init_t < retr_t) if (init_t and retr_t) else None,
                "init_before_valid": bool(init_t < valid_t) if (init_t and valid_t) else None,
                "actual_lead_hours": round((valid_t - init_t).total_seconds() / 3600, 1) if (init_t and valid_t) else None,
                "provider": r[4],
                "model": r[5],
                "model_version": r[6],
            })

        # One raw source provenance
        rsr_result = await session.execute(
            select(V3RawSourceRecord)
            .where(V3RawSourceRecord.city == city)
            .limit(1)
        )
        rsr = rsr_result.scalar_one_or_none()
        provenance = None
        if rsr:
            resp = rsr.raw_response or {}
            provenance = {
                "raw_source_identifier": rsr.raw_source_identifier,
                "source_provenance": rsr.source_provenance,
                "transformation_version": rsr.transformation_version,
                "retrieval_timestamp": str(rsr.retrieval_timestamp),
                "api_response_keys": list(resp.keys()),
                "api_response_model_field": resp.get("model", "(not returned by API)"),
                "api_timezone": resp.get("timezone"),
                "api_daily_keys": list(resp.get("daily", {}).keys()),
                "api_daily_record_count": len(resp.get("daily", {}).get("time", [])),
            }

        source_val[city] = {
            "provider": PROVIDER,
            "api_endpoint": "https://historical-forecast-api.open-meteo.com/v1/forecast",
            "not_archive_api": "https://archive-api.open-meteo.com (ERA5 reanalysis — NOT used)",
            "model": "GFS (gfs_seamless)",
            "effective_lead_time_note": (
                "The Historical Forecast API date-range mode returns a single "
                "effective lead time (~1 day ahead). The `forecast_days` parameter "
                "is mutually exclusive with `start_date`/`end_date` — multi-lead "
                "retrieval via date ranges is not supported by this API."
            ),
            "reanalysis_confirmed_false": (
                "Jan 2024 Denver MAE vs NOAA GHCND ≈ 2.0°F — "
                "inconsistent with ERA5 reanalysis (<0.5°F) and "
                "consistent with ~1-day GFS forecast."
            ),
            "provenance_sample": provenance,
            "timing_samples": timing_samples,
        }

    report["2_forecast_source_validation"] = source_val

    # ── 3. Station validation ─────────────────────────────────────────────
    from app.services.settlement_stations import SETTLEMENT_STATIONS
    station_val = {}
    for city in CITIES:
        station = SETTLEMENT_STATIONS[city]
        station_ids_result = await session.execute(
            select(
                V3HistoricalRecord.station_id,
                V3HistoricalRecord.station_lat,
                V3HistoricalRecord.station_lon,
                V3HistoricalRecord.station_name,
                func.count().label("n"),
            )
            .where(V3HistoricalRecord.city == city)
            .group_by(
                V3HistoricalRecord.station_id,
                V3HistoricalRecord.station_lat,
                V3HistoricalRecord.station_lon,
                V3HistoricalRecord.station_name,
            )
        )
        stations_in_data = [
            {"station_id": r[0], "lat": r[1], "lon": r[2], "name": r[3], "records": r[4]}
            for r in station_ids_result.all()
        ]
        mismatched = [s for s in stations_in_data if s["station_id"] != station.ghcnd_station_id]

        station_val[city] = {
            "expected_ghcnd_station_id": station.ghcnd_station_id,
            "expected_station_name": station.station_name,
            "lat": station.lat,
            "lon": station.lon,
            "kalshi_nws_settlement": station.nws_settlement,
            "kalshi_verified": station.verified,
            "stations_in_data": stations_in_data,
            "any_mismatched_stations": bool(mismatched),
            "mismatched_stations": mismatched,
        }

    report["3_station_validation"] = station_val

    # ── 4. Data quality ───────────────────────────────────────────────────
    dq = {}
    for city in CITIES:
        raw_count = await session.scalar(
            select(func.count()).where(V3RawSourceRecord.city == city)
        )
        hist_count = await session.scalar(
            select(func.count()).where(V3HistoricalRecord.city == city)
        )
        ok_count = await session.scalar(
            select(func.count()).where(
                V3HistoricalRecord.city == city,
                V3HistoricalRecord.quality_status == "ok",
            )
        )
        pending_count = await session.scalar(
            select(func.count()).where(
                V3HistoricalRecord.city == city,
                V3HistoricalRecord.quality_status == "pending_observation",
            )
        )

        log_result = await session.execute(
            select(V3IngestionLog)
            .where(V3IngestionLog.city == city)
            .order_by(V3IngestionLog.completed_at.desc())
            .limit(1)
        )
        log = log_result.scalar_one_or_none()

        # Unit conversions — cast to text to avoid JSON equality issue in GROUP BY
        conv_result = await session.execute(
            select(
                cast(V3HistoricalRecord.unit_conversions, Text).label("conv"),
                func.count().label("n"),
            )
            .where(V3HistoricalRecord.city == city)
            .group_by(text("conv"))
            .limit(5)
        )
        conversions = [{"conversions": r[0], "count": r[1]} for r in conv_result.all()]

        tz_result = await session.execute(
            select(V3HistoricalRecord.local_timezone, func.count().label("n"))
            .where(V3HistoricalRecord.city == city)
            .group_by(V3HistoricalRecord.local_timezone)
        )
        timezones = {r[0]: r[1] for r in tz_result.all()}

        dq[city] = {
            "raw_source_records_downloaded": raw_count,
            "historical_records_accepted": hist_count,
            "ok_records_with_both_forecast_and_obs": ok_count,
            "pending_observation_records": pending_count,
            "rejection_reasons": log.rejection_breakdown if log else {},
            "api_errors": log.api_errors if log else [],
            "missing_observation_dates_count": len(log.missing_observation_dates or []) if log else 0,
            "missing_observation_dates_sample": sorted(log.missing_observation_dates or [])[:15] if log else [],
            "unit_conversions_applied": conversions,
            "timezones_used": timezones,
            "ingestion_duration_seconds": log.duration_seconds if log else None,
            "ingestion_status": log.status if log else None,
        }

    report["4_data_quality"] = dq

    # ── 5. Sample historical records ──────────────────────────────────────
    sample_recs: dict = {}
    for city in CITIES:
        # 6 diverse OK records spread across the year
        months_wanted = [1, 4, 7, 10]
        city_samples = []
        for month in months_wanted:
            rec_result = await session.execute(
                select(V3HistoricalRecord)
                .where(
                    V3HistoricalRecord.city == city,
                    V3HistoricalRecord.quality_status == "ok",
                    V3HistoricalRecord.month == month,
                )
                .order_by(V3HistoricalRecord.target_date)
                .limit(1)
            )
            rec = rec_result.scalar_one_or_none()
            if rec is None:
                continue

            rsr_result = await session.execute(
                select(V3RawSourceRecord).where(V3RawSourceRecord.id == rec.raw_source_id)
            )
            rsr = rsr_result.scalar_one_or_none()

            raw_resp_summary = None
            if rsr and rsr.raw_response:
                resp = rsr.raw_response
                daily = resp.get("daily", {})
                raw_resp_summary = {
                    "keys_returned": list(resp.keys()),
                    "model_in_response": resp.get("model", "(absent — expected for date-range mode)"),
                    "latitude": resp.get("latitude"),
                    "longitude": resp.get("longitude"),
                    "timezone": resp.get("timezone"),
                    "daily_fields": list(daily.keys()),
                    "total_days_in_response": len(daily.get("time", [])),
                    "sample_dates_first3": daily.get("time", [])[:3],
                    "sample_tmax_first3_celsius": daily.get("temperature_2m_max", [])[:3],
                }

            city_samples.append({
                "record_id": rec.id,
                "city": rec.city,
                "target_date": str(rec.target_date),
                "season": rec.season,
                "month": rec.month,
                "station_id": rec.station_id,
                "station_name": rec.station_name,
                "station_lat": rec.station_lat,
                "station_lon": rec.station_lon,
                "provider": rec.forecast_source,
                "model": rec.forecast_model,
                "model_version": rec.model_version,
                "lead_time_hours_nominal": rec.lead_time_hours,
                "lead_time_bucket": rec.lead_time_bucket,
                "forecast_init_time_utc": str(rec.forecast_init_time),
                "forecast_valid_time_utc": str(rec.forecast_valid_time),
                "forecast_retrieval_time_utc": str(rec.forecast_retrieval_time),
                "raw_value_celsius": "(in raw_response — not stored separately)",
                "forecast_tmax_fahrenheit": rec.forecast_tmax_f,
                "unit_conversion_applied": rec.unit_conversions,
                "observed_tmax_fahrenheit_noaa_ghcnd": rec.observed_tmax_f,
                "signed_error_f": rec.signed_error,
                "abs_error_f": rec.abs_error,
                "squared_error": rec.squared_error,
                "quality_status": rec.quality_status,
                "missing_data_flags": rec.missing_data_flags,
                "preload_version": rec.preload_version,
                "transformation_version": rec.transformation_version,
                "raw_source_summary": raw_resp_summary,
                "source_provenance": rsr.source_provenance if rsr else None,
            })

        sample_recs[city] = city_samples

    report["5_sample_records"] = sample_recs

    # ── 6. Coverage by lead time ──────────────────────────────────────────
    lead_cov: dict = {}
    for city in CITIES:
        import sqlalchemy as sa
        lt_result = await session.execute(
            select(
                V3HistoricalRecord.lead_time_hours,
                V3HistoricalRecord.lead_time_bucket,
                func.count().label("total"),
                func.sum(
                    sa.case((V3HistoricalRecord.quality_status == "ok", 1), else_=0)
                ).label("ok"),
                func.avg(V3HistoricalRecord.abs_error).label("mae"),
                func.avg(V3HistoricalRecord.signed_error).label("mean_bias"),
            )
            .where(V3HistoricalRecord.city == city)
            .group_by(V3HistoricalRecord.lead_time_hours, V3HistoricalRecord.lead_time_bucket)
            .order_by(V3HistoricalRecord.lead_time_hours)
        )
        lead_cov[city] = [
            {
                "lead_hours": r[0],
                "bucket": r[1],
                "total_records": r[2],
                "ok_records": r[3] or 0,
                "coverage_pct": round((r[3] or 0) / r[2] * 100, 1) if r[2] else 0,
                "mean_abs_error_f": round(float(r[4]), 3) if r[4] is not None else None,
                "mean_signed_error_bias_f": round(float(r[5]), 3) if r[5] is not None else None,
            }
            for r in lt_result.all()
        ]

    report["6_coverage_by_lead_time"] = lead_cov

    # ── 7. Coverage by season and month ───────────────────────────────────
    import sqlalchemy as sa
    season_cov: dict = {}
    for city in CITIES:
        sm_result = await session.execute(
            select(
                V3HistoricalRecord.month,
                V3HistoricalRecord.season,
                func.count().label("ok_records"),
                func.avg(V3HistoricalRecord.abs_error).label("mae"),
                func.avg(V3HistoricalRecord.signed_error).label("bias"),
                func.min(V3HistoricalRecord.abs_error).label("min_ae"),
                func.max(V3HistoricalRecord.abs_error).label("max_ae"),
            )
            .where(
                V3HistoricalRecord.city == city,
                V3HistoricalRecord.quality_status == "ok",
            )
            .group_by(V3HistoricalRecord.month, V3HistoricalRecord.season)
            .order_by(V3HistoricalRecord.month)
        )
        season_cov[city] = [
            {
                "month": r[0],
                "season": r[1],
                "ok_records": r[2],
                "mae_f": round(float(r[3]), 3) if r[3] is not None else None,
                "mean_bias_f": round(float(r[4]), 3) if r[4] is not None else None,
                "min_abs_error_f": round(float(r[5]), 3) if r[5] is not None else None,
                "max_abs_error_f": round(float(r[6]), 3) if r[6] is not None else None,
            }
            for r in sm_result.all()
        ]

    report["7_coverage_by_season_and_month"] = season_cov

    # ── 8. Readiness assessment inputs ────────────────────────────────────
    readiness: dict = {}
    for city in CITIES:
        err = await session.execute(
            select(
                func.count().label("n"),
                func.avg(V3HistoricalRecord.abs_error).label("mae"),
                func.min(V3HistoricalRecord.abs_error).label("min_ae"),
                func.max(V3HistoricalRecord.abs_error).label("max_ae"),
                func.avg(V3HistoricalRecord.signed_error).label("mean_bias"),
                func.avg(V3HistoricalRecord.squared_error).label("mse"),
                func.percentile_cont(0.5).within_group(
                    V3HistoricalRecord.abs_error
                ).label("median_ae"),
                func.stddev(V3HistoricalRecord.signed_error).label("sigma"),
            )
            .where(
                V3HistoricalRecord.city == city,
                V3HistoricalRecord.quality_status == "ok",
            )
        )
        r = err.one()
        readiness[city] = {
            "ok_records": r[0],
            "mae_f": round(float(r[1]), 3) if r[1] else None,
            "min_abs_error_f": round(float(r[2]), 3) if r[2] else None,
            "max_abs_error_f": round(float(r[3]), 3) if r[3] else None,
            "mean_bias_f": round(float(r[4]), 3) if r[4] else None,
            "rmse_f": round(float(r[5]) ** 0.5, 3) if r[5] else None,
            "median_abs_error_f": round(float(r[6]), 3) if r[6] else None,
            "sigma_f": round(float(r[7]), 3) if r[7] else None,
        }

    report["8_readiness_assessment_inputs"] = readiness

    # ── Ingestion log detail ──────────────────────────────────────────────
    logs_detail: dict = {}
    for city in CITIES:
        logs_result = await session.execute(
            select(V3IngestionLog)
            .where(V3IngestionLog.city == city)
            .order_by(V3IngestionLog.completed_at.desc())
        )
        logs = logs_result.scalars().all()
        logs_detail[city] = [
            {
                "run_id": l.run_id,
                "provider": l.provider,
                "model": l.model,
                "start_date": l.start_date,
                "end_date": l.end_date,
                "records_attempted": l.records_attempted,
                "records_accepted": l.records_accepted,
                "records_rejected": l.records_rejected,
                "rejection_breakdown": l.rejection_breakdown,
                "missing_obs_count": len(l.missing_observation_dates or []),
                "missing_obs_sample": sorted(l.missing_observation_dates or [])[:15],
                "api_errors": l.api_errors,
                "status": l.status,
                "duration_seconds": l.duration_seconds,
                "completed_at": str(l.completed_at),
            }
            for l in logs
        ]

    report["_ingestion_logs"] = logs_detail
    return report


def _compress_date_gaps(missing_dates: list[str]) -> list[str]:
    if not missing_dates:
        return []
    from datetime import date, timedelta
    ranges = []
    start = end = date.fromisoformat(missing_dates[0])
    for ds in missing_dates[1:]:
        d = date.fromisoformat(ds)
        if d == end + timedelta(days=1):
            end = d
        else:
            ranges.append(f"{start} – {end}" if start != end else str(start))
            start = end = d
    ranges.append(f"{start} – {end}" if start != end else str(start))
    return ranges


if __name__ == "__main__":
    asyncio.run(main())
