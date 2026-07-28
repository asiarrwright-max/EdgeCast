"""
Paper Trades API router — Phase 3A / 3B.

Endpoints:
  GET  /paper-trades               List trades (with filters)
  GET  /paper-trades/metrics       Aggregated summary statistics
  GET  /paper-trades/analytics     Performance breakdowns + realistic results
  GET  /paper-trades/calibration   Probability calibration report + Brier score
  POST /paper-trades/settle-now    Trigger immediate settlement pass
  GET  /paper-trades/settings      Current paper-trading settings (admin only)
  PUT  /paper-trades/settings      Update settings (admin only)
  GET  /paper-trades/export.csv    CSV export of filtered trade list
  GET  /paper-trades/{id}          Single trade detail

All endpoints require authentication.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import KalshiMarket, PaperTrade, PredictionSnapshot
from app.services.paper_trading import (
    FLAG_DESCRIPTIONS,
    edge_bucket,
    get_calibration_report,
    get_paper_trade_analytics,
    get_paper_trade_metrics,
    get_paper_trade_settings,
    price_bucket,
    save_paper_trade_settings,
)
from app.services.paper_trading_v2 import (
    V2_FLAG_DESCRIPTIONS,
    get_strategy_agreement,
    get_v2_settings,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["paper-trades"])


# ── Serialisation helper ──────────────────────────────────────────────────────

def _trade_to_dict(t: PaperTrade) -> dict[str, Any]:
    flags: list[str] = t.quality_flags or []
    all_flag_descs = {**FLAG_DESCRIPTIONS, **V2_FLAG_DESCRIPTIONS}
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
        "leadTimeDays": t.lead_time_days,
        "status": t.status,
        "kalshiResult": t.kalshi_result,
        "outcome": t.outcome,
        "settlementTimestamp": t.settlement_timestamp.isoformat() if t.settlement_timestamp else None,
        "grossPayout": t.gross_payout,
        "profitLoss": t.profit_loss,
        "returnPct": t.return_pct,
        "decisionExplanation": t.decision_explanation,
        "warnings": t.warnings,
        "qualityFlags": flags,
        "isFlagged": bool(flags),
        "qualityFlagDescriptions": {f: all_flag_descs.get(f, f) for f in flags},
        # v2 engine metadata (None for v1 trades)
        "sigmaUsed": t.sigma_used,
        "biasCorrection": t.bias_correction,
        "fallbackLevel": t.fallback_level,
        "calibrationAdj": t.calibration_adj,
    }


# ── Settings schema ───────────────────────────────────────────────────────────

class PaperTradeSettingsUpdate(BaseModel):
    enabled: bool | None = None
    min_edge_pct: float | None = None
    min_confidence: str | None = None
    stake: float | None = None
    strategy_version: str | None = None


# ── Shared filter logic ───────────────────────────────────────────────────────

def _matches_filters(
    t: PaperTrade,
    status_filter: str | None,
    direction: str | None,
    confidence: str | None,
    city: str | None,
    contract_type: str | None,
    date_from: str | None,
    date_to: str | None,
    strategy_version: str | None,
    edge_bucket_filter: str | None,
    price_bucket_filter: str | None,
    is_flagged: bool | None,
    outcome: str | None,
) -> bool:
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
    if strategy_version and t.strategy_version != strategy_version:
        return False
    if edge_bucket_filter and edge_bucket(t.edge_pct_points) != edge_bucket_filter:
        return False
    if price_bucket_filter and price_bucket(t.side_market_price) != price_bucket_filter:
        return False
    if is_flagged is not None:
        trade_is_flagged = bool(t.quality_flags)
        if trade_is_flagged != is_flagged:
            return False
    if outcome and t.outcome != outcome.upper():
        return False
    return True


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/paper-trades/comparison")
async def get_comparison(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Side-by-side v1 vs v2 metrics comparison.
    Returns both strategy summaries and their calibration reports.
    """
    v1_metrics = await get_paper_trade_metrics(db, strategy_version="v1.0")
    v2_metrics = await get_paper_trade_metrics(db, strategy_version="v2.0")
    v1_calib = await get_calibration_report(db, strategy_version="v1.0")
    v2_calib = await get_calibration_report(db, strategy_version="v2.0")
    v2_settings = await get_v2_settings(db)
    return {
        "v1": {**v1_metrics, "calibration": v1_calib},
        "v2": {**v2_metrics, "calibration": v2_calib},
        "v2Settings": v2_settings,
    }


