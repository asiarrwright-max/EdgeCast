"""
Tests for app/services/v3_paper_trading.py — V3 Phase 3 paper trading.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.v3_paper_trading import (
    _check_station_nws,
    _decide_v3,
    STRATEGY_VERSION,
)


# ---------------------------------------------------------------------------
# Station NWS check (hard-block only on nws_settlement=False)
# ---------------------------------------------------------------------------

class TestCheckStationNws:
    def test_none_city_fails(self):
        ok, reason = _check_station_nws(None)
        assert ok is False
        assert reason is not None

    def test_city_no_station_fails(self):
        with patch("app.services.v3_paper_trading.get_station", return_value=None):
            ok, reason = _check_station_nws("Unknown")
        assert ok is False

    def test_non_nws_fails(self):
        st = MagicMock()
        st.verified = True
        st.nws_settlement = False
        with patch("app.services.v3_paper_trading.get_station", return_value=st):
            ok, reason = _check_station_nws("Chicago")
        assert ok is False
        assert "nws" in (reason or "").lower()

    def test_unverified_nws_passes_nws_guard(self):
        """
        Hardening change: unverified NWS cities are no longer hard-blocked.
        They pass _check_station_nws and are demoted to RESEARCH_ONLY by the
        eligibility engine (assess_trade_eligibility).
        """
        st = MagicMock()
        st.verified = False
        st.nws_settlement = True
        with patch("app.services.v3_paper_trading.get_station", return_value=st):
            ok, reason = _check_station_nws("Atlanta")
        assert ok is True
        assert reason is None

    def test_verified_nws_passes(self):
        st = MagicMock()
        st.verified = True
        st.nws_settlement = True
        st.lat = 39.86
        st.lon = -104.67
        with patch("app.services.v3_paper_trading.get_station", return_value=st):
            ok, reason = _check_station_nws("Denver")
        assert ok is True
        assert reason is None


# ---------------------------------------------------------------------------
# Core decision
# ---------------------------------------------------------------------------

def _make_v3_snap(ec_prob: float, market_prob: float = 0.50) -> MagicMock:
    snap = MagicMock()
    snap.ec_probability = ec_prob
    snap.market_probability = market_prob
    snap.analysis_status = "ok"
    snap.final_sigma = 4.0
    snap.fallback_level_used = 2
    snap.bias_applied = True
    snap.market_ticker = "TEST-TICKER"
    snap.contract_type = "threshold"
    snap.settlement_variable = "high"
    snap.lead_time_days = 2
    return snap


def _make_market(yes_bid: float = 0.45, yes_ask: float = 0.50,
                 status: str = "active",
                 days_ahead: int = 2) -> MagicMock:
    from datetime import timedelta
    settlement_dt = datetime.now(timezone.utc) + timedelta(days=days_ahead, hours=4)
    m = MagicMock()
    m.yes_bid = yes_bid
    m.yes_ask = yes_ask
    m.no_bid = round(1.0 - yes_ask, 4)
    m.no_ask = round(1.0 - yes_bid, 4)
    m.city = "Denver"
    m.status = status
    m.collection_timestamp = datetime.now(timezone.utc)
    m.target_date = settlement_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return m


_DEFAULT_SETTINGS = {
    "enabled": True,
    "min_edge_pct": 10.0,
    "stake": 10.0,
}

_GOOD_STATION = MagicMock(
    verified=True,
    nws_settlement=True,
    lat=39.86,
    lon=-104.67,
    timezone="America/Denver",
    station_name="Denver International Airport (KDEN)",
)


class TestDecideV3:
    def test_missing_probability_skips(self):
        snap = _make_v3_snap(None)
        market = _make_market()
        with patch("app.services.v3_paper_trading.get_station", return_value=_GOOD_STATION):
            d = _decide_v3(snap, market, _DEFAULT_SETTINGS, datetime.now(timezone.utc))
        assert d["action"] == "SKIP"

    def test_unverified_station_is_research_only(self):
        """
        Hardening change: unverified NWS cities no longer cause a SKIP.
        They produce a direction decision but with eligibility_status='RESEARCH_ONLY'.
        """
        snap = _make_v3_snap(0.70)
        market = _make_market()
        bad_station = MagicMock(
            verified=False, nws_settlement=True,
            lat=33.6, lon=-84.4,
            timezone="America/New_York",
            station_name="Atlanta Hartsfield-Jackson (KATL)",
        )
        with patch("app.services.v3_paper_trading.get_station", return_value=bad_station):
            d = _decide_v3(snap, market, _DEFAULT_SETTINGS, datetime.now(timezone.utc))
        assert d["action"] != "SKIP"
        assert d.get("eligibility_status") == "RESEARCH_ONLY"
        assert d.get("eligibility_reason") == "settlement_station_unverified"

    def test_insufficient_edge_skips(self):
        # ec_prob=0.55, yes_ask=0.50 → yes_edge=0.05 < 0.10
        snap = _make_v3_snap(0.55)
        market = _make_market(yes_bid=0.48, yes_ask=0.50)
        with patch("app.services.v3_paper_trading.get_station", return_value=_GOOD_STATION):
            d = _decide_v3(snap, market, _DEFAULT_SETTINGS, datetime.now(timezone.utc))
        assert d["action"] == "SKIP"
        assert "edge" in (d["skip_reason"] or "").lower()

    def test_sufficient_yes_edge_trades_yes(self):
        # ec_prob=0.75, yes_ask=0.50 → yes_edge=0.25 ≥ 0.10
        snap = _make_v3_snap(0.75)
        market = _make_market(yes_bid=0.48, yes_ask=0.50)
        with patch("app.services.v3_paper_trading.get_station", return_value=_GOOD_STATION):
            d = _decide_v3(snap, market, _DEFAULT_SETTINGS, datetime.now(timezone.utc))
        assert d["action"] == "YES"
        assert d["direction"] == "YES"
        assert d["edge_pct_points"] > 0

    def test_sufficient_no_edge_trades_no(self):
        # ec_prob=0.25, yes_bid=0.40 → no_edge=0.75-0.60=0.15 ≥ 0.10
        snap = _make_v3_snap(0.25)
        market = _make_market(yes_bid=0.40, yes_ask=0.42)
        with patch("app.services.v3_paper_trading.get_station", return_value=_GOOD_STATION):
            d = _decide_v3(snap, market, _DEFAULT_SETTINGS, datetime.now(timezone.utc))
        assert d["action"] == "NO"
        assert d["direction"] == "NO"

    def test_inactive_market_skips(self):
        snap = _make_v3_snap(0.75)
        market = _make_market(status="closed")
        with patch("app.services.v3_paper_trading.get_station", return_value=_GOOD_STATION):
            d = _decide_v3(snap, market, _DEFAULT_SETTINGS, datetime.now(timezone.utc))
        assert d["action"] == "SKIP"
        assert "active" in (d["skip_reason"] or "").lower()

    def test_stale_quote_is_research_only(self):
        """
        Hardening change: stale quotes no longer cause a SKIP.
        They produce a direction decision but with eligibility_status='RESEARCH_ONLY'.
        """
        from datetime import timedelta
        snap = _make_v3_snap(0.75)
        market = _make_market()
        market.collection_timestamp = datetime.now(timezone.utc) - timedelta(hours=6)
        with patch("app.services.v3_paper_trading.get_station", return_value=_GOOD_STATION):
            d = _decide_v3(snap, market, _DEFAULT_SETTINGS, datetime.now(timezone.utc))
        assert d["action"] != "SKIP"
        assert d.get("eligibility_status") == "RESEARCH_ONLY"
        assert d.get("eligibility_reason") == "missing_or_stale_executable_quote"

    def test_higher_yes_edge_beats_no(self):
        # yes_edge=0.30, no_edge=0.15 → direction=YES
        snap = _make_v3_snap(0.80)
        market = _make_market(yes_bid=0.45, yes_ask=0.50)
        with patch("app.services.v3_paper_trading.get_station", return_value=_GOOD_STATION):
            d = _decide_v3(snap, market, _DEFAULT_SETTINGS, datetime.now(timezone.utc))
        assert d["action"] == "YES"

    def test_decision_has_explanation(self):
        snap = _make_v3_snap(0.75)
        market = _make_market(yes_bid=0.48, yes_ask=0.50)
        with patch("app.services.v3_paper_trading.get_station", return_value=_GOOD_STATION):
            d = _decide_v3(snap, market, _DEFAULT_SETTINGS, datetime.now(timezone.utc))
        assert "decision_explanation" in d
        assert d["decision_explanation"]


# ---------------------------------------------------------------------------
# run_paper_trading_v3 smoke tests
# ---------------------------------------------------------------------------

def _make_session_cm(mock_session):
    """Return an async context manager that yields mock_session."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestRunPaperTradingV3:
    @pytest.mark.asyncio
    async def test_disabled_flag_returns_early(self):
        from app.services.v3_paper_trading import run_paper_trading_v3

        mock_session = AsyncMock()
        with (
            patch(
                "app.services.v3_paper_trading.AsyncSessionLocal",
                return_value=_make_session_cm(mock_session),
            ),
            patch(
                "app.services.v3_paper_trading.get_v3_flag",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            result = await run_paper_trading_v3()

        assert result["status"] == "disabled"
        assert result["created"] == 0

    @pytest.mark.asyncio
    async def test_no_v3_snapshots_returns_zero(self):
        from app.services.v3_paper_trading import run_paper_trading_v3

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=_empty_exec)

        with (
            patch(
                "app.services.v3_paper_trading.AsyncSessionLocal",
                return_value=_make_session_cm(mock_session),
            ),
            patch(
                "app.services.v3_paper_trading.get_v3_flag",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.services.v3_paper_trading.get_v3_pt_settings",
                new_callable=AsyncMock,
                return_value=_DEFAULT_SETTINGS,
            ),
        ):
            result = await run_paper_trading_v3()

        assert result["status"] == "ok"
        assert result["created"] == 0


def _empty_exec(_query):
    r = MagicMock()
    r.scalars.return_value.all.return_value = []
    return r


# ---------------------------------------------------------------------------
# Strategy version constant
# ---------------------------------------------------------------------------

def test_strategy_version():
    assert STRATEGY_VERSION == "v3.0"
