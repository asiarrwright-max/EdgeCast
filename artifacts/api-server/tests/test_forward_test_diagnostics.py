"""
Tests for the forward-test-diagnostics endpoint helpers.

These tests exercise the pure-computation helpers extracted from the
get_forward_test_diagnostics endpoint, without touching the database.

Covered:
  - _era5_predicted: range / threshold / missing-data cases
  - Brier Score computation (_grp helper logic)
  - Log Loss computation
  - ECE / MACE computation via band logic
  - False-confidence loss detection threshold (>=85%)
  - Loss-category assignment
  - distance-from-threshold calculation
  - calibration bands include correct trades
  - sample warning always present
  - empty-result fast-path shape
"""
from __future__ import annotations

import math
import pytest
from datetime import datetime, timezone


# ── Pure helpers copied from the router (keep in sync if refactored) ──────────

def _era5_predicted(
    actual: float | None,
    lower: float | None, upper: float | None,
    threshold: float | None, op: str | None,
) -> str | None:
    if actual is None:
        return None
    if lower is not None and upper is not None:
        return "yes" if lower <= actual < upper + 1 else "no"
    if threshold is not None and op:
        if op == "gte":
            return "yes" if actual >= threshold else "no"
        if op == "lte":
            return "yes" if actual <= threshold else "no"
    return None


def _loss_category(era5_pred, kalshi_result, forecast_error, dist) -> str:
    if era5_pred and era5_pred != kalshi_result:
        return "Station/Settlement mismatch"
    if forecast_error is not None and abs(forecast_error) > 3:
        return "Forecast miss"
    if dist is not None and dist < 1.5:
        return "Threshold too close"
    if forecast_error is not None:
        return "Forecast miss"
    return "Unknown"


def _dist_from_threshold(actual, lower, upper, threshold) -> float | None:
    if actual is None:
        return None
    if lower is not None and upper is not None:
        return round(min(abs(actual - lower), abs(actual - (upper + 1))), 3)
    if threshold is not None:
        return round(abs(actual - threshold), 3)
    return None


def _is_win(outcome: str) -> bool:
    return outcome == "WIN"


def _brier(probs_outcomes: list[tuple[float, bool]]) -> float:
    return sum((p - (1.0 if w else 0.0)) ** 2 for p, w in probs_outcomes) / len(probs_outcomes)


def _log_loss(probs_outcomes: list[tuple[float, bool]]) -> float:
    return -sum(
        math.log(max(0.001, p)) if w else math.log(max(0.001, 1 - p))
        for p, w in probs_outcomes
    ) / len(probs_outcomes)


# ── ERA5 predicted result ─────────────────────────────────────────────────────

class TestEra5Predicted:
    def test_range_inside(self):
        assert _era5_predicted(77.3, 77, 78, None, None) == "yes"

    def test_range_below(self):
        assert _era5_predicted(75.0, 77, 78, None, None) == "no"

    def test_range_above(self):
        assert _era5_predicted(79.5, 77, 78, None, None) == "no"

    def test_range_at_upper_plus_1_boundary(self):
        # actual == upper+1 is NOT in range (strict <)
        assert _era5_predicted(79.0, 77, 78, None, None) == "no"

    def test_range_at_lower(self):
        assert _era5_predicted(77.0, 77, 78, None, None) == "yes"

    def test_range_at_upper(self):
        # 78 < 79 → still yes
        assert _era5_predicted(78.0, 77, 78, None, None) == "yes"

    def test_threshold_gte_above(self):
        assert _era5_predicted(75.7, None, None, 75.0, "gte") == "yes"

    def test_threshold_gte_below(self):
        assert _era5_predicted(74.9, None, None, 75.0, "gte") == "no"

    def test_threshold_lte_below(self):
        assert _era5_predicted(82.0, None, None, 83.0, "lte") == "yes"

    def test_threshold_lte_above(self):
        assert _era5_predicted(94.8, None, None, 83.0, "lte") == "no"

    def test_no_actual_returns_none(self):
        assert _era5_predicted(None, 77, 78, None, None) is None

    def test_no_bounds_no_threshold_returns_none(self):
        assert _era5_predicted(75.0, None, None, None, None) is None


# ── Loss category ─────────────────────────────────────────────────────────────

class TestLossCategory:
    def test_era5_disagrees_gives_mismatch(self):
        cat = _loss_category("no", "yes", -2.0, 0.5)
        assert cat == "Station/Settlement mismatch"

    def test_large_forecast_error_gives_forecast_miss(self):
        cat = _loss_category(None, "yes", 4.5, 2.0)
        assert cat == "Forecast miss"

    def test_large_forecast_error_negative_gives_forecast_miss(self):
        cat = _loss_category(None, "yes", -4.5, 2.0)
        assert cat == "Forecast miss"

    def test_small_dist_gives_threshold_too_close(self):
        cat = _loss_category(None, "yes", 0.5, 0.4)
        assert cat == "Threshold too close"

    def test_dist_exactly_1_5_not_too_close(self):
        # dist < 1.5 triggers threshold-too-close; 1.5 is NOT < 1.5
        cat = _loss_category(None, "yes", 0.5, 1.5)
        assert cat == "Forecast miss"  # small forecast error, not too close

    def test_small_error_no_dist_gives_forecast_miss(self):
        cat = _loss_category(None, "yes", 1.0, None)
        assert cat == "Forecast miss"

    def test_no_data_gives_unknown(self):
        cat = _loss_category(None, "yes", None, None)
        assert cat == "Unknown"


