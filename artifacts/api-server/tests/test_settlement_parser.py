"""Tests for the settlement contract parser."""
import pytest
from app.services.settlement_parser import parse_settlement, SettlementContract


class TestSubtitleParsing:
    """Subtitle-based parsing is the highest-confidence path."""

    def test_or_above_subtitle(self):
        c = parse_settlement(
            "Will the **high temp in Miami** be >94° on Jul 27?",
            "95° or above",
        )
        assert c.status == "supported"
        assert c.variable == "high"
        assert c.operator == "gte"
        assert c.threshold == 95.0
        assert c.unit == "F"
        assert c.parse_confidence == "high"
        assert c.unsupported_reason is None
        assert c.contract_type == "threshold"

    def test_or_below_subtitle(self):
        c = parse_settlement(
            "Will the **high temp in Miami** be <87° on Jul 27?",
            "86° or below",
        )
        assert c.status == "supported"
        assert c.variable == "high"
        assert c.operator == "lte"
        assert c.threshold == 86.0
        assert c.parse_confidence == "high"
        assert c.contract_type == "threshold"

    def test_range_subtitle_now_supported(self):
        """Range markets are now supported with contract_type='range'."""
        c = parse_settlement(
            "Will the **high temp in Miami** be 93-94° on Jul 27, 2026?",
            "93° to 94°",
        )
        assert c.status == "supported"
        assert c.contract_type == "range"
        assert c.variable == "high"
        assert c.lower_bound == 93.0
        assert c.upper_bound == 94.0
        assert c.operator is None
        assert c.threshold is None
        assert c.parse_confidence == "high"
        assert c.unsupported_reason is None

    def test_low_temperature_or_above(self):
        c = parse_settlement(
            "Will the minimum temperature be >79° on Jul 27?",
            "80° or above",
        )
        assert c.status == "supported"
        assert c.variable == "low"
        assert c.operator == "gte"
        assert c.threshold == 80.0
        assert c.parse_confidence == "high"

    def test_decimal_threshold(self):
        c = parse_settlement("Will the high temperature exceed something?", "95.5° or above")
        assert c.status == "supported"
        assert c.threshold == 95.5

    def test_subtitle_case_insensitive(self):
        c = parse_settlement("Will the high temperature be above something?", "95° OR ABOVE")
        assert c.status == "supported"
        assert c.operator == "gte"


class TestTitleFallback:
    """Title-based fallback when subtitle is absent or doesn't match."""

    def test_gt_from_title_no_subtitle(self):
        c = parse_settlement(
            "Will the minimum temperature be >82° on Jul 27, 2026?",
            None,
        )
        assert c.status == "supported"
        assert c.variable == "low"
        assert c.operator == "gte"
        # For continuous model, >82 is treated as >= 82
        assert c.threshold == 82.0
        assert c.parse_confidence == "medium"

    def test_lt_from_title_no_subtitle(self):
        c = parse_settlement(
            "Will the maximum temperature be <93° on Jul 27, 2026?",
            None,
        )
        assert c.status == "supported"
        assert c.variable == "high"
        assert c.operator == "lte"
        assert c.threshold == 93.0
        assert c.parse_confidence == "medium"

    def test_range_from_title_no_subtitle_now_supported(self):
        """Range markets from title are now supported with contract_type='range'."""
        c = parse_settlement(
            "Will the high temperature be 93-94° on Jul 27?",
            None,
        )
        assert c.status == "supported"
        assert c.contract_type == "range"
        assert c.variable == "high"
        assert c.lower_bound == 93.0
        assert c.upper_bound == 94.0
        assert c.operator is None
        assert c.parse_confidence == "medium"

    def test_range_operator_is_none(self):
        """Range markets have no operator (they use lower/upper bounds)."""
        c = parse_settlement("Will the high temp be 93-94°?", None)
        assert c.status == "supported"
        assert c.contract_type == "range"
        assert c.operator is None

    def test_range_lower_bound_lt_upper_bound(self):
        c = parse_settlement("Will the high temp be 89-90°?", None)
        assert c.lower_bound < c.upper_bound
        assert c.lower_bound == 89.0
        assert c.upper_bound == 90.0

    def test_empty_title(self):
        c = parse_settlement("", None)
        assert c.status == "no_data"

    def test_unrecognised_pattern(self):
        c = parse_settlement("Will something happen?", None)
        assert c.status == "unsupported"
        assert c.parse_confidence == "low"


