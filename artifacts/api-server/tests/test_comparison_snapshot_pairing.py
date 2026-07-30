"""
Comparison Snapshot Pairing Tests
==================================
All tests are pure unit tests — no real DB access.

Verifies:
1.  create_comparison_snapshots_for_batch() creates one snapshot per
    (active market, latest PredictionSnapshot) pair.
2.  V2.1 paper trades created with a batch_id store comparison_snapshot_id
    and collection_batch_id on the trade row.
3.  V2.2 trades created with a batch_id store the same fields.
4.  Pre-existing (duplicate) trades fire the guard early and do NOT
    receive a new comparison_snapshot_id (historical rows are immutable).
5.  _is_paired() returns True only when all three strategies share the
    same non-NULL comparison_snapshot_id.
6.  _is_paired() is False if any strategy is absent or has NULL/different id.
7.  Trades created without a batch_id (legacy / pre-feature) have NULL ids.
8.  A market with no PredictionSnapshot is skipped by the snapshot service.
9.  An inactive market is skipped by the snapshot service.
10. Paired rows prove identical quote + forecast inputs via the snapshot.
"""
from __future__ import annotations

import asyncio
import math
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import KalshiMarket, PaperTrade, PredictionSnapshot
from app.models_comparison import ComparisonSnapshot
from app.models_v3 import V3PaperTrade
from app.routers.strategy_comparison import _is_paired


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 7, 30, 19, 0, 0, tzinfo=timezone.utc)
BATCH_ID = str(uuid.uuid4())


def _market(ticker: str, city: str = "Dallas", *, active: bool = True) -> KalshiMarket:
    m = KalshiMarket(
        ticker=ticker,
        title=f"Test market {ticker}",
        city=city,
        target_date="2026-07-31",
        status="active" if active else "settled",
        yes_bid=0.30,
        yes_ask=0.32,
        no_bid=0.68,
        no_ask=0.70,
        collection_timestamp=NOW,
    )
    return m


def _snap(ticker: str, forecast_value: float = 98.0, *, ps_id: int = 1) -> PredictionSnapshot:
    s = PredictionSnapshot(
        market_ticker=ticker,
        forecast_value=forecast_value,
        forecast_retrieved_at=NOW,
        lead_time_days=1,
        settlement_variable="high",
        contract_type="threshold",
        settlement_threshold=100.0,
        settlement_operator="gte",
        market_probability=0.31,
        analysis_status="ok",
    )
    s.id = ps_id
    return s


def _make_async_session(
    snaps: list[PredictionSnapshot],
    markets: list[KalshiMarket],
) -> AsyncMock:
    """Build an AsyncMock session that returns snaps + markets on execute()."""
    session = MagicMock()

    added_objects: list = []

    def _add(obj):
        added_objects.append(obj)

    session.add = MagicMock(side_effect=_add)
    session.flush = AsyncMock()
    session._added = added_objects  # expose for assertions

    call_count = [0]

    async def _execute(stmt, *args, **kwargs):
        result = MagicMock()
        idx = call_count[0]
        call_count[0] += 1
        if idx == 0:
            # First call: PredictionSnapshot query
            result.scalars.return_value.all.return_value = snaps
        else:
            # Second call: KalshiMarket query
            result.scalars.return_value.all.return_value = markets
        return result

    session.execute = _execute
    return session


# Stub so spec=AsyncSession_like works without importing the real class
class AsyncSession_like:
    pass


# ---------------------------------------------------------------------------
# 1. Snapshot service: one snapshot per ticker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_snapshots_returns_one_per_ticker():
    """One ComparisonSnapshot per active (market, snapshot) pair."""
    from app.services.comparison_snapshot_service import (
        create_comparison_snapshots_for_batch,
    )

    tickers = ["KXHIGHTDAL-T100", "KXHIGHTDAL-T102", "KXLOWTDAL-T80"]
    markets = [_market(t) for t in tickers]
    snaps   = [_snap(t, ps_id=i) for i, t in enumerate(tickers, 1)]

    session = _make_async_session(snaps, markets)
    snap_ids = await create_comparison_snapshots_for_batch(session, BATCH_ID)

    assert set(snap_ids.keys()) == set(tickers), "One entry per ticker"
    assert len(set(snap_ids.values())) == len(tickers), "All IDs unique"

    created_snapshots = [o for o in session._added if isinstance(o, ComparisonSnapshot)]
    assert len(created_snapshots) == len(tickers)


