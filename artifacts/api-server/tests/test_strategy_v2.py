"""
Tests for Strategy v2 components:
  - probability_engine_v2 σ fallback hierarchy
  - bias correction (applied vs not applied based on sample count)
  - calibration guard (no adjustment when n < 30)
  - paper_trading_v2 quality exclusions
  - get_strategy_agreement logic
  - forecast_verifier _lead_bucket and _season helpers
  - AnalysisResultV2 extends AnalysisResult correctly
"""
from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.services.probability_engine_v2 import (
    AnalysisResultV2,
    _lead_bucket,
    MIN_SAMPLE,
    MIN_CALIB_SAMPLE,
)
from app.services.paper_trading_v2 import (
    FLAG_V2_BELOW_MIN_PRICE,
    FLAG_V2_ZERO_VOLUME,
    FLAG_V2_NO_LIQUIDITY,
    _v2_exclusion_flag,
    _skip_v2,
    STRATEGY_VERSION,
)
from app.services.forecast_verifier import _season, _lead_bucket as fv_lead_bucket


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_market(**kwargs):
    """Create a mock KalshiMarket with sensible defaults."""
    m = MagicMock()
    m.ticker = "TEST-TICKER"
    m.event_ticker = "TEST-EVENT"
    m.title = "Test market"
    m.subtitle = None
    m.city = "Chicago"
    m.target_date = "2026-08-10"
    m.status = "active"
    m.yes_bid = 0.30
    m.yes_ask = 0.35
    m.no_bid = 0.65
    m.no_ask = 0.70
    m.volume = 1000.0
    m.weather_market_type = "temperature"
    m.collection_timestamp = None
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


def _make_snap(**kwargs):
    """Create a mock PredictionSnapshot."""
    s = MagicMock()
    s.id = 1
    s.market_ticker = "TEST-TICKER"
    s.forecast_value = 85.0
    s.forecast_retrieved_at = None
    s.ec_probability = 0.65
    s.market_probability = 0.32
    s.confidence = "High"
    s.analysis_status = "supported"
    s.settlement_variable = "high"
    s.settlement_operator = "gte"
    s.settlement_threshold = 90.0
    s.contract_type = "threshold"
    s.lower_bound = None
    s.upper_bound = None
    s.target_hour = None
    s.lead_time_days = 2
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


# ── _lead_bucket ─────────────────────────────────────────────────────────────

def test_lead_bucket_zero():
    assert _lead_bucket(0) == "0-1d"


def test_lead_bucket_one():
    assert _lead_bucket(1) == "0-1d"


def test_lead_bucket_two():
    assert _lead_bucket(2) == "2-3d"


def test_lead_bucket_three():
    assert _lead_bucket(3) == "2-3d"


def test_lead_bucket_four():
    assert _lead_bucket(4) == "4-7d"


def test_lead_bucket_seven():
    assert _lead_bucket(7) == "4-7d"


def test_lead_bucket_eight():
    assert _lead_bucket(8) == ">7d"


def test_lead_bucket_none():
    assert _lead_bucket(None) == ">7d"


# ── forecast_verifier helpers ─────────────────────────────────────────────────

def test_season_winter():
    assert _season(12) == "winter"
    assert _season(1) == "winter"
    assert _season(2) == "winter"


def test_season_spring():
    assert _season(3) == "spring"
    assert _season(5) == "spring"


def test_season_summer():
    assert _season(6) == "summer"
    assert _season(8) == "summer"


def test_season_fall():
    assert _season(9) == "fall"
    assert _season(11) == "fall"


def test_fv_lead_bucket_matches_engine():
    """Verify that forecast_verifier and engine agree on bucket labels."""
    for days, expected in [(0, "0-1d"), (1, "0-1d"), (3, "2-3d"), (7, "4-7d"), (10, ">7d")]:
        assert fv_lead_bucket(days) == _lead_bucket(days) == expected


# ── V2 exclusion flags ────────────────────────────────────────────────────────

def test_no_exclusion_for_normal_market():
    market = _make_market()
    assert _v2_exclusion_flag(market, 0.15) is None


def test_exclusion_no_liquidity():
    market = _make_market(yes_bid=None, yes_ask=None, no_bid=None, no_ask=None)
    assert _v2_exclusion_flag(market, None) == FLAG_V2_NO_LIQUIDITY


def test_exclusion_zero_volume():
    market = _make_market(volume=0)
    assert _v2_exclusion_flag(market, 0.15) == FLAG_V2_ZERO_VOLUME


