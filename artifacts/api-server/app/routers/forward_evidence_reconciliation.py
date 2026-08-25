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
import math
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import PaperTrade, PredictionSnapshot

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
# Cross-version comparability analysis (YELLOW evidence only, read-only)
# ---------------------------------------------------------------------------

_CLASS_DIRECT = "DIRECTLY_COMPARABLE"
_CLASS_CONTEXT = "CONTEXT_ONLY"
_CLASS_INCOMPATIBLE = "INCOMPATIBLE_EXCLUDED"

_METHOD_PROFILE: dict[str, dict[str, Any]] = {
    "v3.0": {
        "probability_method": "v3_probability_engine (historical-prior preload + shrunk sigma)",
        "calibration_method": "V3 prior/sigma methodology as implemented in V3 services",
        "feature_timing_boundary": "Forward prospective paper-trade snapshot at decision time",
        "eligibility_method": "Guard-based OFFICIAL/RESEARCH_ONLY/LEGACY classification",
        "quote_capture_method": "Entry-time quote snapshot on PaperTrade fields",
        "settlement_interpretation": "Kalshi-authoritative settlement with regime stamp",
        "classification": _CLASS_DIRECT,
        "equivalent_to_v3": True,
        "classification_reason": "Current V3 baseline cohort under active forward protocol.",
    },
    "v2.3": {
        "probability_method": "v2.2 corrected-bias probability engine (stored as strategy v2.3)",
        "calibration_method": "V2 error-stats + calibration adjustments (different from V3 preload)",
        "feature_timing_boundary": "Forward prospective paper-trade snapshot at decision time",
        "eligibility_method": "V2.2 guard set with RESEARCH_ONLY demotions for some conditions",
        "quote_capture_method": "Entry-time quote snapshot on PaperTrade fields",
        "settlement_interpretation": "Kalshi-authoritative settlement under recorded regime",
        "classification": _CLASS_CONTEXT,
        "equivalent_to_v3": False,
        "classification_reason": "Different probability/calibration engine from V3; not poolable into V3 readiness N.",
    },
    "v2.1": {
        "probability_method": "v2.1 probability engine with legacy bias-sign behavior",
        "calibration_method": "V2 error-stats/calibration path, not V3 preload methodology",
        "feature_timing_boundary": "Forward prospective paper-trade snapshot at decision time",
        "eligibility_method": "V2.1 guard set with hard skips for stale/unverified cases",
        "quote_capture_method": "Entry-time quote snapshot on PaperTrade fields",
        "settlement_interpretation": "Kalshi-authoritative settlement under recorded regime",
        "classification": _CLASS_CONTEXT,
        "equivalent_to_v3": False,
        "classification_reason": "Behaviorally different engine/guarding path; useful context, not V3-ready pooled evidence.",
    },
    "v2.0": {
        "probability_method": "v2 shadow probability engine",
        "calibration_method": "Legacy V2 calibration path with different exclusions/quality rules",
        "feature_timing_boundary": "Forward paper-trade records; earlier guard regime",
        "eligibility_method": "Pre-hardening eligibility semantics (no OFFICIAL gate on early rows)",
        "quote_capture_method": "Stored side price; early rows may miss full quote metadata",
        "settlement_interpretation": "Settlement records vary across pre-hardening periods",
        "classification": _CLASS_INCOMPATIBLE,
        "equivalent_to_v3": False,
        "classification_reason": "Legacy cohort with materially different eligibility/calibration semantics.",
    },
}


def _profile_for_strategy(strategy_version: str | None) -> dict[str, Any]:
    strategy = (strategy_version or "unknown").strip() or "unknown"
    if strategy in _METHOD_PROFILE:
        return _METHOD_PROFILE[strategy]
    return {
        "probability_method": "Unknown/legacy strategy metadata",
        "calibration_method": "Unknown/legacy calibration metadata",
        "feature_timing_boundary": "Unknown",
        "eligibility_method": "Unknown",
        "quote_capture_method": "Unknown",
        "settlement_interpretation": "Unknown",
        "classification": _CLASS_INCOMPATIBLE,
        "equivalent_to_v3": False,
        "classification_reason": "No methodology profile available for comparability proof.",
    }


def _brier_components(trade: PaperTrade) -> tuple[float, float] | None:
    if trade.ec_yes_probability is None:
        return None
    if trade.kalshi_result not in ("yes", "no"):
        return None
    actual = 1.0 if trade.kalshi_result == "yes" else 0.0
    err = float(trade.ec_yes_probability) - actual
    return float(trade.ec_yes_probability), err * err