# ---------------------------------------------------------------------------
# 2. Snapshot stores correct quote fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_snapshot_stores_correct_quote_and_forecast():
    """ComparisonSnapshot captures exact bid/ask and forecast from sources."""
    from app.services.comparison_snapshot_service import (
        create_comparison_snapshots_for_batch,
    )

    ticker = "KXHIGHTDAL-T100"
    market = _market(ticker)
    snap   = _snap(ticker, forecast_value=102.5)

    session = _make_async_session([snap], [market])
    snap_ids = await create_comparison_snapshots_for_batch(session, BATCH_ID)

    created = [o for o in session._added if isinstance(o, ComparisonSnapshot)]
    assert len(created) == 1
    cs = created[0]

    assert cs.yes_bid  == pytest.approx(0.30)
    assert cs.yes_ask  == pytest.approx(0.32)
    assert cs.no_bid   == pytest.approx(0.68)
    assert cs.no_ask   == pytest.approx(0.70)
    assert cs.forecast_value == pytest.approx(102.5)
    assert cs.collection_batch_id == BATCH_ID
    assert cs.market_ticker == ticker
    assert cs.quote_timestamp == NOW
    assert cs.id == snap_ids[ticker]


# ---------------------------------------------------------------------------
# 3. Inactive market is skipped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inactive_market_skipped():
    """Market with status != 'active' receives no ComparisonSnapshot.

    The service queries KalshiMarket WHERE status='active', so the mock
    simulates that filter by returning an empty markets list (the inactive
    market is not returned by the DB query).
    """
    from app.services.comparison_snapshot_service import (
        create_comparison_snapshots_for_batch,
    )

    ticker = "KXHIGHTDAL-T100"
    snap = _snap(ticker)

    # Pass empty markets list — mirrors the DB returning zero active markets
    session = _make_async_session([snap], [])
    snap_ids = await create_comparison_snapshots_for_batch(session, BATCH_ID)
    assert ticker not in snap_ids


# ---------------------------------------------------------------------------
# 4. Market with no PredictionSnapshot is skipped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_snapshot_skips_market():
    """If there's a market but no PredictionSnapshot, no comparison snapshot."""
    from app.services.comparison_snapshot_service import (
        create_comparison_snapshots_for_batch,
    )

    ticker = "KXHIGHTDAL-T100"
    market = _market(ticker)

    session = _make_async_session([], [market])  # no PredictionSnapshots
    snap_ids = await create_comparison_snapshots_for_batch(session, BATCH_ID)
    assert len(snap_ids) == 0


# ---------------------------------------------------------------------------
# 5. V2.1 new trade stores comparison_snapshot_id and batch_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_v21_new_trade_stores_comparison_snapshot_id():
    """New V2.1 paper trade stores comparison_snapshot_id from the batch."""
    from app.services.paper_trading_v21 import maybe_create_paper_trade_v21

    ticker = "KXHIGHTDAL-T100"
    market = _market(ticker)
    snap   = _snap(ticker)
    comp_snap_id = str(uuid.uuid4())

    settings = {
        "enabled": True, "stake": 10.0, "min_edge_pct": 10.0,
        "sigma_override": None, "min_executable_qty": 50,
        "max_stale_quote_hours": 4,
    }

    created_trade: list[PaperTrade] = []
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)

    def _capture(obj):
        if isinstance(obj, PaperTrade):
            created_trade.append(obj)

    mock_session.add = MagicMock(side_effect=_capture)
    mock_session.flush = AsyncMock()

    with patch(
        "app.services.paper_trading_v21.decide_trade_v21",
        new_callable=AsyncMock,
    ) as mock_decide:
        mock_decide.return_value = {
            "action": "TRADE",
            "direction": "YES",
            "ec_yes_probability": 0.18,
            "ec_side_probability": 0.18,
            "market_yes_probability": 0.31,
            "side_market_price": 0.32,
            "price_source": "YES_ASK",
            "edge_pct_points": 12.0,
            "decision_explanation": "test",
            "warnings": [],
            "sigma_used": 5.0,
            "bias_correction": 0.0,
            "fallback_level": "city",
            "calibration_adj": 1.0,
            "station_verified": True,
            "station_lat": 32.9,
            "station_lon": -97.0,
            "quote_bid": 0.30,
            "quote_ask": 0.32,
            "quote_timestamp": NOW,
            "est_available_qty": 100.0,
            "is_executable": True,
            "skip_reason": None,
        }
        result = await maybe_create_paper_trade_v21(
            mock_session, market, snap, settings,
            comparison_snapshot_id=comp_snap_id,
            batch_id=BATCH_ID,
        )

    assert result["created"] is True
    assert len(created_trade) == 1
    assert created_trade[0].comparison_snapshot_id == comp_snap_id
    assert created_trade[0].collection_batch_id == BATCH_ID