def test_exclusion_1_cent():
    market = _make_market()
    assert _v2_exclusion_flag(market, 0.01) == FLAG_V2_BELOW_MIN_PRICE


def test_exclusion_below_1_cent():
    market = _make_market()
    assert _v2_exclusion_flag(market, 0.005) == FLAG_V2_BELOW_MIN_PRICE


def test_no_exclusion_at_exactly_2_cents():
    market = _make_market()
    assert _v2_exclusion_flag(market, 0.02) is None


def test_no_liquidity_check_takes_priority_over_volume():
    """No-liquidity flag should fire even if volume is also zero."""
    market = _make_market(yes_bid=None, yes_ask=None, no_bid=None, no_ask=None, volume=0)
    assert _v2_exclusion_flag(market, None) == FLAG_V2_NO_LIQUIDITY


# ── _skip_v2 helper ───────────────────────────────────────────────────────────

def test_skip_v2_returns_correct_structure():
    result = _skip_v2("Test reason", 0.5, 0.4, ["warning"])
    assert result["action"] == "SKIP"
    assert result["skip_reason"] == "Test reason"
    assert result["direction"] is None
    assert result["bias_correction"] == 0.0
    assert result["calibration_adj"] == 1.0
    assert result["fallback_level"] == "fixed_table"


# ── AnalysisResultV2 extends AnalysisResult ───────────────────────────────────

def test_analysis_result_v2_inherits_fields():
    from app.services.probability_engine import AnalysisResult
    r = AnalysisResultV2(
        ec_probability=0.65,
        market_probability=0.32,
        confidence="High",
        explanation="Test",
        forecast_value=85.0,
        lead_time_days=2,
        sigma=3.5,
        analysis_status="supported",
        analysis_reason=None,
        sigma_used=3.2,
        bias_correction=0.5,
        fallback_level="city",
        calibration_adj=0.95,
        raw_ec_probability=0.68,
    )
    # It should be an AnalysisResult
    assert isinstance(r, AnalysisResult)
    # V2-specific fields
    assert r.sigma_used == 3.2
    assert r.bias_correction == 0.5
    assert r.fallback_level == "city"
    assert r.calibration_adj == 0.95
    assert r.raw_ec_probability == 0.68


def test_analysis_result_v2_defaults():
    """V2 fields should have sensible defaults."""
    r = AnalysisResultV2(
        ec_probability=0.5,
        market_probability=None,
        confidence="Medium",
        explanation="",
        forecast_value=None,
        lead_time_days=None,
        sigma=None,
        analysis_status="unsupported",
        analysis_reason="test",
    )
    assert r.sigma_used is None
    assert r.bias_correction == 0.0
    assert r.fallback_level == "fixed_table"
    assert r.calibration_adj == 1.0
    assert r.raw_ec_probability is None


# ── run_analysis_v2 (async) ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_analysis_v2_unsupported():
    """Unsupported markets should return AnalysisResultV2 with None ec_probability."""
    from app.services.probability_engine_v2 import run_analysis_v2
    result = await run_analysis_v2(
        title="Test",
        subtitle=None,
        city="Chicago",
        target_date_str="2026-08-10",
        weather_variable="high",
        operator="gte",
        threshold=90.0,
        parse_confidence="high",
        settlement_status="unsupported",
        unsupported_reason="Test unsupported",
        forecast_high=85.0,
        forecast_low=70.0,
        forecast_retrieved_at=None,
        yes_bid=0.30,
        yes_ask=0.35,
        session=None,
    )
    assert isinstance(result, AnalysisResultV2)
    assert result.ec_probability is None
    assert result.analysis_status == "unsupported"


@pytest.mark.asyncio
async def test_run_analysis_v2_no_session_uses_fixed_table():
    """Without a session, engine should fall back to v1 fixed σ table."""
    from app.services.probability_engine_v2 import run_analysis_v2
    result = await run_analysis_v2(
        title="Test",
        subtitle=None,
        city="Chicago",
        target_date_str="2026-08-10",
        weather_variable="high",
        operator="gte",
        threshold=90.0,
        parse_confidence="high",
        settlement_status="supported",
        unsupported_reason=None,
        forecast_high=85.0,
        forecast_low=70.0,
        forecast_retrieved_at=None,
        yes_bid=0.30,
        yes_ask=0.35,
        session=None,
    )
    assert result.analysis_status == "supported"
    assert result.fallback_level == "fixed_table"
    assert result.bias_correction == 0.0
    assert result.calibration_adj == 1.0
    assert result.ec_probability is not None


