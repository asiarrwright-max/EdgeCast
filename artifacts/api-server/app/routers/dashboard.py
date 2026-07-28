from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import AppError, JobRun, KalshiMarket

router = APIRouter(tags=["dashboard"])


def _market_to_dict(m: KalshiMarket) -> dict:
    return {
        "id": m.id,
        "ticker": m.ticker,
        "eventTicker": m.event_ticker,
        "title": m.title,
        "subtitle": m.subtitle,
        "city": m.city,
        "targetDate": m.target_date,
        "openTime": m.open_time.isoformat() if m.open_time else None,
        "closeTime": m.close_time.isoformat() if m.close_time else None,
        "status": m.status,
        "yesBid": m.yes_bid,
        "yesAsk": m.yes_ask,
        "noBid": m.no_bid,
        "noAsk": m.no_ask,
        "volume": m.volume,
        "weatherMatched": m.weather_matched,
        "lastUpdated": m.updated_at.isoformat() if m.updated_at else None,
    }


def _error_to_dict(e: AppError) -> dict:
    return {
        "id": e.id,
        "errorType": e.error_type,
        "message": e.message,
        "context": e.context,
        "occurredAt": e.occurred_at.isoformat(),
    }


@router.get("/dashboard")
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    markets_q = await db.execute(
        select(KalshiMarket)
        .where(KalshiMarket.status == "active")
        .order_by(KalshiMarket.close_time.asc().nullslast())
    )
    markets = markets_q.scalars().all()

    job_q = await db.execute(
        select(JobRun).order_by(JobRun.started_at.desc()).limit(1)
    )
    last_job = job_q.scalar_one_or_none()

    errors_q = await db.execute(
        select(AppError).order_by(AppError.occurred_at.desc()).limit(5)
    )
    recent_errors = errors_q.scalars().all()

    markets_with_weather = sum(1 for m in markets if m.weather_matched)

    return {
        "totalActiveMarkets": len(markets),
        "marketsWithWeather": markets_with_weather,
        "lastCollectionTime": (
            last_job.started_at.isoformat() if last_job else None
        ),
        "lastCollectionStatus": last_job.status if last_job else None,
        "markets": [_market_to_dict(m) for m in markets],
        "recentErrors": [_error_to_dict(e) for e in recent_errors],
    }