@router.get("/paper-trades/agreement")
async def get_agreement(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Strategy v1 vs v2 agreement/divergence summary."""
    return await get_strategy_agreement(db)


@router.post("/paper-trades/run-verification")
async def run_verification(
    _user: dict = Depends(get_current_user),
):
    """
    Trigger an immediate forecast verification pass (fetch actuals + recompute error stats).
    Normally runs every 24 hours automatically.
    """
    from app.database import AsyncSessionLocal
    from app.services.forecast_verifier import (
        fetch_and_store_verifications,
        recompute_error_stats,
    )
    if AsyncSessionLocal is None:
        return {"error": "Database not initialised"}
    async with AsyncSessionLocal() as session:
        vstats = await fetch_and_store_verifications(session)
        estats = await recompute_error_stats(session)
    return {
        "verifications": vstats,
        "errorStats": estats,
    }


@router.get("/paper-trades/metrics")
async def get_metrics(
    strategy_version: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Aggregated summary statistics. Pass strategy_version to scope to one version."""
    return await get_paper_trade_metrics(db, strategy_version=strategy_version)


@router.get("/paper-trades/analytics")
async def get_analytics(
    strategy_version: str | None = Query(default=None),
    include_flagged: bool = Query(default=True),
    fee_pct: float = Query(default=0.0, ge=0.0, le=100.0),
    slippage_pct: float = Query(default=0.0, ge=0.0, le=100.0),
    spread_adj: float = Query(default=0.0, ge=0.0, le=100.0),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Performance breakdowns by direction, edge bucket, price bucket, city,
    contract type, and lead time, plus cumulative and daily P/L series.

    Optional realistic-result adjustments (fee_pct, slippage_pct, spread_adj
    expressed as % of stake) produce adjProfitLoss / adjRoi alongside raw figures.
    These are simplified model approximations — not guaranteed real-world performance.
    """
    return await get_paper_trade_analytics(
        db,
        strategy_version=strategy_version,
        include_flagged=include_flagged,
        fee_pct=fee_pct,
        slippage_pct=slippage_pct,
        spread_adj=spread_adj,
    )


@router.get("/paper-trades/calibration")
async def get_calibration(
    strategy_version: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Probability calibration report comparing EdgeCast YES-probability estimates
    against actual Kalshi settlement outcomes, plus overall Brier score.
    Uses only settled (non-void) trades.
    """
    return await get_calibration_report(db, strategy_version=strategy_version)


@router.post("/paper-trades/settle-now")
async def settle_now(
    _user: dict = Depends(get_current_user),
):
    """
    Trigger an immediate settlement check for all open paper trades.
    Normally runs automatically every 3 hours; this allows a manual pass.
    """
    from app.services.settlement import run_settlement_job
    stats = await run_settlement_job()
    return {
        "checked": stats.get("checked", 0),
        "settled": stats.get("settled", 0),
        "voided": stats.get("voided", 0),
        "pendingSettlement": stats.get("pending_settlement", 0),
        "errors": stats.get("errors", 0),
        "stillOpen": stats.get("still_open", 0),
    }


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
    Changing strategy_version causes future trades to be created under the new version;
    existing v1 trades are permanently preserved under their original version.
    """
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
    strategy_version: str | None = Query(default=None),
    edge_bucket_filter: str | None = Query(default=None, alias="edge_bucket"),
    price_bucket_filter: str | None = Query(default=None, alias="price_bucket"),
    is_flagged: bool | None = Query(default=None),
    outcome: str | None = Query(default=None),
    limit: int = Query(default=200, le=1000),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    List paper trades with optional filters.

    Filter params:
      status           OPEN | SETTLED | VOID | ERROR
      direction        YES | NO
      confidence       confidence label substring
      city             city name substring
      contract_type    threshold | range | hourly_threshold
      date_from/to     ISO date — filter by target_settlement_date
      strategy_version exact match (e.g. v1.0)
      edge_bucket      <10pp | 10-20pp | 20-30pp | 30-40pp | ≥40pp
      price_bucket     1-5¢ | 6-15¢ | 16-30¢ | 31-50¢ | >50¢
      is_flagged       true | false
      outcome          WIN | LOSS | VOID
    """
    q = select(PaperTrade).order_by(PaperTrade.created_at.desc()).limit(limit)
    result = await db.execute(q)
    trades = result.scalars().all()

    filtered = [
        _trade_to_dict(t) for t in trades
        if _matches_filters(
            t, status_filter, direction, confidence, city, contract_type,
            date_from, date_to, strategy_version, edge_bucket_filter,
            price_bucket_filter, is_flagged, outcome,
        )
    ]
    return {"trades": filtered, "total": len(filtered)}


@router.get("/paper-trades/export.csv")
async def export_paper_trades(
    status_filter: str | None = Query(default=None, alias="status"),
    direction: str | None = Query(default=None),
    confidence: str | None = Query(default=None),
    city: str | None = Query(default=None),
    contract_type: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    strategy_version: str | None = Query(default=None),
    edge_bucket_filter: str | None = Query(default=None, alias="edge_bucket"),
    price_bucket_filter: str | None = Query(default=None, alias="price_bucket"),
    is_flagged: bool | None = Query(default=None),
    outcome: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """CSV export of filtered paper trades."""
    q = select(PaperTrade).order_by(PaperTrade.created_at.desc())
    result = await db.execute(q)
    trades = result.scalars().all()

    filtered = [
        t for t in trades
        if _matches_filters(
            t, status_filter, direction, confidence, city, contract_type,
            date_from, date_to, strategy_version, edge_bucket_filter,
            price_bucket_filter, is_flagged, outcome,
        )
    ]

    CSV_FIELDS = [
        "id", "createdAt", "marketTicker", "city", "strategyVersion",
        "direction", "status", "outcome", "contractType", "targetSettlementDate",
        "ecYesProbability", "marketYesProbability", "sideMarketPrice", "edgePctPoints",
        "confidenceLabel", "stake", "quantity", "leadTimeDays",
        "grossPayout", "profitLoss", "returnPct", "settlementTimestamp",
        "kalshiResult", "isFlagged", "qualityFlags",
    ]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for t in filtered:
        row = _trade_to_dict(t)
        # Flatten list fields for CSV
        row["qualityFlags"] = "|".join(row.get("qualityFlags") or [])
        writer.writerow(row)

    buf.seek(0)
    filename = f"paper_trades_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/paper-trades/{trade_id}")
async def get_paper_trade(
    trade_id: int,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Full detail for a single paper trade, including linked snapshot and market data."""
    trade_q = await db.execute(
        select(PaperTrade).where(PaperTrade.id == trade_id)
    )
    trade = trade_q.scalar_one_or_none()
    if trade is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found")

    result = _trade_to_dict(trade)

    # Attach linked market info (prices at time of collection — best approximation of entry)
    market_q = await db.execute(
        select(KalshiMarket).where(KalshiMarket.ticker == trade.market_ticker)
    )
    market = market_q.scalar_one_or_none()
    if market:
        result["market"] = {
            "title": market.title,
            "subtitle": market.subtitle,
            "targetDate": market.target_date,
            "openTime": market.open_time.isoformat() if market.open_time else None,
            "closeTime": market.close_time.isoformat() if market.close_time else None,
            "yesBid": market.yes_bid,
            "yesAsk": market.yes_ask,
            "noBid": market.no_bid,
            "noAsk": market.no_ask,
            "volume": market.volume,
            "weatherMarketType": market.weather_market_type,
            "collectionTimestamp": market.collection_timestamp.isoformat() if market.collection_timestamp else None,
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
                "forecastDate": snap.forecast_date,
                "forecastValue": snap.forecast_value,
                "forecastRetrievedAt": snap.forecast_retrieved_at.isoformat() if snap.forecast_retrieved_at else None,
                "leadTimeDays": snap.lead_time_days,
                "ecProbability": snap.ec_probability,
                "marketProbability": snap.market_probability,
                "confidence": snap.confidence,
                "explanation": snap.explanation,
                "settlementVariable": snap.settlement_variable,
                "settlementOperator": snap.settlement_operator,
                "settlementThreshold": snap.settlement_threshold,
                "contractType": snap.contract_type,
                "targetHour": snap.target_hour,
                "targetTimezoneStr": snap.target_timezone_str,
                "lowerBound": snap.lower_bound,
                "upperBound": snap.upper_bound,
                "analysisStatus": snap.analysis_status,
                "analysisReason": snap.analysis_reason,
            }
        else:
            result["snapshot"] = None
    else:
        result["snapshot"] = None

    return result
