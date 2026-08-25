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

# ---------------------------------------------------------------------------
# Diagnosis codes and plain-language descriptions
# ---------------------------------------------------------------------------

_PLAIN_LANGUAGE: dict[str, str] = {
    "NO_TRADES": (
        "No V3 paper trades exist in the database. "
        "V3 paper trading may be disabled (check v3.paper_trading_enabled and "
        "paper_trading_v3.enabled flags) or no eligible signals have been generated yet."
    ),
    "NO_SETTLEMENTS_YET_NOT_DUE": (
        "V3 paper trades exist but none have passed their expected settlement time yet. "
        "Zero settlements is expected at this stage — no action required."
    ),
    "DUE_UNSETTLED_REQUIRES_INVESTIGATION": (
        "One or more V3 trades are past their expected settlement time and have not been "
        "settled. The settlement scheduler appears to be running. Investigate: Kalshi API "
        "reachability, ticker format compatibility, and decision_explanation on error samples."
    ),
    "DUE_UNSETTLED_SCHEDULER_NOT_RUNNING": (
        "One or more V3 trades are past their expected settlement time and the settlement "
        "scheduler is NOT running. Restart the scheduler to resume V3 settlement processing."
    ),
    "ALL_ERRORS": (
        "All settled-eligible V3 trades are in ERROR status. This likely indicates a "
        "Kalshi API compatibility issue: 404 responses for every ticker suggest the market "
        "ticker format does not match the Kalshi endpoint. Verify ticker format and "
        "fetch_kalshi_market() URL construction."
    ),
    "SETTLEMENTS_PRESENT": (
        "V3 settlement is functioning normally — settled trades are present."
    ),
}


def _health_summary(
    trades: list[V3PaperTrade],
    now: datetime | None = None,
    scheduler_running: bool | None = None,
) -> dict:
    """
    Build a plain-language settlement health summary.

    Parameters
    ----------
    trades:
        All V3PaperTrade rows to summarise (not filtered by caller).
    now:
        Reference timestamp; defaults to UTC now. Inject for deterministic tests.
    scheduler_running:
        True/False when the caller knows the scheduler state; None when unknown.
        Used to refine the diagnosis when due-unsettled trades are present.
    """
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
    expected_ts = [t.expected_settlement_timestamp for t in trades if t.expected_settlement_timestamp is not None]
    errors = [t for t in trades if t.status == "ERROR"]

    # Terminal non-SETTLED/non-VOID trades: useful to detect all-error patterns
    terminal_checked = statuses.get("ERROR", 0) + statuses.get("SETTLED", 0) + statuses.get("VOID", 0)
    all_errors = (
        terminal_checked > 0
        and statuses.get("ERROR", 0) == terminal_checked
        and statuses.get("SETTLED", 0) == 0
        and statuses.get("VOID", 0) == 0
    )

    if not trades:
        diagnosis = "NO_TRADES"
    elif due_unsettled and scheduler_running is False:
        diagnosis = "DUE_UNSETTLED_SCHEDULER_NOT_RUNNING"
    elif due_unsettled:
        diagnosis = "DUE_UNSETTLED_REQUIRES_INVESTIGATION"
    elif all_errors:
        diagnosis = "ALL_ERRORS"
    elif statuses.get("SETTLED", 0) == 0:
        diagnosis = "NO_SETTLEMENTS_YET_NOT_DUE"
    else:
        diagnosis = "SETTLEMENTS_PRESENT"

    return {
        "diagnosis": diagnosis,
        "plain_language_summary": _PLAIN_LANGUAGE.get(diagnosis, diagnosis),
        "scheduler_running": scheduler_running,
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
        "earliest_expected_settlement": min(expected_ts) if expected_ts else None,
        "latest_expected_settlement": max(expected_ts) if expected_ts else None,
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
    """
    Explain why V3 settlements are zero (or not).

    Distinguishes: NO_TRADES, NO_SETTLEMENTS_YET_NOT_DUE,
    DUE_UNSETTLED_REQUIRES_INVESTIGATION, DUE_UNSETTLED_SCHEDULER_NOT_RUNNING,
    ALL_ERRORS, and SETTLEMENTS_PRESENT.
    Includes scheduler running status and a plain-language summary.
    """
    from app.scheduler import get_scheduler_status
    sched = get_scheduler_status()
    raw = sched.get("running")
    scheduler_running: bool | None = bool(raw) if raw is not None else None

    result = await session.execute(select(V3PaperTrade).order_by(V3PaperTrade.created_at.asc()))
    return _health_summary(list(result.scalars().all()), scheduler_running=scheduler_running)
