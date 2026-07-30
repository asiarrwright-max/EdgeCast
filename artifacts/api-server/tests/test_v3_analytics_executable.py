"""
Tests for V3 analytics — executable vs non-executable trade separation.

Verifies that:
  - Official ROI and win rate use ONLY is_executable=True settled trades.
  - Non-executable settled trades never affect official headline metrics.
  - Brier score uses only executable settled trades.
  - Observation-only count (PENDING snaps with no linked trade) is reported separately.
  - Fee estimates are computed correctly.
  - The three-section structure (executable, non_executable, observation_only) is correct.
"""
from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any


# ---------------------------------------------------------------------------
# Helpers to build mock trade objects (no DB required)
# ---------------------------------------------------------------------------

def _trade(
    *,
    is_executable: bool | None,
    status: str = "SETTLED",
    outcome: str | None = None,
    profit_loss: float | None = None,
    ec_yes_probability: float = 0.5,
    direction: str = "YES",
    stake: float = 10.0,
    side_market_price: float = 0.10,
    quantity: float = 100.0,
) -> SimpleNamespace:
    """Create a minimal mock V3PaperTrade-like object."""
    return SimpleNamespace(
        is_executable=is_executable,
        status=status,
        outcome=outcome,
        profit_loss=profit_loss,
        ec_yes_probability=ec_yes_probability,
        ec_side_probability=ec_yes_probability if direction == "YES" else 1.0 - ec_yes_probability,
        direction=direction,
        stake=stake,
        side_market_price=side_market_price,
        quantity=quantity,
    )


def _open_trade(is_executable: bool | None = True, **kwargs) -> SimpleNamespace:
    return _trade(is_executable=is_executable, status="OPEN", outcome=None,
                  profit_loss=None, **kwargs)


def _settled_win(is_executable: bool | None, stake: float = 10.0,
                 side_price: float = 0.10, qty: float = 100.0,
                 ec_yes: float = 0.70, direction: str = "NO") -> SimpleNamespace:
    # NO trade at $0.10: payout = stake + stake*(1-price)/price = 10 + 10*0.9/0.1 = $100
    gross = stake + stake * (1.0 - side_price) / side_price
    return _trade(
        is_executable=is_executable,
        status="SETTLED",
        outcome="WIN",
        profit_loss=round(gross - stake, 2),
        ec_yes_probability=ec_yes,
        direction=direction,
        stake=stake,
        side_market_price=side_price,
        quantity=qty,
    )


def _settled_loss(is_executable: bool | None, stake: float = 10.0,
                  side_price: float = 0.10, qty: float = 100.0,
                  ec_yes: float = 0.20, direction: str = "NO") -> SimpleNamespace:
    return _trade(
        is_executable=is_executable,
        status="SETTLED",
        outcome="LOSS",
        profit_loss=-stake,
        ec_yes_probability=ec_yes,
        direction=direction,
        stake=stake,
        side_market_price=side_price,
        quantity=qty,
    )


# ---------------------------------------------------------------------------
# Import the helpers from v3_analytics (defined at module level)
# ---------------------------------------------------------------------------

def _get_helpers():
    """
    Import the helper functions extracted from the analytics endpoint.
    We test _compute_v3_trade_sections directly.
    """
    from app.routers.v3_analytics import (
        _compute_v3_trade_sections,
        _fee_estimate,
        _v3_brier_score,
    )
    return _compute_v3_trade_sections, _fee_estimate, _v3_brier_score


# ---------------------------------------------------------------------------
# Fee estimation tests
# ---------------------------------------------------------------------------

class TestFeeEstimate:

    def test_low_price_fee(self):
        from app.routers.v3_analytics import _fee_estimate
        # price=0.04, qty=250: fee = max(0.01, 0.035 * 0.04 * 250) = max(0.01, 0.35) = 0.35
        assert abs(_fee_estimate(0.04, 250) - 0.35) < 1e-4

    def test_high_price_fee(self):
        from app.routers.v3_analytics import _fee_estimate
        # price=0.17, qty=58.8: fee = max(0.01, 0.035 * 0.17 * 58.8) ≈ 0.35
        assert abs(_fee_estimate(0.17, 58.8) - 0.35) < 0.01

    def test_fee_uses_min_of_price_and_one_minus_price(self):
        from app.routers.v3_analytics import _fee_estimate
        # price=0.90 → min(0.90, 0.10) = 0.10
        f1 = _fee_estimate(0.90, 100)
        f2 = _fee_estimate(0.10, 100)
        assert abs(f1 - f2) < 1e-6

    def test_zero_quantity_returns_none(self):
        from app.routers.v3_analytics import _fee_estimate
        assert _fee_estimate(0.10, 0) is None

    def test_none_price_returns_none(self):
        from app.routers.v3_analytics import _fee_estimate
        assert _fee_estimate(None, 100) is None


