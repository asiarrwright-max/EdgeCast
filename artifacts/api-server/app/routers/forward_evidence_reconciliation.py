"""
app/routers/forward_evidence_reconciliation.py
Forward-Evidence Completeness and Reconciliation Diagnostics — read-only.

PURPOSE
-------
Make the remaining readiness evidence gap *measurable* from the deployed
runtime without changing any forecasting, calibration, eligibility,
settlement semantics, readiness thresholds, or trading behaviour.

SAFETY GUARANTEES (enforced, not assumed)
-----------------------------------------
• Read-only: no INSERT / UPDATE / DELETE at any point.
• OFFICIAL / RESEARCH_ONLY / LEGACY populations are reported separately
  and are NEVER pooled.
• NULL eligibility_status rows form their own "UNCLASSIFIED" bucket.
• No readiness threshold is evaluated or activated.
• No fabrication: missing or unjoinable stage data is labelled
  "Unavailable" / "Insufficient data" — never guessed.
• trading_state_modified is always False.
• real_money_execution_enabled is always False.

MISSING-DATA POLICY
-------------------
If a stage (e.g. the prediction/signal count before paper-trade creation)
is not reliably persisted or cannot be joined without risk of double-
counting, the corresponding field is set to None and the
``unavailable_reasons`` list documents the exact missing input.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import PaperTrade

logger = logging.getLogger(__name__)

router = APIRouter(tags=["forward-evidence-reconciliation"])

# ---------------------------------------------------------------------------
# Population constants
# ---------------------------------------------------------------------------
_POP_OFFICIAL = "OFFICIAL"
_POP_RESEARCH = "RESEARCH_ONLY"
_POP_LEGACY = "LEGACY"
_POP_UNCLASSIFIED = "UNCLASSIFIED"

# All known eligibility_status values; NULL maps to _POP_UNCLASSIFIED.
_ALL_POPULATIONS = [_POP_OFFICIAL, _POP_RESEARCH, _POP_LEGACY, _POP_UNCLASSIFIED]

# Lifecycle statuses tracked by PaperTrade.status
_LIFECYCLE_STATUSES = ["OPEN", "SETTLED", "VOID", "PENDING_SETTLEMENT", "ERROR"]


# ---------------------------------------------------------------------------
# Pure helpers — no DB access, fully unit-testable
# ---------------------------------------------------------------------------

def _population_key(eligibility_status: str | None) -> str:
    """Map a raw eligibility_status value to one of the four population keys."""
    if eligibility_status is None:
        return _POP_UNCLASSIFIED
    v = eligibility_status.strip().upper()
    if v in (_POP_OFFICIAL, _POP_RESEARCH, _POP_LEGACY):
        return v
    return _POP_UNCLASSIFIED


def _lifecycle_counts(trades: list[PaperTrade]) -> dict[str, int]:
    """Count trades by lifecycle status; unknown values land in 'OTHER'."""
    counts: dict[str, int] = {s: 0 for s in _LIFECYCLE_STATUSES}
    counts["OTHER"] = 0
    for t in trades:
        s = (t.status or "").upper()
        if s in counts:
            counts[s] += 1
        else:
            counts["OTHER"] += 1
    return counts


def _date_range(trades: list[PaperTrade]) -> dict[str, str | None]:
    """
    Oldest/newest forecast entry date (created_at) and target settlement date.
    Uses stored string fields only; None when the field is absent for all trades.
    """
    created_vals = [
        t.created_at.isoformat() if t.created_at else None
        for t in trades
    ]
    settlement_vals = [t.target_settlement_date for t in trades if t.target_settlement_date]
    created_notnull = [v for v in created_vals if v]
    return {
        "oldest_entry": min(created_notnull) if created_notnull else None,
        "newest_entry": max(created_notnull) if created_notnull else None,
        "oldest_target_settlement_date": min(settlement_vals) if settlement_vals else None,
        "newest_target_settlement_date": max(settlement_vals) if settlement_vals else None,
    }


def _missing_entry_price_count(trades: list[PaperTrade]) -> int:
    """
    Records where side_market_price (the contemporaneous entry price) is NULL.
    Uses the stored field value only — no fabrication.
    """
    return sum(1 for t in trades if t.side_market_price is None)


def _stale_or_missing_quote_count(trades: list[PaperTrade]) -> dict[str, int]:
    """
    Stale/missing quote counts using *only* the stored freshness/quality fields
    (quote_timestamp, side_market_price, quote_age_seconds).

    Stale definition follows the stored quote_age_seconds field: a trade is
    stale when quote_age_seconds > 300 (the existing 300-second freshness
    gate).  The threshold value is READ from stored data semantics; it is
    NOT the protected eligibility gate — this is purely a diagnostic count.
    """
    missing = sum(
        1 for t in trades
        if t.quote_timestamp is None and t.side_market_price is None
    )
    stale = sum(
        1 for t in trades
        if t.quote_age_seconds is not None and t.quote_age_seconds > 300
    )
    return {"missing_quote": missing, "stale_quote": stale}


def _integrity_exception_count(trades: list[PaperTrade]) -> int:
    """
    Count of trades with non-empty quality_flags (settlement/integrity
    exception indicators stored on the trade record).
    Uses the existing JSON column only — no inference.
    """
    return sum(1 for t in trades if t.quality_flags)


def _strategy_summary(trades: list[PaperTrade]) -> list[dict[str, Any]]:
    """
    Per-strategy-version breakdown for one population bucket.
    Returns counts only; does NOT compute win rates or metrics that would
    require interpretation of protected semantics.
    """
    by_strat: dict[str, list[PaperTrade]] = defaultdict(list)
    for t in trades:
        by_strat[t.strategy_version or "unknown"].append(t)

    rows = []
    for strat, strat_trades in sorted(by_strat.items()):
        lifecycle = _lifecycle_counts(strat_trades)
        rows.append({
            "strategy_version": strat,
            "total": len(strat_trades),
            "lifecycle_counts": lifecycle,
            "settled_count": lifecycle["SETTLED"],
            "missing_entry_price": _missing_entry_price_count(strat_trades),
            "quote_quality": _stale_or_missing_quote_count(strat_trades),
            "integrity_exception_count": _integrity_exception_count(strat_trades),
            "date_range": _date_range(strat_trades),
        })
    return rows


def _funnel_narrative(
    paper_trade_count: int,
    lifecycle: dict[str, int],
    missing_entry_price: int,
    integrity_exceptions: int,
) -> list[str]:
    """
    Plain-language description of where records drop out in the pipeline
    from paper-trade creation → settled evidence.

    NOTE: The prediction/signal count *before* paper-trade creation is not
    reliably persisted as a separate counter, so that stage is documented as
    Unavailable.  See ``unavailable_reasons``.
    """
    notes: list[str] = []
    settled = lifecycle.get("SETTLED", 0)
    open_c = lifecycle.get("OPEN", 0)
    pending = lifecycle.get("PENDING_SETTLEMENT", 0)
    void_c = lifecycle.get("VOID", 0)

    if paper_trade_count == 0:
        notes.append("No paper trades found for this population.")
        return notes

    notes.append(
        f"Paper-trade stage: {paper_trade_count} total records "
        f"({settled} settled, {open_c} open, {pending} pending settlement, "
        f"{void_c} void)."
    )
    if missing_entry_price > 0:
        notes.append(
            f"{missing_entry_price} record(s) are missing a contemporaneous "
            "entry price (side_market_price is NULL)."
        )
    if integrity_exceptions > 0:
        notes.append(
            f"{integrity_exceptions} settled record(s) carry quality_flags "
            "indicating settlement or data-integrity issues."
        )
    if settled == 0:
        notes.append(
            "No settled evidence yet — readiness metrics cannot be computed."
        )
    return notes


def _build_population_block(
    population: str,
    trades: list[PaperTrade],
) -> dict[str, Any]:
    """
    Build a complete diagnostic block for one population.
    All fields are computed from stored data only.
    """
    lifecycle = _lifecycle_counts(trades)
    settled = [t for t in trades if t.status == "SETTLED"]
    missing_ep = _missing_entry_price_count(trades)
    quote_q = _stale_or_missing_quote_count(trades)
    int_exc = _integrity_exception_count(trades)

    return {
        "population": population,
        "total_paper_trades": len(trades),
        "lifecycle_counts": lifecycle,
        "settled_count": lifecycle["SETTLED"],
        "settlement_coverage_pct": (
            round(lifecycle["SETTLED"] / len(trades) * 100, 1)
            if trades else None
        ),
        "missing_entry_price_count": missing_ep,
        "quote_quality": quote_q,
        "integrity_exception_count": int_exc,
        "date_range": _date_range(trades),
        "strategy_breakdown": _strategy_summary(trades),
        "funnel_narrative": _funnel_narrative(
            len(trades), lifecycle, missing_ep, int_exc
        ),
    }


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/forward-evidence-reconciliation")
async def get_forward_evidence_reconciliation(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Forward-evidence completeness and reconciliation diagnostics.

    Returns per-population (OFFICIAL / RESEARCH_ONLY / LEGACY / UNCLASSIFIED)
    and per-strategy-version diagnostic counts derived exclusively from stored
    fields on PaperTrade.

    Safety invariants
    -----------------
    - trading_state_modified is always False.
    - real_money_execution_enabled is always False.
    - No historical records are written, reclassified, or modified.
    - Populations are strictly separated; counts are never pooled.
    - If a stage cannot be reliably measured from stored data it is listed
      in ``unavailable_reasons`` with an explanation.
    """
    # Load all paper trades in a single query; partition in Python to keep
    # the query simple and avoid risky per-population sub-queries.
    # NOTE: This fetches all PaperTrade rows. For large tables this may
    # become a memory concern.  A future optimisation could select only the
    # columns consumed by the helper functions; for now the table is expected
    # to remain small enough that full-row loading is acceptable, and
    # correctness is prioritised over premature optimisation.
    all_result = await session.execute(select(PaperTrade))
    all_trades: list[PaperTrade] = list(all_result.scalars().all())

    # Partition into population buckets — never mix across boundaries.
    buckets: dict[str, list[PaperTrade]] = {p: [] for p in _ALL_POPULATIONS}
    for t in all_trades:
        buckets[_population_key(t.eligibility_status)].append(t)

    populations = [
        _build_population_block(pop, buckets[pop])
        for pop in _ALL_POPULATIONS
    ]

    # Unavailable-stage documentation: the prediction/signal count that
    # precedes paper-trade creation is NOT stored as a separate persisted
    # counter and cannot be safely derived without risking double-counting
    # across strategy versions.
    unavailable_reasons: list[str] = [
        (
            "Prediction/signal count before paper-trade creation: not persisted "
            "as a separate counter. The PredictionSnapshot table stores individual "
            "snapshots but does not have a single 'eligible signal' counter that "
            "can be joined to PaperTrade without risk of double-counting across "
            "strategy versions. Stage reported as Unavailable."
        ),
    ]

    return {
        # Safety invariants — always present, always False
        "trading_state_modified": False,
        "real_money_execution_enabled": False,

        # Aggregate totals (cross-population summary only — not used for metrics)
        "total_paper_trades": len(all_trades),

        # Per-population diagnostic blocks (strictly separated)
        "populations": populations,

        # Stages that cannot be measured from stored data
        "unavailable_reasons": unavailable_reasons,

        "note": (
            "All counts are derived from stored PaperTrade fields only. "
            "No values are fabricated, inferred, or reinterpreted. "
            "OFFICIAL / RESEARCH_ONLY / LEGACY / UNCLASSIFIED populations "
            "are never pooled. No readiness threshold is evaluated."
        ),
    }
