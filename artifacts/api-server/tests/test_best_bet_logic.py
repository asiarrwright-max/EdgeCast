"""
tests/test_best_bet_logic.py
Regression tests for best-bet-today selected-side probability logic.

Rules enforced:
  YES trade → selectedSideMarketProbability = market_yes_probability
  NO  trade → selectedSideMarketProbability = 1 − market_yes_probability
  selectedSideAsk = side_market_price (the entry price on our side — never bid or opposite side)
  selectedSideModelProbability = ec_side_probability (already rotated to chosen side)
  whyWeLikeThisTrade must reference selected-side values only
"""
from __future__ import annotations

import pytest
from app.routers.paper_trades import _selected_side_values


class TestSelectedSideValuesYES:
    """YES trade: market probability and ask come from the YES side directly."""

    def test_yes_selected_side_label(self):
        result = _selected_side_values(
            direction="YES",
            ec_side_probability=0.75,
            market_yes_probability=0.50,
            side_market_price=0.50,
            edge_pct_points=25.0,
        )
        assert result["selectedSide"] == "YES"

    def test_yes_market_probability_is_yes_prob(self):
        """For YES trades the selected-side market prob equals market_yes_probability."""
        result = _selected_side_values(
            direction="YES",
            ec_side_probability=0.75,
            market_yes_probability=0.50,
            side_market_price=0.50,
            edge_pct_points=25.0,
        )
        assert result["selectedSideMarketProbability"] == pytest.approx(0.50)

    def test_yes_model_probability_passthrough(self):
        """ec_side_probability is passed through unchanged."""
        result = _selected_side_values(
            direction="YES",
            ec_side_probability=0.73,
            market_yes_probability=0.48,
            side_market_price=0.48,
            edge_pct_points=25.0,
        )
        assert result["selectedSideModelProbability"] == pytest.approx(0.73)

    def test_yes_ask_is_side_market_price(self):
        """selectedSideAsk must equal side_market_price, not any opposite-side price."""
        result = _selected_side_values(
            direction="YES",
            ec_side_probability=0.70,
            market_yes_probability=0.45,
            side_market_price=0.45,
            edge_pct_points=25.0,
        )
        assert result["selectedSideAsk"] == pytest.approx(0.45)

    def test_yes_edge_passthrough(self):
        result = _selected_side_values(
            direction="YES",
            ec_side_probability=0.70,
            market_yes_probability=0.45,
            side_market_price=0.45,
            edge_pct_points=25.0,
        )
        assert result["selectedSideEdgePctPoints"] == pytest.approx(25.0)


class TestSelectedSideValuesNO:
    """NO trade: market probability must be 1 − market_yes_probability."""

    def test_no_selected_side_label(self):
        result = _selected_side_values(
            direction="NO",
            ec_side_probability=0.75,
            market_yes_probability=0.35,
            side_market_price=0.65,
            edge_pct_points=10.0,
        )
        assert result["selectedSide"] == "NO"

    def test_no_market_probability_is_complement(self):
        """
        For NO trades, selected-side market probability = 1 − market_yes_probability.
        market_yes_probability = 0.35 → NO probability = 0.65.
        """
        result = _selected_side_values(
            direction="NO",
            ec_side_probability=0.75,
            market_yes_probability=0.35,
            side_market_price=0.65,
            edge_pct_points=10.0,
        )
        assert result["selectedSideMarketProbability"] == pytest.approx(0.65)

    def test_no_market_probability_never_uses_yes_prob(self):
        """
        Regression: if we naively used market_yes_probability for a NO trade
        we would get 0.35 instead of 0.65.  This test would fail with the old code.
        """
        result = _selected_side_values(
            direction="NO",
            ec_side_probability=0.75,
            market_yes_probability=0.35,
            side_market_price=0.65,
            edge_pct_points=10.0,
        )
        # Must NOT equal market_yes_probability (the bug we're guarding against)
        assert result["selectedSideMarketProbability"] != pytest.approx(0.35)

    def test_no_model_probability_passthrough(self):
        """ec_side_probability for NO trades is the NO model probability — passed through."""
        result = _selected_side_values(
            direction="NO",
            ec_side_probability=0.78,
            market_yes_probability=0.30,
            side_market_price=0.70,
            edge_pct_points=8.0,
        )
        assert result["selectedSideModelProbability"] == pytest.approx(0.78)

    def test_no_ask_is_side_market_price(self):
        """selectedSideAsk must be the NO ask (side_market_price), not the YES ask."""
        result = _selected_side_values(
            direction="NO",
            ec_side_probability=0.78,
            market_yes_probability=0.30,
            side_market_price=0.70,
            edge_pct_points=8.0,
        )
        assert result["selectedSideAsk"] == pytest.approx(0.70)

    def test_no_edge_passthrough(self):
        result = _selected_side_values(
            direction="NO",
            ec_side_probability=0.78,
            market_yes_probability=0.30,
            side_market_price=0.70,
            edge_pct_points=8.0,
        )
        assert result["selectedSideEdgePctPoints"] == pytest.approx(8.0)


class TestSelectedSideEdgeCases:
    def test_yes_prob_zero(self):
        """Market_yes_probability = 0 → YES side market prob = 0, NO side = 1."""
        yes_result = _selected_side_values("YES", 0.1, 0.0, 0.01, 9.0)
        no_result  = _selected_side_values("NO",  0.9, 0.0, 0.99, 9.0)
        assert yes_result["selectedSideMarketProbability"] == pytest.approx(0.0)
        assert no_result["selectedSideMarketProbability"]  == pytest.approx(1.0)

    def test_yes_prob_one(self):
        """Market_yes_probability = 1 → YES = 1, NO = 0."""
        yes_result = _selected_side_values("YES", 0.9, 1.0, 0.99, 9.0)
        no_result  = _selected_side_values("NO",  0.1, 1.0, 0.01, 9.0)
        assert yes_result["selectedSideMarketProbability"] == pytest.approx(1.0)
        assert no_result["selectedSideMarketProbability"]  == pytest.approx(0.0)

    def test_symmetric_around_50pct(self):
        """At mkt_yes_prob=0.5, YES and NO selected-side market probs are both 0.5."""
        yes_result = _selected_side_values("YES", 0.6, 0.5, 0.5, 10.0)
        no_result  = _selected_side_values("NO",  0.6, 0.5, 0.5, 10.0)
        assert yes_result["selectedSideMarketProbability"] == pytest.approx(0.5)
        assert no_result["selectedSideMarketProbability"]  == pytest.approx(0.5)

    def test_required_keys_present(self):
        """All five specified response keys must be present."""
        result = _selected_side_values("YES", 0.7, 0.5, 0.5, 20.0)
        for key in [
            "selectedSide", "selectedSideModelProbability", "selectedSideAsk",
            "selectedSideMarketProbability", "selectedSideEdgePctPoints",
        ]:
            assert key in result, f"Missing key: {key}"
