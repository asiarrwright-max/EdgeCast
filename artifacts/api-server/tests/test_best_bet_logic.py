"""
tests/test_best_bet_logic.py
Regression tests for best-bet-today selected-side probability logic.

Contract enforced:
  selectedSideAsk               = side_market_price   (executable ask on the chosen side)
  selectedSideMarketProbability = side_market_price   (= ask; NOT 1 − market_yes_probability)
  selectedSideModelProbability  = ec_side_probability (already rotated to chosen side)

In a Kalshi binary market, the executable ask IS the market-implied probability
(0.50 ask = 50 ¢ per $1 = 50 % probability).  The raw mid-market YES probability
(market_yes_probability) is retained as a legacy/raw field only and must never be
used to derive selectedSideMarketProbability.
"""
from __future__ import annotations

import pytest
from app.routers.paper_trades import _selected_side_values


# ── YES trades ────────────────────────────────────────────────────────────────

class TestSelectedSideValuesYES:
    """YES trade: selectedSideMarketProbability = yes_ask = side_market_price."""

    def test_yes_selected_side_label(self):
        result = _selected_side_values(
            direction="YES",
            ec_side_probability=0.75,
            market_yes_probability=0.50,
            side_market_price=0.50,
            edge_pct_points=25.0,
        )
        assert result["selectedSide"] == "YES"

    def test_yes_selected_side_mkt_prob_equals_ask(self):
        """
        selectedSideMarketProbability must equal the YES ask (side_market_price).
        market_yes_probability=0.48 differs from ask=0.50 — the ask must win.
        """
        result = _selected_side_values(
            direction="YES",
            ec_side_probability=0.75,
            market_yes_probability=0.48,   # raw mid-market; differs from ask
            side_market_price=0.50,         # executable ask
            edge_pct_points=25.0,
        )
        assert result["selectedSideMarketProbability"] == pytest.approx(0.50)

    def test_yes_market_probability_not_substituted_for_ask(self):
        """
        Regression: must not use market_yes_probability for selectedSideMarketProbability.
        When ask (0.50) ≠ market_yes_probability (0.48), the result must be the ask.
        """
        result = _selected_side_values(
            direction="YES",
            ec_side_probability=0.75,
            market_yes_probability=0.48,
            side_market_price=0.50,
            edge_pct_points=25.0,
        )
        assert result["selectedSideMarketProbability"] != pytest.approx(0.48)

    def test_yes_ask_equals_mkt_prob(self):
        """selectedSideAsk and selectedSideMarketProbability must be identical."""
        result = _selected_side_values(
            direction="YES",
            ec_side_probability=0.70,
            market_yes_probability=0.45,
            side_market_price=0.50,
            edge_pct_points=20.0,
        )
        assert result["selectedSideAsk"] == pytest.approx(result["selectedSideMarketProbability"])

    def test_yes_model_probability_passthrough(self):
        result = _selected_side_values(
            direction="YES",
            ec_side_probability=0.73,
            market_yes_probability=0.48,
            side_market_price=0.50,
            edge_pct_points=23.0,
        )
        assert result["selectedSideModelProbability"] == pytest.approx(0.73)

    def test_yes_edge_passthrough(self):
        result = _selected_side_values(
            direction="YES",
            ec_side_probability=0.70,
            market_yes_probability=0.45,
            side_market_price=0.50,
            edge_pct_points=25.0,
        )
        assert result["selectedSideEdgePctPoints"] == pytest.approx(25.0)


# ── NO trades ─────────────────────────────────────────────────────────────────

