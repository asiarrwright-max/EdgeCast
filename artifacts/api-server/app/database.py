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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _apply_migrations(conn)
    # Repair any markets that failed city extraction in a previous run
    # because the Kalshi API omitted series_ticker from the response body.
    await repair_stale_parse_failures()
    logger.info("Database ready.")


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