def _normal_95_ci(samples: list[float]) -> dict[str, float] | None:
    n = len(samples)
    if n == 0:
        return None
    mean = sum(samples) / n
    if n == 1:
        return {"mean": round(mean, 6), "ci95_low": round(mean, 6), "ci95_high": round(mean, 6)}
    variance = sum((x - mean) ** 2 for x in samples) / (n - 1)
    stderr = math.sqrt(max(variance, 0.0) / n)
    delta = 1.96 * stderr
    return {
        "mean": round(mean, 6),
        "ci95_low": round(mean - delta, 6),
        "ci95_high": round(mean + delta, 6),
    }


def _calibration_table(trades: list[PaperTrade]) -> list[dict[str, Any]]:
    bins: list[list[tuple[float, float]]] = [[] for _ in range(10)]
    for t in trades:
        comp = _brier_components(t)
        if comp is None:
            continue
        p, brier = comp
        idx = min(9, max(0, int(p * 10)))
        bins[idx].append((p, brier))

    rows: list[dict[str, Any]] = []
    for idx, vals in enumerate(bins):
        if not vals:
            continue
        lo = idx / 10
        hi = (idx + 1) / 10
        n = len(vals)
        avg_p = sum(v[0] for v in vals) / n
        # actual_yes = p - signed_error where brier=(p-actual)^2; for binary event:
        # derive actual rate from observed binary outcomes instead of inferred error.
        # Recompute directly for clarity.
        bucket_trades = []
        for t in trades:
            comp = _brier_components(t)
            if comp is None:
                continue
            p = comp[0]
            bucket_idx = min(9, max(0, int(p * 10)))
            if bucket_idx == idx:
                bucket_trades.append(t)
        yes_count = sum(1 for t in bucket_trades if t.kalshi_result == "yes")
        actual_rate = yes_count / n if n else None
        rows.append({
            "bucket_lo_inclusive": round(lo, 1),
            "bucket_hi_exclusive": round(hi, 1),
            "n": n,
            "avg_predicted_yes_probability": round(avg_p, 6),
            "observed_yes_rate": round(actual_rate, 6) if actual_rate is not None else None,
            "bucket_brier_mean": round(sum(v[1] for v in vals) / n, 6),
        })
    return rows


def _cohort_metrics(trades: list[PaperTrade]) -> dict[str, Any]:
    settled = [t for t in trades if t.status == "SETTLED"]
    scored = []
    for t in settled:
        comp = _brier_components(t)
        if comp is not None:
            scored.append(comp[1])
    brier = _normal_95_ci(scored)
    return {
        "settled_n": len(settled),
        "scored_n": len(scored),
        "brier": brier,
        "calibration_table": _calibration_table(settled),
    }


def _evidence_counts_and_pooling(settled: list[PaperTrade]) -> dict[str, Any]:
    current_v3_forward_settled_n = sum(
        1
        for t in settled
        if (t.strategy_version or "unknown") == "v3.0"
        and _population_key(t.eligibility_status) == _POP_OFFICIAL
    )
    older_direct_n = sum(
        1
        for t in settled
        if (t.strategy_version or "unknown") != "v3.0"
        and _profile_for_strategy(t.strategy_version)["classification"] == _CLASS_DIRECT
    )
    older_context_or_excluded_n = sum(
        1
        for t in settled
        if (t.strategy_version or "unknown") != "v3.0"
        and _profile_for_strategy(t.strategy_version)["classification"] != _CLASS_DIRECT
    )
    pooled_direct_candidates = [
        t for t in settled if _profile_for_strategy(t.strategy_version)["classification"] == _CLASS_DIRECT
    ]
    pooling_permitted = older_direct_n > 0 and len(pooled_direct_candidates) > 0
    return {
        "current_clean_v3_forward_settled_n": current_v3_forward_settled_n,
        "older_directly_comparable_settled_n": older_direct_n,
        "older_context_only_or_excluded_settled_n": older_context_or_excluded_n,
        "pooling_permitted": pooling_permitted,
        "pooled_direct_candidates": pooled_direct_candidates,
    }


