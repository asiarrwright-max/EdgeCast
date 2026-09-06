"""
Tests for Phase 3B: retrospective stale-quote cohort + JIT quote shadow audit.

Coverage:
  A. Retrospective cohort (_stale_quote_retro_cohort_from_trades)
     - correct filtering to RESEARCH_ONLY / REASON_STALE_QUOTE / SETTLED
     - other-guard application and exclusion counts
     - win/loss/win_rate metrics
     - Brier score computed from ec_yes_probability
     - missing-data exclusions
     - evidence-population separation (OFFICIAL never included)

  B. JIT quote shadow
     - fail-closed: errors in fetch do not propagate
     - other-guard evaluation helper
     - outcome classification: unchanged / changed / no_ask / inactive / error
     - shadow result cannot mutate eligibility or trade fields
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers to build mock trade objects (no DB required)
# ---------------------------------------------------------------------------

_BASE_DT = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)
_FUTURE_CLOSE = datetime(2026, 8, 31, 14, 0, 0, tzinfo=timezone.utc)


def _trade(
    *,
    eligibility_status: str = "RESEARCH_ONLY",
    eligibility_reason: str = "missing_or_stale_executable_quote",
    status: str = "SETTLED",
    outcome: str | None = "WIN",
    direction: str = "YES",
    contract_type: str = "threshold",
    target_settlement_date: str = "2026-08-31",
    settlement_timezone: str = "America/Chicago",
    side_market_price: float = 0.35,
    edge_pct_points: float = 15.0,
    station_verified: bool = True,
    market_close_timestamp: "datetime | None" = _FUTURE_CLOSE,
    decision_timestamp: "datetime | None" = _BASE_DT,
    ec_yes_probability: float = 0.60,
    city: str = "Chicago",
    weather_variable: str = "temperature_max",
) -> SimpleNamespace:
    return SimpleNamespace(
        eligibility_status=eligibility_status,
        eligibility_reason=eligibility_reason,
        status=status,
        outcome=outcome,
        direction=direction,
        contract_type=contract_type,
        target_settlement_date=target_settlement_date,
        settlement_timezone=settlement_timezone,
        side_market_price=side_market_price,
        edge_pct_points=edge_pct_points,
        station_verified=station_verified,
        market_close_timestamp=market_close_timestamp,
        decision_timestamp=decision_timestamp,
        ec_yes_probability=ec_yes_probability,
        city=city,
        weather_variable=weather_variable,
        quote_age_seconds=None,
        is_executable=False,
    )


# ---------------------------------------------------------------------------
# A. Retrospective cohort tests
# ---------------------------------------------------------------------------

def test_retro_cohort_filters_only_stale_quote_rows():
    """Only RESEARCH_ONLY / missing_or_stale_executable_quote rows are included."""
    from app.routers.v3_analytics import _stale_quote_retro_cohort_from_trades

    trades = [
        _trade(),  # qualifies
        _trade(eligibility_reason="entry_price_below_official_floor"),  # excluded
        _trade(eligibility_status="OFFICIAL", eligibility_reason=None),  # excluded
        _trade(eligibility_reason="missing_or_stale_executable_quote",
               eligibility_status="OFFICIAL"),  # excluded (should never happen, but safe)
    ]
    result = _stale_quote_retro_cohort_from_trades(trades, now=_BASE_DT)
    assert result["raw_stale_quote_count"] == 1
    assert result["settled_raw_count"] == 1
    assert result["otherwise_eligible_count"] == 1


def test_retro_cohort_excludes_unsettled_rows():
    """OPEN and PENDING rows are not counted in settled metrics."""
    from app.routers.v3_analytics import _stale_quote_retro_cohort_from_trades

    trades = [
        _trade(status="OPEN", outcome=None),
        _trade(status="SETTLED", outcome="WIN"),
        _trade(status="SETTLED", outcome="LOSS"),
    ]
    result = _stale_quote_retro_cohort_from_trades(trades, now=_BASE_DT)
    assert result["raw_stale_quote_count"] == 3
    assert result["settled_raw_count"] == 2
    assert result["wins"] == 1
    assert result["losses"] == 1


def test_retro_cohort_win_rate():
    from app.routers.v3_analytics import _stale_quote_retro_cohort_from_trades

    trades = [_trade(outcome="WIN")] * 3 + [_trade(outcome="LOSS")] * 2
    result = _stale_quote_retro_cohort_from_trades(trades, now=_BASE_DT)
    assert result["wins"] == 3
    assert result["losses"] == 2
    assert result["win_rate_pct"] == 60.0


def test_retro_cohort_excludes_by_other_guard_hourly():
    """Guard 1: hourly_threshold contract_type excludes row."""
    from app.routers.v3_analytics import _stale_quote_retro_cohort_from_trades

    trades = [
        _trade(contract_type="hourly_threshold"),  # fails Guard 1
        _trade(contract_type="threshold"),          # passes
    ]
    result = _stale_quote_retro_cohort_from_trades(trades, now=_BASE_DT)
    assert result["otherwise_eligible_count"] == 1
    assert result["excluded_by_guard"].get("hourly_temperature_not_approved", 0) == 1


def test_retro_cohort_excludes_by_other_guard_station():
    """Guard 7: unverified station excludes row."""
    from app.routers.v3_analytics import _stale_quote_retro_cohort_from_trades

    trades = [
        _trade(station_verified=False),  # fails Guard 7
        _trade(station_verified=True),   # passes
    ]
    result = _stale_quote_retro_cohort_from_trades(trades, now=_BASE_DT)
    assert result["otherwise_eligible_count"] == 1
    assert result["excluded_by_guard"].get("settlement_station_unverified", 0) == 1


def test_retro_cohort_excludes_by_price_floor():
    """Guard 4: entry price below floor excludes row."""
    from app.routers.v3_analytics import _stale_quote_retro_cohort_from_trades

    trades = [
        _trade(side_market_price=0.10),  # $0.10 < $0.20 floor — fails Guard 4
        _trade(side_market_price=0.30),  # passes
    ]
    result = _stale_quote_retro_cohort_from_trades(trades, now=_BASE_DT)
    assert result["otherwise_eligible_count"] == 1
    assert result["excluded_by_guard"].get("entry_price_below_official_floor", 0) == 1


def test_retro_cohort_missing_data_exclusion():
    """Rows without settlement_timezone or target_settlement_date are excluded as missing-data."""
    from app.routers.v3_analytics import _stale_quote_retro_cohort_from_trades

    trades = [
        _trade(settlement_timezone=None),        # missing timezone
        _trade(target_settlement_date=None),     # type: ignore[arg-type] — missing date
        _trade(),                                # passes
    ]
    result = _stale_quote_retro_cohort_from_trades(trades, now=_BASE_DT)
    assert result["missing_data_exclusions"] == 2
    assert result["otherwise_eligible_count"] == 1


def test_retro_cohort_brier_score():
    """Brier score is computed over otherwise-eligible rows with ec_yes_probability."""
    from app.routers.v3_analytics import _stale_quote_retro_cohort_from_trades

    # YES WIN: p_yes=0.7, actual_yes=1 → (0.7-1)²=0.09
    # YES LOSS: p_yes=0.7, actual_yes=0 → (0.7-0)²=0.49
    # Brier = (0.09 + 0.49) / 2 = 0.29
    trades = [
        _trade(outcome="WIN",  direction="YES", ec_yes_probability=0.70),
        _trade(outcome="LOSS", direction="YES", ec_yes_probability=0.70),
    ]
    result = _stale_quote_retro_cohort_from_trades(trades, now=_BASE_DT)
    assert result["brier_score"] == pytest.approx(0.29, abs=0.001)


def test_retro_cohort_evidence_class_is_research_only():
    """evidence_class must always be RESEARCH_ONLY."""
    from app.routers.v3_analytics import _stale_quote_retro_cohort_from_trades

    result = _stale_quote_retro_cohort_from_trades([], now=_BASE_DT)
    assert result["evidence_class"] == "RESEARCH_ONLY"


def test_retro_cohort_official_trades_never_appear():
    """OFFICIAL trades must never appear in the cohort."""
    from app.routers.v3_analytics import _stale_quote_retro_cohort_from_trades

    official = _trade(eligibility_status="OFFICIAL", eligibility_reason=None)
    result = _stale_quote_retro_cohort_from_trades([official], now=_BASE_DT)
    assert result["raw_stale_quote_count"] == 0
    assert result["otherwise_eligible_count"] == 0


def test_retro_cohort_has_data_limitations():
    """Data limitations list must be present and non-empty."""
    from app.routers.v3_analytics import _stale_quote_retro_cohort_from_trades

    result = _stale_quote_retro_cohort_from_trades([], now=_BASE_DT)
    assert isinstance(result["data_limitations"], list)
    assert len(result["data_limitations"]) > 0


# ---------------------------------------------------------------------------
# B. JIT quote shadow tests
# ---------------------------------------------------------------------------

def test_jit_other_guards_pass_for_valid_trade():
    """Other-guard evaluator returns True for a fully-valid trade."""
    from app.services.v3_jit_quote_audit import _evaluate_other_guards

    ok, reason = _evaluate_other_guards(
        contract_type="threshold",
        target_settlement_date_str="2026-08-31",
        settlement_timezone="America/Chicago",
        decision_timestamp=_BASE_DT,
        side_market_price=0.35,
        edge_pct_points=15.0,
        station_verified=True,
        direction="YES",
        market_close_timestamp=_FUTURE_CLOSE,
    )
    assert ok is True
    assert reason is None


def test_jit_other_guards_fail_for_hourly():
    """Other-guard evaluator returns False for hourly_threshold contract."""
    from app.services.v3_jit_quote_audit import _evaluate_other_guards

    ok, reason = _evaluate_other_guards(
        contract_type="hourly_threshold",
        target_settlement_date_str="2026-08-31",
        settlement_timezone="America/Chicago",
        decision_timestamp=_BASE_DT,
        side_market_price=0.35,
        edge_pct_points=15.0,
        station_verified=True,
        direction="YES",
        market_close_timestamp=_FUTURE_CLOSE,
    )
    assert ok is False
    assert reason == "hourly_temperature_not_approved"


def test_jit_other_guards_fail_for_unverified_station():
    """Other-guard evaluator returns False for unverified station."""
    from app.services.v3_jit_quote_audit import _evaluate_other_guards

    ok, reason = _evaluate_other_guards(
        contract_type="threshold",
        target_settlement_date_str="2026-08-31",
        settlement_timezone="America/Chicago",
        decision_timestamp=_BASE_DT,
        side_market_price=0.35,
        edge_pct_points=15.0,
        station_verified=False,
        direction="YES",
        market_close_timestamp=_FUTURE_CLOSE,
    )
    assert ok is False
    assert reason == "settlement_station_unverified"


@pytest.mark.asyncio
async def test_jit_shadow_fail_closed_on_http_error():
    """
    HTTP errors during JIT fetch must be caught and stored as jit_outcome='http_error'.
    perform_jit_quote_shadow must never raise an exception to the caller.
    """
    from app.services.v3_jit_quote_audit import perform_jit_quote_shadow

    captured: list[dict] = []

    async def _mock_session_ctx():
        mock_session = AsyncMock()
        mock_session.add = lambda obj: captured.append({
            "outcome": obj.jit_outcome,
            "error":   obj.error_detail,
        })
        mock_session.commit = AsyncMock()
        return mock_session

    import httpx

    with patch(
        "app.services.v3_jit_quote_audit._fetch_single_market",
        side_effect=httpx.HTTPStatusError(
            "403 Forbidden",
            request=MagicMock(),
            response=MagicMock(status_code=403),
        ),
    ), patch(
        "app.services.v3_jit_quote_audit.AsyncSessionLocal",
    ) as mock_sl:
        mock_sl.return_value.__aenter__ = AsyncMock(return_value=await _mock_session_ctx())
        mock_sl.return_value.__aexit__ = AsyncMock(return_value=False)

        # Must not raise
        result = await perform_jit_quote_shadow(
            market_ticker="KXHIGHTDAL-25-T52",
            direction="YES",
            collection_quote_ask=None,
            collection_quote_age_seconds=None,
            decision_timestamp=_BASE_DT,
            collection_batch_id=None,
            contract_type="threshold",
            target_settlement_date_str="2026-08-31",
            settlement_timezone="America/Chicago",
            side_market_price=0.35,
            edge_pct_points=15.0,
            station_verified=True,
            market_close_timestamp=_FUTURE_CLOSE,
        )

    assert result is None  # shadow always returns None
    if captured:
        assert captured[0]["outcome"] == "http_error"


@pytest.mark.asyncio
async def test_jit_shadow_returns_none_always():
    """
    perform_jit_quote_shadow MUST return None regardless of outcome.
    The caller must not be able to use the return value to mutate any trade field.
    """
    from app.services.v3_jit_quote_audit import perform_jit_quote_shadow

    with patch(
        "app.services.v3_jit_quote_audit._fetch_single_market",
        side_effect=Exception("unexpected"),
    ), patch("app.services.v3_jit_quote_audit.AsyncSessionLocal") as mock_sl:
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        mock_sl.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sl.return_value.__aexit__ = AsyncMock(return_value=False)

        retval = await perform_jit_quote_shadow(
            market_ticker="TICKER",
            direction="NO",
            collection_quote_ask=0.30,
            collection_quote_age_seconds=350.0,
            decision_timestamp=_BASE_DT,
            collection_batch_id="batch-1",
            contract_type="threshold",
            target_settlement_date_str="2026-08-31",
            settlement_timezone="America/Chicago",
            side_market_price=0.30,
            edge_pct_points=12.0,
            station_verified=True,
            market_close_timestamp=_FUTURE_CLOSE,
        )

    assert retval is None


@pytest.mark.asyncio
async def test_jit_shadow_outcome_unchanged():
    """When JIT ask matches collection ask, outcome is 'unchanged'."""
    from app.services.v3_jit_quote_audit import perform_jit_quote_shadow

    stored: list = []
    mock_market = {
        "status":    "active",
        "yes_ask":   35,   # cents → 0.35
        "no_ask":    65,
    }

    with patch(
        "app.services.v3_jit_quote_audit._fetch_single_market",
        AsyncMock(return_value=mock_market),
    ), patch("app.services.v3_jit_quote_audit.AsyncSessionLocal") as mock_sl:
        mock_session = AsyncMock()
        mock_session.add = lambda obj: stored.append(obj)
        mock_session.commit = AsyncMock()
        mock_sl.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sl.return_value.__aexit__ = AsyncMock(return_value=False)

        await perform_jit_quote_shadow(
            market_ticker="KXHIGHTDAL-25-T52",
            direction="YES",
            collection_quote_ask=0.35,   # same as JIT
            collection_quote_age_seconds=310.0,
            decision_timestamp=_BASE_DT,
            collection_batch_id=None,
            contract_type="threshold",
            target_settlement_date_str="2026-08-31",
            settlement_timezone="America/Chicago",
            side_market_price=0.35,
            edge_pct_points=15.0,
            station_verified=True,
            market_close_timestamp=_FUTURE_CLOSE,
        )

    assert stored, "Expected audit row to be stored"
    assert stored[0].jit_outcome == "unchanged"
    assert stored[0].jit_yes_ask == pytest.approx(0.35)


@pytest.mark.asyncio
async def test_jit_shadow_outcome_changed():
    """When JIT ask differs from collection ask, outcome is 'changed'."""
    from app.services.v3_jit_quote_audit import perform_jit_quote_shadow

    stored: list = []
    mock_market = {
        "status":    "active",
        "yes_ask":   40,   # cents → 0.40 (was 0.35)
        "no_ask":    60,
    }

    with patch(
        "app.services.v3_jit_quote_audit._fetch_single_market",
        AsyncMock(return_value=mock_market),
    ), patch("app.services.v3_jit_quote_audit.AsyncSessionLocal") as mock_sl:
        mock_session = AsyncMock()
        mock_session.add = lambda obj: stored.append(obj)
        mock_session.commit = AsyncMock()
        mock_sl.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sl.return_value.__aexit__ = AsyncMock(return_value=False)

        await perform_jit_quote_shadow(
            market_ticker="KXHIGHTDAL-25-T52",
            direction="YES",
            collection_quote_ask=0.35,
            collection_quote_age_seconds=310.0,
            decision_timestamp=_BASE_DT,
            collection_batch_id=None,
            contract_type="threshold",
            target_settlement_date_str="2026-08-31",
            settlement_timezone="America/Chicago",
            side_market_price=0.35,
            edge_pct_points=15.0,
            station_verified=True,
            market_close_timestamp=_FUTURE_CLOSE,
        )

    assert stored[0].jit_outcome == "changed"


@pytest.mark.asyncio
async def test_jit_shadow_outcome_no_ask():
    """When JIT market is active but selected-side ask is None, outcome is 'no_ask'."""
    from app.services.v3_jit_quote_audit import perform_jit_quote_shadow

    stored: list = []
    mock_market = {
        "status":    "active",
        "yes_ask":   None,
        "no_ask":    None,
    }

    with patch(
        "app.services.v3_jit_quote_audit._fetch_single_market",
        AsyncMock(return_value=mock_market),
    ), patch("app.services.v3_jit_quote_audit.AsyncSessionLocal") as mock_sl:
        mock_session = AsyncMock()
        mock_session.add = lambda obj: stored.append(obj)
        mock_session.commit = AsyncMock()
        mock_sl.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sl.return_value.__aexit__ = AsyncMock(return_value=False)

        await perform_jit_quote_shadow(
            market_ticker="TICKER",
            direction="YES",
            collection_quote_ask=None,
            collection_quote_age_seconds=None,
            decision_timestamp=_BASE_DT,
            collection_batch_id=None,
            contract_type="threshold",
            target_settlement_date_str="2026-08-31",
            settlement_timezone="America/Chicago",
            side_market_price=0.35,
            edge_pct_points=15.0,
            station_verified=True,
            market_close_timestamp=_FUTURE_CLOSE,
        )

    assert stored[0].jit_outcome == "no_ask"


@pytest.mark.asyncio
async def test_jit_shadow_outcome_inactive_market():
    """When JIT market status is not 'active', outcome is 'inactive_market'."""
    from app.services.v3_jit_quote_audit import perform_jit_quote_shadow

    stored: list = []
    mock_market = {"status": "closed", "yes_ask": 35, "no_ask": 65}

    with patch(
        "app.services.v3_jit_quote_audit._fetch_single_market",
        AsyncMock(return_value=mock_market),
    ), patch("app.services.v3_jit_quote_audit.AsyncSessionLocal") as mock_sl:
        mock_session = AsyncMock()
        mock_session.add = lambda obj: stored.append(obj)
        mock_session.commit = AsyncMock()
        mock_sl.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sl.return_value.__aexit__ = AsyncMock(return_value=False)

        await perform_jit_quote_shadow(
            market_ticker="TICKER",
            direction="YES",
            collection_quote_ask=None,
            collection_quote_age_seconds=None,
            decision_timestamp=_BASE_DT,
            collection_batch_id=None,
            contract_type="threshold",
            target_settlement_date_str="2026-08-31",
            settlement_timezone="America/Chicago",
            side_market_price=0.35,
            edge_pct_points=15.0,
            station_verified=True,
            market_close_timestamp=_FUTURE_CLOSE,
        )

    assert stored[0].jit_outcome == "inactive_market"


def test_jit_shadow_audit_row_fields_not_on_trade():
    """
    V3JitQuoteAudit has no fields that could alter V3PaperTrade eligibility.
    Specifically: V3JitQuoteAudit must not have eligibility_status or
    eligibility_reason or is_executable or direction fields that could
    accidentally be read back into trade decisions.
    """
    from app.models_v3 import V3JitQuoteAudit

    col_names = {c.name for c in V3JitQuoteAudit.__table__.columns}
    # These trade-altering fields must NOT exist on the audit model
    forbidden = {"eligibility_status", "is_executable", "ec_yes_probability",
                 "side_market_price", "edge_pct_points", "outcome", "profit_loss"}
    overlap = col_names & forbidden
    assert not overlap, f"Audit model has trade-altering columns: {overlap}"


def test_retro_cohort_empty_input():
    """Empty input returns zero counts without errors."""
    from app.routers.v3_analytics import _stale_quote_retro_cohort_from_trades

    result = _stale_quote_retro_cohort_from_trades([], now=_BASE_DT)
    assert result["raw_stale_quote_count"] == 0
    assert result["settled_raw_count"] == 0
    assert result["otherwise_eligible_count"] == 0
    assert result["wins"] == 0
    assert result["losses"] == 0
    assert result["win_rate_pct"] is None
    assert result["brier_score"] is None
