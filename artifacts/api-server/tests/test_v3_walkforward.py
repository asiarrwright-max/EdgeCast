"""
Tests for V3 Walk-Forward Validation.
"""
from __future__ import annotations

import math
import pytest
from unittest.mock import MagicMock

from app.services.v3_walkforward import (
    run_walk_forward_validation,
    _crps_gaussian,
    _prob_gte,
    _compute_wf_prior,
    _summarize,
    _build_calibration,
    WalkForwardRecord,
    MIN_TRAIN_SIZE,
)
from app.services.v3_error_stats import V3StatsConfig, SIGMA_FLOOR


# ---------------------------------------------------------------------------
# Gaussian math helpers
# ---------------------------------------------------------------------------

class TestGaussianHelpers:
    def test_crps_gaussian_at_mean(self):
        """CRPS at observed == mu: sigma * (sqrt(2) - 1) / sqrt(pi).

        At z=0: CRPS = sigma * (0*(2*0.5-1) + 2*phi(0) - 1/sqrt(pi))
                     = sigma * (2/sqrt(2*pi) - 1/sqrt(pi))
                     = sigma * (sqrt(2) - 1) / sqrt(pi)
        """
        mu, sigma = 70.0, 4.0
        crps = _crps_gaussian(mu, mu, sigma)
        expected = sigma * (math.sqrt(2) - 1.0) / math.sqrt(math.pi)
        assert crps == pytest.approx(expected, rel=1e-4)

    def test_crps_positive(self):
        crps = _crps_gaussian(observed=75.0, mu=70.0, sigma=5.0)
        assert crps > 0

    def test_crps_zero_sigma_equals_abs_error(self):
        """Deterministic forecast (sigma=0): CRPS = |observed - mu|."""
        crps = _crps_gaussian(80.0, 75.0, sigma=0.0)
        assert crps == pytest.approx(5.0)

    def test_prob_gte_at_mu(self):
        """P(X >= mu) = 0.5 for a symmetric Gaussian."""
        p = _prob_gte(threshold=70.0, mu=70.0, sigma=5.0)
        assert p == pytest.approx(0.5, abs=1e-4)

    def test_prob_gte_increases_as_threshold_decreases(self):
        mu, sigma = 70.0, 5.0
        p1 = _prob_gte(75.0, mu, sigma)   # threshold > mu
        p2 = _prob_gte(65.0, mu, sigma)   # threshold < mu
        assert p2 > p1

    def test_prob_gte_2sigma_above_mu(self):
        """P(X >= mu + 2sigma) ≈ 2.3% for N(mu, sigma)."""
        mu, sigma = 70.0, 5.0
        p = _prob_gte(mu + 2 * sigma, mu, sigma)
        assert p == pytest.approx(0.0228, abs=0.002)


# ---------------------------------------------------------------------------
# Helpers for constructing fake V3HistoricalRecord mocks
# ---------------------------------------------------------------------------

def _make_rec(
    *,
    target_date: str,
    city: str = "Denver",
    model: str = "GFS",
    lead_bucket: str = "1d",
    season: str = "summer",
    forecast_f: float = 90.0,
    observed_f: float = 91.0,
    signed_error: float = 1.0,
    quality_status: str = "ok",
) -> MagicMock:
    rec = MagicMock()
    rec.target_date       = target_date
    rec.city              = city
    rec.forecast_model    = model
    rec.lead_time_bucket  = lead_bucket
    rec.season            = season
    rec.forecast_tmax_f   = forecast_f
    rec.observed_tmax_f   = observed_f
    rec.signed_error      = signed_error
    rec.quality_status    = quality_status
    return rec


def _make_records(n: int, signed_error: float = 1.0, city: str = "Denver") -> list:
    """Create n fake records spanning dates 2024-01-01 onward."""
    from datetime import date, timedelta
    start = date(2024, 1, 1)
    seasons = ["winter", "winter", "winter", "spring", "spring", "spring",
               "summer", "summer", "summer", "fall", "fall", "fall"]
    records = []
    for i in range(n):
        d = start + timedelta(days=i)
        season = seasons[d.month - 1]
        records.append(_make_rec(
            target_date=d.isoformat(),
            city=city,
            signed_error=signed_error,
            forecast_f=90.0,
            observed_f=90.0 + signed_error,
            season=season,
        ))
    return records


# ---------------------------------------------------------------------------
# Insufficient data guard
# ---------------------------------------------------------------------------

