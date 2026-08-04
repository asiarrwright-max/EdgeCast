from __future__ import annotations

import logging
from typing import AsyncGenerator

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = logging.getLogger(__name__)

# Module-level singletons populated by init_db()
engine = None
AsyncSessionLocal: async_sessionmaker | None = None


class Base(DeclarativeBase):
    pass


def get_engine():
    """
    Return the module-level engine singleton at call time.
    Must be used instead of 'from app.database import engine' in other modules,
    because a direct import captures None (the value at import time) and never
    sees the AsyncEngine assigned later by init_db().
    """
    return engine


async def _apply_migrations(conn) -> None:
    """
    Idempotent column additions for schema evolution.
    SQLAlchemy create_all() only creates missing tables, not missing columns.
    This runs ALTER TABLE ... ADD COLUMN IF NOT EXISTS for new columns.
    """
    migrations = [
        # KalshiMarket new columns (Phase 1.5)
        "ALTER TABLE kalshi_markets ADD COLUMN IF NOT EXISTS parsing_status VARCHAR(50)",
        "ALTER TABLE kalshi_markets ADD COLUMN IF NOT EXISTS parsing_reason TEXT",
        "ALTER TABLE kalshi_markets ADD COLUMN IF NOT EXISTS weather_market_type VARCHAR(50)",
        "ALTER TABLE kalshi_markets ADD COLUMN IF NOT EXISTS collection_timestamp TIMESTAMPTZ",
        # JobRun new columns (Phase 1.5)
        "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS markets_skipped INTEGER",
        "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS markets_rejected INTEGER",
        "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS duration_seconds FLOAT",
        # WeatherForecast new columns (Phase 2B)
        "ALTER TABLE weather_forecasts ADD COLUMN IF NOT EXISTS hourly_data JSONB",
        # PredictionSnapshot new columns (Phase 2B)
        "ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS contract_type VARCHAR(30)",
        "ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS target_hour INTEGER",
        "ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS target_timezone_str VARCHAR(20)",
        "ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS lower_bound FLOAT",
        "ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS upper_bound FLOAT",
        # Widen settlement_variable to accommodate 'hourly_temperature'
        "ALTER TABLE prediction_snapshots ALTER COLUMN settlement_variable TYPE VARCHAR(30)",
        # Phase 3A: JobRun paper-trading counts
        "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS pt_candidates INTEGER",
        "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS pt_created INTEGER",
        "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS pt_yes_trades INTEGER",
        "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS pt_no_trades INTEGER",
        "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS pt_skipped INTEGER",
        "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS pt_errors INTEGER",
        # Phase 3B: PaperTrade data-quality and analytics columns
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS quality_flags JSONB",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS lead_time_days INTEGER",
        # Phase v2: PaperTrade engine metadata (nullable — v1 rows stay NULL)
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS sigma_used FLOAT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS bias_correction FLOAT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS fallback_level VARCHAR(20)",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS calibration_adj FLOAT",
        # Phase v2: JobRun v2 paper-trading counts
        "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS pt_v2_created INTEGER",
        "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS pt_v2_skipped INTEGER",
        # GHCND integration: settlement station ID on verification rows
        "ALTER TABLE forecast_verifications ADD COLUMN IF NOT EXISTS ghcnd_station_id VARCHAR(30)",
        # Widen source_label to accommodate new label strings (≤ 60 chars)
        "ALTER TABLE forecast_verifications ALTER COLUMN source_label TYPE VARCHAR(60)",
        # Phase v2.1: PaperTrade execution-quality and station fields (nullable — pre-v2.1 rows stay NULL)
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS quote_bid FLOAT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS quote_ask FLOAT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS quote_timestamp TIMESTAMPTZ",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS est_available_qty FLOAT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS is_executable BOOLEAN",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS station_verified BOOLEAN",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS station_lat FLOAT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS station_lon FLOAT",
        # V3: nullable comparison_group_id on prediction_snapshots (additive; no constraint on existing rows)
        "ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS comparison_group_id VARCHAR(36)",
        # V3 Phase 1 refinement: timestamp provenance field on historical records
        # Distinguishes API-provided init timestamps from conservatively derived ones.
        # Default is 'derived_prior_day_00z' (matches Open-Meteo date-range mode behavior).
        "ALTER TABLE v3_historical_records ADD COLUMN IF NOT EXISTS init_time_source VARCHAR(30) DEFAULT 'derived_prior_day_00z'",
        # V3 Phase 3 design: bias gate fields on v3_error_stats.
        # Separates sigma (always applied for calibration) from bias (gated on
        # statistical significance, effective N, and magnitude thresholds).
        "ALTER TABLE v3_error_stats ADD COLUMN IF NOT EXISTS bias_t_stat FLOAT",
        "ALTER TABLE v3_error_stats ADD COLUMN IF NOT EXISTS bias_gate_passed BOOLEAN",
        "ALTER TABLE v3_error_stats ADD COLUMN IF NOT EXISTS bias_suppressed_reason VARCHAR(200)",
        # V3 Phase 3 — two-component fields on v3_prediction_snapshots.
        # bias_applied tracks whether the bias gate passed for this prediction.
        # bias_suppressed_reason explains why bias was NOT applied (gate failed).
        "ALTER TABLE v3_prediction_snapshots ADD COLUMN IF NOT EXISTS bias_applied BOOLEAN",
        "ALTER TABLE v3_prediction_snapshots ADD COLUMN IF NOT EXISTS bias_suppressed_reason VARCHAR(200)",
        # Prospective comparison linkage — added to both paper trade tables so
        # all three strategies can reference the same ComparisonSnapshot row.
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS comparison_snapshot_id VARCHAR(36)",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS collection_batch_id VARCHAR(36)",
        "ALTER TABLE v3_paper_trades ADD COLUMN IF NOT EXISTS comparison_snapshot_id VARCHAR(36)",
        "ALTER TABLE v3_paper_trades ADD COLUMN IF NOT EXISTS collection_batch_id VARCHAR(36)",
        # Official Trade Eligibility hardening pass — three new columns per trade table
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS eligibility_status VARCHAR(20)",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS eligibility_reason VARCHAR(60)",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS quote_age_seconds FLOAT",
        "ALTER TABLE v3_paper_trades ADD COLUMN IF NOT EXISTS eligibility_status VARCHAR(20)",
        "ALTER TABLE v3_paper_trades ADD COLUMN IF NOT EXISTS eligibility_reason VARCHAR(60)",
        "ALTER TABLE v3_paper_trades ADD COLUMN IF NOT EXISTS quote_age_seconds FLOAT",
        # Safety hardening pass 2 — market close time and decision audit fields
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS market_close_timestamp TIMESTAMPTZ",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS expected_settlement_timestamp TIMESTAMPTZ",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS decision_timestamp TIMESTAMPTZ",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS minutes_to_market_close FLOAT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS settlement_timezone VARCHAR(100)",
        "ALTER TABLE v3_paper_trades ADD COLUMN IF NOT EXISTS market_close_timestamp TIMESTAMPTZ",
        "ALTER TABLE v3_paper_trades ADD COLUMN IF NOT EXISTS expected_settlement_timestamp TIMESTAMPTZ",
        "ALTER TABLE v3_paper_trades ADD COLUMN IF NOT EXISTS decision_timestamp TIMESTAMPTZ",
        "ALTER TABLE v3_paper_trades ADD COLUMN IF NOT EXISTS minutes_to_market_close FLOAT",
        "ALTER TABLE v3_paper_trades ADD COLUMN IF NOT EXISTS settlement_timezone VARCHAR(100)",
    ]
    for stmt in migrations:
        try:
            await conn.execute(text(stmt))
        except Exception as exc:
            logger.warning("Migration skipped (%s): %s", stmt[:60], exc)


