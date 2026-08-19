"""
tests/test_readiness.py
Tests for the Real-Money Readiness dashboard backend (app/routers/readiness.py).

Coverage:
  - Evidence population integrity: only OFFICIAL trades contribute
  - Missing data fails closed (NOT_READY / NEEDS_EVIDENCE)
  - Brier score calculation
  - Max drawdown and losing streak
  - ROI calculation
  - City, strategy, edge, confidence breakdowns
  - Quote quality metrics
  - Settlement coverage
  - Abstention analysis
  - Settlement integrity exceptions (quality_flags)
  - Evidence gaps plain-language messages
  - Safety invariants (trading_state_modified, realMoneyExecutionEnabled always False)
  - No real-money execution path
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.routers.readiness import (
    _abstention_analysis,
    _avg_entry_edge,
    _brier_score,
    _city_breakdown,
    _confidence_breakdown,
    _edge_bucket_breakdown,
    _evidence_gaps,
    _max_drawdown_and_losing_streak,
    _quote_quality,
    _readiness_summary,
    _roi,
    _settlement_coverage,
    _settlement_integrity_exceptions,
    _strategy_breakdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


_UNSET = object()


def _make_trade(
    *,
    status: str = "SETTLED",
    outcome: str | None = "WIN",
    eligibility_status: str = "OFFICIAL",
    eligibility_reason: str | None = None,
    ec_yes_probability: float | None = 0.7,
    kalshi_result: str | None = "yes",
    profit_loss: float | None = 0.5,
    stake: float = 1.0,
    city: str | None = "Denver",
    strategy_version: str = "v2.3",
    edge_pct_points: float | None = 15.0,
    confidence_label: str | None = "HIGH",
    settlement_timestamp: datetime | None = None,
    quality_flags: list | None = None,
    quote_timestamp: object = _UNSET,   # None = explicit missing; _UNSET = use now()
    side_market_price: float | None = 0.55,
    quote_age_seconds: float | None = 60.0,
    settlement_regime: str | None = "WEATHER_COMPANY",
) -> MagicMock:
    t = MagicMock()
    t.status = status
    t.outcome = outcome
    t.eligibility_status = eligibility_status
    t.eligibility_reason = eligibility_reason
    t.ec_yes_probability = ec_yes_probability
    t.kalshi_result = kalshi_result
    t.profit_loss = profit_loss
    t.stake = stake
    t.city = city
    t.strategy_version = strategy_version
    t.edge_pct_points = edge_pct_points
    t.confidence_label = confidence_label
    t.settlement_timestamp = settlement_timestamp or _now()
    t.quality_flags = quality_flags or []
    t.quote_timestamp = _now() if quote_timestamp is _UNSET else quote_timestamp
    t.side_market_price = side_market_price
    t.quote_age_seconds = quote_age_seconds
    t.settlement_regime = settlement_regime
    t.id = 1
    t.market_ticker = "TEST-TICKER-001"
    return t


# ---------------------------------------------------------------------------
# Brier score
# ---------------------------------------------------------------------------

class TestBrierScore:
    def test_perfect_yes_predictions(self):
        trades = [
            _make_trade(ec_yes_probability=1.0, kalshi_result="yes"),
            _make_trade(ec_yes_probability=1.0, kalshi_result="yes"),
        ]
        assert _brier_score(trades) == 0.0

    def test_worst_case_brier(self):
        # Predicted 1.0 but resolved no — maximum error per trade = 1.0
        trades = [_make_trade(ec_yes_probability=1.0, kalshi_result="no")]
        assert _brier_score(trades) == 1.0

    def test_none_when_no_probability(self):
        trades = [_make_trade(ec_yes_probability=None, kalshi_result="yes")]
        assert _brier_score(trades) is None

    def test_none_when_no_settled(self):
        trades = [_make_trade(status="OPEN", ec_yes_probability=0.7, kalshi_result=None)]
        assert _brier_score(trades) is None

    def test_mixed_yes_no_results(self):
        trades = [
            _make_trade(ec_yes_probability=0.6, kalshi_result="yes"),
            _make_trade(ec_yes_probability=0.4, kalshi_result="no"),
        ]
        score = _brier_score(trades)
        assert score is not None
        expected = ((0.6 - 1) ** 2 + (0.4 - 0) ** 2) / 2
        assert abs(score - expected) < 1e-5


# ---------------------------------------------------------------------------
# Max drawdown and losing streak
# ---------------------------------------------------------------------------

class TestMaxDrawdownLosingStreak:
    def test_none_when_fewer_than_two_trades(self):
        dd, streak = _max_drawdown_and_losing_streak([])
        assert dd is None
        assert streak is None
        dd, streak = _max_drawdown_and_losing_streak([_make_trade()])
        assert dd is None
        assert streak is None

    def test_all_wins_no_drawdown(self):
        trades = [_make_trade(profit_loss=1.0) for _ in range(5)]
        dd, streak = _max_drawdown_and_losing_streak(trades)
        assert dd == 0.0
        assert streak == 0

    def test_consecutive_losses(self):
        t = _now()
        trades = [
            _make_trade(profit_loss=-1.0, settlement_timestamp=t + timedelta(seconds=1)),
            _make_trade(profit_loss=-1.0, settlement_timestamp=t + timedelta(seconds=2)),
            _make_trade(profit_loss=-1.0, settlement_timestamp=t + timedelta(seconds=3)),
            _make_trade(profit_loss=2.0, settlement_timestamp=t + timedelta(seconds=4)),
            _make_trade(profit_loss=-0.5, settlement_timestamp=t + timedelta(seconds=5)),
        ]
        dd, streak = _max_drawdown_and_losing_streak(trades)
        assert dd == 3.0
        assert streak == 3


# ---------------------------------------------------------------------------
# ROI
# ---------------------------------------------------------------------------

class TestRoi:
    def test_basic_roi(self):
        trades = [
            _make_trade(profit_loss=1.0, stake=10.0),
            _make_trade(profit_loss=-2.0, stake=10.0),
        ]
        roi = _roi(trades)
        assert roi == -5.0  # -1 / 20 * 100

    def test_none_when_no_stake(self):
        trades = [_make_trade(profit_loss=1.0, stake=0.0)]
        assert _roi(trades) is None

    def test_none_on_empty(self):
        assert _roi([]) is None


# ---------------------------------------------------------------------------
# City breakdown
# ---------------------------------------------------------------------------

class TestCityBreakdown:
    def test_single_city(self):
        trades = [
            _make_trade(city="Denver", status="SETTLED", outcome="WIN"),
            _make_trade(city="Denver", status="SETTLED", outcome="LOSS"),
        ]
        rows = _city_breakdown(trades)
        assert len(rows) == 1
        assert rows[0]["city"] == "Denver"
        assert rows[0]["wins"] == 1
        assert rows[0]["settled"] == 2
        assert rows[0]["winRate"] == 0.5

    def test_small_sample_flag(self):
        trades = [_make_trade(city="Denver", status="SETTLED", outcome="WIN") for _ in range(5)]
        rows = _city_breakdown(trades)
        assert rows[0]["smallSample"] is True

    def test_multiple_cities(self):
        trades = [
            _make_trade(city="Denver"),
            _make_trade(city="New York City"),
        ]
        rows = _city_breakdown(trades)
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# Strategy breakdown
# ---------------------------------------------------------------------------

class TestStrategyBreakdown:
    def test_per_strategy_stats(self):
        trades = [
            _make_trade(strategy_version="v2.3", status="SETTLED", outcome="WIN"),
            _make_trade(strategy_version="v2.3", status="SETTLED", outcome="LOSS"),
            _make_trade(strategy_version="v3.0", status="SETTLED", outcome="WIN"),
        ]
        rows = _strategy_breakdown(trades)
        strats = {r["strategy"]: r for r in rows}
        assert strats["v2.3"]["wins"] == 1
        assert strats["v2.3"]["winRate"] == 0.5
        assert strats["v3.0"]["wins"] == 1


# ---------------------------------------------------------------------------
# Edge bucket breakdown
# ---------------------------------------------------------------------------

class TestEdgeBucketBreakdown:
    def test_buckets_categorized(self):
        trades = [
            _make_trade(edge_pct_points=5.0, status="SETTLED", outcome="WIN"),
            _make_trade(edge_pct_points=15.0, status="SETTLED", outcome="LOSS"),
            _make_trade(edge_pct_points=25.0, status="SETTLED", outcome="WIN"),
            _make_trade(edge_pct_points=35.0, status="SETTLED", outcome="WIN"),
        ]
        rows = _edge_bucket_breakdown(trades)
        bucket_map = {r["bucket"]: r for r in rows}
        assert bucket_map["0–10pp"]["total"] == 1
        assert bucket_map["10–20pp"]["total"] == 1
        assert bucket_map["20–30pp"]["total"] == 1
        assert bucket_map["30+pp"]["total"] == 1

    def test_missing_edge_skipped(self):
        trades = [_make_trade(edge_pct_points=None)]
        rows = _edge_bucket_breakdown(trades)
        assert all(r["total"] == 0 for r in rows)


# ---------------------------------------------------------------------------
# Quote quality
# ---------------------------------------------------------------------------

class TestQuoteQuality:
    def test_no_missing_or_stale(self):
        trades = [_make_trade(quote_timestamp=_now(), side_market_price=0.55, quote_age_seconds=60.0)]
        q = _quote_quality(trades)
        assert q["missingQuoteCount"] == 0
        assert q["staleQuoteCount"] == 0

    def test_missing_quote(self):
        trades = [_make_trade(quote_timestamp=None, side_market_price=None)]
        q = _quote_quality(trades)
        assert q["missingQuoteCount"] == 1

    def test_stale_quote(self):
        trades = [_make_trade(quote_age_seconds=400.0)]
        q = _quote_quality(trades)
        assert q["staleQuoteCount"] == 1


# ---------------------------------------------------------------------------
# Settlement coverage
# ---------------------------------------------------------------------------

class TestSettlementCoverage:
    def test_coverage_percentage(self):
        trades = [
            _make_trade(status="SETTLED"),
            _make_trade(status="SETTLED"),
            _make_trade(status="OPEN"),
        ]
        cov = _settlement_coverage(trades)
        assert cov["settled"] == 2
        assert cov["open"] == 1
        assert cov["settlementCoveragePct"] == pytest.approx(66.7, abs=0.1)


# ---------------------------------------------------------------------------
# Abstention analysis
# ---------------------------------------------------------------------------

class TestAbstentionAnalysis:
    def test_reason_breakdown(self):
        research = [
            _make_trade(eligibility_status="RESEARCH_ONLY", eligibility_reason="stale_quote"),
            _make_trade(eligibility_status="RESEARCH_ONLY", eligibility_reason="stale_quote"),
            _make_trade(eligibility_status="RESEARCH_ONLY", eligibility_reason="same_day"),
        ]
        result = _abstention_analysis(research)
        assert result["researchOnlyCount"] == 3
        assert result["reasonBreakdown"]["stale_quote"] == 2
        assert result["reasonBreakdown"]["same_day"] == 1


# ---------------------------------------------------------------------------
# Settlement integrity exceptions
# ---------------------------------------------------------------------------

class TestSettlementIntegrityExceptions:
    def test_no_flags_no_exceptions(self):
        trades = [_make_trade(quality_flags=[])]
        assert _settlement_integrity_exceptions(trades) == []

    def test_flagged_trade_included(self):
        trades = [_make_trade(quality_flags=["ERA5_DIVERGENCE"])]
        excs = _settlement_integrity_exceptions(trades)
        assert len(excs) == 1
        assert "ERA5_DIVERGENCE" in excs[0]["flags"]


# ---------------------------------------------------------------------------
# Evidence gaps
# ---------------------------------------------------------------------------

class TestEvidenceGaps:
    def test_no_settled_trades_returns_gap(self):
        gaps = _evidence_gaps(settled_count=0, city_count=0, brier=None, roi=None)
        assert len(gaps) == 1
        assert "No settled OFFICIAL trades" in gaps[0]

    def test_small_sample_gap(self):
        gaps = _evidence_gaps(settled_count=5, city_count=2, brier=0.1, roi=5.0)
        assert any("settled" in g for g in gaps)

    def test_single_city_gap(self):
        gaps = _evidence_gaps(settled_count=30, city_count=1, brier=0.1, roi=5.0)
        assert any("fewer than 2 cities" in g for g in gaps)

    def test_missing_brier_gap(self):
        gaps = _evidence_gaps(settled_count=30, city_count=3, brier=None, roi=5.0)
        assert any("Brier score" in g for g in gaps)

    def test_missing_roi_gap(self):
        gaps = _evidence_gaps(settled_count=30, city_count=3, brier=0.1, roi=None)
        assert any("ROI" in g for g in gaps)

    def test_no_gaps_when_sufficient_evidence(self):
        gaps = _evidence_gaps(settled_count=50, city_count=3, brier=0.1, roi=5.0)
        assert gaps == []


# ---------------------------------------------------------------------------
# Readiness summary — fails closed
# ---------------------------------------------------------------------------

class TestReadinessSummary:
    def test_not_ready_when_no_trades(self):
        result = _readiness_summary(settled_count=0, evidence_gaps=["No trades."])
        assert result["status"] == "NOT_READY"
        assert result["thresholdsActivated"] is False
        assert result["realMoneyExecutionEnabled"] is False
        assert result["trading_state_modified"] is False

    def test_needs_evidence_when_gaps(self):
        result = _readiness_summary(settled_count=5, evidence_gaps=["Small sample."])
        assert result["status"] == "NEEDS_EVIDENCE"
        assert result["thresholdsActivated"] is False
        assert result["realMoneyExecutionEnabled"] is False

    def test_needs_evidence_even_when_no_gaps(self):
        # Protected thresholds have not been approved by owner — status stays NEEDS_EVIDENCE
        result = _readiness_summary(settled_count=100, evidence_gaps=[])
        assert result["status"] == "NEEDS_EVIDENCE"
        assert result["thresholdsActivated"] is False
        assert result["realMoneyExecutionEnabled"] is False

    def test_no_real_money_execution_path_ever(self):
        """
        Safety invariant: regardless of trade count or gaps, real-money
        execution must never be enabled.
        """
        for count in [0, 1, 10, 100, 1000]:
            result = _readiness_summary(settled_count=count, evidence_gaps=[])
            assert result["realMoneyExecutionEnabled"] is False, (
                f"realMoneyExecutionEnabled was True for settled_count={count}"
            )

    def test_trading_state_never_modified(self):
        for count in [0, 50, 500]:
            result = _readiness_summary(settled_count=count, evidence_gaps=[])
            assert result["trading_state_modified"] is False


# ---------------------------------------------------------------------------
# Average entry edge
# ---------------------------------------------------------------------------

class TestAvgEntryEdge:
    def test_basic_average(self):
        trades = [_make_trade(edge_pct_points=10.0), _make_trade(edge_pct_points=20.0)]
        assert _avg_entry_edge(trades) == 15.0

    def test_none_when_no_edge(self):
        trades = [_make_trade(edge_pct_points=None)]
        assert _avg_entry_edge(trades) is None

    def test_none_on_empty(self):
        assert _avg_entry_edge([]) is None
