"""
Paper Trades API router — Phase 3A.

Endpoints:
  GET  /paper-trades           List trades (with filters)
  GET  /paper-trades/metrics   Aggregated summary statistics
  GET  /paper-trades/settings  Current paper-trading settings (admin only)
  PUT  /paper-trades/settings  Update settings (admin only)
  GET  /paper-trades/{id}      Single trade detail

All endpoints require authentication.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import KalshiMarket, PaperTrade, PredictionSnapshot
from app.services.paper_trading import (
    get_paper_trade_metrics,
    get_paper_trade_settings,
    save_paper_trade_settings,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["paper-trades"])


# ── Serialisation helper ──────────────────────────────────────────────────────

def _trade_to_dict(t: PaperTrade) -> dict[str, Any]:
    return {
        "id": t.id,
        "createdAt": t.created_at.isoformat() if t.created_at else None,
        "marketTicker": t.market_ticker,
        "eventTicker": t.event_ticker,
        "city": t.city,
        "weatherVariable": t.weather_variable,
        "contractType": t.contract_type,
        "targetSettlementDate": t.target_settlement_date,
        "snapshotId": t.snapshot_id,
        "strategyVersion": t.strategy_version,
        "direction": t.direction,
        "ecYesProbability": t.ec_yes_probability,
        "ecSideProbability": t.ec_side_probability,
        "marketYesProbability": t.market_yes_probability,
        "sideMarketPrice": t.side_market_price,
        "priceSource": t.price_source,
        "edgePctPoints": t.edge_pct_points,
        "confidenceScore": t.confidence_score,
        "confidenceLabel": t.confidence_label,
        "stake": t.stake,
        "quantity": t.quantity,
        "status": t.status,
        "kalshiResult": t.kalshi_result,
        "outcome": t.outcome,
        "settlementTimestamp": t.settlement_timestamp.isoformat() if t.settlement_timestamp else None,
        "grossPayout": t.gross_payout,
        "profitLoss": t.profit_loss,
        "returnPct": t.return_pct,
        "decisionExplanation": t.decision_explanation,
        "warnings": t.warnings,
    }


# ── Settings schema ───────────────────────────────────────────────────────────

class PaperTradeSettingsUpdate(BaseModel):
    enabled: bool | None = None
    min_edge_pct: float | None = None
    min_confidence: str | None = None
    stake: float | None = None
    strategy_version: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/paper-trades/metrics")
async def get_metrics(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Aggregated summary statistics for all paper trades."""
    return await get_paper_trade_metrics(db)


@router.get("/paper-trades/settings")
async def get_settings(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Return current paper-trading configuration."""
    return await get_paper_trade_settings(db)


@router.put("/paper-trades/settings")
async def update_settings(
    body: PaperTradeSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Update paper-trading settings.
    Changing settings NEVER modifies historical trades.
    """
    # Validate inputs
    if body.min_edge_pct is not None and not (0.0 < body.min_edge_pct <= 100.0):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_edge_pct must be between 0 and 100",
        )
    if body.stake is not None and body.stake <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="stake must be positive",
        )
    if body.min_confidence is not None and body.min_confidence not in (
        "Very High", "High", "Medium", "Low", "Very Low"
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_confidence must be one of: Very High, High, Medium, Low, Very Low",
        )

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    return await save_paper_trade_settings(db, updates)


@router.get("/paper-trades")
async def list_paper_trades(
    status_filter: str | None = Query(default=None, alias="status"),
    direction: str | None = Query(default=None),
    confidence: str | None = Query(default=None),
    city: str | None = Query(default=None),
    contract_type: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    limit: int = Query(default=200, le=500),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    List paper trades with optional filters.

    Filter params:
      status       OPEN | SETTLED | VOID | ERROR
      direction    YES | NO
      confidence   confidence label substring
      city         city name substring
      contract_type  threshold | range | hourly_threshold
      date_from    ISO date — filter by target_settlement_date >=
      date_to      ISO date — filter by target_settlement_date <=
    """
    q = select(PaperTrade).order_by(PaperTrade.created_at.desc()).limit(limit)

    result = await db.execute(q)
    trades = result.scalars().all()

    # Python-side filtering (small dataset; avoids complex SQLAlchemy text queries)
    def _match(t: PaperTrade) -> bool:
        if status_filter and t.status != status_filter.upper():
            return False
        if direction and t.direction != direction.upper():
            return False
        if confidence and (not t.confidence_label or confidence.lower() not in t.confidence_label.lower()):
            return False
        if city and (not t.city or city.lower() not in t.city.lower()):
            return False
        if contract_type and t.contract_type != contract_type:
            return False
        if date_from and (not t.target_settlement_date or t.target_settlement_date < date_from):
            return False
        if date_to and (not t.target_settlement_date or t.target_settlement_date > date_to):
            return False
        return True

    filtered = [_trade_to_dict(t) for t in trades if _match(t)]
    return {"trades": filtered, "total": len(filtered)}


@router.get("/paper-trades/{trade_id}")
async def get_paper_trade(
    trade_id: int,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Full detail for a single paper trade, including the linked prediction snapshot."""
    trade_q = await db.execute(
        select(PaperTrade).where(PaperTrade.id == trade_id)
    )
    trade = trade_q.scalar_one_or_none()
    if trade is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found")

    result = _trade_to_dict(trade)

    # Attach linked market info
    market_q = await db.execute(
        select(KalshiMarket).where(KalshiMarket.ticker == trade.market_ticker)
    )
    market = market_q.scalar_one_or_none()
    if market:
        result["market"] = {
            "title": market.title,
            "subtitle": market.subtitle,
            "targetDate": market.target_date,
            "yesBid": market.yes_bid,
            "yesAsk": market.yes_ask,
            "noBid": market.no_bid,
            "noAsk": market.no_ask,
            "weatherMarketType": market.weather_market_type,
        }
    else:
        result["market"] = None

    # Attach snapshot info
    if trade.snapshot_id:
        snap_q = await db.execute(
            select(PredictionSnapshot).where(PredictionSnapshot.id == trade.snapshot_id)
        )
        snap = snap_q.scalar_one_or_none()
        if snap:
            result["snapshot"] = {
                "id": snap.id,
                "createdAt": snap.created_at.isoformat() if snap.created_at else None,
                "ecProbability": snap.ec_probability,
                "marketProbability": snap.market_probability,
                "confidence": snap.confidence,
                "explanation": snap.explanation,
                "forecastValue": snap.forecast_value,
                "leadTimeDays": snap.lead_time_days,
                "settlementVariable": snap.settlement_variable,
                "settlementOperator": snap.settlement_operator,
                "settlementThreshold": snap.settlement_threshold,
                "contractType": snap.contract_type,
                "lowerBound": snap.lower_bound,
                "upperBound": snap.upper_bound,
            }
        else:
            result["snapshot"] = None
    else:
        result["snapshot"] = None

    return result
