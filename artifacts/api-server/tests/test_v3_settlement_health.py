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
