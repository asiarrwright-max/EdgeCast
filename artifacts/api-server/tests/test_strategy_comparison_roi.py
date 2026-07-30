"""
Regression tests for gross_roi_pct / net_roi_pct in _strategy_summary.

Key invariants
--------------
1. gross_roi_pct = round(100 * gross_pl / settled_stake, 1)  — always before fees.
2. net_roi_pct   = round(100 * net_pl   / settled_stake, 1)  — after settled fees;
   may be more negative than -100 % when fees exceed remaining value.
3. Neither value is capped.
4. Both are None when there are no settled trades.
5. When all settled trades are losses (gross_pl = -settled_stake), gross_roi = -100 %
   and net_roi < -100 % (because fees are still charged on the losing positions).
"""

import sys
import os
import math
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.routers.strategy_comparison import _strategy_summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def trade(
    status: str,
    stake: float,
    pl: float | None,
    price: float,
    qty: float,
    outcome: str | None = None,
    is_executable: bool = True,
    direction: str = "NO",
) -> SimpleNamespace:
    """Build a minimal trade-like namespace accepted by _strategy_summary."""
    return SimpleNamespace(
        status=status,
        stake=stake,
        profit_loss=pl,
        side_market_price=price,
        quantity=qty,
        outcome=outcome,
        is_executable=is_executable,
        direction=direction,
        edge_pct_points=10.0,
        sigma_used=4.0,
        ec_yes_probability=0.20,
        bias_correction=0.0,
        final_sigma=None,
    )


def exec_section(trades_list):
    """Return the executable section dict from _strategy_summary."""
    summary = _strategy_summary(trades_list, label="TEST", description="test")
    return summary["executable"]


FEE_RATE = 0.035  # 3.5 % of min(p, 1-p) × qty, min $0.01


def expected_fee(price: float, qty: float) -> float:
    return max(0.01, FEE_RATE * min(price, 1.0 - price) * qty)


# ---------------------------------------------------------------------------
# Core two-trade scenario (matches live V2.1 settled data)
# ---------------------------------------------------------------------------

class TestTwoSettledLosses:
    """
    Trade 271: NO @ $0.12, qty=83.3333 → stake $10, loss $10, fee $0.35
    Trade 344: NO @ $0.14, qty=71.4286 → stake $10, loss $10, fee $0.35
    Expected:
        settled_stake   = $20.00
        gross_pl        = -$20.00
        estimated_fees  = $0.70
        net_pl          = -$20.70
        gross_roi_pct   = -100.0 %
        net_roi_pct     = -103.5 %
    """

    @pytest.fixture
    def sec(self):
        t1 = trade("SETTLED", 10.0, -10.0, 0.12, 83.3333, outcome="LOSS")
        t2 = trade("SETTLED", 10.0, -10.0, 0.14, 71.4286, outcome="LOSS")
        return exec_section([t1, t2])

    def test_settled_stake(self, sec):
        assert sec["settled_stake"] == 20.00

    def test_gross_pl(self, sec):
        assert sec["gross_pl"] == -20.00

    def test_estimated_fees(self, sec):
        assert sec["estimated_fees"] == pytest.approx(0.70, abs=1e-9)

    def test_net_pl(self, sec):
        assert sec["net_pl"] == pytest.approx(-20.70, abs=1e-4)

    def test_gross_roi_is_minus_100(self, sec):
        assert sec["gross_roi_pct"] == -100.0

    def test_net_roi_is_below_minus_100(self, sec):
        assert sec["net_roi_pct"] == pytest.approx(-103.5, abs=0.1)
        assert sec["net_roi_pct"] < -100.0

    def test_gross_roi_equals_gross_pl_over_settled_stake(self, sec):
        expected = round(100 * sec["gross_pl"] / sec["settled_stake"], 1)
        assert sec["gross_roi_pct"] == expected

    def test_net_roi_equals_net_pl_over_settled_stake(self, sec):
        expected = round(100 * sec["net_pl"] / sec["settled_stake"], 1)
        assert sec["net_roi_pct"] == expected


# ---------------------------------------------------------------------------
# Net ROI can go below -100 % — general property
# ---------------------------------------------------------------------------

class TestNetRoiCanExceedNegative100:
    """Any complete stake loss produces net_roi < -100 % (fees always > 0)."""

    @pytest.mark.parametrize("price,qty,stake", [
        (0.10, 100.0, 10.0),   # cheap options, more contracts
        (0.30, 33.33, 10.0),   # mid-price
        (0.49, 20.0,  9.8),    # near-even price, small stake
    ])
    def test_complete_loss_net_roi_below_minus_100(self, price, qty, stake):
        t = trade("SETTLED", stake, -stake, price, qty, outcome="LOSS")
        sec = exec_section([t])
        # Gross ROI should be exactly -100 %
        assert sec["gross_roi_pct"] == -100.0
        # Net ROI must be strictly more negative
        assert sec["net_roi_pct"] < -100.0

    def test_net_roi_not_capped(self):
        """Explicitly confirm net_roi is NOT clamped to -100."""
        t = trade("SETTLED", 10.0, -10.0, 0.12, 83.3333, outcome="LOSS")
        sec = exec_section([t])
        assert sec["net_roi_pct"] is not None
        assert sec["net_roi_pct"] < -100.0


