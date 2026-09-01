"""Prospective, shadow-only validation for the frozen V3.1 candidate.

The candidate was selected in PR #49 and is frozen before this prospective
cohort begins:

    p_shadow = 0.50 * p_v3_chosen_side + 0.50 * p_kalshi_chosen_side

This module records immutable decision-time inputs and computes read-only
forward metrics.  It is deliberately not imported by forecasting,
eligibility, ranking, readiness, bankroll, or execution code.
"""
from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from typing import Any

from sqlalchemy import select

from app import database
from app.models_v3 import V31ShadowObservation, V3PaperTrade

logger = logging.getLogger(__name__)

CANDIDATE_VERSION = "v31-shadow-pr49-50v3-50market-v1"
V3_WEIGHT = 0.50
MARKET_WEIGHT = 0.50
EVIDENCE_CLASSES = ("OFFICIAL", "RESEARCH_ONLY", "UNCLASSIFIED")

# Prospective event counts are the governing milestones because multiple
# correlated contracts can represent one weather event.  These are evidence-
# interpretation labels only; they do not alter any readiness threshold.
EVENT_MILESTONES = (
    (25, "MINIMUM_SIGNAL", "Minimum event count for an initial comparative signal."),
    (50, "INTERMEDIATE", "Intermediate event count for checking stability across cohorts."),
    (100, "STRONGER_EVIDENCE", "Stronger event count; still requires human review before any proposal."),
)


def evidence_class(raw: str | None) -> str:
    """Return one of the three prospective evidence populations."""
    value = (raw or "").strip().upper()
    return value if value in ("OFFICIAL", "RESEARCH_ONLY") else "UNCLASSIFIED"


def event_key(*, city: str | None, target_date: str | None, weather_variable: str | None) -> str:
    """Match PR #49's correlated weather-event/date grouping exactly."""
    return "|".join((
        city or "UNKNOWN_CITY",
        str(target_date or "UNKNOWN_DATE")[:10],
        weather_variable or "UNKNOWN_VAR",
    ))


def build_shadow_payload(trade: V3PaperTrade, *, station_id: str | None) -> dict[str, Any] | None:
    """Freeze a new trade's candidate inputs, or return ``None`` if incomplete.

    ``side_market_price`` is intentionally used as the market probability.  It
    is the contemporaneous chosen-side price used by the PR #49 Accuracy Lab,
    not a later quote and not the snapshot's display-only market estimate.
    """
    v3_probability = trade.ec_side_probability
    market_probability = trade.side_market_price
    if v3_probability is None or market_probability is None:
        return None
    v3_probability = min(1.0, max(0.0, float(v3_probability)))
    market_probability = min(1.0, max(0.0, float(market_probability)))
    blended = V3_WEIGHT * v3_probability + MARKET_WEIGHT * market_probability
    return {
        "candidate_version": CANDIDATE_VERSION,
        "v3_weight": V3_WEIGHT,
        "market_weight": MARKET_WEIGHT,
        "v3_paper_trade_id": trade.id,
        "market_ticker": trade.market_ticker,
        "event_key": event_key(
            city=trade.city,
            target_date=trade.target_settlement_date,
            weather_variable=trade.weather_variable,
        ),
        "collection_batch_id": trade.collection_batch_id,
        "decision_timestamp": trade.decision_timestamp,
        "direction": trade.direction,
        "v3_yes_probability": trade.ec_yes_probability,
        "market_yes_probability": trade.market_yes_probability,
        "v3_side_probability": v3_probability,
        "market_side_probability": market_probability,
        "blended_side_probability": blended,
        "model_market_disagreement": abs(v3_probability - market_probability),
        "city": trade.city,
        "station_id": station_id,
        "weather_variable": trade.weather_variable,
        "contract_type": trade.contract_type,
        "target_settlement_date": trade.target_settlement_date,
        "lead_time_days": trade.lead_time_days,
        "evidence_class": evidence_class(trade.eligibility_status),
        "eligibility_reason": trade.eligibility_reason,
    }