@pytest.mark.asyncio
async def test_run_analysis_v2_sigma_from_db():
    """When DB has stats, σ should come from learned value."""
    from app.services.probability_engine_v2 import run_analysis_v2

    # Stats row returned for sigma/bias queries
    mock_stats = MagicMock()
    mock_stats.std_dev = 2.1
    mock_stats.fallback_level = "city"
    mock_stats.sample_size = 10
    mock_stats.mean_error = 0.5

    # Each _get_error_stats makes 1 execute call when the first query hits.
    # sigma → call 1, bias → call 2, calibration → call 3 (should return None).
    call_idx = [0]
    async def mock_execute(q):
        m = MagicMock()
        call_idx[0] += 1
        if call_idx[0] <= 2:   # sigma + bias queries get mock_stats
            m.scalar_one_or_none.return_value = mock_stats
        else:
            m.scalar_one_or_none.return_value = None  # calibration → no adjustment
        return m

    mock_session = AsyncMock()
    mock_session.execute.side_effect = mock_execute

    result = await run_analysis_v2(
        title="Test",
        subtitle=None,
        city="Chicago",
        target_date_str="2026-08-10",
        weather_variable="high",
        operator="gte",
        threshold=90.0,
        parse_confidence="high",
        settlement_status="supported",
        unsupported_reason=None,
        forecast_high=85.0,
        forecast_low=70.0,
        forecast_retrieved_at=None,
        yes_bid=0.30,
        yes_ask=0.35,
        session=mock_session,
    )
    assert result.analysis_status == "supported"
    # Learned σ=2.1°F is below SIGMA_FLOOR (3.5°F), so it gets clamped up.
    # The fallback_level should still be "city" (stats came from DB, not the fixed table).
    from app.services.probability_engine_v2 import SIGMA_FLOOR
    assert result.sigma_used == SIGMA_FLOOR, (
        f"σ={result.sigma_used} should be clamped to SIGMA_FLOOR={SIGMA_FLOOR} "
        "when the learned value is below the floor"
    )
    assert result.fallback_level == "city"
    # Bias correction should be applied (mean_error=0.5 → mu adjusted by -0.5)
    assert result.bias_correction == pytest.approx(0.5, abs=0.01)


@pytest.mark.asyncio
async def test_run_analysis_v2_calibration_not_applied_small_sample():
    """Calibration should NOT be applied when sample_size < MIN_CALIB_SAMPLE.
    
    The CalibrationAdjustment SQL query includes sample_size >= MIN_CALIB_SAMPLE
    in the WHERE clause, so it simply returns None when sample is too small.
    """
    from app.services.probability_engine_v2 import run_analysis_v2

    # All DB lookups return None → fixed-table fallback, no calibration
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    result = await run_analysis_v2(
        title="Test",
        subtitle=None,
        city="Chicago",
        target_date_str="2026-08-10",
        weather_variable="high",
        operator="gte",
        threshold=90.0,
        parse_confidence="high",
        settlement_status="supported",
        unsupported_reason=None,
        forecast_high=85.0,
        forecast_low=70.0,
        forecast_retrieved_at=None,
        yes_bid=0.30,
        yes_ask=0.35,
        session=mock_session,
    )
    # No calibration should be applied (adj = 1.0)
    assert result.calibration_adj == pytest.approx(1.0, abs=0.001)
    # raw == final when no adjustment
    assert result.raw_ec_probability == pytest.approx(result.ec_probability, abs=0.0001)
    # Fallback level should be fixed_table since no stats available
    assert result.fallback_level == "fixed_table"


# ── get_strategy_agreement ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_strategy_agreement_no_trades():
    """Agreement should return all zeros when no trades exist."""
    from app.services.paper_trading_v2 import get_strategy_agreement

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    result = await get_strategy_agreement(mock_session)
    assert result["bothTrade"] == 0
    assert result["onlyV1"] == 0
    assert result["onlyV2"] == 0
    assert result["differentSides"] == 0