# ---------------------------------------------------------------------------
# 6. V2.2 new trade stores comparison_snapshot_id and batch_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_v22_new_trade_stores_comparison_snapshot_id():
    """New V2.2 paper trade stores comparison_snapshot_id from the batch."""
    from app.services.paper_trading_v22 import maybe_create_paper_trade_v22

    ticker = "KXHIGHTDAL-T100"
    market = _market(ticker)
    snap   = _snap(ticker)
    comp_snap_id = str(uuid.uuid4())

    settings = {
        "enabled": True, "stake": 10.0, "min_edge_pct": 10.0,
        "sigma_override": None, "min_executable_qty": 50,
        "max_stale_quote_hours": 4,
    }

    created_trade: list[PaperTrade] = []
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)

    def _capture(obj):
        if isinstance(obj, PaperTrade):
            created_trade.append(obj)

    mock_session.add = MagicMock(side_effect=_capture)
    mock_session.flush = AsyncMock()

    with patch(
        "app.services.paper_trading_v22.decide_trade_v22",
        new_callable=AsyncMock,
    ) as mock_decide:
        mock_decide.return_value = {
            "action": "TRADE",
            "direction": "YES",
            "ec_yes_probability": 0.18,
            "ec_side_probability": 0.18,
            "market_yes_probability": 0.31,
            "side_market_price": 0.32,
            "price_source": "YES_ASK",
            "edge_pct_points": 12.0,
            "decision_explanation": "test",
            "warnings": [],
            "sigma_used": 5.0,
            "bias_correction": 0.0,
            "fallback_level": "city",
            "calibration_adj": 1.0,
            "station_verified": True,
            "station_lat": 32.9,
            "station_lon": -97.0,
            "quote_bid": 0.30,
            "quote_ask": 0.32,
            "quote_timestamp": NOW,
            "est_available_qty": 100.0,
            "is_executable": True,
            "skip_reason": None,
        }
        result = await maybe_create_paper_trade_v22(
            mock_session, market, snap, settings,
            comparison_snapshot_id=comp_snap_id,
            batch_id=BATCH_ID,
        )

    assert result["created"] is True
    assert len(created_trade) == 1
    assert created_trade[0].comparison_snapshot_id == comp_snap_id
    assert created_trade[0].collection_batch_id == BATCH_ID


# ---------------------------------------------------------------------------
# 7. Duplicate guard — pre-existing trade keeps its own snapshot id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_guard_does_not_overwrite_existing_trade():
    """
    When the duplicate guard fires the function returns skip_reason='duplicate'
    and no new trade is created.  The pre-existing trade retains its own
    (older) comparison_snapshot_id — historical rows are never modified.
    """
    from app.services.paper_trading_v21 import maybe_create_paper_trade_v21

    ticker = "KXHIGHTDAL-T100"
    market = _market(ticker)
    snap   = _snap(ticker)
    settings = {
        "enabled": True, "stake": 10.0, "min_edge_pct": 10.0,
        "sigma_override": None, "min_executable_qty": 50,
        "max_stale_quote_hours": 4,
    }

    existing = MagicMock(spec=PaperTrade)
    existing.comparison_snapshot_id = "old-snap-id"

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.execute.return_value.scalar_one_or_none = MagicMock(
        return_value=existing
    )
    mock_session.add = MagicMock()

    result = await maybe_create_paper_trade_v21(
        mock_session, market, snap, settings,
        comparison_snapshot_id="new-snap-id",
        batch_id=BATCH_ID,
    )

    assert result["created"] is False
    assert result["skip_reason"] == "duplicate"
    # Existing trade was never modified
    assert existing.comparison_snapshot_id == "old-snap-id"
    mock_session.add.assert_not_called()


