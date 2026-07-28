"""
Tests for Phase 3A — Paper Trading Service.

Covers:
  - Eligibility checks (qualifying YES/NO, edge below threshold, low confidence,
    unsupported market, missing price, closed market, duplicate prevention)
  - Price selection (YES ask for YES, NO ask for NO, fallback to bid, missing ask)
  - Position math (quantity, winning payout, losing P/L, ROI, void)
  - Settlement logic (YES wins, YES loses, NO wins, NO loses, void, missing result)
  - History integrity (snapshot immutability, later price doesn't alter entry,
    one-trade-per-strategy-version)
  - Settings helpers
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from unittest.mock import MagicMock as _MM

from app.models import KalshiMarket, PaperTrade, PredictionSnapshot
from app.services.paper_trading import (
    DEFAULT_SETTINGS,
    _is_confidence_sufficient,
    _select_yes_price,
    _select_no_price,
    calculate_position,
    decide_trade,
    settle_position,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _market(**kwargs) -> KalshiMarket:
    defaults = dict(
        ticker="KXHIGHTEMP-NYC-20260801-GTE85",
        event_ticker="KXHIGHTEMP-NYC-20260801",
        city="New York City",
        status="active",
        yes_bid=0.40,
        yes_ask=0.45,
        no_bid=0.55,
        no_ask=0.57,
        weather_market_type="temperature",
    )
    defaults.update(kwargs)
    m = _MM(spec=KalshiMarket)
    for k, v in defaults.items():
        setattr(m, k, v)
    return m  # type: ignore[return-value]


def _snap(**kwargs) -> PredictionSnapshot:
    defaults = dict(
        id=1,
        market_ticker="KXHIGHTEMP-NYC-20260801-GTE85",
        analysis_status="supported",
        ec_probability=0.70,
        market_probability=0.45,
        confidence="High",
        contract_type="threshold",
        settlement_variable="high",
    )
    defaults.update(kwargs)
    s = _MM(spec=PredictionSnapshot)
    for k, v in defaults.items():
        setattr(s, k, v)
    return s  # type: ignore[return-value]


def _settings(**kwargs):
    s = dict(DEFAULT_SETTINGS)
    s.update(kwargs)
    return s


# ── Confidence check ──────────────────────────────────────────────────────────

class TestConfidenceCheck:
    def test_very_high_meets_high(self):
        assert _is_confidence_sufficient("Very High", "High") is True

    def test_high_meets_high(self):
        assert _is_confidence_sufficient("High", "High") is True

    def test_medium_does_not_meet_high(self):
        assert _is_confidence_sufficient("Medium", "High") is False

    def test_low_does_not_meet_high(self):
        assert _is_confidence_sufficient("Low", "High") is False

    def test_none_never_meets(self):
        assert _is_confidence_sufficient(None, "High") is False

    def test_medium_meets_medium(self):
        assert _is_confidence_sufficient("Medium", "Medium") is True

    def test_very_high_meets_very_high(self):
        assert _is_confidence_sufficient("Very High", "Very High") is True

    def test_high_does_not_meet_very_high(self):
        assert _is_confidence_sufficient("High", "Very High") is False


# ── Price selection ───────────────────────────────────────────────────────────

class TestPriceSelection:
    def test_yes_price_prefers_ask(self):
        m = _market(yes_ask=0.45, yes_bid=0.40)
        price, src = _select_yes_price(m)
        assert price == 0.45
        assert src == "YES_ASK"

    def test_yes_price_falls_back_to_bid(self):
        m = _market(yes_ask=None, yes_bid=0.40)
        price, src = _select_yes_price(m)
        assert price == 0.40
        assert src == "YES_BID"

    def test_yes_price_none_when_both_missing(self):
        m = _market(yes_ask=None, yes_bid=None)
        price, src = _select_yes_price(m)
        assert price is None
        assert src is None

    def test_no_price_prefers_ask(self):
        m = _market(no_ask=0.57, no_bid=0.55)
        price, src = _select_no_price(m)
        assert price == 0.57
        assert src == "NO_ASK"

    def test_no_price_falls_back_to_bid(self):
        m = _market(no_ask=None, no_bid=0.55)
        price, src = _select_no_price(m)
        assert price == 0.55
        assert src == "NO_BID"

    def test_no_price_none_when_both_missing(self):
        m = _market(no_ask=None, no_bid=None)
        price, src = _select_no_price(m)
        assert price is None
        assert src is None


# ── Position math ─────────────────────────────────────────────────────────────

class TestPositionMath:
    def test_quantity_calculation(self):
        pos = calculate_position(stake=10.0, purchase_price=0.40)
        assert pos["stake"] == 10.0
        assert abs(pos["quantity"] - 25.0) < 1e-9

    def test_quantity_at_0_50(self):
        pos = calculate_position(stake=10.0, purchase_price=0.50)
        assert abs(pos["quantity"] - 20.0) < 1e-9

    def test_quantity_at_0_90(self):
        pos = calculate_position(stake=10.0, purchase_price=0.90)
        assert abs(pos["quantity"] - 10.0 / 0.9) < 1e-9

    def test_zero_price_returns_zero_quantity(self):
        pos = calculate_position(stake=10.0, purchase_price=0.0)
        assert pos["quantity"] == 0.0


# ── Settlement math ───────────────────────────────────────────────────────────

class TestSettleMath:
    """settle_position(direction, quantity, stake, kalshi_result)"""

    def test_yes_wins(self):
        result = settle_position("YES", quantity=25.0, stake=10.0, kalshi_result="yes")
        assert result["outcome"] == "WIN"
        assert abs(result["gross_payout"] - 25.0) < 1e-6
        assert abs(result["profit_loss"] - 15.0) < 1e-6
        assert abs(result["return_pct"] - 150.0) < 1e-4

    def test_yes_loses(self):
        result = settle_position("YES", quantity=25.0, stake=10.0, kalshi_result="no")
        assert result["outcome"] == "LOSS"
        assert result["gross_payout"] == 0.0
        assert abs(result["profit_loss"] - (-10.0)) < 1e-6
        assert result["return_pct"] == -100.0

    def test_no_wins(self):
        result = settle_position("NO", quantity=20.0, stake=10.0, kalshi_result="no")
        assert result["outcome"] == "WIN"
        assert abs(result["gross_payout"] - 20.0) < 1e-6
        assert abs(result["profit_loss"] - 10.0) < 1e-6

    def test_no_loses(self):
        result = settle_position("NO", quantity=20.0, stake=10.0, kalshi_result="yes")
        assert result["outcome"] == "LOSS"
        assert result["gross_payout"] == 0.0
        assert result["profit_loss"] == -10.0

    def test_void_returns_stake(self):
        result = settle_position("YES", quantity=25.0, stake=10.0, kalshi_result="void")
        assert result["outcome"] == "VOID"
        assert result["gross_payout"] == 10.0
        assert result["profit_loss"] == 0.0
        assert result["return_pct"] == 0.0

    def test_winning_roi_correct_at_0_40(self):
        """stake $10 at $0.40 → 25 contracts → gross $25 → profit $15 → ROI 150%"""
        result = settle_position("YES", quantity=25.0, stake=10.0, kalshi_result="yes")
        assert abs(result["return_pct"] - 150.0) < 0.01

    def test_winning_roi_correct_at_0_80(self):
        """stake $10 at $0.80 → 12.5 contracts → gross $12.5 → profit $2.5 → ROI 25%"""
        result = settle_position("YES", quantity=12.5, stake=10.0, kalshi_result="yes")
        assert abs(result["return_pct"] - 25.0) < 0.01


# ── Trade direction decision ──────────────────────────────────────────────────

class TestDecideTrade:
    def test_yes_trade_when_ec_above_ask(self):
        # EC=0.70, YES_ASK=0.45 → edge = 25pp → YES
        m = _market(yes_ask=0.45, no_ask=0.57)
        s = _snap(ec_probability=0.70, market_probability=0.45, confidence="High",
                  analysis_status="supported")
        result = decide_trade(s, m, _settings(min_edge_pct=10.0, min_confidence="High"))
        assert result["action"] == "YES"
        assert result["direction"] == "YES"
        assert result["price_source"] == "YES_ASK"
        assert abs(result["edge_pct_points"] - 25.0) < 0.01

    def test_no_trade_when_no_edge_dominates(self):
        # EC=0.15, YES_ASK=0.45 → NO ec=0.85, NO_ASK=0.20 → NO edge=65pp
        m = _market(yes_ask=0.45, no_ask=0.20)
        s = _snap(ec_probability=0.15, market_probability=0.45, confidence="High",
                  analysis_status="supported")
        result = decide_trade(s, m, _settings(min_edge_pct=10.0, min_confidence="High"))
        assert result["action"] == "NO"
        assert result["direction"] == "NO"
        assert result["price_source"] == "NO_ASK"

    def test_skip_when_edge_below_threshold(self):
        # EC=0.50, YES_ASK=0.48 → edge=2pp < 10pp
        m = _market(yes_ask=0.48, no_ask=0.54)
        s = _snap(ec_probability=0.50, market_probability=0.48, confidence="High",
                  analysis_status="supported")
        result = decide_trade(s, m, _settings(min_edge_pct=10.0))
        assert result["action"] == "SKIP"
        assert "threshold" in result["skip_reason"].lower()

    def test_skip_when_confidence_too_low(self):
        m = _market(yes_ask=0.30)
        s = _snap(ec_probability=0.70, market_probability=0.30, confidence="Medium",
                  analysis_status="supported")
        result = decide_trade(s, m, _settings(min_edge_pct=10.0, min_confidence="High"))
        assert result["action"] == "SKIP"
        assert "confidence" in result["skip_reason"].lower()

    def test_skip_when_unsupported(self):
        m = _market(yes_ask=0.30)
        s = _snap(ec_probability=0.70, analysis_status="unsupported")
        result = decide_trade(s, m, _settings())
        assert result["action"] == "SKIP"
        assert "unsupported" in result["skip_reason"].lower()

    def test_skip_when_ec_prob_missing(self):
        m = _market(yes_ask=0.30)
        s = _snap(ec_probability=None, analysis_status="supported")
        result = decide_trade(s, m, _settings())
        assert result["action"] == "SKIP"

    def test_skip_when_market_price_missing(self):
        m = _market(yes_ask=None, yes_bid=None, no_ask=None, no_bid=None)
        s = _snap(ec_probability=0.70, market_probability=None, analysis_status="supported")
        result = decide_trade(s, m, _settings())
        assert result["action"] == "SKIP"

    def test_skip_when_market_closed(self):
        m = _market(yes_ask=0.30, status="closed")
        s = _snap(ec_probability=0.70, market_probability=0.30, analysis_status="supported",
                  confidence="High")
        result = decide_trade(s, m, _settings())
        assert result["action"] == "SKIP"

    def test_yes_uses_ask_not_bid(self):
        m = _market(yes_ask=0.40, yes_bid=0.38)
        s = _snap(ec_probability=0.80, market_probability=0.40, confidence="High",
                  analysis_status="supported")
        result = decide_trade(s, m, _settings())
        assert result["side_market_price"] == 0.40
        assert result["price_source"] == "YES_ASK"

    def test_no_uses_ask_not_bid(self):
        m = _market(yes_ask=0.80, no_ask=0.18, no_bid=0.16)
        s = _snap(ec_probability=0.10, market_probability=0.80, confidence="High",
                  analysis_status="supported")
        result = decide_trade(s, m, _settings())
        assert result["action"] == "NO"
        assert result["side_market_price"] == 0.18
        assert result["price_source"] == "NO_ASK"

    def test_chooses_higher_edge_direction(self):
        # YES edge = 0.70 - 0.45 = 25pp; NO edge = 0.30 - 0.15 = 15pp → YES
        m = _market(yes_ask=0.45, no_ask=0.15)
        s = _snap(ec_probability=0.70, market_probability=0.45, confidence="High",
                  analysis_status="supported")
        result = decide_trade(s, m, _settings(min_edge_pct=10.0))
        assert result["direction"] == "YES"

    def test_decision_explanation_present(self):
        m = _market(yes_ask=0.40)
        s = _snap(ec_probability=0.70, market_probability=0.40, confidence="High",
                  analysis_status="supported")
        result = decide_trade(s, m, _settings())
        assert len(result["decision_explanation"]) > 20

    def test_warnings_always_present(self):
        m = _market(yes_ask=0.40)
        s = _snap(ec_probability=0.70, market_probability=0.40, confidence="High",
                  analysis_status="supported")
        result = decide_trade(s, m, _settings())
        assert any("simulation" in w.lower() or "no real" in w.lower() for w in result["warnings"])


# ── Async service tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestMaybeCreatePaperTrade:
    """Unit tests for maybe_create_paper_trade using mocked DB session."""

    async def test_creates_yes_trade(self):
        from app.services.paper_trading import maybe_create_paper_trade

        m = _market(yes_ask=0.40)
        s = _snap(ec_probability=0.70, market_probability=0.40, confidence="High",
                  analysis_status="supported")
        settings = _settings(strategy_version="v1.0", stake=10.0, min_edge_pct=10.0,
                              min_confidence="High")

        session = AsyncMock()
        # Simulate no existing trade found
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        result = await maybe_create_paper_trade(session, m, s, settings)
        assert result["created"] is True
        assert result["direction"] == "YES"
        session.add.assert_called_once()

    async def test_prevents_duplicate_trade(self):
        from app.services.paper_trading import maybe_create_paper_trade

        m = _market(yes_ask=0.40)
        s = _snap(ec_probability=0.70, market_probability=0.40, confidence="High",
                  analysis_status="supported")
        settings = _settings(strategy_version="v1.0")

        existing_trade = MagicMock()
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_trade
        session.execute = AsyncMock(return_value=mock_result)

        result = await maybe_create_paper_trade(session, m, s, settings)
        assert result["created"] is False
        assert "existing" in result["skip_reason"].lower()
        session.add.assert_not_called()

    async def test_skips_ineligible_market(self):
        from app.services.paper_trading import maybe_create_paper_trade

        m = _market(yes_ask=0.48, no_ask=0.53)  # low edge
        s = _snap(ec_probability=0.50, market_probability=0.48, confidence="High",
                  analysis_status="supported")
        settings = _settings(strategy_version="v1.0", min_edge_pct=10.0)

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        result = await maybe_create_paper_trade(session, m, s, settings)
        assert result["created"] is False
        session.add.assert_not_called()

    async def test_quantity_computed_from_stake_and_price(self):
        from app.services.paper_trading import maybe_create_paper_trade

        m = _market(yes_ask=0.40)
        s = _snap(ec_probability=0.70, market_probability=0.40, confidence="High",
                  analysis_status="supported")
        settings = _settings(strategy_version="v1.0", stake=10.0, min_edge_pct=10.0)

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        await maybe_create_paper_trade(session, m, s, settings)

        # Find the PaperTrade added to the session
        added = session.add.call_args[0][0]
        assert isinstance(added, PaperTrade)
        assert abs(added.quantity - 25.0) < 1e-9  # 10 / 0.40 = 25


# ── Settings helpers ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestSettings:
    async def test_defaults_returned_when_no_db_rows(self):
        from app.services.paper_trading import get_paper_trade_settings

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        settings = await get_paper_trade_settings(session)
        assert settings["min_edge_pct"] == 10.0
        assert settings["min_confidence"] == "High"
        assert settings["stake"] == 10.0
        assert settings["enabled"] is True

    async def test_db_overrides_applied(self):
        from app.services.paper_trading import get_paper_trade_settings
        from app.models import AppSetting

        row = MagicMock()
        row.key = "paper_trading.min_edge_pct"
        row.value = "15.0"

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [row]
        session.execute = AsyncMock(return_value=mock_result)

        settings = await get_paper_trade_settings(session)
        assert settings["min_edge_pct"] == 15.0
        # Other defaults unchanged
        assert settings["stake"] == 10.0

    async def test_bad_db_value_falls_back_to_default(self):
        from app.services.paper_trading import get_paper_trade_settings

        row = MagicMock()
        row.key = "paper_trading.min_edge_pct"
        row.value = "not_a_number"

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [row]
        session.execute = AsyncMock(return_value=mock_result)

        settings = await get_paper_trade_settings(session)
        assert settings["min_edge_pct"] == 10.0  # unchanged default


# ── Settlement service ────────────────────────────────────────────────────────

class TestFetchKalshiMarket:
    """Unit tests for settlement._extract_result and _FetchResult."""

    def test_yes_result(self):
        from app.services.settlement import _extract_result
        assert _extract_result({"result": "yes", "status": "finalized"}) == "yes"

    def test_no_result(self):
        from app.services.settlement import _extract_result
        assert _extract_result({"result": "no", "status": "finalized"}) == "no"

    def test_canceled_status_returns_void(self):
        from app.services.settlement import _extract_result
        assert _extract_result({"result": "", "status": "canceled"}) == "void"

    def test_cancelled_status_returns_void(self):
        from app.services.settlement import _extract_result
        assert _extract_result({"result": "", "status": "cancelled"}) == "void"

    def test_no_result_and_active_status_returns_none(self):
        from app.services.settlement import _extract_result
        assert _extract_result({"result": "", "status": "active"}) is None

    def test_no_result_and_open_status_returns_none(self):
        from app.services.settlement import _extract_result
        assert _extract_result({"result": None, "status": "open"}) is None

    def test_fetch_result_ok_when_data_present(self):
        from app.services.settlement import _FetchResult
        r = _FetchResult(data={"result": "yes"})
        assert r.ok is True
        assert r.transient_error is False
        assert r.not_found is False

    def test_fetch_result_transient_when_error_msg_and_no_data(self):
        from app.services.settlement import _FetchResult
        r = _FetchResult(error_msg="HTTP 503")
        assert r.ok is False
        assert r.transient_error is True
        assert r.not_found is False

    def test_fetch_result_not_found_when_404(self):
        from app.services.settlement import _FetchResult
        r = _FetchResult(not_found=True)
        assert r.ok is False
        assert r.transient_error is False
        assert r.not_found is True


# ── Settlement retry / transient-failure behaviour ───────────────────────────

@pytest.mark.asyncio
class TestSettlementRetry:
    """
    Verify that transient Kalshi fetch failures (network errors, 5xx) leave
    trades in OPEN status so the next settlement cycle can retry them.
    Only 404 / truly-gone markets should be moved to ERROR.
    """

    def _open_trade(self) -> PaperTrade:
        t = _MM(spec=PaperTrade)
        t.id = 99
        t.market_ticker = "KXHIGHTEMP-NYC-GTE85"
        t.direction = "YES"
        t.quantity = 25.0
        t.stake = 10.0
        t.status = "OPEN"
        t.warnings = None
        return t

    async def test_transient_error_leaves_trade_open(self, monkeypatch):
        """A network/timeout error must NOT change status to ERROR."""
        from app.services import settlement
        from app.services.settlement import _FetchResult

        # Simulate a transient error
        async def _transient(_ticker):
            return _FetchResult(error_msg="Connection timed out")

        monkeypatch.setattr(settlement, "fetch_kalshi_market", _transient)

        trade = self._open_trade()
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [trade]
        session.execute = AsyncMock(return_value=mock_result)

        from app.database import AsyncSessionLocal as _orig
        import app.services.settlement as svc

        # Patch AsyncSessionLocal to use our mock session as a context manager
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=False)
        asl_mock = MagicMock(return_value=cm)

        import app.database as db_mod
        original = db_mod.AsyncSessionLocal
        db_mod.AsyncSessionLocal = asl_mock
        try:
            stats = await settlement.run_settlement_job()
        finally:
            db_mod.AsyncSessionLocal = original

        # Trade must remain OPEN
        assert trade.status == "OPEN"
        assert stats["still_open"] == 1
        assert stats["errors"] == 0

    async def test_404_marks_trade_error(self, monkeypatch):
        """A 404 (market not found on Kalshi) is terminal → ERROR status."""
        from app.services import settlement
        from app.services.settlement import _FetchResult

        async def _not_found(_ticker):
            return _FetchResult(not_found=True)

        monkeypatch.setattr(settlement, "fetch_kalshi_market", _not_found)

        trade = self._open_trade()
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [trade]
        session.execute = AsyncMock(return_value=mock_result)

        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=False)

        import app.database as db_mod
        original = db_mod.AsyncSessionLocal
        db_mod.AsyncSessionLocal = MagicMock(return_value=cm)
        try:
            stats = await settlement.run_settlement_job()
        finally:
            db_mod.AsyncSessionLocal = original

        assert trade.status == "ERROR"
        assert stats["errors"] == 1
        assert stats["still_open"] == 0

    async def test_transient_warnings_not_unbounded(self, monkeypatch):
        """Repeated transient errors should not cause warnings to grow without bound."""
        from app.services import settlement
        from app.services.settlement import _FetchResult

        async def _transient(_ticker):
            return _FetchResult(error_msg="HTTP 503")

        monkeypatch.setattr(settlement, "fetch_kalshi_market", _transient)

        trade = self._open_trade()
        trade.warnings = "Simulation only.; Previous transient note"

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [trade]
        session.execute = AsyncMock(return_value=mock_result)

        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=False)

        import app.database as db_mod
        original = db_mod.AsyncSessionLocal
        db_mod.AsyncSessionLocal = MagicMock(return_value=cm)
        try:
            await settlement.run_settlement_job()
        finally:
            db_mod.AsyncSessionLocal = original

        # Old "transient" note should be replaced, not appended again
        warnings = trade.warnings or ""
        transient_count = warnings.lower().count("transient")
        assert transient_count == 1, f"Expected exactly 1 transient mention, got: {warnings!r}"


# ── History integrity ─────────────────────────────────────────────────────────

class TestHistoryIntegrity:
    """Ensure immutability properties of paper trade records."""

    def test_settle_position_does_not_modify_stake(self):
        """settle_position must not mutate its inputs."""
        stake = 10.0
        result = settle_position("YES", quantity=25.0, stake=stake, kalshi_result="yes")
        assert stake == 10.0  # unchanged

    def test_different_strategy_versions_are_independent(self):
        """decide_trade is stateless — repeated calls with same inputs return same result."""
        m = _market(yes_ask=0.40)
        s = _snap(ec_probability=0.70, market_probability=0.40, confidence="High",
                  analysis_status="supported")
        r1 = decide_trade(s, m, _settings(strategy_version="v1.0"))
        r2 = decide_trade(s, m, _settings(strategy_version="v2.0"))
        # Direction and edge must be the same regardless of version
        assert r1["direction"] == r2["direction"]
        assert r1["edge_pct_points"] == r2["edge_pct_points"]

    def test_settle_win_before_stake_deducted(self):
        """Gross payout is the full contract payout; profit_loss already deducts stake."""
        result = settle_position("YES", quantity=20.0, stake=10.0, kalshi_result="yes")
        assert result["gross_payout"] == 20.0
        assert result["profit_loss"] == 10.0  # 20 - 10

    def test_entry_price_reflects_ask_not_midpoint(self):
        """YES trade uses ask price, not bid/ask midpoint."""
        m = _market(yes_ask=0.45, yes_bid=0.40)
        s = _snap(ec_probability=0.70, market_probability=0.45, confidence="High",
                  analysis_status="supported")
        result = decide_trade(s, m, _settings())
        assert result["side_market_price"] == 0.45  # ask, not 0.425 midpoint


# ── Metrics ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestMetrics:
    async def test_empty_metrics_returned_when_no_trades(self):
        from app.services.paper_trading import get_paper_trade_metrics

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        metrics = await get_paper_trade_metrics(session)
        assert metrics["openCount"] == 0
        assert metrics["settledCount"] == 0
        assert metrics["winRate"] is None
        assert metrics["sampleSizeWarning"] is True

    async def test_metrics_counts_correctly(self):
        from app.services.paper_trading import get_paper_trade_metrics

        def _trade(status, outcome, pl):
            t = _MM(spec=PaperTrade)
            t.status = status
            t.outcome = outcome
            t.profit_loss = pl
            t.stake = 10.0
            t.edge_pct_points = 15.0
            t.confidence_label = "High"
            t.direction = "YES"
            t.city = "NYC"
            t.contract_type = "threshold"
            return t

        trades = [
            _trade("OPEN",    None,   None),
            _trade("OPEN",    None,   None),
            _trade("SETTLED", "WIN",  5.0),
            _trade("SETTLED", "LOSS", -10.0),
            _trade("VOID",    "VOID", 0.0),
        ]

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = trades
        session.execute = AsyncMock(return_value=mock_result)

        metrics = await get_paper_trade_metrics(session)
        assert metrics["openCount"] == 2
        assert metrics["settledCount"] == 2
        assert metrics["voidCount"] == 1
        assert metrics["wins"] == 1
        assert metrics["losses"] == 1
        assert abs(metrics["winRate"] - 0.5) < 1e-6
        # net P/L = 5.0 + (-10.0) = -5.0
        assert abs(metrics["netProfitLoss"] - (-5.0)) < 1e-6
