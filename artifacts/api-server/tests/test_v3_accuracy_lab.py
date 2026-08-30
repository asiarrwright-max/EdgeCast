from __future__ import annotations

from types import SimpleNamespace

from app.services.v3_accuracy_lab import build_settled_v3_accuracy_lab_report


def _trade(
    *,
    city: str = "Denver",
    target_settlement_date: str = "2026-08-01",
    weather_variable: str = "temperature_max",
    contract_type: str = "threshold",
    outcome: str = "WIN",
    direction: str = "YES",
    ec_side_probability: float = 0.7,
    ec_yes_probability: float | None = None,
    side_market_price: float | None = 0.6,
    lead_time_days: int = 2,
    final_sigma: float | None = 3.5,
    historical_sigma: float | None = None,
    strategy_version: str = "v3.0",
    eligibility_status: str | None = "RESEARCH_ONLY",
    station_verified: bool | None = True,
    stake: float = 10.0,
    profit_loss: float = 5.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        city=city,
        target_settlement_date=target_settlement_date,
        weather_variable=weather_variable,
        contract_type=contract_type,
        outcome=outcome,
        direction=direction,
        ec_side_probability=ec_side_probability,
        ec_yes_probability=ec_yes_probability,
        side_market_price=side_market_price,
        lead_time_days=lead_time_days,
        final_sigma=final_sigma,
        historical_sigma=historical_sigma,
        strategy_version=strategy_version,
        eligibility_status=eligibility_status,
        station_verified=station_verified,
        stake=stake,
        profit_loss=profit_loss,
    )


def test_preserves_official_research_unclassified_separation():
    trades = [
        _trade(eligibility_status="OFFICIAL"),
        _trade(eligibility_status="RESEARCH_ONLY"),
        _trade(eligibility_status="legacy"),
    ]
    report = build_settled_v3_accuracy_lab_report(trades)
    counts = report["frozen_population"]["eligibility_counts"]
    assert counts["OFFICIAL"] == 1
    assert counts["RESEARCH_ONLY"] == 1
    assert counts["UNCLASSIFIED"] == 1


def test_event_group_partitioning_is_chronological_and_disjoint():
    trades = [
        _trade(city="Denver", target_settlement_date="2026-08-01"),
        _trade(city="Denver", target_settlement_date="2026-08-02"),
        _trade(city="Denver", target_settlement_date="2026-08-03"),
        _trade(city="Denver", target_settlement_date="2026-08-04"),
        _trade(city="Denver", target_settlement_date="2026-08-05"),
    ]
    report = build_settled_v3_accuracy_lab_report(trades)
    part = report["partition_protocol"]
    leak = report["candidate_results"]["leakage_checks"]
    assert part["development_event_count"] > 0
    assert part["holdout_event_count"] > 0
    assert leak["no_event_overlap"] is True
    assert leak["chronological_boundaries_non_decreasing"] is True


def test_baseline_and_kalshi_metrics_present():
    trades = [
        _trade(outcome="WIN", ec_side_probability=0.8, side_market_price=0.7),
        _trade(outcome="LOSS", ec_side_probability=0.7, side_market_price=0.65),
        _trade(outcome="LOSS", ec_side_probability=0.3, side_market_price=0.45),
    ]
    report = build_settled_v3_accuracy_lab_report(trades)
    baseline = report["baseline_reproduction"]["research_population_metrics"]
    kalshi = report["baseline_reproduction"]["kalshi_baseline"]
    assert baseline["n"] == 3
    assert baseline["brier"] is not None
    assert baseline["calibration"]
    assert kalshi["coverage_n"] == 3
    assert kalshi["metrics"]["brier"] is not None


def test_candidate_ranking_and_recommendation_exist():
    trades = []
    # Build enough chronological events for non-empty holdout.
    for idx in range(1, 16):
        outcome = "WIN" if idx % 2 == 0 else "LOSS"
        trades.append(_trade(
            city="Chicago" if idx % 3 == 0 else "Denver",
            target_settlement_date=f"2026-08-{idx:02d}",
            contract_type="range" if idx % 4 == 0 else "threshold",
            outcome=outcome,
            ec_side_probability=0.75 if outcome == "WIN" else 0.25,
            side_market_price=0.6 if outcome == "WIN" else 0.4,
            lead_time_days=1 + (idx % 6),
            final_sigma=2.0 + (idx % 5),
        ))

    report = build_settled_v3_accuracy_lab_report(trades)
    ranked = report["candidate_results"]["ranked_on_holdout"]
    assert len(ranked) >= 4
    assert ranked[0]["holdout_metrics"]["brier"] is not None
    assert report["recommendation"]["decision"] in {
        "no_change",
        "more_research",
        "candidate_for_v3_1_shadow",
    }
