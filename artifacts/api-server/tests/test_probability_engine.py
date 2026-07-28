"""Tests for the probability engine and confidence scoring."""
import math
import pytest
from datetime import datetime, timezone, timedelta
from app.services.probability_engine import (
    sigma_for_lead_time,
    calculate_probability,
    calculate_range_probability,
    market_implied_probability,
    confidence_score,
    run_analysis,
)


class TestSigmaForLeadTime:
    def test_same_day(self):
        assert sigma_for_lead_time(0) == 2.5

    def test_day_1(self):
        assert sigma_for_lead_time(1) == 2.5

    def test_day_2(self):
        assert sigma_for_lead_time(2) == 3.5

    def test_day_3(self):
        assert sigma_for_lead_time(3) == 4.3

    def test_day_5(self):
        assert sigma_for_lead_time(5) == 5.5

    def test_day_7(self):
        assert sigma_for_lead_time(7) == 6.8

    def test_day_10(self):
        assert sigma_for_lead_time(10) == 8.2

    def test_day_11_plus(self):
        assert sigma_for_lead_time(11) == 9.5
        assert sigma_for_lead_time(14) == 9.5


class TestCalculateProbability:
    """P(T >= threshold) and P(T <= threshold) correctness."""

    def test_forecast_equals_threshold_gte_is_50pct(self):
        """When forecast == threshold, P(T >= threshold) = 0.5 by symmetry."""
        p = calculate_probability("gte", threshold=94.0, forecast_value=94.0, lead_time_days=1)
        assert abs(p - 0.5) < 0.001

    def test_forecast_equals_threshold_lte_is_50pct(self):
        p = calculate_probability("lte", threshold=86.0, forecast_value=86.0, lead_time_days=1)
        assert abs(p - 0.5) < 0.001

    def test_forecast_well_above_threshold_gte_is_high(self):
        """Forecast 10°F above threshold → very high chance of YES."""
        p = calculate_probability("gte", threshold=90.0, forecast_value=100.0, lead_time_days=1)
        assert p > 0.95

    def test_forecast_well_below_threshold_gte_is_low(self):
        """Forecast 10°F below threshold → very low chance of YES."""
        p = calculate_probability("gte", threshold=100.0, forecast_value=90.0, lead_time_days=1)
        assert p < 0.05

    def test_forecast_well_above_threshold_lte_is_low(self):
        """Forecast 10°F above threshold → very low chance of YES for ≤."""
        p = calculate_probability("lte", threshold=80.0, forecast_value=90.0, lead_time_days=1)
        assert p < 0.05

    def test_forecast_well_below_threshold_lte_is_high(self):
        p = calculate_probability("lte", threshold=90.0, forecast_value=80.0, lead_time_days=1)
        assert p > 0.95

    def test_gte_plus_lte_at_threshold_sum_to_1(self):
        """P(T >= x) + P(T <= x) = 1 exactly when forecast == threshold."""
        p_gte = calculate_probability("gte", threshold=95.0, forecast_value=95.0, lead_time_days=2)
        p_lte = calculate_probability("lte", threshold=95.0, forecast_value=95.0, lead_time_days=2)
        assert abs(p_gte + p_lte - 1.0) < 0.001

    def test_probability_increases_with_longer_lead_time_for_borderline(self):
        """For a borderline market, uncertainty should push probability toward 0.5."""
        p_short = calculate_probability("gte", threshold=95.0, forecast_value=93.0, lead_time_days=1)
        p_long = calculate_probability("gte", threshold=95.0, forecast_value=93.0, lead_time_days=14)
        # p_long should be closer to 0.5 than p_short (more uncertainty = less extreme)
        assert abs(p_long - 0.5) < abs(p_short - 0.5)

    def test_result_is_between_0_and_1(self):
        for lead in [0, 3, 7, 14]:
            for delta in [-20, -5, 0, 5, 20]:
                p = calculate_probability("gte", threshold=95.0, forecast_value=95.0 + delta, lead_time_days=lead)
                assert 0.0 <= p <= 1.0