class TestVariableDetection:
    """Variable (high/low) detection from title keywords."""

    def test_detects_high(self):
        c = parse_settlement("Will the high temp in Dallas be >108°?", "109° or above")
        assert c.variable == "high"

    def test_detects_maximum(self):
        c = parse_settlement("Will the maximum temperature be >100°?", "101° or above")
        assert c.variable == "high"

    def test_detects_low(self):
        c = parse_settlement("Will the low temp be <45°?", "44° or below")
        assert c.variable == "low"

    def test_detects_minimum(self):
        c = parse_settlement("Will the minimum temperature be <63°?", "62° or below")
        assert c.variable == "low"

    def test_unknown_variable_no_subtitle_match(self):
        c = parse_settlement("Will the temperature exceed 94°?", "95° or above")
        # subtitle still matches; variable may be None but contract is supported
        assert c.status == "supported"

    def test_high_temp_in_ticker_style_title(self):
        c = parse_settlement("Will the **high temp in NYC** be >72°?", "73° or above")
        assert c.variable == "high"

    def test_low_temp_in_ticker_style_title(self):
        c = parse_settlement("Will the **low temp in LAX** be <55°?", "54° or below")
        assert c.variable == "low"


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_threshold_zero(self):
        """Threshold of 0° is valid (unlikely for US but shouldn't crash)."""
        c = parse_settlement("Will the low temp be <0°?", None)
        assert c.status == "supported"
        assert c.threshold == 0.0

    def test_high_confidence_wins_over_medium(self):
        """When subtitle matches, confidence should be 'high' regardless of title."""
        c = parse_settlement(
            "Will the maximum temperature be 99-100°?",
            "101° or above",
        )
        # Subtitle says "101° or above" → supported threshold with high confidence
        assert c.status == "supported"
        assert c.contract_type == "threshold"
        assert c.parse_confidence == "high"
        assert c.threshold == 101.0

    def test_subtitle_garbage_falls_back_to_title(self):
        c = parse_settlement(
            "Will the maximum temperature be >100°?",
            "something weird",
        )
        # Subtitle doesn't match known patterns → falls through to title
        assert c.status == "supported"
        assert c.parse_confidence == "medium"
        assert c.threshold == 100.0


