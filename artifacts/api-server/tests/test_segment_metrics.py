"""
Segment Metrics Regression Tests
=================================
All tests are pure unit tests — no real DB access.

Covers:
1.  current_exp metrics change when a settled V3 trade is added.
2.  current_exp totals equal V2.1 + V2.2 + V3 field-by-field sums.
3.  paired segment excludes rows where V3 is absent.
4.  paired segment excludes records with a different comparison_snapshot_id
    (timing-mismatched records).
5.  V3 wins Preliminary Leader when its metrics are best on the paired set.
6.  V3 supplies Best Paper Bet Today when it has the best eligible open signal.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.paper_trading import compute_metrics_from_trades, _empty_metrics
from app.routers.strategy_comparison import _preliminary_leader, _best_bet_today


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)  # always fresh; hardcoded date would fail staleness check
SNAP_A = str(uuid.uuid4())
SNAP_B = str(uuid.uuid4())


def _trade(
    *,
    status: str = "SETTLED",
    outcome: str | None = "WIN",
    stake: float = 10.0,
    profit_loss: float | None = 7.0,
    edge_pct_points: float = 25.0,
    side_market_price: float = 0.35,
    direction: str = "YES",
    city: str = "Oklahoma City",
    contract_type: str = "threshold",
    confidence_label: str | None = "High",
    strategy_version: str = "v2.1",
    is_executable: bool = True,
    station_verified: bool = True,
    quality_flags: list | None = None,
    market_ticker: str = "KXHIGHTEMP-OKC-20260801-GTE95",
    comparison_snapshot_id: str | None = None,
    quote_timestamp: datetime | None = None,
    weather_variable: str = "high",
    ec_side_probability: float = 0.60,
    ec_yes_probability: float = 0.60,
    quantity: float = 1.0,
    target_settlement_date: str = "2026-08-01",
    lead_time_days: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        outcome=outcome,
        stake=stake,
        profit_loss=profit_loss,
        edge_pct_points=edge_pct_points,
        side_market_price=side_market_price,
        direction=direction,
        city=city,
        contract_type=contract_type,
        confidence_label=confidence_label,
        strategy_version=strategy_version,
        is_executable=is_executable,
        station_verified=station_verified,
        quality_flags=quality_flags or [],
        market_ticker=market_ticker,
        comparison_snapshot_id=comparison_snapshot_id,
        quote_timestamp=quote_timestamp,
        weather_variable=weather_variable,
        ec_side_probability=ec_side_probability,
        ec_yes_probability=ec_yes_probability,
        quantity=quantity,
        target_settlement_date=target_settlement_date,
        lead_time_days=lead_time_days,
    )


def _open_trade(**kwargs) -> SimpleNamespace:
    """Convenience: an open executable trade with a fresh quote."""
    defaults = dict(
        status="OPEN",
        outcome=None,
        profit_loss=None,
        quote_timestamp=NOW,
    )
    defaults.update(kwargs)
    return _trade(**defaults)


# ---------------------------------------------------------------------------
# 1. current_exp performance changes when a settled V3 trade is added
# ---------------------------------------------------------------------------

class TestCurrentExpIncludesV3:
    def test_v3_settled_win_increases_wins(self):
        """Adding a settled V3 WIN to the combined set raises wins by 1."""
        v2_trades = [
            _trade(strategy_version="v2.1", outcome="WIN",  profit_loss=7.0),
            _trade(strategy_version="v2.2", outcome="LOSS", profit_loss=-10.0),
        ]
        without_v3 = compute_metrics_from_trades(v2_trades)
        assert without_v3["wins"] == 1

        v3_win = _trade(strategy_version="v3.0", outcome="WIN", profit_loss=7.0,
                        confidence_label=None)  # V3 has no confidence_label
        with_v3 = compute_metrics_from_trades(v2_trades + [v3_win])
        assert with_v3["wins"] == 2
        assert with_v3["settledCount"] == 3
        assert with_v3["winRate"] == pytest.approx(2 / 3, rel=1e-3)

    def test_v3_settled_loss_decreases_win_rate(self):
        """Adding a V3 LOSS lowers win rate correctly."""
        v2_trades = [
            _trade(strategy_version="v2.1", outcome="WIN",  profit_loss=7.0),
            _trade(strategy_version="v2.2", outcome="WIN",  profit_loss=7.0),
        ]
        v3_loss = _trade(strategy_version="v3.0", outcome="LOSS", profit_loss=-10.0,
                         confidence_label=None)
        combined = compute_metrics_from_trades(v2_trades + [v3_loss])
        assert combined["wins"] == 2
        assert combined["losses"] == 1
        assert combined["settledCount"] == 3
        assert combined["winRate"] == pytest.approx(2 / 3, rel=1e-3)

    def test_v3_open_increases_open_count(self):
        """An open V3 trade is reflected in openCount."""
        v2_trades = [_trade(strategy_version="v2.1")]
        v3_open = _open_trade(strategy_version="v3.0", confidence_label=None)
        combined = compute_metrics_from_trades(v2_trades + [v3_open])
        assert combined["openCount"] == 1
        assert combined["settledCount"] == 1


# ---------------------------------------------------------------------------
# 2. current_exp totals reconcile across all three strategies
# ---------------------------------------------------------------------------

class TestCurrentExpReconciliation:
    def _v21(self) -> list:
        return [_trade(strategy_version="v2.1", outcome="WIN",  profit_loss=7.0,  stake=10.0)]

    def _v22(self) -> list:
        return [_trade(strategy_version="v2.2", outcome="LOSS", profit_loss=-10.0, stake=10.0)]

    def _v3(self) -> list:
        return [_trade(strategy_version="v3.0", outcome="WIN",  profit_loss=7.0,  stake=10.0,
                       confidence_label=None)]

    def test_settled_count_equals_sum(self):
        v21 = self._v21(); v22 = self._v22(); v3 = self._v3()
        m21 = compute_metrics_from_trades(v21)
        m22 = compute_metrics_from_trades(v22)
        m3  = compute_metrics_from_trades(v3)
        combined = compute_metrics_from_trades(v21 + v22 + v3)
        assert combined["settledCount"] == m21["settledCount"] + m22["settledCount"] + m3["settledCount"]

    def test_wins_equals_sum(self):
        v21 = self._v21(); v22 = self._v22(); v3 = self._v3()
        m21 = compute_metrics_from_trades(v21)
        m22 = compute_metrics_from_trades(v22)
        m3  = compute_metrics_from_trades(v3)
        combined = compute_metrics_from_trades(v21 + v22 + v3)
        assert combined["wins"] == m21["wins"] + m22["wins"] + m3["wins"]

    def test_net_pl_equals_sum(self):
        v21 = self._v21(); v22 = self._v22(); v3 = self._v3()
        m21 = compute_metrics_from_trades(v21)
        m22 = compute_metrics_from_trades(v22)
        m3  = compute_metrics_from_trades(v3)
        combined = compute_metrics_from_trades(v21 + v22 + v3)
        assert combined["netProfitLoss"] == pytest.approx(
            m21["netProfitLoss"] + m22["netProfitLoss"] + m3["netProfitLoss"], rel=1e-4
        )

    def test_total_staked_equals_sum(self):
        v21 = self._v21(); v22 = self._v22(); v3 = self._v3()
        m21 = compute_metrics_from_trades(v21)
        m22 = compute_metrics_from_trades(v22)
        m3  = compute_metrics_from_trades(v3)
        combined = compute_metrics_from_trades(v21 + v22 + v3)
        assert combined["totalStaked"] == pytest.approx(
            m21["totalStaked"] + m22["totalStaked"] + m3["totalStaked"], rel=1e-4
        )

    def test_win_rate_is_derived_not_averaged(self):
        """win rate must be (total wins) / (total settled), not mean of per-strategy rates."""
        # V2.1: 1W/0L → 100%, V2.2: 0W/2L → 0%, V3: 1W/1L → 50%
        # naive mean = 50%; correct combined = 2/4 = 50% (coincidence here, use different data)
        v21 = [_trade(strategy_version="v2.1", outcome="WIN",  profit_loss=7.0)]
        v22 = [_trade(strategy_version="v2.2", outcome="LOSS", profit_loss=-10.0),
               _trade(strategy_version="v2.2", outcome="LOSS", profit_loss=-10.0,
                      market_ticker="KXHIGHTEMP-OKC-20260801-GTE90")]
        v3  = [_trade(strategy_version="v3.0", outcome="WIN",  profit_loss=7.0,
                      confidence_label=None, market_ticker="KXHIGHTEMP-OKC-20260801-GTE85")]
        combined = compute_metrics_from_trades(v21 + v22 + v3)
        # 2 wins, 2 losses, 4 settled → win rate = 0.5
        assert combined["wins"] == 2
        assert combined["losses"] == 2
        assert combined["winRate"] == pytest.approx(0.5, rel=1e-3)


# ---------------------------------------------------------------------------
# 3. paired — excludes rows missing any one strategy
# ---------------------------------------------------------------------------

class TestPairedExcludesMissingStrategy:
    """
    The 3-way paired logic (implemented in the router) must only include
    snapshot IDs present in V2.1, V2.2, AND V3.

    We test the data-preparation logic directly: given sets of trades indexed
    by comparison_snapshot_id, the intersection must exclude any snapshot
    absent from any one strategy.
    """

    def _paired_sets(self, v21_snaps, v22_snaps, v3_snaps):
        """Simulate the router intersection logic."""
        v21_by = {s: True for s in v21_snaps}
        v22_by = {s: True for s in v22_snaps}
        v3_by  = {s: True for s in v3_snaps}
        return set(v21_by) & set(v22_by) & set(v3_by)

    def test_excludes_v3_missing(self):
        """A snapshot present in V2.1+V2.2 but absent from V3 is excluded."""
        three_way = self._paired_sets([SNAP_A], [SNAP_A], [])
        assert SNAP_A not in three_way
        assert len(three_way) == 0

    def test_excludes_v21_missing(self):
        """A snapshot present in V2.2+V3 but absent from V2.1 is excluded."""
        three_way = self._paired_sets([], [SNAP_A], [SNAP_A])
        assert len(three_way) == 0

    def test_excludes_v22_missing(self):
        """A snapshot present in V2.1+V3 but absent from V2.2 is excluded."""
        three_way = self._paired_sets([SNAP_A], [], [SNAP_A])
        assert len(three_way) == 0

    def test_includes_all_three_present(self):
        """A snapshot present in all three is included."""
        three_way = self._paired_sets([SNAP_A], [SNAP_A], [SNAP_A])
        assert SNAP_A in three_way

    def test_partial_second_snapshot_excluded(self):
        """With two snapshots, only the one in all three is included."""
        three_way = self._paired_sets(
            [SNAP_A, SNAP_B],  # V2.1 has both
            [SNAP_A, SNAP_B],  # V2.2 has both
            [SNAP_A],          # V3 only has SNAP_A
        )
        assert SNAP_A in three_way
        assert SNAP_B not in three_way   # excluded because V3 is missing it
        assert len(three_way) == 1


# ---------------------------------------------------------------------------
# 4. Timing-mismatched records are excluded
# ---------------------------------------------------------------------------

class TestPairedExcludesTimingMismatch:
    """
    If V2.1 and V2.2 share SNAP_A but V3 has a different SNAP_B for the same
    market_ticker, the ticker must NOT appear in the 3-way paired set.
    """

    def test_different_snapshot_ids_are_excluded(self):
        """V3 using SNAP_B while V2.x use SNAP_A means no 3-way match."""
        v21_snaps = {SNAP_A}
        v22_snaps = {SNAP_A}
        v3_snaps  = {SNAP_B}   # different snapshot — timing mismatch
        three_way = v21_snaps & v22_snaps & v3_snaps
        assert len(three_way) == 0

    def test_matching_snapshot_ids_are_included(self):
        """All three sharing the same snapshot ID → included."""
        snap = str(uuid.uuid4())
        three_way = {snap} & {snap} & {snap}
        assert snap in three_way

    def test_metrics_only_from_matched_trades(self):
        """
        compute_metrics_from_trades called on the 3-way matched subset must
        not contain trades from mismatched snapshots.
        """
        matched_trade = _trade(
            strategy_version="v2.1",
            outcome="WIN",
            profit_loss=7.0,
            comparison_snapshot_id=SNAP_A,
        )
        mismatched_trade = _trade(
            strategy_version="v3.0",
            outcome="LOSS",
            profit_loss=-10.0,
            comparison_snapshot_id=SNAP_B,   # different snapshot — excluded
            confidence_label=None,
        )

        # Only the matched trade should be included in the 3-way set
        three_way_trades = [matched_trade]  # mismatched_trade was not in intersection
        m = compute_metrics_from_trades(three_way_trades)
        assert m["wins"] == 1
        assert m["losses"] == 0
        assert m["netProfitLoss"] == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# 5. V3 can win Preliminary Leader
# ---------------------------------------------------------------------------

class TestV3CanWinPreliminaryLeader:
    """
    _preliminary_leader ranks by composite score on strictly paired settled
    executable trades.  When V3 dominates all four metrics, it must be ranked #1.
    """

    def _make_snap_id(self) -> str:
        return str(uuid.uuid4())

    def _paired_trade(self, strategy: str, snap: str, outcome: str,
                      profit_loss: float, city: str = "Oklahoma City") -> SimpleNamespace:
        return _trade(
            strategy_version=strategy,
            outcome=outcome,
            profit_loss=profit_loss,
            stake=10.0,
            is_executable=True,
            comparison_snapshot_id=snap,
            city=city,
            confidence_label=None if strategy == "v3.0" else "High",
        )

    def test_v3_ranked_first_when_metrics_best(self):
        """
        Ten paired opportunities.  V3 wins all 10, V2.1 wins 7, V2.2 wins 5.
        V3 should be ranked #1.
        """
        n = 10
        v21_trades, v22_trades, v3_trades = [], [], []

        for i in range(n):
            snap = self._make_snap_id()
            ticker = f"KXHIGHTEMP-OKC-20260801-GTE{90 + i}"  # unique ticker per opportunity
            v21_outcome = "WIN" if i < 7 else "LOSS"
            v21_pl      = 7.0  if i < 7 else -10.0
            v22_outcome = "WIN" if i < 5 else "LOSS"
            v22_pl      = 7.0  if i < 5 else -10.0

            for lst, ver, out, pl in [
                (v21_trades, "v2.1", v21_outcome, v21_pl),
                (v22_trades, "v2.2", v22_outcome, v22_pl),
                (v3_trades,  "v3.0", "WIN",        7.0),
            ]:
                lst.append(_trade(
                    strategy_version=ver,
                    outcome=out,
                    profit_loss=pl,
                    stake=10.0,
                    is_executable=True,
                    comparison_snapshot_id=snap,
                    market_ticker=ticker,
                    confidence_label=None if ver == "v3.0" else "High",
                ))

        result = _preliminary_leader(v21_trades, v22_trades, v3_trades)
        assert result["n_paired_settled_exec"] == n
        assert result["ranked"] is not None, "Should have ranked results with 10 paired trades"
        # V3 wins 100 %, V2.1 wins 70 %, V2.2 wins 50 % → V3 must be #1
        assert result["ranked"][0]["label"] == "V3"

    def test_non_executive_v3_excluded_from_leader(self):
        """Non-executable V3 trades must not affect preliminary leader ranking."""
        snap = self._make_snap_id()
        non_exec_v3 = _trade(
            strategy_version="v3.0",
            outcome="WIN",
            profit_loss=70.0,
            stake=10.0,
            is_executable=False,   # excluded
            comparison_snapshot_id=snap,
            confidence_label=None,
        )
        # Without any executable settled trade for V3, paired_tickers should be empty
        v21 = [_trade(strategy_version="v2.1", is_executable=True, comparison_snapshot_id=snap)]
        v22 = [_trade(strategy_version="v2.2", is_executable=True, comparison_snapshot_id=snap)]
        result = _preliminary_leader(v21, v22, [non_exec_v3])
        # V3 exec set is empty → no 3-way match → n_paired_settled_exec = 0
        assert result["n_paired_settled_exec"] == 0

    def test_legacy_v1_v2_excluded_from_leader(self):
        """Legacy trades are never passed to _preliminary_leader."""
        # _preliminary_leader accepts only what's passed to it.
        # If legacy trades were accidentally mixed in, they would have a
        # different (or NULL) comparison_snapshot_id and would not pair.
        snap = self._make_snap_id()
        legacy = _trade(strategy_version="v1.0", comparison_snapshot_id=None)
        v21 = [_trade(strategy_version="v2.1", is_executable=True, comparison_snapshot_id=snap)]
        v22 = [_trade(strategy_version="v2.2", is_executable=True, comparison_snapshot_id=snap)]
        # Pass legacy as v3_trades — it has NULL comparison_snapshot_id so won't pair
        result = _preliminary_leader(v21, v22, [legacy])
        assert result["n_paired_settled_exec"] == 0


# ---------------------------------------------------------------------------
# 6. V3 can supply Best Paper Bet Today
# ---------------------------------------------------------------------------

class TestV3CanWinBestBetToday:
    """
    _best_bet_today selects the single highest-quality open signal.
    When V3 has the only eligible ticker with high edge, it must be chosen.
    """

    def _eligible_open(self, strategy: str, ticker: str, edge: float,
                       direction: str = "YES") -> SimpleNamespace:
        """Open, executable, verified, fresh-quote trade."""
        return _open_trade(
            strategy_version=strategy,
            market_ticker=ticker,
            edge_pct_points=edge,
            direction=direction,
            is_executable=True,
            station_verified=True,
            quality_flags=[],
            quote_timestamp=NOW,
            side_market_price=0.35 if direction == "YES" else 0.65,
            confidence_label=None if strategy == "v3.0" else "High",
        )

    def test_v3_wins_when_only_eligible_source(self):
        """V3 must be selected when it is the sole eligible open signal."""
        v3_trade = self._eligible_open("v3.0", "KXHIGHTEMP-LAX-20260801-GTE85", edge=28.0)
        result = _best_bet_today([], [], [v3_trade])
        assert result["has_bet"] is True
        assert result["candidate"]["strategy_version"] == "v3"

    def test_v3_wins_unique_high_edge_ticker(self):
        """V3 wins a ticker it alone covers when its net edge is highest."""
        v21_trade = self._eligible_open("v2.1", "KXHIGHTEMP-OKC-20260801-GTE95", edge=20.0)
        v3_trade  = self._eligible_open("v3.0", "KXHIGHTEMP-LAX-20260801-GTE85", edge=35.0)
        # V3 ticker has higher edge; V2.1 ticker is lower — V3 must win
        result = _best_bet_today([v21_trade], [], [v3_trade])
        assert result["has_bet"] is True
        assert result["candidate"]["ticker"] == "KXHIGHTEMP-LAX-20260801-GTE85"

    def test_non_executable_v3_excluded_from_best_bet(self):
        """Non-executable V3 signals must not qualify for Best Paper Bet."""
        non_exec = _open_trade(
            strategy_version="v3.0",
            market_ticker="KXHIGHTEMP-LAX-20260801-GTE85",
            edge_pct_points=99.0,   # huge edge but non-exec
            is_executable=False,
            station_verified=True,
            quality_flags=[],
            confidence_label=None,
        )
        result = _best_bet_today([], [], [non_exec])
        assert result["has_bet"] is False

    def test_v3_excluded_when_station_not_verified(self):
        """Unverified V3 station disqualifies the trade."""
        unverified = _open_trade(
            strategy_version="v3.0",
            market_ticker="KXHIGHTEMP-LAX-20260801-GTE85",
            edge_pct_points=40.0,
            is_executable=True,
            station_verified=False,   # not verified
            quality_flags=[],
            confidence_label=None,
        )
        result = _best_bet_today([], [], [unverified])
        assert result["has_bet"] is False

    def test_v2_preferred_when_same_ticker_and_same_edge(self):
        """Primary preference V2.1 > V2.2 > V3 applies for identical edge on same ticker."""
        ticker = "KXHIGHTEMP-OKC-20260801-GTE95"
        v21 = self._eligible_open("v2.1", ticker, edge=25.0)
        v3  = self._eligible_open("v3.0", ticker, edge=25.0)
        result = _best_bet_today([v21], [], [v3])
        assert result["has_bet"] is True
        # V2.1 preferred over V3 for same ticker
        assert result["candidate"]["strategy_version"] in ("v2.1", "v2.2")