async def repair_stale_parse_failures() -> int:
    """
    Re-run city extraction on stored parsing_failure markets using their
    event_ticker field (always stored, even when series_ticker was empty).
    Returns the number of markets repaired. Safe to call on every startup.
    """
    if AsyncSessionLocal is None:
        return 0
    from app.models import KalshiMarket  # local import – models depend on Base
    from app.services.kalshi import extract_city  # local import – avoids circular

    repaired = 0
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(KalshiMarket).where(KalshiMarket.parsing_status == "parsing_failure")
        )
        markets = result.scalars().all()
        for m in markets:
            raw = {
                "ticker": m.ticker,
                "series_ticker": "",
                "event_ticker": m.event_ticker or "",
                "title": m.title or "",
                "subtitle": m.subtitle or "",
            }
            city, _lat, _lon = extract_city(raw)
            if city:
                m.city = city
                m.parsing_status = "collected"
                m.parsing_reason = None
                repaired += 1
        if repaired:
            await session.commit()
            logger.info(
                "Startup repair: resolved city for %d previously-failed market(s).", repaired
            )
    return repaired


async def init_db() -> None:
    global engine, AsyncSessionLocal
    settings = get_settings()
    db_url, connect_args = settings.get_async_db_url()
    logger.info("Connecting to database…")
    engine = create_async_engine(
        db_url, echo=False, pool_pre_ping=True, connect_args=connect_args
    )
    AsyncSessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    # Create all tables if they don't exist yet
    from app import models  # noqa: F401 – ensure models are registered
    from app import models_comparison  # noqa: F401 – register ComparisonSnapshot table
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _apply_migrations(conn)
    # Repair any markets that failed city extraction in a previous run
    # because the Kalshi API omitted series_ticker from the response body.
    await repair_stale_parse_failures()
    # V3: seed feature flags (idempotent — only inserts if missing)
    from app.services.v3_flags import ensure_v3_feature_flags
    # V2.2: seed feature flags (idempotent — only inserts if missing)
    from app.services.paper_trading_v22 import ensure_v22_feature_flags
    async with AsyncSessionLocal() as session:
        await ensure_v3_feature_flags(session)
        await ensure_v22_feature_flags(session)
        await session.commit()
    # Upgrade any feature flags that exist in the DB as "false" but should be
    # active in the current paper-trading system.  This is idempotent and safe
    # to run on every startup — it only writes rows whose values are not already
    # "true".  v3.ingestion_enabled is intentionally excluded (Phase-1 data
    # pipeline, managed separately via the audit UI).
    async with AsyncSessionLocal() as session:
        await _enable_required_flags(session)
        await session.commit()
    # Mark any job_runs still stuck in "running" as failed.  These are orphans
    # left by a previous process that was killed mid-job (e.g. a deployment).
    # Without this cleanup the /jobs/collect endpoint returns the stale row
    # instead of starting a fresh collection.
    async with AsyncSessionLocal() as session:
        await _cleanup_orphaned_jobs(session)
        await session.commit()
    logger.info("Database ready.")