class TestCalculateRangeProbability:
    """P(lo ≤ T ≤ hi) range probability."""

    def test_forecast_at_midpoint_gives_nonzero_probability(self):
        """Forecast at midpoint of [89, 90] should give nonzero probability."""
        p = calculate_range_probability(89.0, 90.0, 89.5, lead_time_days=1)
        # CDF(0.2) - CDF(-0.2) ≈ 0.158 for σ=2.5
        assert 0.10 < p < 0.25

    def test_sum_of_adjacent_buckets_near_one(self):
        """Sum of many adjacent 1°F buckets spanning ±3σ should approach 1."""
        mu = 95.0
        lead = 1
        # σ = 2.5, so ±3σ = ±7.5 → range [87.5, 102.5] → 15 buckets
        total = sum(
            calculate_range_probability(lo, lo + 1.0, mu, lead)
            for lo in range(80, 110)
        )
        assert 0.99 < total < 1.01

    def test_range_far_from_forecast_is_near_zero(self):
        """Range 20°F below forecast → near-zero probability."""
        p = calculate_range_probability(75.0, 76.0, 95.0, lead_time_days=1)
        assert p < 0.001

    def test_range_probability_always_in_zero_one(self):
        for mu in [80.0, 95.0, 110.0]:
            for lo in [70.0, 80.0, 90.0, 100.0, 110.0]:
                p = calculate_range_probability(lo, lo + 1.0, mu, lead_time_days=3)
                assert 0.0 <= p <= 1.0, f"p={p} out of range for lo={lo}, mu={mu}"

    def test_range_symmetric_around_mu(self):
        """P(mu-0.5 ≤ T ≤ mu+0.5) = P(mu+0.5 ≤ T ≤ mu+1.5) is NOT symmetric — just that
        the bucket containing mu has higher probability than a distant bucket."""
        mu = 90.0
        p_center = calculate_range_probability(mu - 0.5, mu + 0.5, mu, lead_time_days=1)
        p_distant = calculate_range_probability(mu + 3.0, mu + 4.0, mu, lead_time_days=1)
        assert p_center > p_distant

    def test_larger_range_has_higher_probability(self):
        """A 2°F range should have higher probability than the equivalent 1°F range."""
        mu = 95.0
        p1 = calculate_range_probability(94.0, 95.0, mu, lead_time_days=2)
        p2 = calculate_range_probability(94.0, 96.0, mu, lead_time_days=2)
        assert p2 > p1

    def test_range_formula(self):
        """Verify P = Φ((hi-μ)/σ) - Φ((lo-μ)/σ) numerically."""
        import math
        lo, hi, mu, lead = 89.0, 90.0, 91.0, 1
        sigma = 2.5  # lead_time <= 1
        z_hi = (hi - mu) / sigma
        z_lo = (lo - mu) / sigma
        expected = 0.5 * (1 + math.erf(z_hi / math.sqrt(2))) - 0.5 * (1 + math.erf(z_lo / math.sqrt(2)))
        actual = calculate_range_probability(lo, hi, mu, lead)
        assert abs(actual - expected) < 0.0001


class TestMarketImpliedProbability:
    def test_both_bid_ask(self):
        p = market_implied_probability(0.30, 0.34)
        assert abs(p - 0.32) < 0.001

    def test_ask_only(self):
        p = market_implied_probability(None, 0.72)
        assert abs(p - 0.72) < 0.001

    def test_bid_only(self):
        p = market_implied_probability(0.40, None)
        assert abs(p - 0.40) < 0.001

    def test_both_none(self):
        assert market_implied_probability(None, None) is None

    def test_symmetric_midpoint(self):
        p = market_implied_probability(0.60, 0.60)
        assert abs(p - 0.60) < 0.001

    def test_prices_not_summing_to_100_pct(self):
        """YES ask + NO ask may not sum to 1.0 on Kalshi — we use YES only."""
        # YES ask = 0.55, NO ask = 0.50 (sum = 1.05, typical spread scenario)
        p = market_implied_probability(0.50, 0.55)
        # Should be midpoint of YES prices only
        assert abs(p - 0.525) < 0.001