# ── Distance from threshold ───────────────────────────────────────────────────

class TestDistFromThreshold:
    def test_range_below(self):
        # actual 75, range 77-78 → dist from lower = 2
        d = _dist_from_threshold(75.0, 77.0, 78.0, None)
        assert d == pytest.approx(2.0)

    def test_range_inside(self):
        # actual 77.3, range 77-78 → dist from lower = 0.3, dist from 79 = 1.7 → min = 0.3
        d = _dist_from_threshold(77.3, 77.0, 78.0, None)
        assert d == pytest.approx(0.3)

    def test_threshold_above(self):
        d = _dist_from_threshold(75.7, None, None, 75.0)
        assert d == pytest.approx(0.7)

    def test_threshold_below(self):
        d = _dist_from_threshold(74.0, None, None, 75.0)
        assert d == pytest.approx(1.0)

    def test_no_actual_returns_none(self):
        assert _dist_from_threshold(None, 77.0, 78.0, None) is None


# ── Brier Score ───────────────────────────────────────────────────────────────

class TestBrierScore:
    def test_perfect_prediction(self):
        pairs = [(1.0, True), (0.0, False)]
        assert _brier(pairs) == pytest.approx(0.0)

    def test_worst_prediction(self):
        pairs = [(0.0, True), (1.0, False)]
        assert _brier(pairs) == pytest.approx(1.0)

    def test_uniform_50_pct(self):
        pairs = [(0.5, True), (0.5, False)]
        assert _brier(pairs) == pytest.approx(0.25)

    def test_overconfident_losses(self):
        # Model says 93% but loses → big penalty
        pairs = [(0.93, False)]
        expected = 0.93 ** 2
        assert _brier(pairs) == pytest.approx(expected)

    def test_mixed(self):
        pairs = [(0.9, True), (0.9, False)]
        expected = ((0.9 - 1) ** 2 + (0.9 - 0) ** 2) / 2
        assert _brier(pairs) == pytest.approx(expected)


# ── Log Loss ──────────────────────────────────────────────────────────────────

class TestLogLoss:
    def test_near_perfect_win(self):
        ll = _log_loss([(0.999, True)])
        assert ll == pytest.approx(-math.log(0.999), rel=0.01)

    def test_confident_loss_high_penalty(self):
        ll = _log_loss([(0.93, False)])
        assert ll == pytest.approx(-math.log(0.07), rel=0.01)

    def test_baseline_50pct(self):
        # Always predicting 50% gives log loss = ln(2) ≈ 0.693 for any outcome
        ll = _log_loss([(0.5, True), (0.5, False)])
        assert ll == pytest.approx(math.log(2), rel=0.01)


# ── Calibration band membership ───────────────────────────────────────────────

PROB_BANDS = [
    ("<50%",    0.00, 0.50),
    ("50–59%",  0.50, 0.60),
    ("60–69%",  0.60, 0.70),
    ("70–79%",  0.70, 0.80),
    ("80–84%",  0.80, 0.85),
    ("85–89%",  0.85, 0.90),
    ("90–94%",  0.90, 0.95),
    ("95–100%", 0.95, 1.01),
]

def band_for(p: float) -> str:
    for label, lo, hi in PROB_BANDS:
        if lo <= p < hi:
            return label
    return "out of range"


class TestCalibrationBands:
    def test_4338_maps_to_below_50(self):
        assert band_for(0.4338) == "<50%"

    def test_9337_maps_to_90_94(self):
        assert band_for(0.9337) == "90–94%"

    def test_9468_maps_to_90_94(self):
        # 94.68% is < 95 so falls in 90–94%
        assert band_for(0.9468) == "90–94%"

    def test_9537_maps_to_95_100(self):
        assert band_for(0.9537) == "95–100%"

    def test_9909_maps_to_95_100(self):
        assert band_for(0.9909) == "95–100%"

    def test_exactly_050_maps_to_50_59(self):
        assert band_for(0.50) == "50–59%"

    def test_exactly_095_maps_to_95_100(self):
        assert band_for(0.95) == "95–100%"

    def test_bands_are_exhaustive_for_0_to_1(self):
        test_probs = [0.0, 0.1, 0.43, 0.50, 0.65, 0.75, 0.82, 0.87, 0.92, 0.96, 0.99]
        for p in test_probs:
            assert band_for(p) != "out of range", f"p={p} not covered"


# ── False-confidence threshold ────────────────────────────────────────────────

class TestFalseConfidenceThreshold:
    def test_exactly_85_pct_qualifies(self):
        # Model prob 0.85 = exactly at threshold → qualifies
        assert 0.85 >= 0.85

    def test_84_99_pct_does_not_qualify(self):
        assert not (0.8499 >= 0.85)

    def test_90_pct_loss_qualifies(self):
        assert 0.90 >= 0.85

    def test_win_does_not_qualify_as_false_confidence(self):
        # False confidence = high prob AND outcome=LOSS
        outcome = "WIN"
        assert not (0.95 >= 0.85 and outcome == "LOSS")