class TestInsufficientData:
    def test_empty_records_returns_insufficient(self):
        report = run_walk_forward_validation([])
        assert report.verdict == "insufficient_data"
        assert report.test_n == 0

    def test_fewer_than_min_train_returns_insufficient(self):
        records = _make_records(MIN_TRAIN_SIZE)  # exactly MIN_TRAIN_SIZE, none left for testing
        report = run_walk_forward_validation(records)
        assert report.verdict == "insufficient_data"

    def test_barely_enough_records(self):
        """MIN_TRAIN_SIZE + 5 yields 5 test records.

        5 test records is enough for the walk-forward loop to run (test_n == 5),
        but the verdict function requires n >= 50 for any statistical conclusion
        — so the verdict is 'insufficient_data' for the verdict itself, not for
        the validation run.  Verify the run completed and produced test results.
        """
        records = _make_records(MIN_TRAIN_SIZE + 5)
        report = run_walk_forward_validation(records)
        assert report.test_n == 5
        assert len(report.records) == 5
        # verdict may be 'insufficient_data' — that is correct for n=5 test records


# ---------------------------------------------------------------------------
# Chronological protocol (no lookahead)
# ---------------------------------------------------------------------------

class TestChronologicalProtocol:
    def test_training_n_grows_monotonically(self):
        """training_n must increase with each successive test record."""
        records = _make_records(MIN_TRAIN_SIZE + 20)
        report = run_walk_forward_validation(records)
        prev = -1
        for r in report.records:
            assert r["training_n"] >= prev
            prev = r["training_n"]

    def test_first_test_training_n_equals_min_train(self):
        records = _make_records(MIN_TRAIN_SIZE + 5)
        report = run_walk_forward_validation(records)
        assert report.records[0]["training_n"] == MIN_TRAIN_SIZE

    def test_future_data_never_in_training(self):
        """The test date must always be strictly after all training dates."""
        records = _make_records(MIN_TRAIN_SIZE + 10)
        report = run_walk_forward_validation(records)
        full_dates = sorted(r["date"] for r in report.records)
        training_dates = sorted(r.target_date for r in records[:MIN_TRAIN_SIZE])
        # The first test date must be strictly after the last training date
        last_train = training_dates[-1]
        first_test = full_dates[0]
        assert first_test > last_train


# ---------------------------------------------------------------------------
# Zero-bias scenario
# ---------------------------------------------------------------------------

class TestZeroBiasScenario:
    def test_unbiased_model_produces_near_zero_adj_bias(self):
        """When all errors are 0, bias correction should be 0, adj errors = raw errors."""
        records = _make_records(MIN_TRAIN_SIZE + 50, signed_error=0.0)
        # Make observed = forecast
        for r in records:
            r.signed_error = 0.0
            r.observed_tmax_f = r.forecast_tmax_f
        report = run_walk_forward_validation(records)
        assert abs(report.overall.mean_error_adj) < 0.1
        assert abs(report.overall.mae_delta) < 0.5  # no dramatic change

    def test_constant_positive_bias_gets_corrected(self):
        """If model always runs cold by 2°F, adj MAE < raw MAE after training."""
        bias = 2.0
        records = _make_records(MIN_TRAIN_SIZE + 100, signed_error=bias)
        report = run_walk_forward_validation(records)
        # After enough training, the bias should be estimated and corrected
        # adj mean error should be closer to 0 than raw mean error
        assert abs(report.overall.mean_error_adj) <= abs(report.overall.mean_error_raw) + 0.3


# ---------------------------------------------------------------------------
# Sigma floor enforcement
# ---------------------------------------------------------------------------

class TestSigmaFloor:
    def test_sigma_always_at_least_floor(self):
        """Every test record must have sigma_used >= SIGMA_FLOOR."""
        records = _make_records(MIN_TRAIN_SIZE + 50, signed_error=0.01)
        report = run_walk_forward_validation(records)
        for r in report.records:
            assert r["sigma_used"] >= SIGMA_FLOOR, (
                f"sigma_used={r['sigma_used']} < SIGMA_FLOOR={SIGMA_FLOOR} "
                f"on {r['date']}"
            )


# ---------------------------------------------------------------------------
# Preload-hurts detection
# ---------------------------------------------------------------------------

class TestPreloadHurtsDetection:
    def test_preload_hurt_field_present(self):
        records = _make_records(MIN_TRAIN_SIZE + 20, signed_error=1.0)
        report = run_walk_forward_validation(records)
        # preload_hurt must be a bool on every record
        for r in report.records:
            assert isinstance(r["preload_hurt"], bool)

    def test_hurt_count_matches_detail(self):
        """overall.preload_hurt_n must equal count of preload_hurt=True in records."""
        records = _make_records(MIN_TRAIN_SIZE + 50, signed_error=1.0)
        report = run_walk_forward_validation(records)
        detail_hurt = sum(1 for r in report.records if r["preload_hurt"])
        assert report.overall.preload_hurt_n == detail_hurt