# ---------------------------------------------------------------------------
# Winning trade: gross_roi and net_roi are both positive but net < gross
# ---------------------------------------------------------------------------

class TestWinningTrade:
    """A WIN trade: net_roi is lower than gross_roi (fees reduce the gain)."""

    @pytest.fixture
    def sec(self):
        # NO @ $0.25, qty=40, stake $10, win = collect $10 profit
        t = trade("SETTLED", 10.0, 10.0, 0.25, 40.0, outcome="WIN")
        return exec_section([t])

    def test_gross_roi_positive(self, sec):
        assert sec["gross_roi_pct"] == 100.0

    def test_net_roi_lower_than_gross_roi(self, sec):
        fee = expected_fee(0.25, 40.0)
        expected_net_roi = round(100 * (10.0 - fee) / 10.0, 1)
        assert sec["net_roi_pct"] == pytest.approx(expected_net_roi, abs=0.05)
        assert sec["net_roi_pct"] < sec["gross_roi_pct"]

    def test_net_roi_above_minus_100(self, sec):
        # A pure WIN should give net_roi >> 0, certainly not negative
        assert sec["net_roi_pct"] > 0


# ---------------------------------------------------------------------------
# Mixed settled: one win, one loss
# ---------------------------------------------------------------------------

class TestMixedSettled:
    """One WIN ($10 profit) + one LOSS ($10 loss) → gross_pl = 0, net_pl < 0."""

    @pytest.fixture
    def sec(self):
        win  = trade("SETTLED", 10.0,  10.0, 0.25, 40.0,    outcome="WIN")
        loss = trade("SETTLED", 10.0, -10.0, 0.12, 83.3333, outcome="LOSS")
        return exec_section([win, loss])

    def test_gross_pl_zero(self, sec):
        assert sec["gross_pl"] == 0.0

    def test_gross_roi_zero(self, sec):
        assert sec["gross_roi_pct"] == 0.0

    def test_net_pl_negative_due_to_fees(self, sec):
        assert sec["net_pl"] < 0.0

    def test_net_roi_negative(self, sec):
        # gross = 0, fees > 0 → net roi < 0
        assert sec["net_roi_pct"] < 0.0

    def test_net_roi_consistent_with_net_pl(self, sec):
        expected = round(100 * sec["net_pl"] / sec["settled_stake"], 1)
        assert sec["net_roi_pct"] == expected


# ---------------------------------------------------------------------------
# No settled trades — both ROI fields are None
# ---------------------------------------------------------------------------

class TestNoSettledTrades:
    """Open-only population: both ROI fields must be None (no denominator)."""

    def test_both_roi_fields_none_when_no_settled_trades(self):
        open_trade = trade("OPEN", 10.0, None, 0.30, 33.33)
        sec = exec_section([open_trade])
        assert sec["gross_roi_pct"] is None
        assert sec["net_roi_pct"]   is None

    def test_both_roi_fields_none_when_empty(self):
        sec = exec_section([])
        assert sec["gross_roi_pct"] is None
        assert sec["net_roi_pct"]   is None


# ---------------------------------------------------------------------------
# Open trades do NOT affect either ROI field
# ---------------------------------------------------------------------------

class TestOpenTradesDoNotAffectRoi:
    """Adding open trades alongside settled trades must not change ROI."""

    def test_open_trades_ignored_in_roi(self):
        settled = trade("SETTLED", 10.0, -10.0, 0.12, 83.3333, outcome="LOSS")
        open1   = trade("OPEN",    10.0, None,  0.63, 15.87)
        open2   = trade("OPEN",    10.0, None,  0.77, 12.99)

        sec_settled_only = exec_section([settled])
        sec_with_open    = exec_section([settled, open1, open2])

        assert sec_settled_only["gross_roi_pct"] == sec_with_open["gross_roi_pct"]
        assert sec_settled_only["net_roi_pct"]   == sec_with_open["net_roi_pct"]


# ---------------------------------------------------------------------------
# Labelling: gross_roi_pct and net_roi_pct are separate keys (not roi_pct)
# ---------------------------------------------------------------------------

class TestKeyNames:
    """The old `roi_pct` key must not appear; the two new keys must be present."""

    def test_old_roi_pct_key_absent(self):
        t = trade("SETTLED", 10.0, -10.0, 0.12, 83.3333, outcome="LOSS")
        sec = exec_section([t])
        assert "roi_pct" not in sec, (
            "roi_pct must be removed; use gross_roi_pct and net_roi_pct instead"
        )

    def test_gross_roi_pct_key_present(self):
        t = trade("SETTLED", 10.0, -10.0, 0.12, 83.3333, outcome="LOSS")
        sec = exec_section([t])
        assert "gross_roi_pct" in sec

    def test_net_roi_pct_key_present(self):
        t = trade("SETTLED", 10.0, -10.0, 0.12, 83.3333, outcome="LOSS")
        sec = exec_section([t])
        assert "net_roi_pct" in sec
