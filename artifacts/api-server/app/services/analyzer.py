"""
Analyzer — orchestrates settlement parsing, probability calculation,
and PredictionSnapshot persistence for all active markets.

Called from the collector after every successful collection run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KalshiMarket, PredictionSnapshot, WeatherForecast
from app.services.settlement_parser import parse_settlement
from app.services.probability_engine import run_analysis

logger = logging.getLogger(__name__)


async def analyze_market(
    session: AsyncSession,
    market: KalshiMarket,
) -> PredictionSnapshot | None:
    """
    Run the full analysis pipeline for one market and return a new
    PredictionSnapshot.  Returns None if the market cannot be processed at all.
    """
    # Only analyze temperature markets (the only type we have settlement
    # contracts for in this phase).
    if market.weather_market_type not in ("temperature", None):
        return None  # skip rain/snow/wind

    # Parse the settlement contract
    contract = parse_settlement(market.title or "", market.subtitle)

    # Look up the matching forecast row
    forecast: WeatherForecast | None = None
    target_ds: str | None = None
    if market.city and market.target_date:
        target_ds = str(market.target_date)[:10]
        fc_q = await session.execute(
            select(WeatherForecast).where(
                WeatherForecast.city == market.city,
                WeatherForecast.forecast_date == target_ds,
            ).limit(1)
        )
        forecast = fc_q.scalar_one_or_none()

    # Run the probability engine
    result = run_analysis(
        title=market.title or "",
        subtitle=market.subtitle,
        city=market.city,
        target_date_str=target_ds,
        weather_variable=contract.variable,
        operator=contract.operator,
        threshold=contract.threshold,
        parse_confidence=contract.parse_confidence,
        settlement_status=contract.status,
        unsupported_reason=contract.unsupported_reason,
        forecast_high=forecast.temperature_high if forecast else None,
        forecast_low=forecast.temperature_low if forecast else None,
        forecast_retrieved_at=forecast.retrieved_at if forecast else None,
        yes_bid=market.yes_bid,
        yes_ask=market.yes_ask,
    )

    snapshot = PredictionSnapshot(
        market_ticker=market.ticker,
        created_at=datetime.now(timezone.utc),
        forecast_date=target_ds,
        forecast_value=result.forecast_value,
        forecast_retrieved_at=forecast.retrieved_at if forecast else None,
        lead_time_days=result.lead_time_days,
        settlement_variable=contract.variable,
        settlement_operator=contract.operator,
        settlement_threshold=contract.threshold,
        ec_probability=result.ec_probability,
        market_probability=result.market_probability,
        confidence=result.confidence,
        explanation=result.explanation,
        analysis_status=result.analysis_status,
        analysis_reason=result.analysis_reason,
    )
    session.add(snapshot)
    return snapshot


async def analyze_all_markets(session: AsyncSession) -> dict:
    """
    Analyze every active market and store one PredictionSnapshot per market.
    Returns a summary dict with counts.
    """
    q = await session.execute(
        select(KalshiMarket).where(KalshiMarket.status == "active")
    )
    markets = q.scalars().all()

    supported = 0
    unsupported = 0
    no_forecast = 0
    errors = 0

    for market in markets:
        try:
            snap = await analyze_market(session, market)
            if snap is None:
                continue
            if snap.analysis_status == "supported":
                supported += 1
            elif snap.analysis_status == "no_forecast":
                no_forecast += 1
            else:
                unsupported += 1
        except Exception as exc:
            errors += 1
            logger.warning("Analysis failed for %s: %s", market.ticker, exc)

    await session.commit()

    logger.info(
        "Analysis complete: %d supported, %d unsupported, %d no-forecast, %d errors",
        supported, unsupported, no_forecast, errors,
    )
    return {
        "supported": supported,
        "unsupported": unsupported,
        "no_forecast": no_forecast,
        "errors": errors,
        "total": supported + unsupported + no_forecast,
    }


async def get_latest_snapshot(
    session: AsyncSession, ticker: str
) -> PredictionSnapshot | None:
    """Return the most recent PredictionSnapshot for a market ticker."""
    q = await session.execute(
        select(PredictionSnapshot)
        .where(PredictionSnapshot.market_ticker == ticker)
        .order_by(PredictionSnapshot.created_at.desc())
        .limit(1)
    )
    return q.scalar_one_or_none()