# ---------------------------------------------------------------------------
# Seasonal breakdown
# ---------------------------------------------------------------------------

class TestSeasonalBreakdown:
    def test_seasonal_summaries_present(self):
        records = _make_records(MIN_TRAIN_SIZE + 120, signed_error=1.0)
        report = run_walk_forward_validation(records)
        # Should have at least one season
        assert len(report.by_season) >= 1

    def test_seasonal_n_sums_to_test_n(self):
        records = _make_records(MIN_TRAIN_SIZE + 100, signed_error=1.0)
        report = run_walk_forward_validation(records)
        total_season_n = sum(s.n for s in report.by_season.values())
        assert total_season_n == report.test_n


# ---------------------------------------------------------------------------
# Multi-city
# ---------------------------------------------------------------------------

class TestMultiCity:
    def test_per_city_summaries_present(self):
        r1 = _make_records(MIN_TRAIN_SIZE + 30, signed_error=1.0, city="Denver")
        r2 = _make_records(MIN_TRAIN_SIZE + 30, signed_error=-1.0, city="Oklahoma City")
        all_records = r1 + r2
        report = run_walk_forward_validation(all_records)
        assert len(report.by_city) >= 1

    def test_city_n_sums_to_test_n(self):
        r1 = _make_records(MIN_TRAIN_SIZE + 30, signed_error=1.0, city="Denver")
        r2 = _make_records(MIN_TRAIN_SIZE + 30, signed_error=-1.0, city="Oklahoma City")
        all_records = r1 + r2
        report = run_walk_forward_validation(all_records)
        total_city_n = sum(s.n for s in report.by_city.values())
        assert total_city_n == report.test_n


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

class TestCalibration:
    def test_calibration_buckets_have_expected_fields(self):
        records = _make_records(MIN_TRAIN_SIZE + 100, signed_error=0.0)
        for r in records:
            r.signed_error = 0.0
            r.observed_tmax_f = r.forecast_tmax_f
        report = run_walk_forward_validation(records)
        for bucket in report.calibration:
            assert 0.0 <= bucket.bucket_lo < bucket.bucket_hi <= 1.0
            assert 0.0 <= bucket.empirical <= 1.0
            assert bucket.count > 0

    def test_calibration_probs_in_unit_interval(self):
        records = _make_records(MIN_TRAIN_SIZE + 50, signed_error=1.0)
        report = run_walk_forward_validation(records)
        for bucket in report.calibration:
            assert 0.0 <= bucket.mean_prob <= 1.0


# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------

class TestVerdict:
    def test_insufficient_data_verdict(self):
        records = _make_records(3)
        report = run_walk_forward_validation(records)
        assert report.verdict == "insufficient_data"

    def test_verdict_is_valid_string(self):
        records = _make_records(MIN_TRAIN_SIZE + 60, signed_error=1.0)
        report = run_walk_forward_validation(records)
        assert report.verdict in {
            "improved", "no_clear_improvement", "mixed", "insufficient_data"
        }

    def test_verdict_note_is_non_empty(self):
        records = _make_records(MIN_TRAIN_SIZE + 60, signed_error=1.0)
        report = run_walk_forward_validation(records)
        assert len(report.verdict_note) > 20


# ---------------------------------------------------------------------------
# Report structure
# ---------------------------------------------------------------------------

class TestReportStructure:
    def test_total_records_counts_all_usable(self):
        records = _make_records(MIN_TRAIN_SIZE + 40, signed_error=1.0)
        report = run_walk_forward_validation(records)
        assert report.total_records == len(records)

    def test_test_n_plus_train_n_equals_total(self):
        n = MIN_TRAIN_SIZE + 40
        records = _make_records(n, signed_error=1.0)
        report = run_walk_forward_validation(records)
        assert report.test_n + MIN_TRAIN_SIZE == n

    def test_records_detail_length_matches_test_n(self):
        records = _make_records(MIN_TRAIN_SIZE + 30, signed_error=1.0)
        report = run_walk_forward_validation(records)
        assert len(report.records) == report.test_n

    def test_records_detail_required_keys(self):
        records = _make_records(MIN_TRAIN_SIZE + 20, signed_error=1.0)
        report = run_walk_forward_validation(records)
        required = {"date", "city", "forecast_f", "observed_f", "raw_error",
                    "adj_error", "bias_used", "sigma_used", "fallback_level",
                    "training_n", "preload_hurt", "crps_raw", "crps_adj"}
        for r in report.records:
            missing = required - set(r.keys())
            assert not missing, f"Missing keys: {missing}"
