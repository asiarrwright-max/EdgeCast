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
        "parsingStatus": m.parsing_status,
        "parsingReason": m.parsing_reason,
        "weatherMarketType": m.weather_market_type,
        "collectionTimestamp": m.collection_timestamp.isoformat() if m.collection_timestamp else None,
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


def _job_to_dict(j: JobRun) -> dict:
    return {
        "id": j.id,
        "jobType": j.job_type,
        "startedAt": j.started_at.isoformat(),
        "completedAt": j.completed_at.isoformat() if j.completed_at else None,
        "status": j.status,
        "marketsFound": j.markets_found,
        "marketsSkipped": j.markets_skipped,
        "marketsRejected": j.markets_rejected,
        "forecastsRetrieved": j.forecasts_retrieved,
        "durationSeconds": j.duration_seconds,
        "errorMessage": j.error_message,
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

    active_markets = [m for m in markets if m.parsing_status != "parsing_failure"]
    markets_with_weather = sum(1 for m in active_markets if m.weather_matched)
    markets_collected = sum(1 for m in markets if m.parsing_status == "collected")
    markets_parse_failures = sum(1 for m in markets if m.parsing_status == "parsing_failure")

    # Build a human-readable summary when zero markets collected
    collection_summary: str | None = None
    if last_job and last_job.status == "success" and (last_job.markets_found or 0) == 0:
        collection_summary = (
            "Collection ran successfully but found no active Kalshi weather markets. "
            "This can happen outside active trading hours or if Kalshi has restructured its series."
        )

    return {
        "totalActiveMarkets": len(active_markets),
        "marketsWithWeather": markets_with_weather,
        "marketsCollected": markets_collected,
        "marketsParseFailures": markets_parse_failures,
        "lastCollectionTime": (
            last_job.completed_at.isoformat()
            if last_job and last_job.completed_at
            else None
        ),
        "lastCollectionStatus": last_job.status if last_job else None,
        "lastCollectionDuration": last_job.duration_seconds if last_job else None,
        "lastCollectionMarketsFound": last_job.markets_found if last_job else None,
        "lastCollectionMarketsSkipped": last_job.markets_skipped if last_job else None,
        "collectionSummary": collection_summary,
        "markets": [_market_to_dict(m) for m in markets],
        "recentErrors": [_error_to_dict(e) for e in recent_errors],
        "lastJob": _job_to_dict(last_job) if last_job else None,
    }
