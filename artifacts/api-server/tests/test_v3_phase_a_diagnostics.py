from __future__ import annotations

from types import SimpleNamespace

from app.routers.v3_analytics import _phase_a_diagnostics_from_trades


def _trade(
    *,
    city: str = "Chicago",
    target_settlement_date: str = "2026-08-01",
    weather_variable: str = "temperature_max",
    outcome: str = "WIN",
    direction: str = "YES",
    ec_side_probability: float | None = None,
    ec_yes_probability: float | None = 0.8,
    side_market_price: float | None = 0.55,
    edge_pct_points: float | None = 25.0,
    profit_loss: float | None = 10.0,
    stake: float | None = 10.0,
    contract_type: str | None = "threshold",
    lead_time_days: int | None = 2,
    quote_age_seconds: float | None = 120.0,
    is_executable: bool | None = True,
    eligibility_status: str | None = "OFFICIAL",
) -> SimpleNamespace:
    return SimpleNamespace(
        city=city,
        target_settlement_date=target_settlement_date,
        weather_variable=weather_variable,
        outcome=outcome,
        direction=direction,
        ec_side_probability=ec_side_probability,
        ec_yes_probability=ec_yes_probability,
        side_market_price=side_market_price,
        edge_pct_points=edge_pct_points,
        profit_loss=profit_loss,
        stake=stake,
        contract_type=contract_type,
        lead_time_days=lead_time_days,
        quote_age_seconds=quote_age_seconds,
        is_executable=is_executable,
        eligibility_status=eligibility_status,
    )


def test_phase_a_counts_events_and_dates_without_pooling():
    trades = [
        _trade(city="Chicago", target_settlement_date="2026-08-01", outcome="WIN", eligibility_status="OFFICIAL"),
        _trade(city="Chicago", target_settlement_date="2026-08-01", outcome="LOSS", eligibility_status="RESEARCH_ONLY"),
        _trade(city="Denver", target_settlement_date="2026-08-02", outcome="LOSS", eligibility_status="LEGACY"),
    ]
    result = _phase_a_diagnostics_from_trades(trades)
    assert result["total_settled_rows"] == 3
    assert result["analyzed_rows"] == 3
    assert result["skipped_non_binary_outcome_rows"] == 0
    assert result["distinct_event_count"] == 2
    assert result["distinct_settlement_date_count"] == 2
    assert result["data_quality_gaps"]["eligibility_class_counts"]["OFFICIAL"] == 1
    assert result["data_quality_gaps"]["eligibility_class_counts"]["RESEARCH_ONLY"] == 1
    assert result["data_quality_gaps"]["eligibility_class_counts"]["LEGACY"] == 1


def test_phase_a_tracks_unlabeled_and_non_executable_rows():
    trades = [
        _trade(eligibility_status=None, is_executable=False, quote_age_seconds=None, outcome="LOSS"),
        _trade(eligibility_status="OFFICIAL", is_executable=True, outcome="WIN"),
    ]
    result = _phase_a_diagnostics_from_trades(trades)
    gaps = result["data_quality_gaps"]
    assert gaps["unlabeled_rows"] == 1
    assert gaps["missing_quote_age_rows"] == 1
    assert gaps["non_executable_rows"] == 1


def test_phase_a_reports_skipped_non_binary_outcomes():
    trades = [
        _trade(outcome="WIN"),
        _trade(outcome="PENDING_SETTLEMENT"),  # filtered from model metrics
    ]
    result = _phase_a_diagnostics_from_trades(trades)
    assert result["total_settled_rows"] == 2
    assert result["analyzed_rows"] == 1
    assert result["skipped_non_binary_outcome_rows"] == 1


def test_phase_a_baselines_and_calibration_outputs_present():
    trades = [
        _trade(ec_side_probability=0.95, ec_yes_probability=None, side_market_price=0.80, outcome="WIN"),
        _trade(ec_side_probability=0.92, ec_yes_probability=None, side_market_price=0.78, outcome="LOSS"),
        _trade(ec_side_probability=0.91, ec_yes_probability=None, side_market_price=0.75, outcome="LOSS"),
    ]
    result = _phase_a_diagnostics_from_trades(trades)
    baseline = result["baseline_comparison"]
    assert baseline["model_trade_brier"] is not None
    assert baseline["market_trade_brier"] is not None
    assert baseline["constant_rate_trade_brier"] is not None
    assert baseline["shrinkage_trade_brier"] is not None
    bucket = [b for b in result["calibration_table"] if b["bucket"] == "90-100%"][0]
    assert bucket["count"] == 3
    assert bucket["observed_win_rate_wilson95_pct"] is not None
    assert bucket["observed_win_rate_event_clustered95_pct"] is not None