class TestHourlyTemperatureContracts:
    """Hourly temperature contracts — new in Phase 2B."""

    def test_hourly_above_threshold(self):
        """KXTEMPCHIH-style market: 'above X° at 12am EDT'."""
        c = parse_settlement(
            "Will the temp in Chicago be above 70.99° on Jul 28, 2026 at 12am EDT?",
            "-1° or below",  # Kalshi price-change floor — should be IGNORED
        )
        assert c.status == "supported"
        assert c.contract_type == "hourly_threshold"
        assert c.variable == "hourly_temperature"
        assert c.operator == "gte"
        assert c.threshold == 70.99
        assert c.target_hour == 0   # 12am = hour 0
        assert c.target_timezone_str == "EDT"
        assert c.parse_confidence == "high"
        assert c.unsupported_reason is None

    def test_hourly_below_threshold(self):
        c = parse_settlement(
            "Will the temp in Dallas be below 95.5° on Aug 1, 2026 at 2pm CDT?",
            None,
        )
        assert c.status == "supported"
        assert c.contract_type == "hourly_threshold"
        assert c.operator == "lte"
        assert c.threshold == 95.5
        assert c.target_hour == 14   # 2pm = 14
        assert c.target_timezone_str == "CDT"

    def test_hourly_midnight_is_hour_zero(self):
        c = parse_settlement(
            "Will the temp in Chicago be above 71.99° on Jul 28, 2026 at 12am EDT?",
            None,
        )
        assert c.target_hour == 0

    def test_hourly_noon_is_hour_twelve(self):
        c = parse_settlement(
            "Will the temp somewhere be above 90° on Jul 28, 2026 at 12pm EDT?",
            None,
        )
        assert c.target_hour == 12

    def test_hourly_1pm_is_hour_13(self):
        c = parse_settlement(
            "Will the temp be above 85° on Jul 28, 2026 at 1pm CDT?",
            None,
        )
        assert c.target_hour == 13

    def test_hourly_11pm_is_hour_23(self):
        c = parse_settlement(
            "Will the temp be below 70° on Jul 28, 2026 at 11pm EDT?",
            None,
        )
        assert c.target_hour == 23

    def test_hourly_misleading_subtitle_ignored(self):
        """Subtitle '-1° or below' must NOT be parsed as a temperature threshold."""
        c = parse_settlement(
            "Will the temp in Chicago be above 70.99° on Jul 28, 2026 at 12am EDT?",
            "-1° or below",
        )
        # The -1° subtitle must be ignored entirely for hourly markets
        assert c.threshold == 70.99    # NOT -1.0
        assert c.operator == "gte"     # NOT "lte"
        assert c.contract_type == "hourly_threshold"

    def test_hourly_variable_is_hourly_temperature(self):
        c = parse_settlement(
            "Will the temp in Chicago be above 70.99° on Jul 28, 2026 at 12am EDT?",
            None,
        )
        assert c.variable == "hourly_temperature"

    def test_hourly_no_time_indicator_falls_through_to_normal(self):
        """A title with 'above X°' but no time spec is NOT hourly."""
        c = parse_settlement(
            "Will the temp in Chicago be above 70.99° on Jul 28?",
            None,
        )
        # No 'at Xam/pm' in title → not detected as hourly
        # Falls through to title >/<  patterns, but "above" ≠ ">", so unsupported
        assert c.contract_type != "hourly_threshold"

    def test_hourly_various_timezone_abbreviations(self):
        for tz in ["EDT", "CDT", "PDT", "MDT", "CST", "PST"]:
            c = parse_settlement(
                f"Will the temp be above 80° on Jul 28, 2026 at 6pm {tz}?",
                None,
            )
            assert c.status == "supported"
            assert c.target_timezone_str == tz

    def test_hourly_multiple_markets_same_series(self):
        """All threshold variants in KXTEMPCHIH should parse correctly."""
        thresholds = [69.99, 70.99, 71.99, 72.99, 73.99, 74.99, 75.99, 76.99, 77.99]
        for thresh in thresholds:
            c = parse_settlement(
                f"Will the temp in Chicago be above {thresh}° on Jul 28, 2026 at 12am EDT?",
                "-1° or below",
            )
            assert c.status == "supported", f"threshold {thresh} should be supported"
            assert abs(c.threshold - thresh) < 0.001


class TestRangeContracts:
    """Range / bucket markets — promoted to supported in Phase 2B."""

    def test_one_degree_range_subtitle(self):
        c = parse_settlement(
            "Will the **high temp in Miami** be 89-90° on Jul 27, 2026?",
            "89° to 90°",
        )
        assert c.status == "supported"
        assert c.contract_type == "range"
        assert c.lower_bound == 89.0
        assert c.upper_bound == 90.0
        assert c.variable == "high"
        assert c.operator is None
        assert c.threshold is None
        assert c.parse_confidence == "high"

    def test_one_degree_range_title_only(self):
        c = parse_settlement(
            "Will the minimum temperature be 65-66° on Jul 27, 2026?",
            None,
        )
        assert c.status == "supported"
        assert c.contract_type == "range"
        assert c.lower_bound == 65.0
        assert c.upper_bound == 66.0
        assert c.variable == "low"
        assert c.parse_confidence == "medium"

    def test_two_degree_range(self):
        c = parse_settlement(
            "Will the maximum temperature be 97-100° on Jul 27?",
            None,
        )
        assert c.status == "supported"
        assert c.contract_type == "range"
        assert c.lower_bound == 97.0
        assert c.upper_bound == 100.0

    def test_decimal_range(self):
        c = parse_settlement(
            "Will the high temperature be 89.5-90.5° on Jul 27?",
            None,
        )
        assert c.status == "supported"
        assert c.contract_type == "range"
        assert abs(c.lower_bound - 89.5) < 0.001
        assert abs(c.upper_bound - 90.5) < 0.001

    def test_range_unsupported_reason_is_none(self):
        """Range contracts are now supported — unsupported_reason should be None."""
        c = parse_settlement(
            "Will the **high temp in Miami** be 91-92° on Jul 27?",
            "91° to 92°",
        )
        assert c.unsupported_reason is None

    def test_range_no_operator(self):
        c = parse_settlement(
            "Will the maximum temperature be 97-98° on Jul 27?",
            None,
        )
        assert c.operator is None

    def test_range_no_threshold(self):
        c = parse_settlement(
            "Will the maximum temperature be 97-98° on Jul 27?",
            None,
        )
        assert c.threshold is None