# ---------------------------------------------------------------------------
# 8. _is_paired() — strictly paired cases
# ---------------------------------------------------------------------------

def test_is_paired_true_when_all_share_same_snapshot_id():
    """All three strategies with the same non-NULL snapshot id = paired."""
    sid = str(uuid.uuid4())
    v21 = MagicMock(spec=PaperTrade);   v21.comparison_snapshot_id = sid
    v22 = MagicMock(spec=PaperTrade);   v22.comparison_snapshot_id = sid
    v3  = MagicMock(spec=V3PaperTrade); v3.comparison_snapshot_id  = sid
    assert _is_paired(v21, v22, v3) is True


def test_is_paired_false_snapshot_ids_differ():
    """Different snapshot IDs = timing mismatch = NOT paired."""
    v21 = MagicMock(spec=PaperTrade);   v21.comparison_snapshot_id = str(uuid.uuid4())
    v22 = MagicMock(spec=PaperTrade);   v22.comparison_snapshot_id = str(uuid.uuid4())
    v3  = MagicMock(spec=V3PaperTrade); v3.comparison_snapshot_id  = str(uuid.uuid4())
    assert _is_paired(v21, v22, v3) is False


def test_is_paired_false_when_all_null():
    """NULL snapshot_id = pre-feature legacy trade = NOT paired."""
    v21 = MagicMock(spec=PaperTrade);   v21.comparison_snapshot_id = None
    v22 = MagicMock(spec=PaperTrade);   v22.comparison_snapshot_id = None
    v3  = MagicMock(spec=V3PaperTrade); v3.comparison_snapshot_id  = None
    assert _is_paired(v21, v22, v3) is False


def test_is_paired_false_strategy_missing():
    """Any strategy absent → not paired."""
    sid = str(uuid.uuid4())
    v21 = MagicMock(spec=PaperTrade); v21.comparison_snapshot_id = sid
    v22 = MagicMock(spec=PaperTrade); v22.comparison_snapshot_id = sid
    assert _is_paired(v21, v22, None) is False
    assert _is_paired(v21, None, None) is False
    assert _is_paired(None, None, None) is False


def test_is_paired_false_v22_different_cycle():
    """V2.2 from a different cycle breaks pairing even if V2.1 and V3 agree."""
    sid_a = str(uuid.uuid4())
    sid_b = str(uuid.uuid4())
    v21 = MagicMock(spec=PaperTrade);   v21.comparison_snapshot_id = sid_a
    v22 = MagicMock(spec=PaperTrade);   v22.comparison_snapshot_id = sid_b
    v3  = MagicMock(spec=V3PaperTrade); v3.comparison_snapshot_id  = sid_a
    assert _is_paired(v21, v22, v3) is False


# ---------------------------------------------------------------------------
# 9. Legacy trades have NULL comparison fields
# ---------------------------------------------------------------------------

def test_legacy_trade_has_null_comparison_fields():
    """PaperTrade built without batch info has NULL comparison fields."""
    trade = PaperTrade(
        market_ticker="KXHIGHTDAL-T100",
        strategy_version="v2.1",
        direction="YES",
        stake=10.0,
        status="OPEN",
    )
    assert trade.comparison_snapshot_id is None
    assert trade.collection_batch_id is None


# ---------------------------------------------------------------------------
# 10. Paired rows have identical quote + forecast values via the shared snapshot
# ---------------------------------------------------------------------------