class TestConfidenceScore:
    def test_perfect_conditions_very_high(self):
        now = datetime.now(timezone.utc)
        score = confidence_score(
            lead_time_days=0,
            parse_confidence="high",
            forecast_retrieved_at=now,
            market_probability=0.5,
        )
        assert score == "Very High"

    def test_long_lead_time_reduces_confidence(self):
        now = datetime.now(timezone.utc)
        score = confidence_score(
            lead_time_days=10,
            parse_confidence="high",
            forecast_retrieved_at=now,
            market_probability=0.5,
        )
        assert score in ("Medium", "Low", "Very Low")

    def test_medium_parse_confidence_deducts(self):
        now = datetime.now(timezone.utc)
        high = confidence_score(1, "high", now, 0.5)
        med = confidence_score(1, "medium", now, 0.5)
        levels = ["Very Low", "Low", "Medium", "High", "Very High"]
        assert levels.index(med) < levels.index(high)

    def test_stale_forecast_deducts(self):
        fresh = datetime.now(timezone.utc)
        stale = datetime.now(timezone.utc) - timedelta(hours=24)
        high = confidence_score(1, "high", fresh, 0.5)
        low = confidence_score(1, "high", stale, 0.5)
        levels = ["Very Low", "Low", "Medium", "High", "Very High"]
        assert levels.index(low) <= levels.index(high)

    def test_no_market_price_deducts_half_point(self):
        now = datetime.now(timezone.utc)
        with_price = confidence_score(1, "high", now, 0.5)
        without_price = confidence_score(1, "high", now, None)
        levels = ["Very Low", "Low", "Medium", "High", "Very High"]
        # No-price can be same level or one lower (0.5 deduction)
        assert levels.index(without_price) <= levels.index(with_price)

    def test_none_lead_time_degrades_confidence(self):
        now = datetime.now(timezone.utc)
        none_lt = confidence_score(None, "high", now, 0.5)
        assert none_lt in ("Medium", "Low", "Very Low")


class TestRunAnalysis:
    """Integration tests for the full analysis pipeline."""

    _base = dict(
        title="Will the **high temp in Miami** be >94° on Jul 27?",
        subtitle="95° or above",
        city="Miami",
        target_date_str="2026-07-29",   # 1 day from today (mocked)
        weather_variable="high",
        operator="gte",
        threshold=95.0,
        parse_confidence="high",
        settlement_status="supported",
        unsupported_reason=None,
        forecast_high=93.8,
        forecast_low=78.0,
        forecast_retrieved_at=datetime.now(timezone.utc),
        yes_bid=0.44,
        yes_ask=0.48,
    )

    def test_supported_market_returns_probability(self):
        r = run_analysis(**self._base)
        assert r.analysis_status == "supported"
        assert r.ec_probability is not None
        assert 0.0 <= r.ec_probability <= 1.0

    def test_market_probability_is_midpoint(self):
        r = run_analysis(**self._base)
        assert r.market_probability is not None
        assert abs(r.market_probability - 0.46) < 0.001

    def test_explanation_contains_city(self):
        r = run_analysis(**self._base)
        assert "Miami" in r.explanation

    def test_explanation_contains_threshold(self):
        r = run_analysis(**self._base)
        assert "95" in r.explanation

    def test_explanation_contains_forecast_value(self):
        r = run_analysis(**self._base)
        assert "93.8" in r.explanation

    def test_confidence_is_valid_label(self):
        valid = {"Very High", "High", "Medium", "Low", "Very Low"}
        r = run_analysis(**self._base)
        assert r.confidence in valid

    def test_unsupported_contract_returns_no_ec_probability(self):
        kwargs = dict(self._base)
        kwargs.update(
            settlement_status="unsupported",
            unsupported_reason="Could not identify temperature variable",
            operator=None,
            threshold=None,
        )
        r = run_analysis(**kwargs)
        assert r.ec_probability is None
        assert r.analysis_status == "unsupported"
        assert r.market_probability is not None  # prices still available

    def test_no_forecast_returns_no_ec_probability(self):
        kwargs = dict(self._base)
        kwargs.update(forecast_high=None, forecast_low=None)
        r = run_analysis(**kwargs)
        assert r.ec_probability is None
        assert r.analysis_status == "no_forecast"

    def test_forecast_at_threshold_gives_near_50pct(self):
        kwargs = dict(self._base)
        kwargs.update(forecast_high=95.0, target_date_str="2026-07-28")
        r = run_analysis(**kwargs)
        assert r.ec_probability is not None
        assert abs(r.ec_probability - 0.5) < 0.05

    def test_forecast_far_below_threshold_gives_low_probability(self):
        kwargs = dict(self._base)
        kwargs.update(forecast_high=80.0, target_date_str="2026-07-28")
        r = run_analysis(**kwargs)
        assert r.ec_probability is not None
        assert r.ec_probability < 0.1

    def test_no_prices_produces_explanation(self):
        kwargs = dict(self._base)
        kwargs.update(yes_bid=None, yes_ask=None)
        r = run_analysis(**kwargs)
        assert r.market_probability is None
        assert "not available" in r.explanation.lower()


