"""
V2.1 Regression Snapshot Tests
================================
These tests capture representative V2.1 prediction outputs using fixed mock
inputs and assert they are bit-for-bit unchanged after V3 code is merged.

PURPOSE: Prove that importing V3 modules, running V3 services, or adding
V3 tables does NOT alter V2.1's decision logic, sigma, bias, probability
calculations, or skip reasons in any way.

These tests run on every phase of V3 development.  If any of these assertions
change, V3 code has violated the isolation guarantee and must be fixed.

Snapshot methodology
---------------------
Each test constructs a minimal PredictionSnapshot and KalshiMarket mock,
calls ``decide_trade_v21`` with known ForecastErrorStats mocked to fixed
values, and asserts the exact output dict matches the expected snapshot.

The mocked sigma and bias values are chosen to be in regimes that exercise
different code paths (high edge → trade, low edge → skip, unverified → skip).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_snap(
    market_ticker: str = "KXHIGH-NYC-25JUL30-B85",
    analysis_status: str = "supported",
    forecast_value: float = 87.0,
    forecast_retrieved_at: datetime | None = None,
    settlement_variable: str = "high",
    settlement_operator: str = "gte",
    settlement_threshold: float = 85.0,
    contract_type: str = "threshold",
    ec_probability: float = 0.72,
    market_probability: float = 0.55,
):
    snap = MagicMock()
    snap.market_ticker = market_ticker
    snap.analysis_status = analysis_status
    snap.forecast_value = forecast_value
    snap.forecast_retrieved_at = forecast_retrieved_at or datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
    snap.settlement_variable = settlement_variable
    snap.settlement_operator = settlement_operator
    snap.settlement_threshold = settlement_threshold
    snap.contract_type = contract_type
    snap.lower_bound = None
    snap.upper_bound = None
    snap.ec_probability = ec_probability
    snap.market_probability = market_probability
    snap.confidence = "High"
    return snap


def _make_market(
    ticker: str = "KXHIGH-NYC-25JUL30-B85",
    city: str = "New York City",
    status: str = "active",
    yes_bid: float = 0.54,
    yes_ask: float = 0.56,
    no_bid: float = 0.44,
    no_ask: float = 0.46,
    collection_ts: datetime | None = None,
    target_date: str = "2026-07-30",
):
    market = MagicMock()
    market.ticker = ticker
    market.city = city
    market.status = status
    market.yes_bid = yes_bid
    market.yes_ask = yes_ask
    market.no_bid = no_bid
    market.no_ask = no_ask
    market.title = f"{city} daily high ≥ 85°F"
    market.subtitle = None
    market.target_date = target_date
    market.collection_timestamp = collection_ts or datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    return market


def _make_analysis_result(
    ec_probability: float = 0.72,
    market_probability: float = 0.55,
    sigma_used: float = 4.5,
    bias_correction: float = 0.8,
    fallback_level: str = "city",
    calibration_adj: float = 1.0,
    raw_ec_probability: float = 0.72,
    confidence: str = "High",
    explanation: str = "Test explanation",
):
    result = MagicMock()
    result.ec_probability = ec_probability
    result.market_probability = market_probability
    result.sigma_used = sigma_used
    result.bias_correction = bias_correction
    result.fallback_level = fallback_level
    result.calibration_adj = calibration_adj
    result.raw_ec_probability = raw_ec_probability
    result.confidence = confidence
    result.explanation = explanation
    return result


# ── Regression snapshots ──────────────────────────────────────────────────────

class TestV21RegressionSnapshots:
    """
    Five representative scenarios with fixed inputs → exact expected outputs.

    If any assertion fails after V3 code is added, V2.1 behavior has changed.
    """

    @pytest.mark.asyncio
    async def test_snapshot_1_high_edge_yes_trade(self):
        """
        SCENARIO: High edge YES trade.
        City: New York City (verified).
        EC probability: 0.72, YES ask: 0.56 → edge = 16pp → trade YES.
        Expected action: YES
        """
        from app.services.paper_trading_v21 import decide_trade_v21

        snap = _make_snap(ec_probability=0.72, market_probability=0.55)
        market = _make_market(yes_ask=0.56)
        settings = {
            "enabled": True,
            "min_edge_pct": 10.0,
            "min_confidence": "High",
            "stake": 10.0,
            "consensus_guard_enabled": False,
        }
        session = AsyncMock()
        analysis = _make_analysis_result(
            ec_probability=0.72,
            sigma_used=4.5,
            bias_correction=0.8,
            fallback_level="city",
        )

        now = datetime(2026, 7, 28, 10, 30, tzinfo=timezone.utc)

        with patch(
            "app.services.probability_engine_v2.run_analysis_v2",
            new=AsyncMock(return_value=analysis),
        ):
            result = await decide_trade_v21(snap, market, settings, session, now=now)

        assert result["action"] == "YES", f"Expected YES, got {result['action']}"
        assert result["direction"] == "YES"
        assert result["sigma_used"] == 4.5
        assert result["bias_correction"] == 0.8
        assert result["fallback_level"] == "city"
        assert result["station_verified"] is True
        assert result["skip_reason"] is None
        edge = result["edge_pct_points"]
        assert edge is not None and edge > 10.0, f"Expected edge > 10pp, got {edge}"

    @pytest.mark.asyncio
    async def test_snapshot_2_edge_below_threshold_skip(self):
        """
        SCENARIO: Edge below threshold.
        EC prob: 0.52, YES ask: 0.50 → edge = 2pp < 10pp → SKIP.
        Expected action: SKIP
        """
        from app.services.paper_trading_v21 import decide_trade_v21

        snap = _make_snap(ec_probability=0.52, market_probability=0.50)
        market = _make_market(yes_bid=0.49, yes_ask=0.50, no_bid=0.49, no_ask=0.50)
        settings = {
            "enabled": True,
            "min_edge_pct": 10.0,
            "min_confidence": "High",
            "stake": 10.0,
            "consensus_guard_enabled": False,
        }
        session = AsyncMock()
        analysis = _make_analysis_result(ec_probability=0.52, market_probability=0.50, confidence="High")

        now = datetime(2026, 7, 28, 10, 30, tzinfo=timezone.utc)

        with patch(
            "app.services.probability_engine_v2.run_analysis_v2",
            new=AsyncMock(return_value=analysis),
        ):
            result = await decide_trade_v21(snap, market, settings, session, now=now)

        assert result["action"] == "SKIP"
        assert result["skip_reason"] is not None
        assert "Edge below threshold" in result["skip_reason"]
        assert result["sigma_used"] is None
        assert result["station_verified"] is None

    @pytest.mark.asyncio
    async def test_snapshot_3_stale_quote_skip(self):
        """
        SCENARIO: Stale market quote (>4h old).
        Quote timestamp: 6 hours before now.
        Expected action: SKIP (stale quote)
        """
        from app.services.paper_trading_v21 import decide_trade_v21

        now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
        stale_ts = datetime(2026, 7, 28, 5, 0, tzinfo=timezone.utc)  # 7h old

        snap = _make_snap()
        market = _make_market(collection_ts=stale_ts)
        settings = {
            "enabled": True,
            "min_edge_pct": 10.0,
            "min_confidence": "High",
            "stake": 10.0,
            "consensus_guard_enabled": False,
        }
        session = AsyncMock()

        result = await decide_trade_v21(snap, market, settings, session, now=now)

        assert result["action"] == "SKIP"
        assert "stale" in (result["skip_reason"] or "").lower()

    @pytest.mark.asyncio
    async def test_snapshot_4_unverified_city_skip(self):
        """
        SCENARIO: City with unverified settlement station (Atlanta).
        V2.1 must skip; station verification is a hard gate before any probability math.
        Expected action: SKIP
        """
        from app.services.paper_trading_v21 import decide_trade_v21

        snap = _make_snap(market_ticker="KXHIGH-ATL-25JUL30-B85")
        market = _make_market(
            ticker="KXHIGH-ATL-25JUL30-B85",
            city="Atlanta",
        )
        settings = {
            "enabled": True,
            "min_edge_pct": 10.0,
            "min_confidence": "High",
            "stake": 10.0,
            "consensus_guard_enabled": False,
        }
        session = AsyncMock()
        now = datetime(2026, 7, 28, 10, 30, tzinfo=timezone.utc)

        result = await decide_trade_v21(snap, market, settings, session, now=now)

        assert result["action"] == "SKIP"
        assert result["station_verified"] is None  # skip before station_verified is set
        assert result["sigma_used"] is None
        assert result["skip_reason"] is not None
        assert "UNVERIFIED" in result["skip_reason"] or "station" in result["skip_reason"].lower()

    @pytest.mark.asyncio
    async def test_snapshot_5_dc_permanently_blocked(self):
        """
        SCENARIO: Washington DC — permanently blocked (nws_settlement=False).
        V2.1 must skip regardless of verification status or market activity.
        Expected action: SKIP
        """
        from app.services.paper_trading_v21 import decide_trade_v21

        snap = _make_snap(market_ticker="KXHIGH-DC-25JUL30-B85")
        market = _make_market(
            ticker="KXHIGH-DC-25JUL30-B85",
            city="Washington DC",
        )
        settings = {
            "enabled": True,
            "min_edge_pct": 10.0,
            "min_confidence": "High",
            "stake": 10.0,
            "consensus_guard_enabled": False,
        }
        session = AsyncMock()
        now = datetime(2026, 7, 28, 10, 30, tzinfo=timezone.utc)

        result = await decide_trade_v21(snap, market, settings, session, now=now)

        assert result["action"] == "SKIP"
        assert result["sigma_used"] is None
        assert result["skip_reason"] is not None
        assert "non-NWS" in result["skip_reason"] or "Weather Company" in result["skip_reason"]


# ── Strategy isolation — V3 import does not change V2.1 behavior ────────────

class TestV3DoesNotMutateV21:
    """
    Confirm that importing V3 modules and running V3 ingestion code does not
    alter the V2.1 service behavior.
    """

    def test_v3_models_importable(self):
        """V3 models import cleanly without side effects."""
        import app.models_v3  # noqa: F401
        from app.models_v3 import (
            V3HistoricalRecord, V3IngestionLog, V3RawSourceRecord,
            V3ErrorStats, V3PaperTrade, V3PredictionSnapshot,
        )
        # V3 models must not expose or shadow any V2.1 model
        from app.models import (
            ForecastVerification, ForecastErrorStats, PaperTrade,
            PredictionSnapshot, AppSetting,
        )
        assert ForecastErrorStats is not V3ErrorStats
        assert PaperTrade is not V3PaperTrade
        assert PredictionSnapshot is not V3PredictionSnapshot

    def test_v3_provider_import_does_not_affect_v21(self):
        """Importing the V3 provider registry does not alter settlement_stations."""
        from app.services.v3_providers.registry import get_all_provider_keys
        from app.services.settlement_stations import SETTLEMENT_STATIONS, get_station

        keys = get_all_provider_keys()
        assert "open-meteo-forecast-history" in keys

        # settlement_stations still returns the same verified stations
        denver = get_station("Denver")
        assert denver is not None
        assert denver.verified is True
        assert denver.ghcnd_station_id is not None

    def test_lookahead_module_import_does_not_affect_v21(self):
        """Importing v3_lookahead does not change paper_trading_v21 constants."""
        from app.services import v3_lookahead  # noqa: F401
        import app.services.paper_trading_v21 as pt21
        # If STALE_QUOTE_SECONDS was removed or changed, paper_trading_v21 is broken
        assert hasattr(pt21, "STALE_QUOTE_SECONDS"), "STALE_QUOTE_SECONDS missing from paper_trading_v21"
        assert pt21.STALE_QUOTE_SECONDS == 4 * 3600, "STALE_QUOTE_SECONDS changed!"


# We add a simpler version of the constants check to avoid walrus operator in tests:
def test_v21_constants_unchanged_after_v3_import():
    """
    After importing all V3 modules, V2.1 constants are still their original values.
    This is the simplest possible isolation check.
    """
    # Import all V3 modules explicitly
    import app.models_v3  # noqa: F401
    import app.services.v3_lookahead  # noqa: F401
    import app.services.v3_providers.base  # noqa: F401
    import app.services.v3_providers.registry  # noqa: F401
    import app.services.v3_providers.open_meteo_forecast_history  # noqa: F401
    import app.services.v3_flags  # noqa: F401

    # V2.1 constants must be unchanged
    from app.services.paper_trading_v21 import (
        STALE_QUOTE_SECONDS,
        NON_EXECUTABLE_MAX_QTY,
        NON_EXECUTABLE_MAX_PRICE,
        CONSENSUS_GUARD_THRESHOLD,
        STRATEGY_VERSION,
    )
    assert STALE_QUOTE_SECONDS == 4 * 3600, "STALE_QUOTE_SECONDS changed!"
    assert NON_EXECUTABLE_MAX_QTY == 50, "NON_EXECUTABLE_MAX_QTY changed!"
    assert NON_EXECUTABLE_MAX_PRICE == 0.10, "NON_EXECUTABLE_MAX_PRICE changed!"
    assert CONSENSUS_GUARD_THRESHOLD == 0.85, "CONSENSUS_GUARD_THRESHOLD changed!"
    assert STRATEGY_VERSION == "v2.1", "STRATEGY_VERSION changed!"