@router.get("/forward-evidence-comparability")
async def get_forward_evidence_comparability(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Read-only cross-version probability-evidence comparability diagnostics.

    This endpoint inventories settled cohorts and classifies older strategy
    versions as DIRECTLY_COMPARABLE, CONTEXT_ONLY, or INCOMPATIBLE_EXCLUDED
    using documented methodology profiles. No data is modified.
    """
    all_result = await session.execute(select(PaperTrade))
    all_trades: list[PaperTrade] = list(all_result.scalars().all())
    settled = [t for t in all_trades if t.status == "SETTLED"]

    snapshot_ids = [t.snapshot_id for t in settled if t.snapshot_id is not None]
    snapshots_by_id: dict[int, PredictionSnapshot] = {}
    if snapshot_ids:
        snap_result = await session.execute(
            select(PredictionSnapshot).where(PredictionSnapshot.id.in_(set(snapshot_ids)))
        )
        snapshots = list(snap_result.scalars().all())
        snapshots_by_id = {s.id: s for s in snapshots}

    settled_inventory: list[dict[str, Any]] = []
    cohort_buckets: dict[tuple[str, str], list[PaperTrade]] = defaultdict(list)
    methodology_rows: dict[str, dict[str, Any]] = {}

    for t in settled:
        strategy = t.strategy_version or "unknown"
        population = _population_key(t.eligibility_status)
        profile = _profile_for_strategy(strategy)
        snapshot = snapshots_by_id.get(t.snapshot_id) if t.snapshot_id is not None else None
        forecast_ts = (
            snapshot.forecast_retrieved_at.isoformat()  # type: ignore[union-attr]
            if snapshot and snapshot.forecast_retrieved_at
            else (t.created_at.isoformat() if t.created_at else None)
        )
        forecast_ts_source = (
            "prediction_snapshots.forecast_retrieved_at"
            if snapshot and snapshot.forecast_retrieved_at
            else "paper_trades.created_at"
        )

        settled_inventory.append({
            "paper_trade_id": t.id,
            "forecast_timestamp": forecast_ts,
            "forecast_timestamp_provenance": forecast_ts_source,
            "provenance_class": "FORWARD_PROSPECTIVE",
            "strategy_version": strategy,
            "probability_method": profile["probability_method"],
            "calibration_method": profile["calibration_method"],
            "feature_timing_boundary": profile["feature_timing_boundary"],
            "eligibility_method": profile["eligibility_method"],
            "quote_capture_method": profile["quote_capture_method"],
            "city": t.city,
            "market_type": t.contract_type,
            "market_ticker": t.market_ticker,
            "evidence_class": population,
            "forecast_yes_probability": t.ec_yes_probability,
            "market_yes_probability": t.market_yes_probability,
            "entry_side_price": t.side_market_price,
            "settlement_source_regime": t.settlement_regime,
            "settlement_quality_flags": t.quality_flags or [],
            "outcome_verified": t.outcome_verified,
            "classification": profile["classification"],
            "classification_reason": profile["classification_reason"],
        })
        cohort_buckets[(strategy, population)].append(t)

        if strategy not in methodology_rows:
            methodology_rows[strategy] = {
                "strategy_version": strategy,
                "classification": profile["classification"],
                "classification_reason": profile["classification_reason"],
                "equivalent_to_v3": bool(profile["equivalent_to_v3"]),
                "probability_method": profile["probability_method"],
                "calibration_method": profile["calibration_method"],
                "feature_timing_boundary": profile["feature_timing_boundary"],
                "eligibility_method": profile["eligibility_method"],
                "quote_capture_method": profile["quote_capture_method"],
                "settlement_interpretation": profile["settlement_interpretation"],
                "settled_n_total": 0,
            }
        methodology_rows[strategy]["settled_n_total"] += 1

    cohort_metrics = []
    for (strategy, population), trades in sorted(cohort_buckets.items()):
        profile = _profile_for_strategy(strategy)
        cohort_metrics.append({
            "strategy_version": strategy,
            "evidence_class": population,
            "classification": profile["classification"],
            "metrics": _cohort_metrics(trades),
        })

    evidence_counts = _evidence_counts_and_pooling(settled)
    pooling_permitted = bool(evidence_counts["pooling_permitted"])
    pooled_direct_candidates = evidence_counts["pooled_direct_candidates"]
    pooled_metrics = _cohort_metrics(pooled_direct_candidates) if pooling_permitted else None

    pooling_explanation = (
        "Pooling includes V3 plus older DIRECTLY_COMPARABLE cohorts with documented equivalence."
        if pooling_permitted
        else (
            "Pooling not performed: no older cohort currently classified DIRECTLY_COMPARABLE. "
            "Version-separated metrics remain authoritative."
        )
    )

    return {
        "trading_state_modified": False,
        "real_money_execution_enabled": False,
        "methodology_matrix": sorted(methodology_rows.values(), key=lambda r: r["strategy_version"]),
        "version_separated_metrics": cohort_metrics,
        "pooled_directly_comparable_metrics": {
            "pooling_permitted": pooling_permitted,
            "explanation": pooling_explanation,
            "metrics": pooled_metrics,
        },
        "evidence_counts": {
            "current_clean_v3_forward_settled_n": evidence_counts["current_clean_v3_forward_settled_n"],
            "older_directly_comparable_settled_n": evidence_counts["older_directly_comparable_settled_n"],
            "older_context_only_or_excluded_settled_n": evidence_counts["older_context_only_or_excluded_settled_n"],
        },
        "settled_observation_inventory": settled_inventory,
        "note": (
            "This is a read-only evidence report. OFFICIAL/RESEARCH_ONLY/LEGACY/UNCLASSIFIED "
            "boundaries are preserved. Older cohorts are not pooled into V3 readiness N unless "
            "methodology equivalence is explicitly documented."
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