class TestRunAnalysisRangeContracts:
    """Tests for run_analysis with contract_type='range'."""

    _base_range = dict(
        title="Will the **high temp in Miami** be 89-90° on Jul 27, 2026?",
        subtitle="89° to 90°",
        city="Miami",
        target_date_str="2026-07-29",
        weather_variable="high",
        operator=None,
        threshold=None,
        parse_confidence="high",
        settlement_status="supported",
        unsupported_reason=None,
        forecast_high=89.5,
        forecast_low=75.0,
        forecast_retrieved_at=datetime.now(timezone.utc),
        yes_bid=0.10,
        yes_ask=0.14,
        contract_type="range",
        lower_bound=89.0,
        upper_bound=90.0,
    )

    def test_range_contract_produces_ec_probability(self):
        r = run_analysis(**self._base_range)
        assert r.analysis_status == "supported"
        assert r.ec_probability is not None
        assert 0.0 < r.ec_probability < 1.0

    def test_range_explanation_contains_bounds(self):
        r = run_analysis(**self._base_range)
        assert "89" in r.explanation
        assert "90" in r.explanation

    def test_range_explanation_contains_city(self):
        r = run_analysis(**self._base_range)
        assert "Miami" in r.explanation

    def test_range_no_forecast_returns_no_forecast(self):
        kwargs = dict(self._base_range)
        kwargs.update(forecast_high=None)
        r = run_analysis(**kwargs)
        assert r.analysis_status == "no_forecast"
        assert r.ec_probability is None

    def test_range_far_from_forecast_gives_low_probability(self):
        """Range [89, 90]°F when forecast = 105°F → very low probability."""
        kwargs = dict(self._base_range)
        kwargs.update(forecast_high=105.0)
        r = run_analysis(**kwargs)
        assert r.ec_probability < 0.05

    def test_range_centered_on_forecast_gives_reasonable_probability(self):
        """Range [89, 90]°F when forecast = 89.5°F → ~16% for σ=2.5."""
        kwargs = dict(self._base_range)
        kwargs.update(forecast_high=89.5, target_date_str="2026-07-28")
        r = run_analysis(**kwargs)
        assert r.ec_probability is not None
        # Rough check: should be between 5% and 40% for a 1°F bucket
        assert 0.05 < r.ec_probability < 0.40

    def test_range_low_variable(self):
        """Range contract on daily LOW."""
        r = run_analysis(
            title="Will the minimum temperature be 65-66° on Jul 27?",
            subtitle=None,
            city="New York City",
            target_date_str="2026-07-29",
            weather_variable="low",
            operator=None,
            threshold=None,
            parse_confidence="medium",
            settlement_status="supported",
            unsupported_reason=None,
            forecast_high=88.0,
            forecast_low=65.5,
            forecast_retrieved_at=datetime.now(timezone.utc),
            yes_bid=0.30,
            yes_ask=0.34,
            contract_type="range",
            lower_bound=65.0,
            upper_bound=66.0,
        )
        assert r.analysis_status == "supported"
        assert r.ec_probability is not None
        assert r.forecast_value == 65.5