@pytest.mark.asyncio
async def test_get_strategy_agreement_only_v1():
    """When v2 has no trades, all should be counted as only_v1."""
    from app.services.paper_trading_v2 import get_strategy_agreement

    v1_trade = MagicMock()
    v1_trade.market_ticker = "ABC-1"
    v1_trade.strategy_version = "v1.0"
    v1_trade.status = "OPEN"
    v1_trade.direction = "YES"
    v1_trade.ec_yes_probability = 0.7

    mock_session = AsyncMock()
    call_count = [0]

    async def mock_execute(q):
        m = MagicMock()
        call_count[0] += 1
        if call_count[0] == 1:
            m.scalars.return_value.all.return_value = [v1_trade]
        else:
            m.scalars.return_value.all.return_value = []
        return m

    mock_session.execute.side_effect = mock_execute

    result = await get_strategy_agreement(mock_session)
    assert result["onlyV1"] == 1
    assert result["bothTrade"] == 0
    assert result["onlyV2"] == 0


@pytest.mark.asyncio
async def test_get_strategy_agreement_different_sides():
    """Trades on the same market but opposite directions should count as different_sides."""
    from app.services.paper_trading_v2 import get_strategy_agreement

    v1_trade = MagicMock()
    v1_trade.market_ticker = "ABC-1"
    v1_trade.strategy_version = "v1.0"
    v1_trade.status = "OPEN"
    v1_trade.direction = "YES"
    v1_trade.ec_yes_probability = 0.75
    v1_trade.fallback_level = "fixed_table"
    v1_trade.bias_correction = 0.0

    v2_trade = MagicMock()
    v2_trade.market_ticker = "ABC-1"
    v2_trade.strategy_version = "v2.0"
    v2_trade.status = "OPEN"
    v2_trade.direction = "NO"
    v2_trade.ec_yes_probability = 0.40
    v2_trade.fallback_level = "city"
    v2_trade.bias_correction = 2.0

    mock_session = AsyncMock()
    call_count = [0]

    async def mock_execute(q):
        m = MagicMock()
        call_count[0] += 1
        if call_count[0] == 1:
            m.scalars.return_value.all.return_value = [v1_trade]
        else:
            m.scalars.return_value.all.return_value = [v2_trade]
        return m

    mock_session.execute.side_effect = mock_execute

    result = await get_strategy_agreement(mock_session)
    assert result["bothTrade"] == 1
    assert result["differentSides"] == 1
    assert result["sameSides"] == 0
    assert result["probDivergenceGt10pp"] == 1  # |0.75 - 0.40| = 0.35 > 0.10
    assert len(result["samples"]) == 1
    assert result["samples"][0]["agree"] is False


@pytest.mark.asyncio
async def test_get_strategy_agreement_same_sides():
    """Trades agreeing on direction should count as same_sides."""
    from app.services.paper_trading_v2 import get_strategy_agreement

    v1_trade = MagicMock()
    v1_trade.market_ticker = "ABC-1"
    v1_trade.strategy_version = "v1.0"
    v1_trade.status = "OPEN"
    v1_trade.direction = "YES"
    v1_trade.ec_yes_probability = 0.65
    v1_trade.fallback_level = "fixed_table"
    v1_trade.bias_correction = 0.0

    v2_trade = MagicMock()
    v2_trade.market_ticker = "ABC-1"
    v2_trade.strategy_version = "v2.0"
    v2_trade.status = "OPEN"
    v2_trade.direction = "YES"
    v2_trade.ec_yes_probability = 0.68
    v2_trade.fallback_level = "city"
    v2_trade.bias_correction = 0.3

    mock_session = AsyncMock()
    call_count = [0]

    async def mock_execute(q):
        m = MagicMock()
        call_count[0] += 1
        if call_count[0] == 1:
            m.scalars.return_value.all.return_value = [v1_trade]
        else:
            m.scalars.return_value.all.return_value = [v2_trade]
        return m

    mock_session.execute.side_effect = mock_execute

    result = await get_strategy_agreement(mock_session)
    assert result["sameSides"] == 1
    assert result["differentSides"] == 0
    assert result["probDivergenceGt10pp"] == 0  # |0.65 - 0.68| = 0.03 < 0.10
    assert result["samples"][0]["agree"] is True


# ── MIN_SAMPLE and MIN_CALIB_SAMPLE constants ─────────────────────────────────

def test_min_sample_constant():
    # Raised from 5 to 30 in v2.1 to prevent 5-sample σ values (e.g. 1.22°F)
    # from generating false 90+pp edges. Tests that relied on MIN_SAMPLE==5
    # should use the actual constant value, not a hardcoded literal.
    assert MIN_SAMPLE == 30


def test_min_calib_sample_constant():
    assert MIN_CALIB_SAMPLE == 30


# ── STRATEGY_VERSION constant ─────────────────────────────────────────────────

def test_strategy_version_is_v2():
    assert STRATEGY_VERSION == "v2.0"
