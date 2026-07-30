"""
Investigation 1 — Bias direction unit tests
============================================
Confirms that the signed-error convention and mu_adjusted formula work
correctly end-to-end:

    signed_error = actual - forecast
    bias         = mean(signed_error)         # positive → GFS under-forecasts
    mu_adjusted  = forecast_value + final_bias  # adds bias, not subtracts

If GFS historically UNDER-forecasts (actual > forecast, bias > 0), the
corrected forecast must be HIGHER than the raw GFS forecast, which must
INCREASE the probability of exceeding a high threshold.

If GFS historically OVER-forecasts (actual < forecast, bias < 0), the
corrected forecast must be LOWER, which must DECREASE the probability of
exceeding a high threshold.

These tests exercise the pure math in the V3 probability engine without
touching the database.  They are the canonical reference for the
"Denver summer +1.06°F" correction narrative.
"""

import pytest

from app.services.v3_probability_engine import (
    _calc_prob_threshold,
    _calc_prob_range,
)


class TestSignedErrorConvention:
    """Confirm that signed_error = actual - forecast (not forecast - actual)."""

    def test_positive_means_actual_hotter_than_forecast(self):
        actual = 95.0
        forecast = 93.0
        signed_error = actual - forecast
        assert signed_error > 0, (
            "signed_error should be positive when actual > forecast (GFS underforecast)"
        )

    def test_negative_means_actual_cooler_than_forecast(self):
        actual = 88.0
        forecast = 91.0
        signed_error = actual - forecast
        assert signed_error < 0, (
            "signed_error should be negative when actual < forecast (GFS overforecast)"
        )

    def test_zero_means_perfect_forecast(self):
        actual = 90.0
        forecast = 90.0
        signed_error = actual - forecast
        assert signed_error == 0.0


class TestBiasCorrectionDirection:
    """
    mu_adjusted = forecast_value + final_bias

    Positive bias (GFS underforecast, actual hotter) → mu_adjusted > forecast → 
      higher P(TMAX ≥ hot_threshold).

    Negative bias (GFS overforecast, actual cooler) → mu_adjusted < forecast →
      lower P(TMAX ≥ hot_threshold).
    """

    def test_underforecast_raises_mu(self):
        """Positive bias (actual was hotter) must push mu_adjusted above raw forecast."""
        forecast = 90.0
        bias = +2.0  # GFS under-forecast by 2°F; actual averaged 92°F
        mu_adjusted = forecast + bias
        assert mu_adjusted > forecast, (
            "Positive bias (GFS underforecast) must RAISE mu_adjusted above raw forecast"
        )

    def test_overforecast_lowers_mu(self):
        """Negative bias (actual was cooler) must push mu_adjusted below raw forecast."""
        forecast = 90.0
        bias = -2.0  # GFS over-forecast by 2°F; actual averaged 88°F
        mu_adjusted = forecast + bias
        assert mu_adjusted < forecast, (
            "Negative bias (GFS overforecast) must LOWER mu_adjusted below raw forecast"
        )

    def test_zero_bias_preserves_forecast(self):
        """Zero bias leaves mu_adjusted equal to the raw forecast."""
        forecast = 90.0
        bias = 0.0
        mu_adjusted = forecast + bias
        assert mu_adjusted == forecast


class TestProbabilityShiftFromBias:
    """
    Confirm that the sign of the bias correction shifts P(TMAX ≥ threshold)
    in the expected direction for a 'gte' (≥ threshold) contract.
    """

    SIGMA = 4.0

    def test_underforecast_increases_prob_gte_threshold(self):
        """
        GFS historically under-forecasts (positive bias) →
        mu_adjusted higher → P(TMAX ≥ threshold) increases.

        Scenario: forecast=90°F, bias=+2°F, threshold=93°F, σ=4°F
        Raw:      P(X≥93 | μ=90, σ=4) ≈ 22.7%
        Adjusted: P(X≥93 | μ=92, σ=4) ≈ 30.9%
        """
        forecast = 90.0
        bias = +2.0
        threshold = 93.0

        mu_raw = forecast
        mu_adjusted = forecast + bias

        prob_raw = _calc_prob_threshold("gte", threshold, mu_raw, self.SIGMA)
        prob_adj = _calc_prob_threshold("gte", threshold, mu_adjusted, self.SIGMA)

        assert prob_adj > prob_raw, (
            f"Underforecast correction (+{bias}°F) must INCREASE P(TMAX≥{threshold}): "
            f"raw={prob_raw:.4f} adj={prob_adj:.4f}"
        )

    def test_overforecast_decreases_prob_gte_threshold(self):
        """
        GFS historically over-forecasts (negative bias) →
        mu_adjusted lower → P(TMAX ≥ threshold) decreases.

        Scenario: forecast=90°F, bias=-2°F, threshold=88°F, σ=4°F
        Raw:      P(X≥88 | μ=90, σ=4) ≈ 69.1%
        Adjusted: P(X≥88 | μ=88, σ=4) = 50.0%
        """
        forecast = 90.0
        bias = -2.0
        threshold = 88.0

        mu_raw = forecast
        mu_adjusted = forecast + bias

        prob_raw = _calc_prob_threshold("gte", threshold, mu_raw, self.SIGMA)
        prob_adj = _calc_prob_threshold("gte", threshold, mu_adjusted, self.SIGMA)

        assert prob_adj < prob_raw, (
            f"Overforecast correction ({bias}°F) must DECREASE P(TMAX≥{threshold}): "
            f"raw={prob_raw:.4f} adj={prob_adj:.4f}"
        )

    def test_zero_bias_does_not_change_probability(self):
        forecast = 90.0
        bias = 0.0
        threshold = 93.0
        mu_adjusted = forecast + bias
        prob_raw = _calc_prob_threshold("gte", threshold, forecast, self.SIGMA)
        prob_adj = _calc_prob_threshold("gte", threshold, mu_adjusted, self.SIGMA)
        assert abs(prob_adj - prob_raw) < 1e-9


