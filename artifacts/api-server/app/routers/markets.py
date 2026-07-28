from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import KalshiMarket

router = APIRouter(tags=["markets"])


def _to_dict(m: KalshiMarket) -> dict:
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


@router.get("/markets")
async def get_markets(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(KalshiMarket)
        .order_by(KalshiMarket.close_time.asc().nullslast())
    )
    markets = result.scalars().all()

    if not markets:
        return {
            "markets": [],
            "summary": (
                "No markets have been collected yet. "
                "Use the dashboard to trigger a data collection run."
            ),
        }

    collected = [m for m in markets if m.parsing_status == "collected"]
    failures = [m for m in markets if m.parsing_status == "parsing_failure"]

    summary: str | None = None
    if not collected and failures:
        summary = (
            f"{len(failures)} weather market(s) were found but could not be matched to a city. "
            "Weather forecast data cannot be retrieved without a known city."
        )
    elif not collected:
        summary = (
            "Markets were returned by Kalshi but none matched weather criteria. "
            "Check the Jobs page for collection details."
        )

    return {
        "markets": [_to_dict(m) for m in markets],
        "summary": summary,
    }


@router.get("/markets/{ticker}")
async def get_market(
    ticker: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(KalshiMarket).where(KalshiMarket.ticker == ticker)
    )
    market = result.scalar_one_or_none()
    if not market:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Market not found")
    return _to_dict(market)
