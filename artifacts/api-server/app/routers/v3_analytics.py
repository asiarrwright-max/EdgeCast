"""
V3 Analytics API — Phase 1 + Phase 2 + Phase 3
================================================
Phase 1 endpoints:
  GET  /analytics/v3/flags            — current V3 feature flag states
  GET  /analytics/v3/ingestion-audit  — per-city ingestion summary
  POST /analytics/v3/run-ingestion    — trigger historical ingestion (admin)

Phase 2 endpoints (gated on v3.validation_enabled):
  POST /analytics/v3/compute-error-stats   — build V3ErrorStats from historical records
  GET  /analytics/v3/error-stats           — view computed V3ErrorStats rows
  GET  /analytics/v3/walk-forward-report   — run walk-forward validation and return report

Phase 3 endpoints (live parallel predictions + paper trading):
  GET  /analytics/v3/phase-a-diagnostics  — read-only V3 calibration/readiness diagnostics
  GET  /analytics/v3/live-predictions      — recent V3 prediction snapshots
  GET  /analytics/v3/live-paper-trades     — V3 paper trades with P/L summary
  GET  /analytics/v3/live-comparison       — V3 vs V2.1 probabilities, same markets
  POST /analytics/v3/run-v3-predictions    — manually trigger V3 prediction step
  POST /analytics/v3/run-v3-paper-trading  — manually trigger V3 paper-trading step
  POST /analytics/v3/run-v3-settlement     — manually trigger V3 settlement step
  POST /analytics/v3/enable-predictions    — set v3.predictions_enabled=true
  POST /analytics/v3/enable-paper-trading  — set v3.paper_trading_enabled=true
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from math import sqrt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import AppSetting
from app.models_v3 import (
    V3_FLAG_DEFAULTS,
    V3ErrorStats,
    V3HistoricalRecord,
    V3IngestionLog,
    V3PaperTrade,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["V3 Analytics"])


# ---------------------------------------------------------------------------
# Shared helpers — exported so tests can import and call them directly
# ---------------------------------------------------------------------------

def _fee_estimate(side_price: float | None, quantity: float | None) -> float | None:
    """
    Estimate Kalshi entry fee per trade:
        fee = max($0.01, 3.5¢ × min(price, 1−price) × contracts)

    The 3.5% rate applies to the cheaper side.  Returns None when inputs are
    missing or quantity ≤ 0 (non-executable trades have quantity=0).
    """
    if side_price is None or quantity is None or quantity <= 0:
        return None
    return round(max(0.01, 0.035 * min(side_price, 1.0 - side_price) * quantity), 4)


def _v3_brier_score(trades: list) -> float | None:
    """
    Brier score on a collection of settled V3PaperTrade objects.

    Uses ec_yes_probability as the predicted P(YES resolves).
    Outcome encoding:
        YES-WIN  → actual_yes = 1   YES-LOSS → actual_yes = 0
        NO-WIN   → actual_yes = 0   NO-LOSS  → actual_yes = 1
    Returns None when no settled trades exist.
    """
    settled = [
        t for t in trades
        if t.status == "SETTLED" and t.outcome in ("WIN", "LOSS")
    ]
    if not settled:
        return None
    scores = []
    for t in settled:
        p_yes = t.ec_yes_probability if t.ec_yes_probability is not None else 0.5
        if t.direction == "YES":
            actual_yes = 1.0 if t.outcome == "WIN" else 0.0
        else:  # NO trade: win means NO resolved → YES did NOT
            actual_yes = 0.0 if t.outcome == "WIN" else 1.0
        scores.append((p_yes - actual_yes) ** 2)
    return round(sum(scores) / len(scores), 4)


def _compute_v3_trade_sections(trades: list, observation_only_count: int) -> dict:
    """
    Split V3PaperTrade objects into three sections and compute metrics.

    Parameters
    ----------
    trades : list of V3PaperTrade (or duck-typed objects with the same attributes)
    observation_only_count : int
        Count of PENDING prediction snapshots with no linked V3PaperTrade row.

    Returns
    -------
    dict with keys: executable, non_executable, observation_only
    """
    executable     = [t for t in trades if t.is_executable is True]
    non_executable = [t for t in trades if t.is_executable is not True]

    def _build(subset: list, include_roi: bool) -> dict:
        settled  = [t for t in subset if t.status == "SETTLED"]
        open_    = [t for t in subset if t.status == "OPEN"]
        pending  = [t for t in subset if t.status == "PENDING_SETTLEMENT"]
        wins     = sum(1 for t in settled if t.outcome == "WIN")
        losses   = sum(1 for t in settled if t.outcome == "LOSS")
        stake    = sum(t.stake or 0 for t in subset)
        gross_pl = sum(t.profit_loss or 0 for t in settled)

        fees_list = [
            _fee_estimate(getattr(t, "side_market_price", None),
                          getattr(t, "quantity", None))
            for t in subset
        ]
        fees_total = round(sum(f for f in fees_list if f is not None), 4)
        net_pl = round(gross_pl - fees_total, 4)

        result: dict = {
            "count":         len(subset),
            "open":          len(open_),
            "pending_settlement": len(pending),
            "settled":       len(settled),
            "wins":          wins,
            "losses":        losses,
            "win_rate_pct":  round(100 * wins / len(settled), 1) if settled else None,
            "total_stake":   round(stake, 2),
            "gross_pl":      round(gross_pl, 2),
            "estimated_fees": fees_total,
            "net_pl":        net_pl,
        }
        if include_roi:
            result["roi_pct"]     = round(100 * gross_pl / stake, 1) if stake and settled else None
            result["brier_score"] = _v3_brier_score(subset)
        else:
            result["note"] = (
                "Excluded from official win rate, P/L, and ROI. "
                "Signal accuracy is tracked here for research only."
            )
        return result

    exec_section = _build(executable, include_roi=True)
    exec_section["label"] = "Executable paper trades (is_executable=True)"

    nonexec_section = _build(non_executable, include_roi=False)
    nonexec_section["label"] = (
        "Non-executable signals (is_executable=False) — excluded from official ROI"
    )

    return {
        "executable": exec_section,
        "non_executable": nonexec_section,
        "observation_only": {
            "label": "Observation-only predictions (PENDING snap, no linked trade)",
            "count": observation_only_count,
            "note": (
                "PENDING V3PredictionSnapshot rows with no V3PaperTrade row. "
                "Excluded from all trade performance metrics."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Phase A read-only diagnostic helpers (V3 calibration/readiness track)
# ---------------------------------------------------------------------------

_PHASE_A_PROB_BUCKETS: list[tuple[str, float, float]] = [
    ("0-9%", 0.00, 0.10),
    ("10-19%", 0.10, 0.20),
    ("20-29%", 0.20, 0.30),
    ("30-39%", 0.30, 0.40),
    ("40-49%", 0.40, 0.50),
    ("50-59%", 0.50, 0.60),
    ("60-69%", 0.60, 0.70),
    ("70-79%", 0.70, 0.80),
    ("80-89%", 0.80, 0.90),
    ("90-100%", 0.90, 1.01),
]


def _clamp01(value: float | None) -> float | None:
    if value is None:
        return None
    return min(1.0, max(0.0, value))


def _trade_actual_win(trade: V3PaperTrade) -> float | None:
    if trade.outcome == "WIN":
        return 1.0
    if trade.outcome == "LOSS":
        return 0.0
    return None


def _trade_model_side_probability(trade: V3PaperTrade) -> float | None:
    if trade.ec_side_probability is not None:
        return _clamp01(trade.ec_side_probability)
    if trade.ec_yes_probability is None:
        return None
    if trade.direction == "YES":
        return _clamp01(trade.ec_yes_probability)
    if trade.direction == "NO":
        return _clamp01(1.0 - trade.ec_yes_probability)
    return None


def _bucket_for_probability(probability: float | None) -> str:
    if probability is None:
        return "unknown"
    for label, lo, hi in _PHASE_A_PROB_BUCKETS:
        if lo <= probability < hi:
            return label
    return "unknown"


def _bucket_for_lead_time(days: int | None) -> str:
    if days is None:
        return "unknown"
    if days <= 1:
        return "0-1d"
    if days <= 3:
        return "2-3d"
    if days <= 7:
        return "4-7d"
    return "8d+"


def _bucket_for_quote_age(seconds: float | None) -> str:
    if seconds is None:
        return "missing"
    if seconds <= 60:
        return "0-60s"
    if seconds <= 300:
        return "61-300s"
    return "301s+"


def _bucket_for_market_price(price: float | None) -> str:
    if price is None:
        return "missing"
    if price < 0.20:
        return "<0.20"
    if price < 0.40:
        return "0.20-0.39"
    if price < 0.60:
        return "0.40-0.59"
    if price < 0.80:
        return "0.60-0.79"
    return "0.80-1.00"


def _eligibility_class(eligibility_status: str | None) -> str:
    if eligibility_status is None:
        return "UNLABELED"
    v = eligibility_status.strip().upper()
    if v in ("OFFICIAL", "RESEARCH_ONLY", "LEGACY"):
        return v
    return "UNLABELED"


def _event_key(trade: V3PaperTrade) -> str:
    city = trade.city or "UNKNOWN_CITY"
    target_date = trade.target_settlement_date or "UNKNOWN_DATE"
    variable = trade.weather_variable or "UNKNOWN_VAR"
    return f"{city}|{target_date}|{variable}"


def _brier(rows: list[dict[str, Any]], probability_key: str) -> float | None:
    vals = [
        (r[probability_key] - r["actual"]) ** 2
        for r in rows
        if r.get(probability_key) is not None and r.get("actual") is not None
    ]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def _event_level_brier(rows: list[dict[str, Any]], probability_key: str) -> float | None:
    by_event: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        p = r.get(probability_key)
        a = r.get("actual")
        if p is None or a is None:
            continue
        by_event[r["event_key"]].append((p - a) ** 2)
    if not by_event:
        return None
    event_means = [sum(v) / len(v) for v in by_event.values() if v]
    if not event_means:
        return None
    return round(sum(event_means) / len(event_means), 4)


def _wilson_95(wins: int, total: int) -> tuple[float, float] | None:
    if total <= 0:
        return None
    z = 1.96
    phat = wins / total
    denom = 1 + (z * z) / total
    center = (phat + (z * z) / (2 * total)) / denom
    margin = (z / denom) * sqrt((phat * (1 - phat) / total) + ((z * z) / (4 * total * total)))
    lo = max(0.0, center - margin)
    hi = min(1.0, center + margin)
    return round(lo * 100, 2), round(hi * 100, 2)


def _event_clustered_95(rows: list[dict[str, Any]]) -> tuple[float, float] | None:
    grouped: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        grouped[r["event_key"]].append(r["actual"])
    event_rates = [sum(v) / len(v) for v in grouped.values() if v]
    if not event_rates:
        return None
    if len(event_rates) == 1:
        v = round(event_rates[0] * 100, 2)
        return v, v
    mean_rate = sum(event_rates) / len(event_rates)
    var = sum((x - mean_rate) ** 2 for x in event_rates) / (len(event_rates) - 1)
    se = sqrt(var / len(event_rates))
    lo = max(0.0, mean_rate - 1.96 * se)
    hi = min(1.0, mean_rate + 1.96 * se)
    return round(lo * 100, 2), round(hi * 100, 2)


def _group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # rows are expected to contain only binary actual outcomes (0/1).
    n = len(rows)
    if n == 0:
        return {
            "count": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "avg_model_prob_pct": None,
            "avg_market_prob_pct": None,
            "avg_edge_pp": None,
            "roi_pct": None,
            "brier_model": None,
            "brier_market": None,
            "event_level_brier_model": None,
            "event_level_brier_market": None,
        }
    wins = sum(1 for r in rows if r["actual"] == 1.0)
    losses = n - wins
    model_probs = [r["model_prob"] for r in rows if r["model_prob"] is not None]
    market_probs = [r["market_prob"] for r in rows if r["market_prob"] is not None]
    edges = [r["edge_pp"] for r in rows if r["edge_pp"] is not None]
    settled_pl = [r["profit_loss"] for r in rows if r["profit_loss"] is not None]
    settled_stake = [r["stake"] for r in rows if r["stake"] is not None]
    total_stake = sum(settled_stake)
    total_pl = sum(settled_pl)
    return {
        "count": n,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wins / n * 100, 2),
        "avg_model_prob_pct": round(sum(model_probs) / len(model_probs) * 100, 2) if model_probs else None,
        "avg_market_prob_pct": round(sum(market_probs) / len(market_probs) * 100, 2) if market_probs else None,
        "avg_edge_pp": round(sum(edges) / len(edges), 2) if edges else None,
        "roi_pct": round(total_pl / total_stake * 100, 2) if total_stake > 0 else None,
        "brier_model": _brier(rows, "model_prob"),
        "brier_market": _brier(rows, "market_prob"),
        "event_level_brier_model": _event_level_brier(rows, "model_prob"),
        "event_level_brier_market": _event_level_brier(rows, "market_prob"),
    }


def _breakdown(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        grouped[str(r.get(field, "unknown"))].append(r)
    out: list[dict[str, Any]] = []
    for label in sorted(grouped):
        row = {"label": label}
        row.update(_group_summary(grouped[label]))
        out.append(row)
    return out


def _phase_a_diagnostics_from_trades(trades: list[V3PaperTrade]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for trade in trades:
        actual = _trade_actual_win(trade)
        model_prob = _trade_model_side_probability(trade)
        if actual is None:
            continue
        rows.append({
            "event_key": _event_key(trade),
            "actual": actual,
            "model_prob": model_prob,
            "market_prob": _clamp01(trade.side_market_price),
            "edge_pp": trade.edge_pct_points,
            "profit_loss": trade.profit_loss,
            "stake": trade.stake,
            "city": trade.city or "UNKNOWN",
            "contract_type": trade.contract_type or "UNKNOWN",
            "probability_bucket": _bucket_for_probability(model_prob),
            "lead_time_bucket": _bucket_for_lead_time(trade.lead_time_days),
            "quote_age_bucket": _bucket_for_quote_age(trade.quote_age_seconds),
            "market_price_bucket": _bucket_for_market_price(trade.side_market_price),
            "executable_class": "EXECUTABLE" if trade.is_executable is True else "NON_EXECUTABLE",
            "evidence_class": _eligibility_class(trade.eligibility_status),
            "settlement_date": str(trade.target_settlement_date or "UNKNOWN"),
        })

    overall = _group_summary(rows)
    event_keys = sorted({r["event_key"] for r in rows})
    settlement_dates = sorted({r["settlement_date"] for r in rows})
    base_rate = (overall["wins"] / overall["count"]) if overall["count"] else None
    constant_rows = []
    shrink_rows = []
    if base_rate is not None:
        for r in rows:
            constant_rows.append({**r, "constant_prob": base_rate})
            p = r["model_prob"]
            shrink_p = None if p is None else (0.5 + 0.5 * (p - 0.5))
            shrink_rows.append({**r, "shrink_prob": _clamp01(shrink_p)})

    calibration_by_bucket = []
    bucket_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        bucket_groups[r["probability_bucket"]].append(r)
    for label, _, _ in _PHASE_A_PROB_BUCKETS:
        grp = bucket_groups.get(label, [])
        wins = sum(1 for r in grp if r["actual"] == 1.0)
        obs = (wins / len(grp) * 100) if grp else None
        pred_probs = [r["model_prob"] for r in grp if r["model_prob"] is not None]
        pred = (sum(pred_probs) / len(pred_probs) * 100) if pred_probs else None
        wilson = _wilson_95(wins, len(grp))
        clustered = _event_clustered_95(grp)
        calibration_by_bucket.append({
            "bucket": label,
            "count": len(grp),
            "event_count": len({r["event_key"] for r in grp}),
            "wins": wins,
            "observed_win_rate_pct": round(obs, 2) if obs is not None else None,
            "predicted_win_rate_pct": round(pred, 2) if pred is not None else None,
            "calibration_error_pp": round((obs - pred), 2) if obs is not None and pred is not None else None,
            "observed_win_rate_wilson95_pct": (
                {"lower": wilson[0], "upper": wilson[1]} if wilson else None
            ),
            "observed_win_rate_event_clustered95_pct": (
                {"lower": clustered[0], "upper": clustered[1]} if clustered else None
            ),
        })

    eligibility_counts: dict[str, int] = defaultdict(int)
    for t in trades:
        eligibility_counts[_eligibility_class(t.eligibility_status)] += 1

    return {
        "total_settled_rows": len(trades),
        "analyzed_rows": overall["count"],
        "skipped_non_binary_outcome_rows": max(0, len(trades) - len(rows)),
        "distinct_event_count": len(event_keys),
        "distinct_settlement_date_count": len(settlement_dates),
        "overall": overall,
        "baseline_comparison": {
            "model_trade_brier": overall["brier_model"],
            "model_event_brier": overall["event_level_brier_model"],
            "market_trade_brier": overall["brier_market"],
            "market_event_brier": overall["event_level_brier_market"],
            "constant_rate_trade_brier": _brier(constant_rows, "constant_prob") if constant_rows else None,
            "constant_rate_event_brier": _event_level_brier(constant_rows, "constant_prob") if constant_rows else None,
            "shrinkage_trade_brier": _brier(shrink_rows, "shrink_prob") if shrink_rows else None,
            "shrinkage_event_brier": _event_level_brier(shrink_rows, "shrink_prob") if shrink_rows else None,
            "notes": (
                "Constant-rate baseline predicts the global settled win rate for every row. "
                "Shrinkage baseline uses p'=0.5+0.5*(p-0.5) as a simple conservative recalibration."
            ),
        },
        "breakdowns": {
            "by_city": _breakdown(rows, "city"),
            "by_contract_type": _breakdown(rows, "contract_type"),
            "by_probability_bucket": _breakdown(rows, "probability_bucket"),
            "by_lead_time_bucket": _breakdown(rows, "lead_time_bucket"),
            "by_quote_age_bucket": _breakdown(rows, "quote_age_bucket"),
            "by_market_price_bucket": _breakdown(rows, "market_price_bucket"),
            "by_executable_class": _breakdown(rows, "executable_class"),
            "by_evidence_class": _breakdown(rows, "evidence_class"),
        },
        "calibration_table": calibration_by_bucket,
        "data_quality_gaps": {
            "eligibility_class_counts": dict(sorted(eligibility_counts.items())),
            "unlabeled_rows": eligibility_counts.get("UNLABELED", 0),
            "missing_model_prob_rows": sum(
                1 for t in trades if _trade_model_side_probability(t) is None
            ),
            "missing_market_prob_rows": sum(1 for t in trades if t.side_market_price is None),
            "missing_quote_age_rows": sum(1 for t in trades if t.quote_age_seconds is None),
            "non_executable_rows": sum(1 for t in trades if t.is_executable is not True),
        },
    }


# ---------------------------------------------------------------------------
# GET /analytics/v3/phase-a-diagnostics — read-only diagnostics
# ---------------------------------------------------------------------------

@router.get("/analytics/v3/phase-a-diagnostics")
async def get_v3_phase_a_diagnostics(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    V3 Phase A read-only diagnostics for calibration/decision-readiness research.

    Safety guarantees:
    - Read-only metrics only (no behavior/eligibility/settlement updates).
    - Preserves OFFICIAL / RESEARCH_ONLY / LEGACY / UNLABELED separation.
    - Intended for YELLOW evidence-gathering, not model activation.
    """
    settled_q = await session.execute(
        select(V3PaperTrade).where(
            V3PaperTrade.status == "SETTLED",
        )
    )
    settled = settled_q.scalars().all()
    diagnostics = _phase_a_diagnostics_from_trades(list(settled))
    return {
        "classification": "YELLOW",
        "phase": "A_READ_ONLY_DIAGNOSIS",
        "protected_semantic_change_activated": False,
        "trading_state_modified": False,
        "real_money_execution_enabled": False,
        "notes": [
            "Read-only diagnosis only. No calibration, eligibility, settlement, or readiness behavior is activated here.",
            "Any Phase B protected change requires a separate owner-approved YELLOW proposal/PR.",
        ],
        "as_of": datetime.now(timezone.utc).isoformat(),
        "diagnostics": diagnostics,
    }