async def persist_shadow_payload(payload: dict[str, Any]) -> bool:
    """Persist one observation in an isolated, fail-closed transaction.

    The caller invokes this only after the source trade commits.  A shadow
    failure is logged and swallowed, so it cannot change production V3 state.
    Duplicate source-trade IDs are ignored idempotently.
    """
    if database.AsyncSessionLocal is None:
        logger.warning("V3.1 shadow observation skipped: database not initialised")
        return False
    try:
        async with database.AsyncSessionLocal() as session:
            existing = await session.execute(
                select(V31ShadowObservation.id).where(
                    V31ShadowObservation.v3_paper_trade_id == payload["v3_paper_trade_id"]
                )
            )
            if existing.scalar_one_or_none() is not None:
                return True
            session.add(V31ShadowObservation(**payload))
            await session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - shadow must never affect V3
        logger.warning(
            "V3.1 shadow write failed for trade %s: %s",
            payload.get("v3_paper_trade_id"), exc, exc_info=True,
        )
        return False


def _actual(outcome: str | None) -> float | None:
    value = (outcome or "").upper()
    if value == "WIN":
        return 1.0
    if value == "LOSS":
        return 0.0
    return None


def _calibration(rows: list[dict[str, Any]], probability_key: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index in range(10):
        lower = index / 10.0
        upper = 1.0 if index == 9 else (index + 1) / 10.0
        bucket = [
            row for row in rows
            if row.get(probability_key) is not None
            and row.get("actual") is not None
            and (
                lower <= float(row[probability_key]) <= upper
                if index == 9
                else lower <= float(row[probability_key]) < upper
            )
        ]
        if not bucket:
            result.append({
                "bucket": f"{index * 10:02d}-{100 if index == 9 else index * 10 + 9:02d}%",
                "n": 0,
                "mean_probability": None,
                "observed_rate": None,
                "calibration_error_pp": None,
            })
            continue
        mean_probability = statistics.mean(float(row[probability_key]) for row in bucket)
        observed_rate = statistics.mean(float(row["actual"]) for row in bucket)
        result.append({
            "bucket": f"{index * 10:02d}-{100 if index == 9 else index * 10 + 9:02d}%",
            "n": len(bucket),
            "mean_probability": round(mean_probability, 6),
            "observed_rate": round(observed_rate, 6),
            "calibration_error_pp": round((observed_rate - mean_probability) * 100, 3),
        })
    return result


def _metrics(rows: list[dict[str, Any]], probability_key: str) -> dict[str, Any]:
    valid = [
        row for row in rows
        if row.get(probability_key) is not None and row.get("actual") is not None
    ]
    calibration = _calibration(valid, probability_key)
    calibration_errors = [
        abs(float(bucket["calibration_error_pp"]))
        for bucket in calibration if bucket["calibration_error_pp"] is not None
    ]
    if not valid:
        return {
            "n": 0,
            "event_n": 0,
            "wins": 0,
            "losses": 0,
            "brier": None,
            "event_level_brier": None,
            "mean_abs_calibration_error_pp": None,
            "log_loss": None,
            "event_directional_accuracy_pct": None,
            "calibration": calibration,
        }

    wins = sum(1 for row in valid if row["actual"] == 1.0)
    brier = statistics.mean(
        (float(row[probability_key]) - float(row["actual"])) ** 2 for row in valid
    )
    epsilon = 1e-15
    log_loss = statistics.mean(
        -(
            float(row["actual"]) * math.log(min(1 - epsilon, max(epsilon, float(row[probability_key]))))
            + (1 - float(row["actual"]))
            * math.log(min(1 - epsilon, max(epsilon, 1 - float(row[probability_key]))))
        )
        for row in valid
    )

    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in valid:
        events[row["event_key"]].append(row)
    event_briers: list[float] = []
    event_direction_correct = 0
    for event_rows in events.values():
        event_briers.append(statistics.mean(
            (float(row[probability_key]) - float(row["actual"])) ** 2
            for row in event_rows
        ))
        mean_probability = statistics.mean(float(row[probability_key]) for row in event_rows)
        mean_actual = statistics.mean(float(row["actual"]) for row in event_rows)
        if (mean_probability >= 0.5) == (mean_actual >= 0.5):
            event_direction_correct += 1

    return {
        "n": len(valid),
        "event_n": len(events),
        "wins": wins,
        "losses": len(valid) - wins,
        "brier": round(brier, 6),
        "event_level_brier": round(statistics.mean(event_briers), 6),
        "mean_abs_calibration_error_pp": (
            round(statistics.mean(calibration_errors), 3) if calibration_errors else None
        ),
        "log_loss": round(log_loss, 6),
        "event_directional_accuracy_pct": round(
            event_direction_correct / len(events) * 100, 3
        ),
        "calibration": calibration,
    }


def _milestones(event_n: int) -> dict[str, Any]:
    rows = [
        {
            "event_n": threshold,
            "label": label,
            "description": description,
            "reached": event_n >= threshold,
            "remaining_events": max(0, threshold - event_n),
        }
        for threshold, label, description in EVENT_MILESTONES
    ]
    next_row = next((row for row in rows if not row["reached"]), None)
    return {
        "governing_unit": "distinct correlated weather events",
        "current_event_n": event_n,
        "too_small_for_comparative_conclusion": event_n < EVENT_MILESTONES[0][0],
        "next": next_row,
        "levels": rows,
        "readiness_semantics_changed": False,
    }


def build_shadow_report(
    joined_rows: list[tuple[V31ShadowObservation, V3PaperTrade | None]],
) -> dict[str, Any]:
    """Build cumulative forward metrics with evidence classes kept separate."""
    records: list[dict[str, Any]] = []
    for observation, trade in joined_rows:
        outcome = trade.outcome if trade is not None else None
        records.append({
            "observation_id": observation.id,
            "v3_paper_trade_id": observation.v3_paper_trade_id,
            "market_ticker": observation.market_ticker,
            "event_key": observation.event_key,
            "evidence_class": evidence_class(observation.evidence_class),
            "v3_probability": observation.v3_side_probability,
            "market_probability": observation.market_side_probability,
            "blend_probability": observation.blended_side_probability,
            "actual": _actual(outcome),
            "status": trade.status if trade is not None else "SOURCE_TRADE_MISSING",
            "outcome": outcome,
        })

    populations: dict[str, Any] = {}
    for population in EVIDENCE_CLASSES:
        population_rows = [row for row in records if row["evidence_class"] == population]
        event_n = len({row["event_key"] for row in population_rows if row["actual"] is not None})
        populations[population] = {
            "observations": len(population_rows),
            "open_or_unsettled": sum(1 for row in population_rows if row["actual"] is None),
            "settled": sum(1 for row in population_rows if row["actual"] is not None),
            "metrics": {
                "v3": _metrics(population_rows, "v3_probability"),
                "frozen_50_50_blend": _metrics(population_rows, "blend_probability"),
                "kalshi": _metrics(population_rows, "market_probability"),
            },
            "milestones": _milestones(event_n),
        }

    return {
        "candidate": {
            "version": CANDIDATE_VERSION,
            "formula": "0.50 * V3 chosen-side probability + 0.50 * contemporaneous Kalshi chosen-side executable price",
            "v3_weight": V3_WEIGHT,
            "market_weight": MARKET_WEIGHT,
            "frozen": True,
            "historical_holdout_used_for_retuning": False,
        },
        "total_observations": len(records),
        "populations": populations,
        "safety": {
            "shadow_only": True,
            "production_probability_changed": False,
            "eligibility_changed": False,
            "settlement_changed": False,
            "quote_freshness_changed": False,
            "real_money_capability": False,
        },
    }


async def load_shadow_report(session) -> dict[str, Any]:
    """Load linked prospective observations and authoritative outcomes read-only."""
    result = await session.execute(
        select(V31ShadowObservation, V3PaperTrade)
        .outerjoin(
            V3PaperTrade,
            V3PaperTrade.id == V31ShadowObservation.v3_paper_trade_id,
        )
        .where(V31ShadowObservation.candidate_version == CANDIDATE_VERSION)
        .order_by(V31ShadowObservation.id.asc())
    )
    return build_shadow_report(list(result.all()))