class TestRunAnalysisHourlyContracts:
    """Tests for run_analysis with contract_type='hourly_threshold'."""

    _base_hourly = dict(
        title="Will the temp in Chicago be above 70.99° on Jul 28, 2026 at 12am EDT?",
        subtitle="-1° or below",
        city="Chicago",
        target_date_str="2026-07-28",
        weather_variable="hourly_temperature",
        operator="gte",
        threshold=70.99,
        parse_confidence="high",
        settlement_status="supported",
        unsupported_reason=None,
        forecast_high=None,
        forecast_low=None,
        forecast_retrieved_at=datetime.now(timezone.utc),
        yes_bid=0.40,
        yes_ask=0.44,
        contract_type="hourly_threshold",
        forecast_hourly_value=73.2,
    )

    def test_hourly_threshold_produces_ec_probability(self):
        r = run_analysis(**self._base_hourly)
        assert r.analysis_status == "supported"
        assert r.ec_probability is not None
        assert 0.0 <= r.ec_probability <= 1.0

    def test_hourly_threshold_uses_hourly_value_not_daily(self):
        """The forecast_value in result should be the hourly value, not daily high/low."""
        r = run_analysis(**self._base_hourly)
        assert r.forecast_value == 73.2

    def test_hourly_explanation_contains_forecast_value(self):
        r = run_analysis(**self._base_hourly)
        assert "73.2" in r.explanation

    def test_hourly_no_hourly_forecast_returns_no_forecast(self):
        kwargs = dict(self._base_hourly)
        kwargs.update(forecast_hourly_value=None)
        r = run_analysis(**kwargs)
        assert r.analysis_status == "no_forecast"
        assert r.ec_probability is None

    def test_hourly_no_forecast_reason_mentions_hour(self):
        kwargs = dict(self._base_hourly)
        kwargs.update(forecast_hourly_value=None)
        r = run_analysis(**kwargs)
        assert "hourly" in r.analysis_reason.lower()

    def test_hourly_forecast_above_threshold_gives_high_probability(self):
        """Hourly forecast 10°F above threshold → P(YES) > 0.9."""
        kwargs = dict(self._base_hourly)
        kwargs.update(forecast_hourly_value=81.0)  # threshold = 70.99
        r = run_analysis(**kwargs)
        assert r.ec_probability > 0.9

    def test_hourly_forecast_below_threshold_gives_low_probability(self):
        """Hourly forecast 10°F below threshold → P(YES) < 0.1."""
        kwargs = dict(self._base_hourly)
        kwargs.update(forecast_hourly_value=60.0)  # threshold = 70.99
        r = run_analysis(**kwargs)
        assert r.ec_probability < 0.1

    def test_hourly_lte_operator(self):
        """Hourly below-threshold market."""
        r = run_analysis(
            title="Will the temp in Dallas be below 95.5° at 2pm CDT?",
            subtitle=None,
            city="Dallas",
            target_date_str="2026-07-29",
            weather_variable="hourly_temperature",
            operator="lte",
            threshold=95.5,
            parse_confidence="high",
            settlement_status="supported",
            unsupported_reason=None,
            forecast_high=None,
            forecast_low=None,
            forecast_retrieved_at=datetime.now(timezone.utc),
            yes_bid=0.60,
            yes_ask=0.64,
            contract_type="hourly_threshold",
            forecast_hourly_value=93.0,
        )
        assert r.analysis_status == "supported"
        assert r.ec_probability is not None
        # Forecast (93) < threshold (95.5) → high P(T <= 95.5)
        assert r.ec_probability > 0.5

    def test_hourly_backward_compat_default_contract_type(self):
        """Calling run_analysis without contract_type= still works (defaults to threshold)."""
        r = run_analysis(
            title="Will the **high temp in Miami** be >94° on Jul 27?",
            subtitle="95° or above",
            city="Miami",
            target_date_str="2026-07-28",
            weather_variable="high",
            operator="gte",
            threshold=95.0,
            parse_confidence="high",
            settlement_status="supported",
            unsupported_reason=None,
            forecast_high=93.0,
            forecast_low=75.0,
            forecast_retrieved_at=datetime.now(timezone.utc),
            yes_bid=0.40,
            yes_ask=0.44,
        )
        assert r.analysis_status == "supported"
        assert r.ec_probability is not None