# ---------------------------------------------------------------------------
# GET /analytics/v3/flags — feature flag status
# ---------------------------------------------------------------------------

@router.get("/analytics/v3/flags")
async def get_v3_flags(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Return the current value of all V3 feature flags."""
    from sqlalchemy import select
    result = await session.execute(
        select(AppSetting).where(
            AppSetting.key.in_(list(V3_FLAG_DEFAULTS.keys()))
        )
    )
    rows = {r.key: r.value for r in result.scalars().all()}

    flags: dict[str, Any] = {}
    for key, default in V3_FLAG_DEFAULTS.items():
        raw = rows.get(key, default)
        flags[key] = {
            "value": raw,
            "enabled": (raw or "").lower() in ("true", "1", "yes"),
            "default": default,
        }

    return {
        "flags": flags,
        "note": (
            "Set a flag to 'true' via the app_settings table to enable "
            "the corresponding V3 capability.  All flags default to 'false'."
        ),
    }


# ---------------------------------------------------------------------------
# GET /analytics/v3/ingestion-audit — per-city ingestion summary
# ---------------------------------------------------------------------------

@router.get("/analytics/v3/ingestion-audit")
async def get_ingestion_audit(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Return a per-city summary of all V3 historical ingestion runs.

    Shows:
    - Date range of accepted records in V3HistoricalRecord
    - Records accepted / rejected
    - Rejection reason breakdown
    - Missing observation dates
    - Last successful run timestamp
    - Provider and model coverage
    """
    # Per-city record stats from V3HistoricalRecord
    hist_stats_result = await session.execute(
        select(
            V3HistoricalRecord.city,
            V3HistoricalRecord.forecast_source,
            V3HistoricalRecord.forecast_model,
            func.min(V3HistoricalRecord.target_date).label("earliest_date"),
            func.max(V3HistoricalRecord.target_date).label("latest_date"),
            func.count().label("total_records"),
            func.sum(
                (V3HistoricalRecord.quality_status == "ok").cast(
                    __import__("sqlalchemy").Integer
                )
            ).label("ok_records"),
        ).group_by(
            V3HistoricalRecord.city,
            V3HistoricalRecord.forecast_source,
            V3HistoricalRecord.forecast_model,
        )
    )
    hist_stats = hist_stats_result.all()

    # Per-city latest ingestion log
    log_result = await session.execute(
        select(
            V3IngestionLog.city,
            V3IngestionLog.provider,
            V3IngestionLog.model,
            func.max(V3IngestionLog.completed_at).label("last_run_at"),
            func.sum(V3IngestionLog.records_attempted).label("total_attempted"),
            func.sum(V3IngestionLog.records_accepted).label("total_accepted"),
            func.sum(V3IngestionLog.records_rejected).label("total_rejected"),
        ).group_by(
            V3IngestionLog.city,
            V3IngestionLog.provider,
            V3IngestionLog.model,
        )
    )
    log_stats = log_result.all()

    # Latest per-city rejection details (from most recent log entry)
    latest_logs_result = await session.execute(
        select(V3IngestionLog)
        .order_by(V3IngestionLog.completed_at.desc())
        .limit(200)
    )
    latest_logs = latest_logs_result.scalars().all()
    # Build city → latest log lookup
    city_latest: dict[str, V3IngestionLog] = {}
    for log in latest_logs:
        if log.city not in city_latest:
            city_latest[log.city] = log

    # Assemble per-city entries
    city_map: dict[str, dict] = {}

    for row in hist_stats:
        key = row.city
        if key not in city_map:
            city_map[key] = {
                "city": row.city,
                "sources": [],
                "earliest_date": row.earliest_date,
                "latest_date": row.latest_date,
                "total_ok_records": 0,
                "total_records": 0,
                "last_run_at": None,
                "total_attempted": 0,
                "total_accepted": 0,
                "total_rejected": 0,
                "rejection_breakdown": {},
                "missing_observation_count": 0,
                "api_errors": [],
                "status": "no_data",
            }
        entry = city_map[key]
        entry["sources"].append({
            "provider": row.forecast_source,
            "model": row.forecast_model,
            "records": row.total_records,
            "ok_records": row.ok_records or 0,
        })
        if (entry["earliest_date"] is None or
                (row.earliest_date and row.earliest_date < entry["earliest_date"])):
            entry["earliest_date"] = row.earliest_date
        if (entry["latest_date"] is None or
                (row.latest_date and row.latest_date > entry["latest_date"])):
            entry["latest_date"] = row.latest_date
        entry["total_ok_records"] += row.ok_records or 0
        entry["total_records"] += row.total_records

    for row in log_stats:
        key = row.city
        if key not in city_map:
            city_map[key] = {
                "city": row.city,
                "sources": [],
                "earliest_date": None,
                "latest_date": None,
                "total_ok_records": 0,
                "total_records": 0,
                "last_run_at": None,
                "total_attempted": 0,
                "total_accepted": 0,
                "total_rejected": 0,
                "rejection_breakdown": {},
                "missing_observation_count": 0,
                "api_errors": [],
                "status": "no_data",
            }
        entry = city_map[key]
        entry["total_attempted"] += row.total_attempted or 0
        entry["total_accepted"] += row.total_accepted or 0
        entry["total_rejected"] += row.total_rejected or 0
        if row.last_run_at:
            last = row.last_run_at
            if entry["last_run_at"] is None or last > entry["last_run_at"]:
                entry["last_run_at"] = last.isoformat() if hasattr(last, "isoformat") else str(last)

    for city, log in city_latest.items():
        if city in city_map:
            entry = city_map[city]
            if log.rejection_breakdown:
                for k, v in log.rejection_breakdown.items():
                    entry["rejection_breakdown"][k] = (
                        entry["rejection_breakdown"].get(k, 0) + v
                    )
            if log.missing_observation_dates:
                entry["missing_observation_count"] += len(log.missing_observation_dates)
            if log.api_errors:
                entry["api_errors"].extend(log.api_errors[:3])
            entry["status"] = log.status or "unknown"

    cities_list = sorted(city_map.values(), key=lambda x: x["city"])

    total_records = sum(c["total_records"] for c in cities_list)
    total_ok = sum(c["total_ok_records"] for c in cities_list)

    return {
        "cities": cities_list,
        "summary": {
            "cities_with_data": len([c for c in cities_list if c["total_records"] > 0]),
            "total_records": total_records,
            "total_ok_records": total_ok,
            "total_cities_audited": len(cities_list),
        },
        "note": (
            "ok_records have both forecast and observation populated and passed "
            "look-ahead validation.  pending_observation records are awaiting "
            "NOAA GHCND observations."
        ),
    }


# ---------------------------------------------------------------------------
# POST /analytics/v3/run-ingestion — trigger ingestion (admin, flag-gated)
# ---------------------------------------------------------------------------

@router.post("/analytics/v3/run-ingestion")
async def trigger_ingestion(
    body: dict | None = None,
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Trigger V3 historical ingestion.  Gated on v3.ingestion_enabled.

    Optional body params:
    - start_date: YYYY-MM-DD (default: 2 years ago)
    - end_date:   YYYY-MM-DD (default: yesterday)
    - provider:   provider key (default: "open-meteo-forecast-history")
    - cities:     list of city names (default: all active verified cities)
    - lead_times: list of lead_time_hours (default: [24,48,72,96,120,144,168])
    """
    from app.services.v3_ingestion import run_ingestion

    params = body or {}
    try:
        result = await run_ingestion(
            session=session,
            start_date=params.get("start_date"),
            end_date=params.get("end_date"),
            provider_key=params.get("provider", "open-meteo-forecast-history"),
            lead_times_hours=params.get("lead_times"),
            cities=params.get("cities"),
        )
    except Exception as exc:
        logger.exception("[V3 Ingestion] Unhandled error in run-ingestion endpoint")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result


# ===========================================================================
# Phase 2: Error stats + walk-forward validation
# ===========================================================================

# ---------------------------------------------------------------------------
# POST /analytics/v3/compute-error-stats
# ---------------------------------------------------------------------------

@router.post("/analytics/v3/compute-error-stats")
async def compute_error_stats(
    body: dict | None = None,
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Build V3ErrorStats from V3HistoricalRecord rows.
    Gated on v3.validation_enabled feature flag.

    Optional body params:
    - hist_weight:    float in [0,1]; hist_weight + forward_weight must = 1.0
    - forward_weight: float in [0,1] (default 0.0 in Phase 2)
    - shrinkage_k:    float (default 30.0)

    Deletes all existing V3ErrorStats rows for the current preload_version
    and inserts fresh rows.  Safe to call multiple times — idempotent.
    """
    from app.services.v3_flags import get_v3_flag
    from app.services.v3_error_stats import V3StatsConfig, compute_v3_error_stats

    if not await get_v3_flag(session, "v3.validation_enabled"):
        raise HTTPException(
            status_code=403,
            detail=(
                "v3.validation_enabled is false.  Set it to 'true' in "
                "app_settings to enable Phase 2 endpoints."
            ),
        )

    params = body or {}
    try:
        hist_w    = float(params.get("hist_weight",    1.0))
        fwd_w     = float(params.get("forward_weight", 0.0))
        shrink_k  = float(params.get("shrinkage_k",    30.0))
        cfg = V3StatsConfig(
            hist_weight=hist_w,
            forward_weight=fwd_w,
            shrinkage_k=shrink_k,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid config: {exc}") from exc

    try:
        result = await compute_v3_error_stats(session, config=cfg)
    except Exception as exc:
        logger.exception("[V3 ErrorStats] Unhandled error in compute-error-stats")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result


# ---------------------------------------------------------------------------
# GET /analytics/v3/error-stats
# ---------------------------------------------------------------------------

@router.get("/analytics/v3/error-stats")
async def get_error_stats(
    city: str | None = None,
    model: str | None = None,
    fallback_level: int | None = None,
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Return computed V3ErrorStats rows.  Optional query params:
    - city:           filter by city name (or '__global__')
    - model:          filter by model (e.g. 'GFS')
    - fallback_level: filter by fallback level (0–4)

    Returns rows sorted by fallback_level ASC, city, season.
    """
    from app.services.v3_flags import get_v3_flag

    if not await get_v3_flag(session, "v3.validation_enabled"):
        raise HTTPException(
            status_code=403,
            detail="v3.validation_enabled is false.",
        )

    stmt = select(V3ErrorStats).order_by(
        V3ErrorStats.fallback_level,
        V3ErrorStats.city,
        V3ErrorStats.season,
        V3ErrorStats.lead_time_bucket,
    )
    if city is not None:
        stmt = stmt.where(V3ErrorStats.city == city)
    if model is not None:
        stmt = stmt.where(V3ErrorStats.model == model)
    if fallback_level is not None:
        stmt = stmt.where(V3ErrorStats.fallback_level == fallback_level)

    result = await session.execute(stmt)
    rows = result.scalars().all()

    def _row_dict(r: V3ErrorStats) -> dict:
        return {
            "id":               r.id,
            "preload_version":  r.preload_version,
            "city":             r.city,
            "model":            r.model,
            "lead_time_bucket": r.lead_time_bucket,
            "season":           r.season,
            "fallback_level":   r.fallback_level,
            "raw_sample_size":  r.raw_sample_size,
            "effective_n":      r.effective_n,
            "bias":             r.bias,
            "sigma_raw":        r.sigma_raw,
            "sigma_shrunk":     r.sigma_shrunk,
            "mae":              r.mae,
            "rmse":             r.rmse,
            # Two-component architecture: bias gate fields
            "bias_t_stat":              r.bias_t_stat,
            "bias_gate_passed":         r.bias_gate_passed,
            "bias_suppressed_reason":   r.bias_suppressed_reason,
            "last_computed_at": r.last_computed_at.isoformat() if r.last_computed_at else None,
        }

    return {
        "rows": [_row_dict(r) for r in rows],
        "total": len(rows),
        "note": (
            "sigma_shrunk is ALWAYS applied to predictions (calibration signal). "
            "bias is only applied to mu when bias_gate_passed=True (all three: "
            "n_eff>=50, |t|>=2.0, |bias|>=0.3°F). "
            "fallback_level 0 = most specific (city+model+lead+season), "
            "4 = global conservative prior."
        ),
    }


# ---------------------------------------------------------------------------
# GET /analytics/v3/walk-forward-report
# ---------------------------------------------------------------------------

@router.get("/analytics/v3/walk-forward-report")
async def get_walk_forward_report(
    include_records: bool = False,
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Run and return the V3 walk-forward validation report.
    Gated on v3.validation_enabled.

    Loads all 'ok' V3HistoricalRecord rows, runs the walk-forward validator
    entirely in memory, and returns the full report.  Expensive on large
    datasets — results are not cached; call once and save the output.

    Query params:
    - include_records: bool (default false) — include per-record detail
      in the response.  Can be large (732+ rows per report run).
    """
    from app.services.v3_flags import get_v3_flag
    from app.services.v3_walkforward import run_walk_forward_validation

    if not await get_v3_flag(session, "v3.validation_enabled"):
        raise HTTPException(
            status_code=403,
            detail="v3.validation_enabled is false.",
        )

    # Load all ok records
    result = await session.execute(
        select(V3HistoricalRecord).where(
            V3HistoricalRecord.quality_status == "ok",
            V3HistoricalRecord.signed_error.is_not(None),
            V3HistoricalRecord.forecast_tmax_f.is_not(None),
            V3HistoricalRecord.observed_tmax_f.is_not(None),
        ).order_by(V3HistoricalRecord.target_date)
    )
    records = result.scalars().all()

    try:
        report = run_walk_forward_validation(records)
    except Exception as exc:
        logger.exception("[V3 WalkForward] Unhandled error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    def _summary_dict(s) -> dict:
        return {
            "label":                  s.label,
            "n":                      s.n,
            "mae_raw":                s.mae_raw,
            "mae_adj":                s.mae_adj,
            "mae_delta":              s.mae_delta,
            "mae_ci95":               list(s.mae_ci95),
            "rmse_raw":               s.rmse_raw,
            "rmse_adj":               s.rmse_adj,
            "mean_error_raw":         s.mean_error_raw,
            "mean_error_adj":         s.mean_error_adj,
            "sigma_coverage_68pct":   s.sigma_coverage_68pct,
            "sigma_coverage_95pct":   s.sigma_coverage_95pct,
            "crps_raw":               s.crps_raw,
            "crps_adj":               s.crps_adj,
            "brier_score":            s.brier_score,
            "preload_hurt_n":         s.preload_hurt_n,
            "preload_hurt_pct":       s.preload_hurt_pct,
            "fallback_dist":          {str(k): v for k, v in s.fallback_dist.items()},
        }

    def _outlier_dict(o) -> dict:
        return {
            "target_date":   o.target_date,
            "city":          o.city,
            "season":        o.season,
            "forecast_f":    o.forecast_f,
            "observed_f":    o.observed_f,
            "raw_error":     o.raw_error,
            "adj_error":     o.adj_error,
            "bias_used":     o.bias_used,
            "sigma_used":    o.sigma_used,
            "z_score_raw":   o.z_score_raw,
            "preload_hurt":  o.preload_hurt,
        }

    def _cal_dict(c) -> dict:
        return {
            "bucket_lo":  c.bucket_lo,
            "bucket_hi":  c.bucket_hi,
            "count":      c.count,
            "empirical":  c.empirical,
            "mean_prob":  c.mean_prob,
        }

    response = {
        "generated_at":         report.generated_at,
        "config":               report.config,
        "total_records":        report.total_records,
        "train_cutoff_n":       report.train_cutoff_n,
        "test_n":               report.test_n,
        "verdict":              report.verdict,
        "verdict_note":         report.verdict_note,
        "overall":              _summary_dict(report.overall),
        "by_city":              {k: _summary_dict(v) for k, v in report.by_city.items()},
        "by_season":            {k: _summary_dict(v) for k, v in report.by_season.items()},
        "calibration":          [_cal_dict(c) for c in report.calibration],
        "outliers":             [_outlier_dict(o) for o in report.outliers],
        "preload_hurt_examples":[_outlier_dict(o) for o in report.preload_hurt_examples],
        "note": (
            "MAE delta < 0 means improvement; MAE delta > 0 means the preload "
            "made accuracy worse.  CRPS is the Continuous Ranked Probability Score "
            "(lower is better).  Brier score is for binary event P(T >= raw_forecast)."
        ),
    }

    if include_records:
        response["records"] = report.records

    return response


# ===========================================================================
# Phase 3 — Live predictions, paper trading & analytics
# ===========================================================================

# ---------------------------------------------------------------------------
# GET /analytics/v3/live-predictions
# ---------------------------------------------------------------------------

@router.get("/analytics/v3/live-predictions")
async def get_live_predictions(
    limit: int = 100,
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Return the most recent V3PredictionSnapshot rows (up to `limit`).
    Includes core probability decomposition and bias-gate fields.
    """
    from app.models_v3 import V3PredictionSnapshot

    result = await session.execute(
        select(V3PredictionSnapshot)
        .order_by(V3PredictionSnapshot.id.desc())
        .limit(max(1, min(limit, 500)))
    )
    rows = result.scalars().all()

    return {
        "count": len(rows),
        "predictions": [
            {
                "id":                    r.id,
                "created_at":            r.created_at.isoformat() if r.created_at else None,
                "market_ticker":         r.market_ticker,
                "comparison_group_id":   r.comparison_group_id,
                "forecast_date":         r.forecast_date,
                "forecast_value":        r.forecast_value,
                "settlement_variable":   r.settlement_variable,
                "settlement_operator":   r.settlement_operator,
                "settlement_threshold":  r.settlement_threshold,
                "contract_type":         r.contract_type,
                "ec_probability":        r.ec_probability,
                "market_probability":    r.market_probability,
                "claimed_edge":          r.claimed_edge,
                "confidence":            r.confidence,
                "historical_bias_adj":   r.historical_bias_adj,
                "historical_sigma":      r.historical_sigma,
                "final_bias":            r.final_bias,
                "final_sigma":           r.final_sigma,
                "fallback_level_used":   r.fallback_level_used,
                "hist_sample_count":     r.hist_sample_count,
                "effective_hist_n":      r.effective_hist_n,
                "bias_applied":          r.bias_applied,
                "bias_suppressed_reason": r.bias_suppressed_reason,
                "trade_decision":        r.trade_decision,
                "analysis_status":       r.analysis_status,
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# GET /analytics/v3/live-paper-trades
# ---------------------------------------------------------------------------

@router.get("/analytics/v3/live-paper-trades")
async def get_live_paper_trades(
    limit: int = 100,
    status: str | None = None,
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Return V3PaperTrade rows with P/L summary.
    Optional `?status=OPEN|SETTLED|PENDING_SETTLEMENT` filter.
    """
    from sqlalchemy import and_
    from app.models_v3 import V3PaperTrade

    q = select(V3PaperTrade).order_by(V3PaperTrade.id.desc())
    if status:
        q = q.where(V3PaperTrade.status == status.upper())
    q = q.limit(max(1, min(limit, 500)))

    result = await session.execute(q)
    trades = result.scalars().all()

    # Count observation-only predictions: PENDING snaps with no linked V3PaperTrade
    from app.models_v3 import V3PredictionSnapshot
    obs_q = await session.execute(
        select(func.count()).select_from(V3PredictionSnapshot).where(
            V3PredictionSnapshot.trade_decision == "PENDING",
            ~select(V3PaperTrade.id).where(
                V3PaperTrade.v3_snapshot_id == V3PredictionSnapshot.id
            ).exists(),
        )
    )
    observation_only_count = obs_q.scalar_one() or 0

    # Three-section performance summary
    sections = _compute_v3_trade_sections(list(trades), observation_only_count)

    return {
        "count": len(trades),
        "official_performance_note": (
            "Official ROI, win rate, and Brier score use EXECUTABLE trades only "
            "(is_executable=True). Non-executable signals are recorded for research "
            "but never contribute to headline metrics."
        ),
        "executable":       sections["executable"],
        "non_executable":   sections["non_executable"],
        "observation_only": sections["observation_only"],
        "trades": [
            {
                "id":                   t.id,
                "created_at":           t.created_at.isoformat() if t.created_at else None,
                "market_ticker":        t.market_ticker,
                "city":                 t.city,
                "contract_type":        t.contract_type,
                "comparison_group_id":  t.comparison_group_id,
                "direction":            t.direction,
                "ec_yes_probability":   t.ec_yes_probability,
                "market_yes_probability": t.market_yes_probability,
                "side_market_price":    t.side_market_price,
                "edge_pct_points":      t.edge_pct_points,
                "final_sigma":          t.final_sigma,
                "fallback_level_used":  t.fallback_level_used,
                "stake":                t.stake,
                "quantity":             t.quantity,
                "is_executable":        t.is_executable,
                "status":               t.status,
                "outcome":              t.outcome,
                "profit_loss":          t.profit_loss,
                "return_pct":           t.return_pct,
                "settlement_timestamp": (
                    t.settlement_timestamp.isoformat() if t.settlement_timestamp else None
                ),
            }
            for t in trades
        ],
    }


# ---------------------------------------------------------------------------
# GET /analytics/v3/live-comparison
# ---------------------------------------------------------------------------

@router.get("/analytics/v3/live-comparison")
async def get_live_comparison(
    limit: int = 100,
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Side-by-side V3 vs V2.1 probability comparison for markets where both
    predictions exist, joined on comparison_group_id.

    Useful for measuring probability divergence (V3 vs V2.1 on the same market).
    """
    from app.models import PredictionSnapshot
    from app.models_v3 import V3PredictionSnapshot

    # Get recent V3 snapshots with a comparison_group_id
    v3_q = await session.execute(
        select(V3PredictionSnapshot)
        .where(V3PredictionSnapshot.comparison_group_id.isnot(None))
        .order_by(V3PredictionSnapshot.id.desc())
        .limit(max(1, min(limit, 500)))
    )
    v3_snaps = v3_q.scalars().all()

    if not v3_snaps:
        return {"count": 0, "comparisons": [],
                "note": "No V3 predictions with comparison_group_id found."}

    # Fetch matching V2.1 snapshots
    comp_ids = [s.comparison_group_id for s in v3_snaps]
    v21_q = await session.execute(
        select(PredictionSnapshot).where(
            PredictionSnapshot.comparison_group_id.in_(comp_ids)
        )
    )
    v21_by_comp: dict[str, Any] = {
        s.comparison_group_id: s for s in v21_q.scalars().all()
    }

    comparisons = []
    for v3 in v3_snaps:
        v21 = v21_by_comp.get(v3.comparison_group_id)
        v3_prob = v3.ec_probability
        v21_prob = v21.ec_probability if v21 else None

        divergence = None
        if v3_prob is not None and v21_prob is not None:
            divergence = round(v3_prob - v21_prob, 4)

        comparisons.append({
            "market_ticker":          v3.market_ticker,
            "comparison_group_id":    v3.comparison_group_id,
            "forecast_date":          v3.forecast_date,
            "settlement_threshold":   v3.settlement_threshold,
            "contract_type":          v3.contract_type,
            # V3 side
            "v3_probability":         v3_prob,
            "v3_sigma":               v3.final_sigma,
            "v3_bias_applied":        v3.bias_applied,
            "v3_fallback_level":      v3.fallback_level_used,
            "v3_confidence":          v3.confidence,
            "v3_claimed_edge":        v3.claimed_edge,
            # V2.1 side
            "v21_probability":        v21_prob,
            "v21_created_at":         (
                v21.created_at.isoformat() if v21 and v21.created_at else None
            ),
            # Delta
            "probability_divergence": divergence,
        })

    return {
        "count":       len(comparisons),
        "comparisons": comparisons,
        "note":        "probability_divergence = v3_probability - v21_probability",
    }


# ---------------------------------------------------------------------------
# POST /analytics/v3/run-v3-predictions  (admin, manual trigger)
# ---------------------------------------------------------------------------

@router.post("/analytics/v3/run-v3-predictions")
async def trigger_v3_predictions(
    _user: dict = Depends(get_current_user),
) -> dict:
    """Manually trigger V3 predictions for all active markets."""
    from app.services.v3_predictor import run_v3_predictions
    stats = await run_v3_predictions()
    return {"triggered": True, "stats": stats}


# ---------------------------------------------------------------------------
# POST /analytics/v3/run-v3-paper-trading  (admin, manual trigger)
# ---------------------------------------------------------------------------

@router.post("/analytics/v3/run-v3-paper-trading")
async def trigger_v3_paper_trading(
    _user: dict = Depends(get_current_user),
) -> dict:
    """Manually trigger V3 paper-trading evaluation."""
    from app.services.v3_paper_trading import run_paper_trading_v3
    stats = await run_paper_trading_v3()
    return {"triggered": True, "stats": stats}


# ---------------------------------------------------------------------------
# POST /analytics/v3/run-v3-settlement  (admin, manual trigger)
# ---------------------------------------------------------------------------

@router.post("/analytics/v3/run-v3-settlement")
async def trigger_v3_settlement(
    _user: dict = Depends(get_current_user),
) -> dict:
    """Manually trigger V3 settlement check against Kalshi."""
    from app.services.v3_settlement import run_v3_settlement_job
    stats = await run_v3_settlement_job()
    return {"triggered": True, "stats": stats}


# ---------------------------------------------------------------------------
# POST /analytics/v3/enable-predictions  (admin)
# ---------------------------------------------------------------------------

@router.post("/analytics/v3/enable-predictions")
async def enable_v3_predictions(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Set v3.predictions_enabled = true."""
    return await _set_v3_flag(session, "v3.predictions_enabled", "true")


# ---------------------------------------------------------------------------
# POST /analytics/v3/enable-paper-trading  (admin)
# ---------------------------------------------------------------------------

@router.post("/analytics/v3/enable-paper-trading")
async def enable_v3_paper_trading(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Set v3.paper_trading_enabled = true."""
    return await _set_v3_flag(session, "v3.paper_trading_enabled", "true")


# ---------------------------------------------------------------------------
# Helper: set a V3 flag
# ---------------------------------------------------------------------------

async def _set_v3_flag(session: AsyncSession, key: str, value: str) -> dict:
    """Upsert a V3 feature flag in app_settings."""
    result = await session.execute(
        select(AppSetting).where(AppSetting.key == key)
    )
    row = result.scalar_one_or_none()
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    await session.commit()
    return {"key": key, "value": value, "updated": True}