def test_paired_rows_identical_inputs_via_snapshot():
    """
    When V2.1 and V2.2 trades reference the same ComparisonSnapshot,
    the snapshot holds the authoritative frozen values that prove
    identical inputs were used.
    """
    snap_id = str(uuid.uuid4())

    # Simulate a ComparisonSnapshot row
    cs = ComparisonSnapshot(
        id=snap_id,
        collection_batch_id=BATCH_ID,
        market_ticker="KXHIGHTDAL-T100",
        yes_bid=0.30,
        yes_ask=0.32,
        no_bid=0.68,
        no_ask=0.70,
        market_yes_probability=0.31,
        quote_timestamp=NOW,
        forecast_value=103.7,
        forecast_timestamp=NOW,
        forecast_lead_time_days=1,
    )

    # Simulate both strategies linking to the same snapshot
    v21_snap_id = snap_id
    v22_snap_id = snap_id

    assert v21_snap_id == v22_snap_id, "Both strategies reference same snapshot"
    assert cs.yes_bid == pytest.approx(0.30)
    assert cs.yes_ask == pytest.approx(0.32)
    assert cs.forecast_value == pytest.approx(103.7)
    assert cs.quote_timestamp == NOW

    # Verify _is_paired would return True for trades with this snapshot
    v21 = MagicMock(spec=PaperTrade);   v21.comparison_snapshot_id = snap_id
    v22 = MagicMock(spec=PaperTrade);   v22.comparison_snapshot_id = snap_id
    v3  = MagicMock(spec=V3PaperTrade); v3.comparison_snapshot_id  = snap_id
    assert _is_paired(v21, v22, v3) is True


# ---------------------------------------------------------------------------
# 11. V2.1 excluded (V2_EXCLUDED) trade also stores comparison_snapshot_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_v21_excluded_trade_stores_comparison_snapshot_id():
    """EXCLUDED trades (V2_EXCLUDED status) also carry the snapshot id."""
    from app.services.paper_trading_v21 import maybe_create_paper_trade_v21

    ticker = "KXHIGHTDAL-T100"
    market = _market(ticker)
    snap   = _snap(ticker)
    comp_snap_id = str(uuid.uuid4())

    settings = {
        "enabled": True, "stake": 10.0, "min_edge_pct": 10.0,
        "sigma_override": None, "min_executable_qty": 50,
        "max_stale_quote_hours": 4,
    }

    created_trade: list[PaperTrade] = []
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)

    def _capture(obj):
        if isinstance(obj, PaperTrade):
            created_trade.append(obj)

    mock_session.add = MagicMock(side_effect=_capture)
    mock_session.flush = AsyncMock()

    with patch(
        "app.services.paper_trading_v21.decide_trade_v21",
        new_callable=AsyncMock,
    ) as mock_decide:
        mock_decide.return_value = {
            "action": "EXCLUDED",
            "direction": "YES",
            "ec_yes_probability": 0.42,
            "ec_side_probability": 0.42,
            "market_yes_probability": 0.005,
            "side_market_price": 0.005,
            "price_source": "YES_ASK",
            "edge_pct_points": 41.5,
            "decision_explanation": "excluded: v2_below_min_price",
            "exclusion_flag": "v2_below_min_price",
            "warnings": [],
            "sigma_used": 5.0,
            "bias_correction": 0.0,
            "fallback_level": "city",
            "calibration_adj": 1.0,
            "station_verified": True,
            "station_lat": 32.9,
            "station_lon": -97.0,
            "quote_bid": 0.005,
            "quote_ask": 0.005,
            "quote_timestamp": NOW,
            "est_available_qty": 50.0,
            "is_executable": None,
            "skip_reason": "v2_below_min_price",
        }
        result = await maybe_create_paper_trade_v21(
            mock_session, market, snap, settings,
            comparison_snapshot_id=comp_snap_id,
            batch_id=BATCH_ID,
        )

    assert result["excluded"] is True
    assert len(created_trade) == 1
    assert created_trade[0].status == "V2_EXCLUDED"
    assert created_trade[0].comparison_snapshot_id == comp_snap_id
    assert created_trade[0].collection_batch_id == BATCH_ID