class TestSelectedSideValuesNO:
    """
    NO trade: selectedSideMarketProbability = no_ask = side_market_price.
    Must NOT equal 1 − market_yes_probability when they differ.
    """

    def test_no_selected_side_label(self):
        result = _selected_side_values(
            direction="NO",
            ec_side_probability=0.75,
            market_yes_probability=0.35,
            side_market_price=0.67,
            edge_pct_points=10.0,
        )
        assert result["selectedSide"] == "NO"

    def test_no_selected_side_mkt_prob_equals_ask(self):
        """
        selectedSideMarketProbability must equal the NO ask (side_market_price=0.67).
        1 − market_yes_probability = 1 − 0.35 = 0.65, which differs from the ask.
        The ask must win.
        """
        result = _selected_side_values(
            direction="NO",
            ec_side_probability=0.75,
            market_yes_probability=0.35,   # 1 − 0.35 = 0.65
            side_market_price=0.67,         # actual NO ask; explicitly ≠ 0.65
            edge_pct_points=10.0,
        )
        assert result["selectedSideMarketProbability"] == pytest.approx(0.67)

    def test_no_complement_not_used(self):
        """
        Regression: 1 − market_yes_probability must NOT be used.
        With market_yes_probability=0.35 and NO ask=0.67, the complement is 0.65.
        The result must be 0.67 (ask), not 0.65 (complement).
        """
        result = _selected_side_values(
            direction="NO",
            ec_side_probability=0.75,
            market_yes_probability=0.35,
            side_market_price=0.67,
            edge_pct_points=10.0,
        )
        complement = 1.0 - 0.35   # = 0.65
        assert result["selectedSideMarketProbability"] != pytest.approx(complement), (
            "selectedSideMarketProbability must be the NO ask (0.67), not 1−yes_prob (0.65)"
        )

    def test_no_ask_equals_mkt_prob(self):
        """selectedSideAsk and selectedSideMarketProbability must be identical for NO too."""
        result = _selected_side_values(
            direction="NO",
            ec_side_probability=0.75,
            market_yes_probability=0.35,
            side_market_price=0.67,
            edge_pct_points=10.0,
        )
        assert result["selectedSideAsk"] == pytest.approx(result["selectedSideMarketProbability"])

    def test_no_model_probability_passthrough(self):
        result = _selected_side_values(
            direction="NO",
            ec_side_probability=0.78,
            market_yes_probability=0.30,
            side_market_price=0.70,
            edge_pct_points=8.0,
        )
        assert result["selectedSideModelProbability"] == pytest.approx(0.78)

    def test_no_edge_passthrough(self):
        result = _selected_side_values(
            direction="NO",
            ec_side_probability=0.78,
            market_yes_probability=0.30,
            side_market_price=0.70,
            edge_pct_points=8.0,
        )
        assert result["selectedSideEdgePctPoints"] == pytest.approx(8.0)


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestSelectedSideEdgeCases:
    def test_ask_zero(self):
        """Ask=0 → selectedSideMarketProbability=0 for both directions."""
        yes_result = _selected_side_values("YES", 0.1, 0.0, 0.0, 9.0)
        no_result  = _selected_side_values("NO",  0.9, 0.0, 0.0, 9.0)
        assert yes_result["selectedSideMarketProbability"] == pytest.approx(0.0)
        assert no_result["selectedSideMarketProbability"]  == pytest.approx(0.0)

    def test_ask_one(self):
        """Ask=1 → selectedSideMarketProbability=1 for both directions."""
        yes_result = _selected_side_values("YES", 0.9, 1.0, 1.0, 9.0)
        no_result  = _selected_side_values("NO",  0.1, 1.0, 1.0, 9.0)
        assert yes_result["selectedSideMarketProbability"] == pytest.approx(1.0)
        assert no_result["selectedSideMarketProbability"]  == pytest.approx(1.0)

    def test_ask_is_canonical_regardless_of_yes_prob(self):
        """
        When market_yes_probability and side_market_price differ, the ask wins
        for both YES and NO directions.
        """
        # YES: yes_prob=0.45, yes_ask=0.50
        yes_result = _selected_side_values("YES", 0.70, 0.45, 0.50, 20.0)
        assert yes_result["selectedSideMarketProbability"] == pytest.approx(0.50)
        assert yes_result["selectedSideMarketProbability"] != pytest.approx(0.45)

        # NO: yes_prob=0.35, no_ask=0.67 (complement would be 0.65)
        no_result = _selected_side_values("NO", 0.70, 0.35, 0.67, 20.0)
        assert no_result["selectedSideMarketProbability"] == pytest.approx(0.67)
        assert no_result["selectedSideMarketProbability"] != pytest.approx(0.65)

    def test_required_keys_present(self):
        """All five specified response keys must be present."""
        result = _selected_side_values("YES", 0.7, 0.5, 0.5, 20.0)
        for key in [
            "selectedSide", "selectedSideModelProbability", "selectedSideAsk",
            "selectedSideMarketProbability", "selectedSideEdgePctPoints",
        ]:
            assert key in result, f"Missing key: {key}"
