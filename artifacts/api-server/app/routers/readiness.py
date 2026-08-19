"""
app/routers/readiness.py
Real-Money Readiness Dashboard — read-only evidence layer.

SAFETY GUARANTEES (enforced, not assumed):
  • Only OFFICIAL paper trades (eligibility_status == "OFFICIAL") are included
    in evidence metrics.  RESEARCH_ONLY, LEGACY, and NULL rows are excluded.
  • No thresholds that govern whether the owner is financially ready are
    activated here.  All readiness evaluations display "NOT READY" or
    "Needs evidence" until a separate owner YELLOW decision is recorded.
  • This endpoint NEVER places, modifies, or cancels trades of any kind.
  • trading_state_modified is always False in every response.
  • Real-money execution capability is not present.

Population integrity:
  - OFFICIAL / RESEARCH_ONLY / LEGACY populations remain strictly separate.
  - Historical records are never reclassified.
  - Entry prices are used as-is; missing values are labelled, not fabricated.
  - Small-sample warnings are shown when settled count < 30.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import PaperTrade

logger = logging.getLogger(__name__)

router = APIRouter(tags=["readiness"])

# ---------------------------------------------------------------------------
# Small-sample threshold — display-only warning, NOT a readiness gate.
# Protected readiness thresholds require a separate owner YELLOW decision.
# ---------------------------------------------------------------------------
_SMALL_SAMPLE_WARN = 30

# ---------------------------------------------------------------------------
# Settlement regime labels (mirrors PaperTrade.settlement_regime)
# ---------------------------------------------------------------------------
_REGIME_LEGACY = "LEGACY_NWS"
_REGIME_CURRENT = "WEATHER_COMPANY"


# ---------------------------------------------------------------------------
# Pure helpers — no DB access, fully testable in isolation
# ---------------------------------------------------------------------------

def _brier_score(trades: list[PaperTrade]) -> float | None:
    """Brier score over OFFICIAL settled trades with known probabilities."""
    scored = [
        t for t in trades
        if t.status == "SETTLED"
        and t.kalshi_result in ("yes", "no")
        and t.ec_yes_probability is not None
    ]
    if not scored:
        return None
    total = sum(
        (t.ec_yes_probability - (1 if t.kalshi_result == "yes" else 0)) ** 2  # type: ignore[operator]
        for t in scored
    )
    return round(total / len(scored), 6)


def _max_drawdown_and_losing_streak(
    settled: list[PaperTrade],
) -> tuple[float | None, int | None]:
    """
    Compute maximum drawdown (in dollars) and longest consecutive-loss streak
    from chronologically sorted settled trades.

    Returns (max_drawdown, longest_losing_streak).
    Both are None if fewer than 2 settled trades.
    """
    ordered = sorted(
        settled,
        key=lambda t: t.settlement_timestamp or datetime.min.replace(tzinfo=timezone.utc),
    )
    if len(ordered) < 2:
        return None, None

    peak = 0.0
    cumul = 0.0
    max_dd = 0.0
    streak = 0
    max_streak = 0
    for t in ordered:
        pl = t.profit_loss or 0.0
        cumul += pl
        if cumul > peak:
            peak = cumul
        dd = peak - cumul
        if dd > max_dd:
            max_dd = dd
        if pl < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    return round(max_dd, 4), max_streak


def _roi(settled: list[PaperTrade]) -> float | None:
    total_stake = sum(t.stake or 0.0 for t in settled)
    if total_stake <= 0:
        return None
    net_pl = sum(t.profit_loss or 0.0 for t in settled)
    return round(net_pl / total_stake * 100, 4)


def _avg_entry_edge(trades: list[PaperTrade]) -> float | None:
    vals = [t.edge_pct_points for t in trades if t.edge_pct_points is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def _city_breakdown(trades: list[PaperTrade]) -> list[dict[str, Any]]:
    """Per-city settled win rate and trade count for OFFICIAL trades."""
    by_city: dict[str, list[PaperTrade]] = defaultdict(list)
    for t in trades:
        by_city[t.city or "Unknown"].append(t)
    rows = []
    for city, city_trades in sorted(by_city.items()):
        settled = [t for t in city_trades if t.status == "SETTLED"]
        wins = [t for t in settled if t.outcome == "WIN"]
        rows.append({
            "city": city,
            "total": len(city_trades),
            "settled": len(settled),
            "wins": len(wins),
            "winRate": round(len(wins) / len(settled), 4) if settled else None,
            "smallSample": len(settled) < _SMALL_SAMPLE_WARN,
        })
    return rows


def _strategy_breakdown(trades: list[PaperTrade]) -> list[dict[str, Any]]:
    """Per-strategy-version settled win rate for OFFICIAL trades."""
    by_strat: dict[str, list[PaperTrade]] = defaultdict(list)
    for t in trades:
        by_strat[t.strategy_version or "unknown"].append(t)
    rows = []
    for strat, strat_trades in sorted(by_strat.items()):
        settled = [t for t in strat_trades if t.status == "SETTLED"]
        wins = [t for t in settled if t.outcome == "WIN"]
        rows.append({
            "strategy": strat,
            "total": len(strat_trades),
            "settled": len(settled),
            "wins": len(wins),
            "winRate": round(len(wins) / len(settled), 4) if settled else None,
            "brierScore": _brier_score(strat_trades),
            "avgEdgePp": _avg_entry_edge(strat_trades),
            "smallSample": len(settled) < _SMALL_SAMPLE_WARN,
        })
    return rows


def _edge_bucket_breakdown(trades: list[PaperTrade]) -> list[dict[str, Any]]:
    """Win rate by edge bucket (0-10pp, 10-20pp, 20-30pp, 30+pp)."""
    buckets: dict[str, list[PaperTrade]] = {
        "0–10pp": [],
        "10–20pp": [],
        "20–30pp": [],
        "30+pp": [],
    }
    for t in trades:
        e = t.edge_pct_points
        if e is None:
            continue
        if e < 10:
            buckets["0–10pp"].append(t)
        elif e < 20:
            buckets["10–20pp"].append(t)
        elif e < 30:
            buckets["20–30pp"].append(t)
        else:
            buckets["30+pp"].append(t)

    rows = []
    for label, bucket_trades in buckets.items():
        settled = [t for t in bucket_trades if t.status == "SETTLED"]
        wins = [t for t in settled if t.outcome == "WIN"]
        rows.append({
            "bucket": label,
            "total": len(bucket_trades),
            "settled": len(settled),
            "winRate": round(len(wins) / len(settled), 4) if settled else None,
            "smallSample": len(settled) < _SMALL_SAMPLE_WARN,
        })
    return rows


def _confidence_breakdown(trades: list[PaperTrade]) -> list[dict[str, Any]]:
    """Win rate by confidence label."""
    by_conf: dict[str, list[PaperTrade]] = defaultdict(list)
    for t in trades:
        by_conf[t.confidence_label or "Unknown"].append(t)
    rows = []
    for label, conf_trades in sorted(by_conf.items()):
        settled = [t for t in conf_trades if t.status == "SETTLED"]
        wins = [t for t in settled if t.outcome == "WIN"]
        rows.append({
            "confidenceLabel": label,
            "total": len(conf_trades),
            "settled": len(settled),
            "winRate": round(len(wins) / len(settled), 4) if settled else None,
            "smallSample": len(settled) < _SMALL_SAMPLE_WARN,
        })
    return rows


def _settlement_coverage(trades: list[PaperTrade]) -> dict[str, Any]:
    """Settlement coverage: how many OFFICIAL trades have been settled vs open."""
    total = len(trades)
    settled = [t for t in trades if t.status == "SETTLED"]
    open_t = [t for t in trades if t.status == "OPEN"]
    void_t = [t for t in trades if t.status == "VOID"]
    pending = [t for t in trades if t.status == "PENDING_SETTLEMENT"]
    pct = round(len(settled) / total * 100, 1) if total > 0 else None

    # Settlement regime breakdown
    regime_counts: dict[str, int] = defaultdict(int)
    for t in settled:
        regime_counts[t.settlement_regime or "UNKNOWN"] += 1

    return {
        "total": total,
        "settled": len(settled),
        "open": len(open_t),
        "void": len(void_t),
        "pendingSettlement": len(pending),
        "settlementCoveragePct": pct,
        "regimeBreakdown": dict(regime_counts),
    }


def _quote_quality(trades: list[PaperTrade]) -> dict[str, Any]:
    """Stale/missing quote rate across OFFICIAL trades."""
    total = len(trades)
    missing_quote = sum(
        1 for t in trades
        if t.quote_timestamp is None and t.side_market_price is None
    )
    stale_count = sum(
        1 for t in trades
        if t.quote_age_seconds is not None and t.quote_age_seconds > 300
    )
    return {
        "total": total,
        "missingQuoteCount": missing_quote,
        "staleQuoteCount": stale_count,
        "missingQuoteRate": round(missing_quote / total, 4) if total > 0 else None,
        "staleQuoteRate": round(stale_count / total, 4) if total > 0 else None,
    }


def _abstention_analysis(all_research: list[PaperTrade]) -> dict[str, Any]:
    """
    How many trades were classified RESEARCH_ONLY (i.e. abstentions from the
    OFFICIAL forward-test population).  Breakdown by eligibility_reason.
    """
    reason_counts: dict[str, int] = defaultdict(int)
    for t in all_research:
        reason_counts[t.eligibility_reason or "unknown"] += 1
    return {
        "researchOnlyCount": len(all_research),
        "reasonBreakdown": dict(reason_counts),
    }


def _settlement_integrity_exceptions(settled: list[PaperTrade]) -> list[dict[str, Any]]:
    """
    Trades with quality_flags that indicate settlement or data-integrity issues.
    """
    exceptions = []
    for t in settled:
        flags = t.quality_flags or []
        if flags:
            exceptions.append({
                "tradeId": t.id,
                "ticker": t.market_ticker,
                "city": t.city,
                "flags": flags,
                "settledAt": t.settlement_timestamp.isoformat() if t.settlement_timestamp else None,
            })
    return exceptions


def _evidence_gaps(
    settled_count: int,
    city_count: int,
    brier: float | None,
    roi: float | None,
) -> list[str]:
    """
    Plain-language list of evidence gaps.  This function identifies WHAT
    is missing; it does NOT set readiness thresholds.

    Protected thresholds (e.g. minimum trade count, win-rate target) require
    a separate owner YELLOW decision before activation.
    """
    gaps: list[str] = []
    if settled_count == 0:
        # All other gaps (missing Brier, ROI, city coverage) are downstream of
        # having zero settled trades. Return a single clear root-cause message
        # rather than a confusing list of metric-level gaps.
        gaps.append("No settled OFFICIAL trades — all readiness indicators require settled forward-test evidence.")
        return gaps
    if settled_count < _SMALL_SAMPLE_WARN:
        gaps.append(
            f"Only {settled_count} settled OFFICIAL trade(s). "
            f"At least {_SMALL_SAMPLE_WARN} settled trades are needed before statistical "
            "estimates are meaningful. All metrics show preliminary estimates only."
        )
    if city_count < 2:
        gaps.append(
            "OFFICIAL evidence covers fewer than 2 cities. "
            "Multi-city diversification evidence is needed to assess concentration risk."
        )
    if brier is None:
        gaps.append(
            "Brier score cannot be computed — settled trades lack ec_yes_probability "
            "or binary (yes/no) kalshi_result. Probability-quality evidence is missing."
        )
    if roi is None:
        gaps.append(
            "ROI cannot be computed — no settled trades with valid stake data. "
            "Financial outcome evidence is missing."
        )
    return gaps


def _readiness_summary(
    settled_count: int,
    evidence_gaps: list[str],
) -> dict[str, Any]:
    """
    Deterministic readiness state — always fails closed when evidence is
    missing or invalid.

    IMPORTANT: No protected financial readiness threshold is activated here.
    The status will remain NOT_READY / NEEDS_EVIDENCE until a separate owner
    YELLOW decision defines and approves specific criteria for advancement.
    """
    if settled_count == 0:
        status = "NOT_READY"
        reason = "No settled OFFICIAL forward-test trades. No evidence to evaluate."
    elif evidence_gaps:
        status = "NEEDS_EVIDENCE"
        reason = (
            "Evidence gaps prevent readiness evaluation. "
            "See the 'What EdgeCast needs next' panel below."
        )
    else:
        # Even with sufficient data, readiness thresholds are owner-gated.
        status = "NEEDS_EVIDENCE"
        reason = (
            "Evidence is accumulating. Specific readiness thresholds have not yet "
            "been defined and approved by the owner (separate YELLOW decision required). "
            "Status will remain NEEDS_EVIDENCE until those thresholds are set."
        )

    return {
        "status": status,           # "NOT_READY" | "NEEDS_EVIDENCE"
        "reason": reason,
        "thresholdsActivated": False,   # NEVER True — protected owner-gate
        "realMoneyExecutionEnabled": False,  # ALWAYS False — safety invariant
        "trading_state_modified": False,     # ALWAYS False — safety invariant
    }


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/readiness")
async def get_readiness(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Real-Money Readiness dashboard — read-only evidence layer.

    Returns metrics computed exclusively from OFFICIAL paper trades.
    RESEARCH_ONLY, LEGACY, and NULL eligibility_status rows are excluded
    from all evidence metrics and never mixed in.

    The endpoint always returns trading_state_modified=False and
    realMoneyExecutionEnabled=False.  No readiness threshold is activated.
    """
    # Load OFFICIAL trades only
    official_q = select(PaperTrade).where(
        PaperTrade.eligibility_status == "OFFICIAL"
    )
    official_result = await session.execute(official_q)
    official_trades: list[PaperTrade] = list(official_result.scalars().all())

    # Load RESEARCH_ONLY trades for abstention analysis (read-only)
    research_q = select(PaperTrade).where(
        PaperTrade.eligibility_status == "RESEARCH_ONLY"
    )
    research_result = await session.execute(research_q)
    research_trades: list[PaperTrade] = list(research_result.scalars().all())

    settled = [t for t in official_trades if t.status == "SETTLED"]
    open_trades = [t for t in official_trades if t.status == "OPEN"]
    wins = [t for t in settled if t.outcome == "WIN"]
    losses = [t for t in settled if t.outcome == "LOSS"]

    brier = _brier_score(official_trades)
    roi = _roi(settled)
    net_pl = sum(t.profit_loss or 0.0 for t in settled)
    win_rate = round(len(wins) / len(settled), 4) if settled else None
    avg_edge = _avg_entry_edge(official_trades)
    city_count = len({t.city for t in settled if t.city})
    max_dd, longest_streak = _max_drawdown_and_losing_streak(settled)

    coverage = _settlement_coverage(official_trades)
    quote_quality = _quote_quality(official_trades)
    abstentions = _abstention_analysis(research_trades)
    exceptions = _settlement_integrity_exceptions(settled)

    gaps = _evidence_gaps(
        settled_count=len(settled),
        city_count=city_count,
        brier=brier,
        roi=roi,
    )
    readiness = _readiness_summary(
        settled_count=len(settled),
        evidence_gaps=gaps,
    )

    return {
        # Safety invariants — always present, always False
        "trading_state_modified": False,
        "realMoneyExecutionEnabled": False,

        # Readiness state
        "readiness": readiness,

        # Core evidence summary (OFFICIAL trades only)
        "evidence": {
            "officialTradeCount": len(official_trades),
            "settledCount": len(settled),
            "openCount": len(open_trades),
            "wins": len(wins),
            "losses": len(losses),
            "winRate": win_rate,
            "roi": roi,
            "netProfitLoss": round(net_pl, 4) if settled else None,
            "brierScore": brier,
            "avgEntryEdgePp": avg_edge,
            "maxDrawdown": max_dd,
            "longestLosingStreak": longest_streak,
            "cityCount": city_count,
            "smallSampleWarning": len(settled) < _SMALL_SAMPLE_WARN,
            "populationNote": (
                "All metrics use OFFICIAL paper trades only. "
                "RESEARCH_ONLY, LEGACY, and NULL-eligibility rows are excluded."
            ),
        },

        # Granular breakdowns
        "settlementCoverage": coverage,
        "cityBreakdown": _city_breakdown(official_trades),
        "strategyBreakdown": _strategy_breakdown(official_trades),
        "edgeBucketBreakdown": _edge_bucket_breakdown(official_trades),
        "confidenceBreakdown": _confidence_breakdown(official_trades),
        "quoteQuality": quote_quality,
        "abstentionAnalysis": abstentions,
        "settlementIntegrityExceptions": exceptions,

        # Plain-language evidence gaps
        "evidenceGaps": gaps,
    }