# ---------------------------------------------------------------------------
# Brier score tests
# ---------------------------------------------------------------------------

class TestBrierScore:

    def test_perfect_yes_win(self):
        """p_yes=1.0, YES-WIN → actual_yes=1 → BS=0."""
        from app.routers.v3_analytics import _v3_brier_score
        trade = _settled_win(True, ec_yes=1.0, direction="YES", side_price=0.9)
        trade.outcome = "WIN"
        result = _v3_brier_score([trade])
        assert result == 0.0

    def test_worst_yes_win(self):
        """p_yes=0.0, YES-WIN → actual_yes=1 → BS=(0-1)^2=1."""
        from app.routers.v3_analytics import _v3_brier_score
        trade = _trade(is_executable=True, status="SETTLED", outcome="WIN",
                       profit_loss=90.0, ec_yes_probability=0.0, direction="YES",
                       stake=10.0, side_market_price=0.1, quantity=100.0)
        result = _v3_brier_score([trade])
        assert abs(result - 1.0) < 1e-6

    def test_no_win_outcome_yes_zero(self):
        """p_yes=0.30, NO-WIN (NO resolved) → actual_yes=0 → BS=(0.3-0)^2=0.09."""
        from app.routers.v3_analytics import _v3_brier_score
        trade = _settled_win(True, ec_yes=0.30, direction="NO")
        result = _v3_brier_score([trade])
        assert abs(result - 0.09) < 1e-6

    def test_no_loss_outcome_yes_one(self):
        """p_yes=0.70, NO-LOSS (YES resolved) → actual_yes=1 → BS=(0.7-1)^2=0.09."""
        from app.routers.v3_analytics import _v3_brier_score
        trade = _settled_loss(True, ec_yes=0.70, direction="NO")
        result = _v3_brier_score([trade])
        assert abs(result - 0.09) < 1e-6

    def test_empty_returns_none(self):
        from app.routers.v3_analytics import _v3_brier_score
        assert _v3_brier_score([]) is None

    def test_only_open_returns_none(self):
        from app.routers.v3_analytics import _v3_brier_score
        assert _v3_brier_score([_open_trade()]) is None

    def test_mean_of_multiple(self):
        """BS = mean((0.3-0)^2, (0.7-1)^2) = mean(0.09, 0.09) = 0.09."""
        from app.routers.v3_analytics import _v3_brier_score
        t1 = _settled_win(True, ec_yes=0.30, direction="NO")
        t2 = _settled_loss(True, ec_yes=0.70, direction="NO")
        result = _v3_brier_score([t1, t2])
        assert abs(result - 0.09) < 1e-6


# ---------------------------------------------------------------------------
# Section computation tests — the core isolation guarantee
# ---------------------------------------------------------------------------

