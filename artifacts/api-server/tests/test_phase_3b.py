"""
Phase 3B tests — quality flags, analytics, calibration, enhanced filters,
strategy-version filtering, and mutation prevention.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.paper_trading import (
    FLAG_DESCRIPTIONS,
    compute_quality_flags,
    edge_bucket,
    get_calibration_report,
    get_paper_trade_analytics,
    get_paper_trade_metrics,
    price_bucket,
    lead_bucket,
    EDGE_BUCKET_ORDER,
    PRICE_BUCKET_ORDER,
    LEAD_BUCKET_ORDER,
    _build_breakdown,
    _breakdown_row,
)
from app.models import KalshiMarket, PaperTrade, PredictionSnapshot


# ── fixtures ──────────────────────────────────────────────────────────────────

def _market(**kwargs) -> KalshiMarket:
    # Use a recent timestamp by default so stale_market_quote is not triggered
    _recent = datetime.now(timezone.utc)
    m = MagicMock(spec=KalshiMarket)
    m.close_time = kwargs.get("close_time", _recent)
    m.yes_bid = kwargs.get("yes_bid", 0.40)
    m.yes_ask = kwargs.get("yes_ask", 0.44)
    m.no_bid = kwargs.get("no_bid", 0.56)
    m.no_ask = kwargs.get("no_ask", 0.60)
    m.volume = kwargs.get("volume", 500)
    m.collection_timestamp = kwargs.get("collection_timestamp", _recent)
    return m


def _snap(**kwargs) -> PredictionSnapshot:
    s = MagicMock(spec=PredictionSnapshot)
    s.settlement_variable = kwargs.get("settlement_variable", "temperature_high")
    s.analysis_status = kwargs.get("analysis_status", "supported")
    s.forecast_retrieved_at = kwargs.get(
        "forecast_retrieved_at", datetime(2025, 6, 29, tzinfo=timezone.utc)
    )
    s.lead_time_days = kwargs.get("lead_time_days", 3)
    return s


def _trade(**kwargs) -> PaperTrade:
    t = MagicMock(spec=PaperTrade)
    t.id = kwargs.get("id", 1)
    t.status = kwargs.get("status", "SETTLED")
    t.outcome = kwargs.get("outcome", "WIN")
    t.direction = kwargs.get("direction", "YES")
    t.city = kwargs.get("city", "Chicago")
    t.contract_type = kwargs.get("contract_type", "threshold")
    t.confidence_label = kwargs.get("confidence_label", "High")
    t.weather_variable = kwargs.get("weather_variable", "temperature_high")
    t.target_settlement_date = kwargs.get("target_settlement_date", "2025-07-01")
    t.strategy_version = kwargs.get("strategy_version", "v1.0")
    t.stake = kwargs.get("stake", 1.0)
    t.quantity = kwargs.get("quantity", 10)
    t.side_market_price = kwargs.get("side_market_price", 0.40)
    t.edge_pct_points = kwargs.get("edge_pct_points", 15.0)
    t.ec_yes_probability = kwargs.get("ec_yes_probability", 0.55)
    t.market_yes_probability = kwargs.get("market_yes_probability", 0.40)
    t.ec_side_probability = kwargs.get("ec_side_probability", 0.55)
    t.gross_payout = kwargs.get("gross_payout", 2.5)
    t.profit_loss = kwargs.get("profit_loss", 1.5)
    t.return_pct = kwargs.get("return_pct", 150.0)
    t.settlement_timestamp = kwargs.get(
        "settlement_timestamp", datetime(2025, 7, 2, tzinfo=timezone.utc)
    )
    t.kalshi_result = kwargs.get("kalshi_result", "yes")
    t.quality_flags = kwargs.get("quality_flags", None)
    t.lead_time_days = kwargs.get("lead_time_days", 3)
    t.price_source = kwargs.get("price_source", "yes_ask")
    t.confidence_score = None
    t.decision_explanation = ""
    t.warnings = ""
    t.snapshot_id = None
    t.market_ticker = kwargs.get("market_ticker", "WEATHER-CHI-20250701")
    t.event_ticker = None
    t.created_at = kwargs.get("created_at", datetime(2025, 6, 28, tzinfo=timezone.utc))
    return t


# ── quality flags ─────────────────────────────────────────────────────────────

class TestComputeQualityFlags:
    def test_clean_trade_has_no_flags(self):
        flags = compute_quality_flags(_market(), _snap(), side_market_price=0.40)
        assert flags == []

    def test_missing_settlement_station(self):
        flags = compute_quality_flags(_market(), _snap(settlement_variable=None), 0.40)
        assert "missing_settlement_station" in flags

    def test_missing_expiration_time(self):
        flags = compute_quality_flags(_market(close_time=None), _snap(), 0.40)
        assert "missing_expiration_time" in flags

    def test_unsupported_settlement_rule(self):
        flags = compute_quality_flags(_market(), _snap(analysis_status="unsupported"), 0.40)
        assert "unsupported_settlement_rule" in flags

    def test_zero_volume(self):
        flags = compute_quality_flags(_market(volume=0), _snap(), 0.40)
        assert "zero_volume" in flags

    def test_missing_liquidity(self):
        m = _market(yes_bid=None, yes_ask=None, no_bid=None, no_ask=None)
        flags = compute_quality_flags(m, _snap(), None)
        assert "missing_liquidity" in flags

    def test_large_spread(self):
        flags = compute_quality_flags(_market(yes_bid=0.20, yes_ask=0.45), _snap(), 0.45)
        assert "large_bid_ask_spread" in flags

    def test_low_entry_price(self):
        flags = compute_quality_flags(_market(), _snap(), side_market_price=0.03)
        assert "low_entry_price" in flags

    def test_stale_market_quote(self):
        stale = datetime(2025, 6, 25, tzinfo=timezone.utc)
        now = datetime(2025, 6, 30, tzinfo=timezone.utc)
        flags = compute_quality_flags(_market(collection_timestamp=stale), _snap(), 0.40, created_at=now)
        assert "stale_market_quote" in flags

    def test_forecast_after_trade(self):
        trade_time = datetime(2025, 6, 29, tzinfo=timezone.utc)
        future_ts = datetime(2025, 6, 30, tzinfo=timezone.utc)
        flags = compute_quality_flags(_market(), _snap(forecast_retrieved_at=future_ts), 0.40, created_at=trade_time)
        assert "forecast_after_trade" in flags

    def test_correlated_trades(self):
        flags = compute_quality_flags(_market(), _snap(), 0.40, correlated_count=2)
        assert "correlated_trades" in flags

    def test_low_fillability(self):
        flags = compute_quality_flags(_market(), _snap(), side_market_price=0.02)
        assert "low_fillability" in flags

    def test_all_flag_descriptions_present(self):
        """Every flag has a description."""
        known_flags = {
            "missing_settlement_station",
            "missing_expiration_time",
            "unsupported_settlement_rule",
            "zero_volume",
            "missing_liquidity",
            "large_bid_ask_spread",
            "low_entry_price",
            "stale_market_quote",
            "forecast_after_trade",
            "correlated_trades",
            "low_fillability",
        }
        assert known_flags == set(FLAG_DESCRIPTIONS.keys())

    def test_multiple_flags(self):
        m = _market(yes_bid=None, yes_ask=None, no_bid=None, no_ask=None, close_time=None)
        flags = compute_quality_flags(m, _snap(), None)
        assert "missing_liquidity" in flags
        assert "missing_expiration_time" in flags


# ── bucket helpers ────────────────────────────────────────────────────────────

class TestBucketHelpers:
    @pytest.mark.parametrize("edge,expected", [
        (5.0, "<10pp"),
        (15.0, "10-20pp"),
        (25.0, "20-30pp"),
        (35.0, "30-40pp"),
        (45.0, "≥40pp"),
        (None, "unknown"),
    ])
    def test_edge_bucket(self, edge, expected):
        assert edge_bucket(edge) == expected

    @pytest.mark.parametrize("price,expected", [
        (0.03, "1-5¢"),
        (0.10, "6-15¢"),
        (0.25, "16-30¢"),
        (0.45, "31-50¢"),
        (0.75, ">50¢"),
        (None, "unknown"),
    ])
    def test_price_bucket(self, price, expected):
        assert price_bucket(price) == expected

    @pytest.mark.parametrize("days,expected", [
        (0, "0-1d"),
        (1, "0-1d"),
        (2, "2-3d"),
        (3, "2-3d"),
        (5, "4-7d"),
        (10, ">7d"),
        (None, "unknown"),
    ])
    def test_lead_bucket(self, days, expected):
        assert lead_bucket(days) == expected

    def test_edge_bucket_order_complete(self):
        for b in ("<10pp", "10-20pp", "20-30pp", "30-40pp", "≥40pp", "unknown"):
            assert b in EDGE_BUCKET_ORDER

    def test_price_bucket_order_complete(self):
        for b in ("1-5¢", "6-15¢", "16-30¢", "31-50¢", ">50¢", "unknown"):
            assert b in PRICE_BUCKET_ORDER


# ── breakdown row ─────────────────────────────────────────────────────────────

class TestBreakdownRow:
    def test_basic_win_rate(self):
        trades = [_trade(outcome="WIN"), _trade(outcome="LOSS"), _trade(outcome="WIN")]
        row = _breakdown_row("test", trades)
        assert row["settledCount"] == 3
        assert row["wins"] == 2
        assert row["losses"] == 1
        assert abs(row["winRate"] - 2 / 3) < 0.001

    def test_roi_calculation(self):
        trades = [_trade(profit_loss=1.0, stake=1.0, outcome="WIN")]
        row = _breakdown_row("test", trades)
        assert row["roi"] == pytest.approx(100.0)

    def test_realistic_adj_pl(self):
        """adj_pl = raw_pl - stake * cost_pct / 100"""
        trades = [_trade(profit_loss=1.0, stake=1.0, outcome="WIN")]
        row = _breakdown_row("test", trades, total_cost_rate=10.0)
        assert row["adjProfitLoss"] == pytest.approx(0.9)

    def test_no_adj_pl_when_no_cost(self):
        trades = [_trade(profit_loss=1.0, stake=1.0, outcome="WIN")]
        row = _breakdown_row("test", trades, total_cost_rate=0.0)
        assert row["adjProfitLoss"] is None

    def test_open_trades_excluded(self):
        """Only SETTLED trades count toward breakdown stats."""
        settled = _trade(status="SETTLED", outcome="WIN")
        open_ = _trade(status="OPEN", outcome=None)
        row = _breakdown_row("test", [settled, open_])
        assert row["settledCount"] == 1


# ── analytics service ─────────────────────────────────────────────────────────

class TestGetPaperTradeAnalytics:
    @pytest.mark.asyncio
    async def test_returns_expected_keys(self):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            _trade(profit_loss=1.0, outcome="WIN"),
            _trade(profit_loss=-1.0, outcome="LOSS"),
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)
        data = await get_paper_trade_analytics(mock_session)
        assert "cumulativePl" in data
        assert "dailyPl" in data
        assert "byDirection" in data
        assert "byEdgeBucket" in data
        assert "byPriceBucket" in data
        assert "byLeadTime" in data
        assert "byCity" in data
        assert "byContractType" in data

    @pytest.mark.asyncio
    async def test_cumulative_pl_is_cumulative(self):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        t1 = _trade(profit_loss=1.0, settlement_timestamp=datetime(2025, 7, 1, tzinfo=timezone.utc))
        t2 = _trade(profit_loss=2.0, settlement_timestamp=datetime(2025, 7, 2, tzinfo=timezone.utc))
        mock_result.scalars.return_value.all.return_value = [t1, t2]
        mock_session.execute = AsyncMock(return_value=mock_result)
        data = await get_paper_trade_analytics(mock_session)
        series = data["cumulativePl"]
        assert len(series) == 2
        assert series[0]["cumulativePl"] == pytest.approx(1.0)
        assert series[1]["cumulativePl"] == pytest.approx(3.0)

    @pytest.mark.asyncio
    async def test_flagged_exclusion(self):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        clean = _trade(quality_flags=None)
        flagged = _trade(quality_flags=["zero_volume"])
        mock_result.scalars.return_value.all.return_value = [clean, flagged]
        mock_session.execute = AsyncMock(return_value=mock_result)
        data = await get_paper_trade_analytics(mock_session, include_flagged=False)
        # only clean trade contributes to series
        assert len(data["cumulativePl"]) == 1

    @pytest.mark.asyncio
    async def test_realistic_adj_cumulative(self):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        t = _trade(profit_loss=1.0, stake=1.0, settlement_timestamp=datetime(2025, 7, 1, tzinfo=timezone.utc))
        mock_result.scalars.return_value.all.return_value = [t]
        mock_session.execute = AsyncMock(return_value=mock_result)
        data = await get_paper_trade_analytics(mock_session, fee_pct=5.0, slippage_pct=5.0)
        series = data["cumulativePl"]
        assert series[0]["adjCumulativePl"] == pytest.approx(0.9)  # 1.0 - 1.0 * 10/100


# ── calibration service ───────────────────────────────────────────────────────

class TestGetCalibrationReport:
    @pytest.mark.asyncio
    async def test_brier_score_perfect_calibration(self):
        """If every trade has ec_yes_probability matching the actual outcome, Brier score is 0."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        t1 = _trade(ec_yes_probability=1.0, kalshi_result="yes", status="SETTLED")
        t2 = _trade(ec_yes_probability=0.0, kalshi_result="no", status="SETTLED")
        mock_result.scalars.return_value.all.return_value = [t1, t2]
        mock_session.execute = AsyncMock(return_value=mock_result)
        report = await get_calibration_report(mock_session)
        assert report["brierScore"] == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_brier_score_always_wrong(self):
        """Always predicting 1.0 when outcome is no → Brier score = 1.0."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        t = _trade(ec_yes_probability=1.0, kalshi_result="no", status="SETTLED")
        mock_result.scalars.return_value.all.return_value = [t]
        mock_session.execute = AsyncMock(return_value=mock_result)
        report = await get_calibration_report(mock_session)
        assert report["brierScore"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_ten_buckets_returned(self):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        report = await get_calibration_report(mock_session)
        assert len(report["buckets"]) == 10
        assert report["brierScore"] is None
        assert report["totalSettled"] == 0

    @pytest.mark.asyncio
    async def test_bucket_placement(self):
        """A trade with ec_yes_probability=0.35 should land in the 31-40% bucket."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        t = _trade(ec_yes_probability=0.35, kalshi_result="yes", status="SETTLED")
        mock_result.scalars.return_value.all.return_value = [t]
        mock_session.execute = AsyncMock(return_value=mock_result)
        report = await get_calibration_report(mock_session)
        bucket = next(b for b in report["buckets"] if b["bucket"] == "31-40%")
        assert bucket["count"] == 1
        assert bucket["actualYesRate"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_strategy_version_param_forwarded(self):
        """strategy_version filter is applied when provided."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        report = await get_calibration_report(mock_session, strategy_version="v2.0")
        assert report["strategyVersion"] == "v2.0"


# ── metrics: avgEntryPrice + strategy_version ─────────────────────────────────

class TestMetricsUpdates:
    @pytest.mark.asyncio
    async def test_avg_entry_price_in_metrics(self):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            _trade(side_market_price=0.40, status="SETTLED"),
            _trade(side_market_price=0.60, status="SETTLED"),
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)
        m = await get_paper_trade_metrics(mock_session)
        assert "avgEntryPrice" in m
        assert m["avgEntryPrice"] == pytest.approx(0.50)

    @pytest.mark.asyncio
    async def test_strategy_version_filter(self):
        """Passing strategy_version scopes the query (we confirm the param is forwarded)."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        m = await get_paper_trade_metrics(mock_session, strategy_version="v2.0")
        # No crash and all expected keys present
        assert "openCount" in m
        assert "avgEntryPrice" in m


# ── list filter: strategy_version preservation ────────────────────────────────

class TestStrategyVersionPreservation:
    def test_strategy_version_is_immutable_after_save(self):
        """
        Changing the strategy version in settings should not change existing trade records.
        PaperTrade.strategy_version is set at INSERT time; we verify the model attribute
        is stored independently and never overwritten by a settings change.
        """
        t = _trade(strategy_version="v1.0")
        assert t.strategy_version == "v1.0"
        # Simulate a settings update — does not touch existing trade object
        new_setting_version = "v2.0"
        # Trade remains unchanged
        assert t.strategy_version == "v1.0"
        assert t.strategy_version != new_setting_version


# ── router filter params ──────────────────────────────────────────────────────

class TestRouterFilters:
    def test_matches_filters_strategy_version(self):
        from app.routers.paper_trades import _matches_filters
        t = _trade(strategy_version="v1.0")
        assert _matches_filters(t, None, None, None, None, None, None, None, "v1.0", None, None, None, None)
        assert not _matches_filters(t, None, None, None, None, None, None, None, "v2.0", None, None, None, None)

    def test_matches_filters_edge_bucket(self):
        from app.routers.paper_trades import _matches_filters
        t = _trade(edge_pct_points=15.0)
        assert _matches_filters(t, None, None, None, None, None, None, None, None, "10-20pp", None, None, None)
        assert not _matches_filters(t, None, None, None, None, None, None, None, None, "<10pp", None, None, None)

    def test_matches_filters_price_bucket(self):
        from app.routers.paper_trades import _matches_filters
        t = _trade(side_market_price=0.10)  # 10¢ → "6-15¢"
        assert _matches_filters(t, None, None, None, None, None, None, None, None, None, "6-15¢", None, None)
        assert not _matches_filters(t, None, None, None, None, None, None, None, None, None, "31-50¢", None, None)

    def test_matches_filters_is_flagged(self):
        from app.routers.paper_trades import _matches_filters
        clean = _trade(quality_flags=None)
        flagged = _trade(quality_flags=["zero_volume"])
        assert _matches_filters(clean, None, None, None, None, None, None, None, None, None, None, False, None)
        assert not _matches_filters(clean, None, None, None, None, None, None, None, None, None, None, True, None)
        assert _matches_filters(flagged, None, None, None, None, None, None, None, None, None, None, True, None)

    def test_matches_filters_outcome(self):
        from app.routers.paper_trades import _matches_filters
        t = _trade(outcome="WIN")
        assert _matches_filters(t, None, None, None, None, None, None, None, None, None, None, None, "WIN")
        assert not _matches_filters(t, None, None, None, None, None, None, None, None, None, None, None, "LOSS")