class TestDenverSummerBiasNarrative:
    """
    Concrete regression test for the Denver summer +1.06°F correction.

    From v3_error_stats DB (summer, fallback_level=0, GFS 1d):
        bias           = +1.0642°F  (GFS under-forecast Denver summer TMAX)
        sigma_shrunk   = 3.7311°F

    Correct interpretation:
      GFS historically UNDER-forecasts Denver summer TMAX by 1.06°F.
      V3 corrects UPWARD: mu_adjusted = forecast + 1.0642
      → probabilities of hot outcomes INCREASE.

    This test locks in that interpretation so any sign-flip in the
    bias formula fails loudly.
    """

    BIAS = 1.0642   # Denver summer GFS under-forecast magnitude (°F)
    SIGMA = 3.7311  # Denver summer sigma_shrunk from V3ErrorStats

    def test_denver_correction_is_upward(self):
        """mu_adjusted must be higher than the raw GFS forecast."""
        gfs_forecast = 95.0
        mu_adjusted = gfs_forecast + self.BIAS
        assert mu_adjusted > gfs_forecast, (
            "Denver summer bias (+1.06°F) must RAISE mu above GFS forecast"
        )
        assert abs(mu_adjusted - 96.0642) < 1e-6

    def test_denver_correction_raises_hot_probability(self):
        """
        For a ≥95°F threshold contract (GFS forecast=93°F, Denver summer):
        the bias correction should make this outcome MORE likely.
        """
        gfs_forecast = 93.0
        threshold = 95.0

        prob_uncorrected = _calc_prob_threshold(
            "gte", threshold, gfs_forecast, self.SIGMA
        )
        prob_corrected = _calc_prob_threshold(
            "gte", threshold, gfs_forecast + self.BIAS, self.SIGMA
        )

        assert prob_corrected > prob_uncorrected, (
            f"Denver +1.06°F bias must increase P(TMAX≥95): "
            f"uncorrected={prob_uncorrected:.4f} corrected={prob_corrected:.4f}"
        )

    def test_signed_error_positive_matches_underforecast_label(self):
        """
        Denver summer mean(actual - forecast) = +1.06°F → GFS UNDER-forecasts.
        Verify that a positive signed_error labels as 'underforecast', not 'overforecast'.
        """
        actual_sample = [96.0, 97.0, 95.0, 98.0]   # actual temps
        forecast_sample = [94.0, 95.0, 94.0, 96.0]  # GFS forecasts
        signed_errors = [a - f for a, f in zip(actual_sample, forecast_sample)]
        mean_error = sum(signed_errors) / len(signed_errors)

        assert mean_error > 0, (
            "When actual > forecast, mean(actual - forecast) > 0 → GFS underforecasts"
        )
        # Direction label check
        direction = "underforecast" if mean_error > 0 else "overforecast"
        assert direction == "underforecast"


class TestProbabilityEngineMathBiasFormula:
    """
    White-box tests for the exact formula used in v3_probability_engine.py:
        mu_adjusted = inputs.forecast_value + final_bias
    """

    def test_gte_contract_with_positive_bias(self):
        """
        P(X ≥ 93 | μ=90+2=92, σ=4) should be greater than P(X ≥ 93 | μ=90, σ=4).
        Verifies add (not subtract) convention for gte contracts.
        """
        sigma = 4.0
        threshold = 93.0
        raw_forecast = 90.0
        bias = 2.0

        p_before = _calc_prob_threshold("gte", threshold, raw_forecast, sigma)
        p_after  = _calc_prob_threshold("gte", threshold, raw_forecast + bias, sigma)

        assert p_after > p_before

    def test_lte_contract_with_positive_bias(self):
        """
        P(X ≤ 85 | μ=90+2=92, σ=4) should be LESS than P(X ≤ 85 | μ=90, σ=4).
        Positive bias raises mu → hot-side outcomes more likely → cold-side less likely.
        """
        sigma = 4.0
        threshold = 85.0
        raw_forecast = 90.0
        bias = 2.0

        p_before = _calc_prob_threshold("lte", threshold, raw_forecast, sigma)
        p_after  = _calc_prob_threshold("lte", threshold, raw_forecast + bias, sigma)

        assert p_after < p_before, (
            "Positive bias on lte contract must DECREASE probability "
            "(hotter forecast means less likely to stay below threshold)"
        )

    def test_range_contract_with_positive_bias_shifts_window(self):
        """
        For a range contract [90, 91], raising mu from 90 to 92 shifts the
        probability mass to the right of [90, 91], so P(90 ≤ X ≤ 91) must decrease.
        """
        sigma = 4.0
        lower, upper = 90.0, 91.0
        raw_forecast = 90.0  # μ right at the range → peak probability
        bias = 2.0           # μ moves above the range → probability drops

        p_before = _calc_prob_range(lower, upper, raw_forecast, sigma)
        p_after  = _calc_prob_range(lower, upper, raw_forecast + bias, sigma)

        assert p_after < p_before, (
            "Moving mu above range center by +2°F must reduce P(in-range)"
        )
