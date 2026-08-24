"""Read-only V3 paper-trade settlement health diagnostics.

GREEN observability only: this module never mutates trades or changes settlement,
eligibility, forecasting, calibration, or execution semantics.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models_v3 import V3PaperTrade

router = APIRouter(tags=["V3 Analytics"])


def _health_summary(trades: list[V3PaperTrade], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    statuses = Counter((t.status or "UNKNOWN") for t in trades)
    eligibility = Counter((t.eligibility_status or "UNCLASSIFIED") for t in trades)
    due_unsettled = []
    for t in trades:
        if t.status not in ("OPEN", "PENDING_SETTLEMENT"):
            continue
        expected = t.expected_settlement_timestamp
        if expected is not None:
            if expected.tzinfo is None:
                expected = expected.replace(tzinfo=timezone.utc)
            if expected <= now:
                due_unsettled.append(t)

    created = [t.created_at for t in trades if t.created_at is not None]
    expected = [t.expected_settlement_timestamp for t in trades if t.expected_settlement_timestamp is not None]
    errors = [t for t in trades if t.status == "ERROR"]

    if not trades:
        diagnosis = "NO_TRADES"
    elif due_unsettled:
        diagnosis = "DUE_UNSETTLED_REQUIRES_INVESTIGATION"
    elif statuses.get("SETTLED", 0) == 0:
        diagnosis = "NO_SETTLEMENTS_YET_NOT_DUE"
    else:
        diagnosis = "SETTLEMENTS_PRESENT"

    return {
        "diagnosis": diagnosis,
        "total": len(trades),
        "by_status": dict(sorted(statuses.items())),
        "by_eligibility": dict(sorted(eligibility.items())),
        "official": eligibility.get("OFFICIAL", 0),
        "research_only": eligibility.get("RESEARCH_ONLY", 0),
        "legacy": eligibility.get("LEGACY", 0),
        "due_unsettled_count": len(due_unsettled),
        "due_unsettled": [
            {
                "id": t.id,
                "market_ticker": t.market_ticker,
                "status": t.status,
                "eligibility_status": t.eligibility_status,
                "expected_settlement_timestamp": t.expected_settlement_timestamp,
            }
            for t in due_unsettled[:50]
        ],
        "error_count": len(errors),
        "error_samples": [
            {"id": t.id, "market_ticker": t.market_ticker, "decision_explanation": t.decision_explanation}
            for t in errors[:20]
        ],
        "oldest_trade_created_at": min(created) if created else None,
        "newest_trade_created_at": max(created) if created else None,
        "earliest_expected_settlement": min(expected) if expected else None,
        "latest_expected_settlement": max(expected) if expected else None,
        "observed_at": now,
        "safety": {
            "read_only": True,
            "historical_outcomes_modified": False,
            "real_money_execution_enabled": False,
        },
    }


@router.get("/analytics/v3/settlement-health")
async def get_v3_settlement_health(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Explain whether zero V3 settlements means no trades, not-yet-due trades, or overdue work."""
    result = await session.execute(select(V3PaperTrade).order_by(V3PaperTrade.created_at.asc()))
    return _health_summary(list(result.scalars().all()))