_REQUIRED_ACTIVE_FLAGS: tuple[str, ...] = (
    "v2.2.predictions_enabled",
    "v2.2.paper_trading_enabled",
    "v3.validation_enabled",
    "v3.predictions_enabled",
    "v3.paper_trading_enabled",
)


async def _enable_required_flags(session: AsyncSession) -> None:
    """
    Ensure that paper-trading feature flags that must be active are set to
    'true'.  This upgrades rows that were seeded as 'false' by an older
    deployment.  v3.ingestion_enabled is deliberately omitted — it is managed
    through the V3 audit UI and must not be auto-enabled.
    """
    from app.models import AppSetting  # local import to avoid circular deps at module level

    for key in _REQUIRED_ACTIVE_FLAGS:
        result = await session.execute(
            select(AppSetting).where(AppSetting.key == key)
        )
        row = result.scalar_one_or_none()
        if row is None:
            session.add(AppSetting(key=key, value="true"))
            logger.info("Startup: inserted flag %s = 'true'", key)
        elif (row.value or "").lower() != "true":
            old_val = row.value
            row.value = "true"
            logger.info("Startup: upgraded flag %s → 'true' (was %r)", key, old_val)


async def _cleanup_orphaned_jobs(session: AsyncSession) -> None:
    """
    Mark any job_runs still in 'running' state as 'failed'.  These are orphans
    left by a process that was killed mid-job (e.g. a deployment restart).
    Without this cleanup the /jobs/collect endpoint returns the stale row
    and refuses to start a fresh collection.
    """
    from app.models import JobRun  # local import to avoid circular deps

    result = await session.execute(select(JobRun).where(JobRun.status == "running"))
    orphans = result.scalars().all()
    for job in orphans:
        job.status = "failed"
        job.error_message = "Orphaned – process was restarted before this job completed."
        logger.info("Startup: marked orphaned job %d as failed (started %s)", job.id, job.started_at)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    if AsyncSessionLocal is None:
        raise RuntimeError("Database not initialised – call init_db() first.")
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
