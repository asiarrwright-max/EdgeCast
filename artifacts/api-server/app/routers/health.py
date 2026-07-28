from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db, engine
from app.models import AppError, JobRun, KalshiMarket, WeatherForecast
from app.scheduler import get_scheduler_status
from app.services.kalshi import check_kalshi_health
from app.services.openmeteo import check_openmeteo_health

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def health_check():
    return {"status": "ok"}


@router.get("/health/services")
async def get_service_health(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    now = datetime.now(timezone.utc).isoformat()

    # External API checks
    kalshi, openmeteo = await _parallel_checks()

    # Database status
    db_status = await _check_db_status(now)

    # Scheduler status
    sched = get_scheduler_status()
    scheduler_status = {
        "name": "Scheduler",
        "status": "ok" if sched["running"] else "error",
        "message": sched["message"],
        "lastChecked": now,
    }

    # Collection history
    last_success_q = await db.execute(
        select(JobRun)
        .where(JobRun.status == "success")
        .order_by(JobRun.completed_at.desc())
        .limit(1)
    )
    last_success = last_success_q.scalar_one_or_none()

    last_fail_q = await db.execute(
        select(JobRun)
        .where(JobRun.status == "failed")
        .order_by(JobRun.completed_at.desc())
        .limit(1)
    )
    last_fail = last_fail_q.scalar_one_or_none()

    # Aggregate counts
    total_markets_q = await db.execute(select(func.count()).select_from(KalshiMarket))
    total_markets = total_markets_q.scalar() or 0

    total_forecasts_q = await db.execute(select(func.count()).select_from(WeatherForecast))
    total_forecasts = total_forecasts_q.scalar() or 0

    return {
        "services": [kalshi, openmeteo, db_status, scheduler_status],
        "lastSuccessfulCollection": (
            last_success.completed_at.isoformat() if last_success and last_success.completed_at else None
        ),
        "lastFailedCollection": (
            last_fail.completed_at.isoformat() if last_fail and last_fail.completed_at else None
        ),
        "totalMarketsStored": total_markets,
        "totalForecastsStored": total_forecasts,
    }


async def _parallel_checks():
    import asyncio
    return await asyncio.gather(check_kalshi_health(), check_openmeteo_health())


async def _check_db_status(now: str) -> dict:
    try:
        if engine is None:
            return {"name": "Database", "status": "error", "message": "Engine not initialised", "lastChecked": now}
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy", fromlist=["text"]).text("SELECT 1"))
        return {"name": "Database", "status": "ok", "message": None, "lastChecked": now}
    except Exception as exc:
        return {"name": "Database", "status": "error", "message": str(exc)[:200], "lastChecked": now}
