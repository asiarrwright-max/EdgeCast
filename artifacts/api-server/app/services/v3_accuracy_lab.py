from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable


_EPS = 1e-6
_INSUFFICIENT_N = 10


@dataclass(frozen=True)
class CandidateResult:
    name: str
    params: dict[str, Any]
    holdout: dict[str, Any]
    validation: dict[str, Any]


def _eligibility_class(raw: str | None) -> str:
    v = (raw or "").strip().upper()
    if v == "OFFICIAL":
        return "OFFICIAL"
    if v == "RESEARCH_ONLY":
        return "RESEARCH_ONLY"
    return "UNCLASSIFIED"


def _side_model_probability(trade: Any) -> float | None:
    p = getattr(trade, "ec_side_probability", None)
    if p is not None:
        return min(1.0, max(0.0, float(p)))
    p_yes = getattr(trade, "ec_yes_probability", None)
    if p_yes is None:
        return None
    p_yes = min(1.0, max(0.0, float(p_yes)))
    direction = (getattr(trade, "direction", "") or "").upper()
    if direction == "NO":
        return 1.0 - p_yes
    return p_yes


def _actual_side_outcome(trade: Any) -> float | None:
    outcome = (getattr(trade, "outcome", "") or "").upper()
    if outcome == "WIN":
        return 1.0
    if outcome == "LOSS":
        return 0.0
    return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    raw = str(value)[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _event_key(trade: Any) -> str:
    city = getattr(trade, "city", None) or "UNKNOWN_CITY"
    target = str(getattr(trade, "target_settlement_date", None) or "UNKNOWN_DATE")[:10]
    variable = getattr(trade, "weather_variable", None) or "UNKNOWN_VAR"
    return f"{city}|{target}|{variable}"


def _prob_bucket(p: float | None) -> str:
    if p is None:
        return "missing"
    idx = min(9, max(0, int(p * 10)))
    lo = idx * 10
    hi = lo + 9 if idx < 9 else 100
    return f"{lo:02d}-{hi:02d}%"


def _lead_bucket(days: int | None) -> str:
    if days is None:
        return "unknown"
    if days <= 1:
        return "0-1d"
    if days <= 3:
        return "2-3d"
    if days <= 7:
        return "4-7d"
    return "8d+"


def _market_bucket(price: float | None) -> str:
    if price is None:
        return "missing"
    if price < 0.2:
        return "<0.20"
    if price < 0.4:
        return "0.20-0.39"
    if price < 0.6:
        return "0.40-0.59"
    if price < 0.8:
        return "0.60-0.79"
    return "0.80-1.00"


def _uncertainty_bucket(sig: float | None) -> str:
    if sig is None:
        return "missing"
    if sig < 2.0:
        return "<2F"
    if sig < 4.0:
        return "2-3.9F"
    if sig < 6.0:
        return "4-5.9F"
    return "6F+"


def _disagreement_bucket(model_prob: float | None, market_prob: float | None) -> str:
    if model_prob is None or market_prob is None:
        return "missing"
    d = abs(model_prob - market_prob)
    if d < 0.05:
        return "<5pp"
    if d < 0.10:
        return "5-9pp"
    if d < 0.20:
        return "10-19pp"
    return "20pp+"


def _clamp(p: float | None) -> float | None:
    if p is None:
        return None
    return min(1.0, max(0.0, float(p)))


def _calibration(rows: list[dict[str, Any]], prob_key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(10):
        lo = i / 10.0
        hi = 1.0 if i == 9 else (i + 1) / 10.0
        bucket = [
            r for r in rows
            if r.get(prob_key) is not None
            and (lo <= float(r[prob_key]) < hi if i < 9 else lo <= float(r[prob_key]) <= hi)
            and r.get("actual") is not None
        ]
        if not bucket:
            out.append({
                "bucket": f"{i*10:02d}-{100 if i == 9 else i*10+9:02d}%",
                "count": 0,
                "mean_pred_pct": None,
                "observed_rate_pct": None,
                "calibration_error_pp": None,
            })
            continue
        mean_p = sum(float(r[prob_key]) for r in bucket) / len(bucket)
        obs = sum(float(r["actual"]) for r in bucket) / len(bucket)
        out.append({
            "bucket": f"{i*10:02d}-{100 if i == 9 else i*10+9:02d}%",
            "count": len(bucket),
            "mean_pred_pct": round(mean_p * 100, 2),
            "observed_rate_pct": round(obs * 100, 2),
            "calibration_error_pp": round((obs - mean_p) * 100, 2),
        })
    return out


def _event_level_accuracy(rows: list[dict[str, Any]], prob_key: str) -> float | None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get(prob_key) is None or r.get("actual") is None:
            continue
        grouped[r["event_key"]].append(r)
    if not grouped:
        return None
    correct = 0
    total = 0
    for event_rows in grouped.values():
        p = sum(float(r[prob_key]) for r in event_rows) / len(event_rows)
        a = sum(float(r["actual"]) for r in event_rows) / len(event_rows)
        pred = 1.0 if p >= 0.5 else 0.0
        actual = 1.0 if a >= 0.5 else 0.0
        total += 1
        if pred == actual:
            correct += 1
    return round(correct / total * 100, 2) if total else None


def _metrics(rows: list[dict[str, Any]], prob_key: str) -> dict[str, Any]:
    valid = [
        r for r in rows
        if r.get(prob_key) is not None and r.get("actual") is not None
    ]
    n = len(valid)
    if n == 0:
        return {
            "n": 0,
            "wins": 0,
            "losses": 0,
            "realized_rate_pct": None,
            "brier": None,
            "log_loss": None,
            "log_loss_supported_n": 0,
            "mean_abs_calibration_error_pp": None,
            "event_level_accuracy_pct": None,
            "paper_return_sensitivity_roi_pct": None,
            "calibration": _calibration([], prob_key),
        }

    wins = sum(1 for r in valid if r["actual"] == 1.0)
    losses = n - wins
    brier = sum((float(r[prob_key]) - float(r["actual"])) ** 2 for r in valid) / n

    log_terms = []
    for r in valid:
        p = float(r[prob_key])
        y = float(r["actual"])
        if p <= 0.0 or p >= 1.0:
            continue
        log_terms.append(-(y * math.log(p) + (1 - y) * math.log(1 - p)))
    log_loss = (sum(log_terms) / len(log_terms)) if log_terms else None

    calibration = _calibration(valid, prob_key)
    cal_errors = [abs(b["calibration_error_pp"]) for b in calibration if b["calibration_error_pp"] is not None]
    avg_cal_err = (sum(cal_errors) / len(cal_errors)) if cal_errors else None

    high_conf = [r for r in valid if abs(float(r[prob_key]) - 0.5) >= 0.1]
    roi = None
    if high_conf:
        stake = sum(float(r.get("stake") or 0.0) for r in high_conf)
        if stake > 0:
            pl = sum(float(r.get("profit_loss") or 0.0) for r in high_conf)
            roi = round(pl / stake * 100, 2)

    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "realized_rate_pct": round(wins / n * 100, 2),
        "brier": round(brier, 4),
        "log_loss": round(log_loss, 4) if log_loss is not None else None,
        "log_loss_supported_n": len(log_terms),
        "mean_abs_calibration_error_pp": round(avg_cal_err, 3) if avg_cal_err is not None else None,
        "event_level_accuracy_pct": _event_level_accuracy(valid, prob_key),
        "paper_return_sensitivity_roi_pct": roi,
        "calibration": calibration,
    }


def _breakdown(rows: list[dict[str, Any]], key_fn: Callable[[dict[str, Any]], str]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[key_fn(row)].append(row)
    out: list[dict[str, Any]] = []
    for label in sorted(groups):
        m = _metrics(groups[label], "model_prob")
        out.append({
            "label": label,
            "n": m["n"],
            "brier": m["brier"],
            "realized_rate_pct": m["realized_rate_pct"],
            "insufficient_n": m["n"] < _INSUFFICIENT_N,
        })
    return out


def _event_group_breakdown(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["event_key"]].append(row)
    return {
        "event_count": len(grouped),
        "min_contracts_per_event": min((len(v) for v in grouped.values()), default=0),
        "max_contracts_per_event": max((len(v) for v in grouped.values()), default=0),
        "mean_contracts_per_event": round(
            statistics.mean(len(v) for v in grouped.values()), 2
        ) if grouped else 0.0,
    }


def _split_partitions(event_keys: list[str], event_dates: dict[str, date | None]) -> dict[str, set[str]]:
    sortable = sorted(
        event_keys,
        key=lambda k: (event_dates.get(k) or date.min, k),
    )
    n = len(sortable)
    if n == 0:
        return {"development": set(), "validation": set(), "holdout": set()}
    if n < 3:
        d = max(1, n - 1)
        return {
            "development": set(sortable[:d]),
            "validation": set(),
            "holdout": set(sortable[d:]),
        }
    dev_n = max(1, int(round(n * 0.6)))
    val_n = max(1, int(round(n * 0.2)))
    if dev_n + val_n >= n:
        val_n = max(1, n - dev_n - 1)
    if dev_n + val_n >= n:
        dev_n = max(1, dev_n - 1)
    if dev_n + val_n >= n:
        val_n = max(0, n - dev_n - 1)
    return {
        "development": set(sortable[:dev_n]),
        "validation": set(sortable[dev_n:dev_n + val_n]),
        "holdout": set(sortable[dev_n + val_n:]),
    }


def _rows_for_events(rows: list[dict[str, Any]], events: set[str]) -> list[dict[str, Any]]:
    return [r for r in rows if r["event_key"] in events]


def _with_prob(rows: list[dict[str, Any]], key: str, fn: Callable[[dict[str, Any]], float | None]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        p = _clamp(fn(r))
        out.append({**r, key: p})
    return out


def _fit_best_alpha(validation_rows: list[dict[str, Any]]) -> float:
    best_alpha = 0.5
    best_brier = float("inf")
    for alpha in [i / 10.0 for i in range(0, 10)]:
        scored = _with_prob(
            validation_rows,
            "candidate_prob",
            lambda r, a=alpha: 0.5 + (1.0 - a) * (float(r["model_prob"]) - 0.5)
            if r.get("model_prob") is not None else None,
        )
        brier = _metrics(scored, "candidate_prob")["brier"]
        if brier is not None and brier < best_brier:
            best_brier = brier
            best_alpha = alpha
    return best_alpha


def _fit_group_rates(rows: list[dict[str, Any]], key: str) -> dict[str, tuple[float, int]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.get("actual") is None:
            continue
        grouped[str(r.get(key, "unknown"))].append(float(r["actual"]))
    rates: dict[str, tuple[float, int]] = {}
    for k, vals in grouped.items():
        n = len(vals)
        # conservative Beta(1,1) shrinkage
        rate = (sum(vals) + 1.0) / (n + 2.0)
        rates[k] = (rate, n)
    return rates


def _fit_city_error(rows: list[dict[str, Any]]) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.get("model_prob") is None or r.get("actual") is None:
            continue
        grouped[r["city"]].append(abs(float(r["model_prob"]) - float(r["actual"])))
    return {
        city: min(0.8, max(0.05, statistics.mean(vals)))
        for city, vals in grouped.items() if vals
    }


def _fit_disagreement_threshold(validation_rows: list[dict[str, Any]]) -> float:
    best_t = 0.1
    best_brier = float("inf")
    for t in (0.05, 0.1, 0.15, 0.2):
        scored = _with_prob(
            validation_rows,
            "candidate_prob",
            lambda r, th=t: (
                0.5 + 0.5 * (float(r["model_prob"]) - 0.5)
                if r.get("model_prob") is not None and r.get("market_prob") is not None
                and abs(float(r["model_prob"]) - float(r["market_prob"])) >= th
                else r.get("model_prob")
            ),
        )
        brier = _metrics(scored, "candidate_prob")["brier"]
        if brier is not None and brier < best_brier:
            best_brier = brier
            best_t = t
    return best_t


def _fit_market_blend_weight(validation_rows: list[dict[str, Any]]) -> float:
    best_w = 0.0
    best_brier = float("inf")
    for w in (0.0, 0.25, 0.5):
        scored = _with_prob(
            validation_rows,
            "candidate_prob",
            lambda r, weight=w: (
                weight * float(r["market_prob"]) + (1 - weight) * float(r["model_prob"])
                if r.get("model_prob") is not None and r.get("market_prob") is not None
                else r.get("model_prob")
            ),
        )
        brier = _metrics(scored, "candidate_prob")["brier"]
        if brier is not None and brier < best_brier:
            best_brier = brier
            best_w = w
    return best_w


def _candidate_ranking_key(c: CandidateResult) -> tuple[float, float, float]:
    brier = c.holdout.get("brier")
    cal = c.holdout.get("mean_abs_calibration_error_pp")
    log_loss = c.holdout.get("log_loss")
    return (
        brier if brier is not None else 999.0,
        cal if cal is not None else 999.0,
        log_loss if log_loss is not None else 999.0,
    )


def build_settled_v3_accuracy_lab_report(
    settled_trades: list[Any],
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    as_of = as_of or datetime.now(timezone.utc)

    rows: list[dict[str, Any]] = []
    for t in settled_trades:
        actual = _actual_side_outcome(t)
        model_prob = _side_model_probability(t)
        market_prob = _clamp(getattr(t, "side_market_price", None))
        event_key = _event_key(t)
        target_date = _parse_date(getattr(t, "target_settlement_date", None))
        city = getattr(t, "city", None) or "UNKNOWN_CITY"
        strategy = getattr(t, "strategy_version", None) or "unknown"
        contract_type = getattr(t, "contract_type", None) or "unknown"
        lead_days = getattr(t, "lead_time_days", None)
        sigma = getattr(t, "final_sigma", None)
        if sigma is None:
            sigma = getattr(t, "historical_sigma", None)
        rows.append({
            "actual": actual,
            "model_prob": model_prob,
            "market_prob": market_prob,
            "event_key": event_key,
            "event_date": target_date,
            "city": city,
            "contract_type": contract_type,
            "lead_time_bucket": _lead_bucket(lead_days),
            "probability_bucket": _prob_bucket(model_prob),
            "disagreement_bucket": _disagreement_bucket(model_prob, market_prob),
            "market_bucket": _market_bucket(market_prob),
            "station_bucket": f"{city}|station_verified={bool(getattr(t, 'station_verified', False))}",
            "uncertainty_bucket": _uncertainty_bucket(sigma),
            "strategy_version": strategy,
            "eligibility_class": _eligibility_class(getattr(t, "eligibility_status", None)),
            "profit_loss": getattr(t, "profit_loss", None),
            "stake": getattr(t, "stake", None),
        })

    eligibility_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        eligibility_counts[r["eligibility_class"]] += 1
    for required in ("OFFICIAL", "RESEARCH_ONLY", "UNCLASSIFIED"):
        eligibility_counts.setdefault(required, 0)

    event_dates: dict[str, date | None] = {}
    for r in rows:
        if r["event_key"] not in event_dates:
            event_dates[r["event_key"]] = r["event_date"]
        elif event_dates[r["event_key"]] is None and r["event_date"] is not None:
            event_dates[r["event_key"]] = r["event_date"]
    partitions = _split_partitions(sorted(event_dates), event_dates)
    dev_rows = _rows_for_events(rows, partitions["development"])
    val_rows = _rows_for_events(rows, partitions["validation"])
    holdout_rows = _rows_for_events(rows, partitions["holdout"])

    research_rows = [r for r in rows if r["eligibility_class"] == "RESEARCH_ONLY" and r["actual"] is not None]
    dev_research = [r for r in dev_rows if r["eligibility_class"] == "RESEARCH_ONLY" and r["actual"] is not None]
    val_research = [r for r in val_rows if r["eligibility_class"] == "RESEARCH_ONLY" and r["actual"] is not None]
    holdout_research = [r for r in holdout_rows if r["eligibility_class"] == "RESEARCH_ONLY" and r["actual"] is not None]

    baseline_metrics = _metrics(research_rows, "model_prob")
    kalshi_baseline = _metrics(
        [r for r in research_rows if r.get("market_prob") is not None],
        "market_prob",
    )

    train_rows = dev_research + val_research
    has_validation_partition = len(val_research) > 0
    # Fit on validation when available, otherwise use pre-holdout rows only.
    fit_rows = val_research or dev_research or train_rows
    alpha = _fit_best_alpha(fit_rows)
    contract_rates = _fit_group_rates(dev_research or train_rows, "contract_type")
    lead_rates = _fit_group_rates(dev_research or train_rows, "lead_time_bucket")
    city_errors = _fit_city_error(dev_research or train_rows)
    disagreement_t = _fit_disagreement_threshold(fit_rows)
    market_w = _fit_market_blend_weight(fit_rows)

    def _apply_group_blend(r: dict[str, Any], key: str, rates: dict[str, tuple[float, int]]) -> float | None:
        p = r.get("model_prob")
        if p is None:
            return None
        k = str(r.get(key, "unknown"))
        rate_n = rates.get(k)
        if rate_n is None:
            return p
        rate, n = rate_n
        w = min(0.35, n / (n + 20.0))
        return (1 - w) * float(p) + w * float(rate)

    candidates: list[CandidateResult] = []
    candidate_defs: list[tuple[str, dict[str, Any], Callable[[dict[str, Any]], float | None]]] = [
        ("v3_baseline", {}, lambda r: r.get("model_prob")),
        ("global_recalibration_shrinkage", {"alpha": alpha},
         lambda r, a=alpha: 0.5 + (1.0 - a) * (float(r["model_prob"]) - 0.5) if r.get("model_prob") is not None else None),
        ("threshold_vs_range_calibration", {"groups": len(contract_rates)},
         lambda r: _apply_group_blend(r, "contract_type", contract_rates)),
        ("lead_time_aware_calibration", {"groups": len(lead_rates)},
         lambda r: _apply_group_blend(r, "lead_time_bucket", lead_rates)),
        ("city_error_uncertainty_shrinkage", {"cities": len(city_errors)},
         lambda r: 0.5 + (1 - city_errors.get(r["city"], 0.2)) * (float(r["model_prob"]) - 0.5) if r.get("model_prob") is not None else None),
        ("model_disagreement_uncertainty_widening", {"threshold_pp": round(disagreement_t * 100, 1)},
         lambda r, t=disagreement_t: (
             0.5 + 0.5 * (float(r["model_prob"]) - 0.5)
             if r.get("model_prob") is not None and r.get("market_prob") is not None
             and abs(float(r["model_prob"]) - float(r["market_prob"])) >= t
             else r.get("model_prob")
         )),
        ("conservative_caps_shrinkage", {"cap_lo": 0.1, "cap_hi": 0.9},
         lambda r: min(0.9, max(0.1, float(r["model_prob"]))) if r.get("model_prob") is not None else None),
        ("market_blend_benchmark", {"market_weight": market_w},
         lambda r, w=market_w: (
             w * float(r["market_prob"]) + (1 - w) * float(r["model_prob"])
             if r.get("model_prob") is not None and r.get("market_prob") is not None
             else r.get("model_prob")
         )),
    ]

    for name, params, fn in candidate_defs:
        hold_scored = _with_prob(holdout_research, "candidate_prob", fn)
        val_scored = _with_prob(val_research, "candidate_prob", fn)
        candidates.append(CandidateResult(
            name=name,
            params=params,
            holdout=_metrics(hold_scored, "candidate_prob"),
            validation=_metrics(val_scored, "candidate_prob"),
        ))

    ranked = sorted(candidates, key=_candidate_ranking_key)
    baseline = next((c for c in ranked if c.name == "v3_baseline"), None)
    top = ranked[0] if ranked else None
    recommendation = "more_research"
    if top and baseline and top.name != baseline.name and _candidate_ranking_key(top) < _candidate_ranking_key(baseline):
        recommendation = "candidate_for_v3_1_shadow"
    elif baseline and top and top.name == baseline.name:
        recommendation = "no_change"

    leakage_checks = {
        "no_event_overlap": (
            partitions["development"].isdisjoint(partitions["validation"])
            and partitions["development"].isdisjoint(partitions["holdout"])
            and partitions["validation"].isdisjoint(partitions["holdout"])
        ),
        "candidate_fit_uses_holdout": False,
        "chronological_boundaries_non_decreasing": None,
    }
    dev_dates = [event_dates[e] for e in partitions["development"] if event_dates.get(e)]
    val_dates = [event_dates[e] for e in partitions["validation"] if event_dates.get(e)]
    hold_dates = [event_dates[e] for e in partitions["holdout"] if event_dates.get(e)]
    chronology_checks: list[bool] = []
    if dev_dates and val_dates:
        chronology_checks.append(max(dev_dates) <= min(val_dates))
    if val_dates and hold_dates:
        chronology_checks.append(max(val_dates) <= min(hold_dates))
    if dev_dates and hold_dates and not val_dates:
        chronology_checks.append(max(dev_dates) <= min(hold_dates))
    if chronology_checks:
        leakage_checks["chronological_boundaries_non_decreasing"] = all(chronology_checks)

    return {
        "classification": "GREEN_READ_ONLY_RESEARCH",
        "generated_at": as_of.isoformat(),
        "frozen_population": {
            "total_settled_v3_rows": len(rows),
            "eligibility_counts": dict(sorted(eligibility_counts.items())),
            "event_grouping": _event_group_breakdown(rows),
            "freeze_note": "Read-only snapshot from settled v3_paper_trades rows. No records modified.",
        },
        "partition_protocol": {
            "method": "Chronological split by grouped weather event/date keys (city|date|variable), never random row split.",
            "development_event_count": len(partitions["development"]),
            "validation_event_count": len(partitions["validation"]),
            "holdout_event_count": len(partitions["holdout"]),
            "development_dates": sorted({str(event_dates[e]) for e in partitions["development"] if event_dates.get(e)}),
            "validation_dates": sorted({str(event_dates[e]) for e in partitions["validation"] if event_dates.get(e)}),
            "holdout_dates": sorted({str(event_dates[e]) for e in partitions["holdout"] if event_dates.get(e)}),
            "development_events": sorted(partitions["development"]),
            "validation_events": sorted(partitions["validation"]),
            "holdout_events": sorted(partitions["holdout"]),
        },
        "baseline_reproduction": {
            "research_population_metrics": baseline_metrics,
            "kalshi_baseline": {
                "coverage_n": kalshi_baseline["n"],
                "coverage_pct_of_research": round(kalshi_baseline["n"] / len(research_rows) * 100, 2) if research_rows else 0.0,
                "metrics": kalshi_baseline,
            },
        },
        "error_breakdowns": {
            "by_threshold_vs_range": _breakdown(research_rows, lambda r: str(r["contract_type"])),
            "by_city_station": _breakdown(research_rows, lambda r: str(r["station_bucket"])),
            "by_lead_time": _breakdown(research_rows, lambda r: str(r["lead_time_bucket"])),
            "by_probability_bucket": _breakdown(research_rows, lambda r: str(r["probability_bucket"])),
            "by_model_disagreement": _breakdown(research_rows, lambda r: str(r["disagreement_bucket"])),
            "by_market_price_bucket": _breakdown(research_rows, lambda r: str(r["market_bucket"])),
            "by_model_strategy_version": _breakdown(research_rows, lambda r: str(r["strategy_version"])),
            "by_uncertainty_bucket": _breakdown(research_rows, lambda r: str(r["uncertainty_bucket"])),
        },
        "candidate_results": {
            "ranked_on_holdout": [
                {
                    "name": c.name,
                    "params": c.params,
                    "holdout_metrics": c.holdout,
                    "validation_metrics": c.validation,
                }
                for c in ranked
            ],
            "ranking_primary": "brier_then_calibration_error",
            "ranking_secondary": "log_loss",
            "validation_partition_available": has_validation_partition,
            "parameter_fit_protocol": (
                "Validation partition used for parameter tuning where available. "
                "When validation is empty, conservative defaults are tuned on pre-holdout rows only."
            ),
            "regressions_vs_baseline": [
                {
                    "candidate": c.name,
                    "brier_delta_vs_baseline": (
                        round(c.holdout["brier"] - baseline.holdout["brier"], 4)
                        if baseline and c.holdout["brier"] is not None and baseline.holdout["brier"] is not None
                        else None
                    ),
                    "calibration_delta_pp_vs_baseline": (
                        round(
                            (c.holdout["mean_abs_calibration_error_pp"] or 0.0)
                            - (baseline.holdout["mean_abs_calibration_error_pp"] or 0.0), 4
                        ) if baseline else None
                    ),
                }
                for c in ranked
            ],
            "leakage_checks": leakage_checks,
        },
        "recommendation": {
            "decision": recommendation,
            "top_candidate": top.name if top else None,
            "notes": (
                "Historical settled results are research evidence only. "
                "Any activation requires a separate prospective V3.1 shadow approval."
            ),
        },
    }