class TestComputeV3TradeSections:

    def test_executable_win_in_official_section(self):
        """A settled executable WIN counts toward official ROI."""
        compute, _, _ = _get_helpers()
        win = _settled_win(True, stake=10.0, side_price=0.10, qty=100.0)
        sections = compute([win], observation_only_count=0)

        exec_s = sections["executable"]
        assert exec_s["wins"] == 1
        assert exec_s["losses"] == 0
        assert exec_s["win_rate_pct"] == 100.0
        assert exec_s["roi_pct"] is not None
        assert exec_s["roi_pct"] > 0

    def test_non_executable_loss_excluded_from_official_roi(self):
        """
        A settled non-executable LOSS must NOT affect official ROI.
        The ROI in the executable section stays positive even when a
        non-executable loss exists.
        """
        compute, _, _ = _get_helpers()
        exec_win  = _settled_win(True,  stake=10.0, side_price=0.10, qty=100.0)
        nonexec_loss = _settled_loss(False, stake=10.0, side_price=0.10, qty=100.0)

        sections = compute([exec_win, nonexec_loss], observation_only_count=0)

        exec_s    = sections["executable"]
        nonexec_s = sections["non_executable"]

        # Executable section sees only the WIN
        assert exec_s["wins"] == 1
        assert exec_s["losses"] == 0
        assert exec_s["roi_pct"] is not None and exec_s["roi_pct"] > 0

        # Non-executable section sees only the LOSS
        assert nonexec_s["wins"] == 0
        assert nonexec_s["losses"] == 1
        assert nonexec_s.get("roi_pct") is None, (
            "Non-executable section must not expose an ROI headline"
        )

    def test_mixing_never_contaminates_official_roi(self):
        """
        Even with many non-executable losses, executable ROI is computed
        only from executable trades.
        """
        compute, _, _ = _get_helpers()
        # 1 executable WIN
        trades = [_settled_win(True, stake=10.0, side_price=0.10, qty=100.0)]
        # 9 non-executable LOSSes
        for _ in range(9):
            trades.append(_settled_loss(False, stake=10.0, side_price=0.10, qty=100.0))

        sections = compute(trades, observation_only_count=0)
        exec_s = sections["executable"]

        assert exec_s["wins"] == 1
        assert exec_s["losses"] == 0
        assert exec_s["roi_pct"] is not None and exec_s["roi_pct"] > 0, (
            "Official ROI must ignore 9 non-executable losses"
        )

    def test_no_executable_settled_roi_is_none(self):
        """With only open executable trades, official roi_pct is None (no settled data)."""
        compute, _, _ = _get_helpers()
        trades = [_open_trade(True), _open_trade(True)]
        sections = compute(trades, observation_only_count=0)
        assert sections["executable"]["roi_pct"] is None

    def test_current_db_counts(self):
        """
        Matches the known current DB state:
        10 executable OPEN + 17 non-executable OPEN + 99 observation-only.
        """
        compute, _, _ = _get_helpers()
        trades = (
            [_open_trade(True)]  * 10
          + [_open_trade(False)] * 17
        )
        sections = compute(trades, observation_only_count=99)

        assert sections["executable"]["count"] == 10
        assert sections["executable"]["open"] == 10
        assert sections["non_executable"]["count"] == 17
        assert sections["observation_only"]["count"] == 99

    def test_observation_only_excluded_from_trade_metrics(self):
        """observation_only count is reported but has no P/L or ROI."""
        compute, _, _ = _get_helpers()
        sections = compute([], observation_only_count=99)
        obs = sections["observation_only"]
        assert obs["count"] == 99
        assert "roi_pct" not in obs
        assert "wins" not in obs

    def test_brier_score_uses_executable_only(self):
        """
        Brier score in the executable section reflects only executable trades.
        A non-executable trade with a very different p_yes must not change the score.
        """
        compute, _, _ = _get_helpers()
        # Executable: p_yes=0.3, NO-WIN → BS=(0.3-0)^2=0.09
        exec_t = _settled_win(True, ec_yes=0.30, direction="NO")
        # Non-executable: p_yes=0.99, NO-WIN → would give BS=(0.99-0)^2≈0.98
        nonexec_t = _settled_win(False, ec_yes=0.99, direction="NO")

        sections = compute([exec_t, nonexec_t], observation_only_count=0)
        brier = sections["executable"].get("brier_score")
        assert brier is not None
        assert abs(brier - 0.09) < 1e-6, (
            f"Brier score should be 0.09 (executable only), got {brier}"
        )

    def test_open_executable_zero_pl(self):
        """Open trades contribute to stake count but zero P/L."""
        compute, _, _ = _get_helpers()
        trades = [_open_trade(True, stake=10.0)]
        sections = compute(trades, observation_only_count=0)
        exec_s = sections["executable"]
        assert exec_s["total_stake"] == 10.0
        assert exec_s["gross_pl"] == 0.0
        assert exec_s["net_pl"] is not None

    def test_fee_subtracted_from_gross_to_get_net(self):
        """net_pl = gross_pl - estimated_fees for settled executable trades."""
        compute, fee_fn, _ = _get_helpers()
        win = _settled_win(True, stake=10.0, side_price=0.10, qty=100.0)
        gross = win.profit_loss  # $90

        sections = compute([win], observation_only_count=0)
        exec_s = sections["executable"]
        expected_fee = fee_fn(0.10, 100.0)
        assert exec_s["gross_pl"] == gross
        assert abs(exec_s["net_pl"] - (gross - expected_fee)) < 1e-4

    def test_win_rate_none_when_no_settled(self):
        """win_rate_pct must be None when no settled trades exist."""
        compute, _, _ = _get_helpers()
        sections = compute([_open_trade(True)], observation_only_count=0)
        assert sections["executable"]["win_rate_pct"] is None

    def test_is_executable_none_treated_as_non_executable(self):
        """Trades with is_executable=None are in the non_executable bucket."""
        compute, _, _ = _get_helpers()
        t = _settled_loss(None, stake=10.0)
        sections = compute([t], observation_only_count=0)
        assert sections["executable"]["count"] == 0
        assert sections["non_executable"]["count"] == 1
