from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import KalshiMarket, PredictionSnapshot

router = APIRouter(tags=["markets"])


def _snap_fields(s: PredictionSnapshot | None) -> dict:
    """Flatten the latest PredictionSnapshot into market dict fields."""
    if s is None:
        return {
            "ecProbability": None,
            "marketProbability": None,
            "probabilityDiff": None,
            "confidence": None,
            "analysisStatus": None,
            "analysisReason": None,
            "explanation": None,
            "settlementVariable": None,
            "settlementOperator": None,
            "settlementThreshold": None,
            "leadTimeDays": None,
            "forecastValue": None,
        }
    diff: float | None = None
    if s.ec_probability is not None and s.market_probability is not None:
        diff = round(s.ec_probability - s.market_probability, 4)
    return {
        "ecProbability": s.ec_probability,
        "marketProbability": s.market_probability,
        "probabilityDiff": diff,
        "confidence": s.confidence,
        "analysisStatus": s.analysis_status,
        "analysisReason": s.analysis_reason,
        "explanation": s.explanation,
        "settlementVariable": s.settlement_variable,
        "settlementOperator": s.settlement_operator,
        "settlementThreshold": s.settlement_threshold,
        "leadTimeDays": s.lead_time_days,
        "forecastValue": s.forecast_value,
    }


def _to_dict(m: KalshiMarket, snap: PredictionSnapshot | None = None) -> dict:
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
        **_snap_fields(snap),
    }


async def _latest_snaps(db: AsyncSession, tickers: list[str]) -> dict[str, PredictionSnapshot]:
    """Return {ticker: latest_snapshot} for the given tickers."""
    if not tickers:
        return {}
    subq = (
        select(
            PredictionSnapshot.market_ticker,
            func.max(PredictionSnapshot.id).label("max_id"),
        )
        .where(PredictionSnapshot.market_ticker.in_(tickers))
        .group_by(PredictionSnapshot.market_ticker)
        .subquery()
    )
    q = await db.execute(
        select(PredictionSnapshot).join(subq, PredictionSnapshot.id == subq.c.max_id)
    )
    return {s.market_ticker: s for s in q.scalars().all()}


@router.get("/markets")
async def get_markets(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(KalshiMarket).order_by(KalshiMarket.close_time.asc().nullslast())
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

    snaps = await _latest_snaps(db, [m.ticker for m in markets])

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
        "markets": [_to_dict(m, snaps.get(m.ticker)) for m in markets],
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

    snaps = await _latest_snaps(db, [ticker])
    return _to_dict(market, snaps.get(ticker))
