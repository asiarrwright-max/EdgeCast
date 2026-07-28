"""
Analysis router — returns EdgeCast probability analysis for markets.

GET /api/analysis/{ticker}         latest snapshot for one market
GET /api/analysis/{ticker}/history all snapshots for one market (newest first)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import PredictionSnapshot

router = APIRouter(tags=["analysis"])


def _snap_to_dict(s: PredictionSnapshot) -> dict:
    return {
        "id": s.id,
        "marketTicker": s.market_ticker,
        "createdAt": s.created_at.isoformat() if s.created_at else None,
        "forecastDate": s.forecast_date,
        "forecastValue": s.forecast_value,
        "forecastRetrievedAt": (
            s.forecast_retrieved_at.isoformat() if s.forecast_retrieved_at else None
        ),
        "leadTimeDays": s.lead_time_days,
        "settlementVariable": s.settlement_variable,
        "settlementOperator": s.settlement_operator,
        "settlementThreshold": s.settlement_threshold,
        "ecProbability": s.ec_probability,
        "marketProbability": s.market_probability,
        "confidence": s.confidence,
        "explanation": s.explanation,
        "analysisStatus": s.analysis_status,
        "analysisReason": s.analysis_reason,
    }


@router.get("/analysis/{ticker}")
async def get_analysis(
    ticker: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    q = await db.execute(
        select(PredictionSnapshot)
        .where(PredictionSnapshot.market_ticker == ticker)
        .order_by(PredictionSnapshot.created_at.desc())
        .limit(1)
    )
    snap = q.scalar_one_or_none()
    if snap is None:
        raise HTTPException(status_code=404, detail=f"No analysis found for {ticker}")
    return _snap_to_dict(snap)


@router.get("/analysis/{ticker}/history")
async def get_analysis_history(
    ticker: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    q = await db.execute(
        select(PredictionSnapshot)
        .where(PredictionSnapshot.market_ticker == ticker)
        .order_by(PredictionSnapshot.created_at.desc())
        .limit(limit)
    )
    snaps = q.scalars().all()
    return {"snapshots": [_snap_to_dict(s) for s in snaps], "total": len(snaps)}
