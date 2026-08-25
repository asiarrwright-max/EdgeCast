from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.routers.v3_settlement_health import _health_summary


def trade(**overrides):
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    values = dict(
        id=1,
        status="OPEN",
        eligibility_status="OFFICIAL",
        created_at=now - timedelta(days=1),
        expected_settlement_timestamp=now + timedelta(days=1),
        market_ticker="KXTEST-26AUG24-T80",
        decision_explanation=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_no_trades_is_explicit():
    result = _health_summary([], datetime(2026, 8, 24, tzinfo=timezone.utc))
    assert result["diagnosis"] == "NO_TRADES"
    assert result["total"] == 0
    assert result["safety"]["real_money_execution_enabled"] is False


def test_no_trades_includes_plain_language():
    result = _health_summary([], datetime(2026, 8, 24, tzinfo=timezone.utc))
    assert "plain_language_summary" in result
    assert len(result["plain_language_summary"]) > 10


def test_zero_settled_but_not_due_is_not_called_failure():
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    result = _health_summary([trade()], now)
    assert result["diagnosis"] == "NO_SETTLEMENTS_YET_NOT_DUE"
    assert result["due_unsettled_count"] == 0
    assert result["official"] == 1


def test_due_unsettled_is_distinguished_for_investigation():
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    t = trade(expected_settlement_timestamp=now - timedelta(hours=2), status="PENDING_SETTLEMENT")
    result = _health_summary([t], now)
    assert result["diagnosis"] == "DUE_UNSETTLED_REQUIRES_INVESTIGATION"
    assert result["due_unsettled_count"] == 1
    assert result["due_unsettled"][0]["market_ticker"] == t.market_ticker


def test_due_unsettled_scheduler_not_running_distinguished():
    """When due-unsettled trades exist AND scheduler is stopped, diagnosis is SCHEDULER-specific."""
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    t = trade(expected_settlement_timestamp=now - timedelta(hours=2), status="PENDING_SETTLEMENT")
    result = _health_summary([t], now, scheduler_running=False)
    assert result["diagnosis"] == "DUE_UNSETTLED_SCHEDULER_NOT_RUNNING"
    assert result["scheduler_running"] is False
    assert "plain_language_summary" in result
    summary = result["plain_language_summary"].lower()
    assert "scheduler" in summary


def test_due_unsettled_scheduler_running_is_investigation():
    """When due-unsettled trades exist AND scheduler is running, diagnosis is INVESTIGATION."""
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    t = trade(expected_settlement_timestamp=now - timedelta(hours=2), status="PENDING_SETTLEMENT")
    result = _health_summary([t], now, scheduler_running=True)
    assert result["diagnosis"] == "DUE_UNSETTLED_REQUIRES_INVESTIGATION"
    assert result["scheduler_running"] is True


def test_scheduler_running_none_leaves_investigation_diagnosis():
    """When scheduler_running is not provided, diagnosis remains INVESTIGATION (not SCHEDULER)."""
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    t = trade(expected_settlement_timestamp=now - timedelta(hours=2), status="PENDING_SETTLEMENT")
    result = _health_summary([t], now)
    assert result["diagnosis"] == "DUE_UNSETTLED_REQUIRES_INVESTIGATION"
    assert result["scheduler_running"] is None


def test_all_errors_detected_as_api_mismatch_signal():
    """When all terminal trades are errors and none settled, diagnosis is ALL_ERRORS."""
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    # Trades are all ERROR (404 from Kalshi), none pending or open
    errors = [
        trade(id=i, status="ERROR",
              expected_settlement_timestamp=now - timedelta(days=2))
        for i in range(1, 4)
    ]
    result = _health_summary(errors, now)
    assert result["diagnosis"] == "ALL_ERRORS"
    assert "plain_language_summary" in result
    assert result["error_count"] == 3


def test_all_errors_with_mixed_terminal_is_not_all_errors():
    """If there's at least one SETTLED trade alongside errors, ALL_ERRORS does not fire."""
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    trades = [
        trade(id=1, status="ERROR", expected_settlement_timestamp=now - timedelta(days=2)),
        trade(id=2, status="SETTLED", expected_settlement_timestamp=now - timedelta(days=2)),
    ]
    result = _health_summary(trades, now)
    assert result["diagnosis"] == "SETTLEMENTS_PRESENT"


def test_population_separation_is_reported_without_reclassification():
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    trades = [
        trade(id=1, eligibility_status="OFFICIAL"),
        trade(id=2, eligibility_status="RESEARCH_ONLY"),
        trade(id=3, eligibility_status="LEGACY"),
        trade(id=4, eligibility_status=None),
    ]
    result = _health_summary(trades, now)
    assert result["official"] == 1
    assert result["research_only"] == 1
    assert result["legacy"] == 1
    assert result["by_eligibility"]["UNCLASSIFIED"] == 1


def test_scheduler_running_included_in_response():
    """scheduler_running field is always present in the response."""
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    result_none = _health_summary([trade()], now)
    result_true = _health_summary([trade()], now, scheduler_running=True)
    result_false = _health_summary([trade()], now, scheduler_running=False)
    assert "scheduler_running" in result_none
    assert result_none["scheduler_running"] is None
    assert result_true["scheduler_running"] is True
    assert result_false["scheduler_running"] is False
