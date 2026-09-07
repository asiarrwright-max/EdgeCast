from __future__ import annotations

from app.services.v3_accuracy_lab import build_same_day_counterfactual_report


def _row(
    *,
    actual: float = 1.0,
    city: str = "Denver",
    contract_type: str = "threshold",
    disagreement_bucket: str = "20pp+",
    eligibility_class: str = "RESEARCH_ONLY",
    event_date: str = "2026-08-01",
    event_key: str = "Denver|2026-08-01|high",
    lead_time_bucket: str = "0-1d",
    lead_time_days: int | None = None,
    market_bucket: str = "0.40-0.59",
    market_prob: float = 0.6,
    model_prob: float = 0.8,
    probability_bucket: str = "80-89%",
    profit_loss: float = 5.0,
    stake: float = 10.0,
    station_bucket: str = "Denver|station_verified=True",
) -> dict[str, object]:
    row: dict[str, object] = {
        "actual": actual,
        "city": city,
        "contract_type": contract_type,
        "disagreement_bucket": disagreement_bucket,
        "eligibility_class": eligibility_class,
        "event_date": event_date,
        "event_key": event_key,
        "lead_time_bucket": lead_time_bucket,
        "market_bucket": market_bucket,
        "market_prob": market_prob,
        "market_ticker": f"{city}-{event_date}-{contract_type}",
        "model_prob": model_prob,
        "partition": "holdout",
        "probability_bucket": probability_bucket,
        "profit_loss": profit_loss,
        "stake": stake,
        "station_bucket": station_bucket,
        "strategy_version": "v3.0",
        "trade_id": 1,
        "uncertainty_bucket": "6F+",
    }
    if lead_time_days is not None:
        row["lead_time_days"] = lead_time_days
    return row


def test_same_day_counterfactual_fails_closed_without_exact_lead_days():
    report = build_same_day_counterfactual_report([
        _row(contract_type="threshold"),
        _row(contract_type="range", actual=0.0, model_prob=0.2, market_prob=0.4),
    ])
    assert report["status"] == "BLOCKED_MISSING_EXACT_SAME_DAY_FIELD"
    assert report["blocker"]["missing_fields"] == ["lead_time_days"]
    assert report["available_fallback_context"]["cohort_label"] == "0-1d proxy (not exact same-day)"


def test_same_day_counterfactual_uses_exact_same_day_when_present():
    rows = [
        _row(lead_time_days=0, contract_type="threshold", event_key="Denver|2026-08-01|high"),
        _row(
            lead_time_days=0,
            contract_type="range",
            actual=0.0,
            model_prob=0.3,
            market_prob=0.45,
            probability_bucket="30-39%",
            event_key="Denver|2026-08-01|high",
        ),
        _row(
            lead_time_days=1,
            contract_type="threshold",
            actual=0.0,
            model_prob=0.2,
            market_prob=0.3,
            event_date="2026-08-02",
            event_key="Denver|2026-08-02|high",
        ),
        _row(
            lead_time_days=0,
            eligibility_class="OFFICIAL",
            event_date="2026-08-03",
            event_key="Denver|2026-08-03|high",
        ),
    ]
    report = build_same_day_counterfactual_report(rows)
    assert report["status"] == "ok"
    assert report["same_day_by_evidence_class"]["RESEARCH_ONLY"]["v3"]["n"] == 2
    assert report["same_day_by_evidence_class"]["OFFICIAL"]["v3"]["n"] == 1
    assert report["research_same_day_counterfactual"]["exclude_range_counterfactual"]["dropped_range_contracts"] == 1
    assert report["research_same_day_breakdowns"]["by_contract_type"][0]["label"] in {"range", "threshold"}


def test_lead_time_distribution_uses_semantic_bucket_order():
    report = build_same_day_counterfactual_report([
        _row(lead_time_days=1, event_key="Denver|2026-08-01|high"),
        _row(lead_time_days=0, event_date="2026-08-02", event_key="Denver|2026-08-02|high"),
        _row(lead_time_days=2, event_date="2026-08-03", event_key="Denver|2026-08-03|high"),
    ])
    labels = [
        row["bucket"]
        for row in report["lead_time_distribution"]["exact_by_evidence_class"]["RESEARCH_ONLY"]
        if row["n"] > 0
    ]
    assert labels == ["same_day", "1d", "2-3d"]
