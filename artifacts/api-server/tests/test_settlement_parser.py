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

    def test_range_subtitle_unsupported(self):
        c = parse_settlement(
            "Will the **high temp in Miami** be 93-94° on Jul 27?",
            "93° to 94°",
        )
        assert c.status == "unsupported"
        assert c.variable == "high"
        assert c.operator is None
        assert c.threshold is None
        assert c.parse_confidence == "high"
        assert "range" in c.unsupported_reason.lower()

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

    def test_range_from_title_no_subtitle(self):
        c = parse_settlement(
            "Will the high temperature be 93-94° on Jul 27?",
            None,
        )
        assert c.status == "unsupported"
        assert c.variable == "high"
        assert "range" in c.unsupported_reason.lower()
        assert c.parse_confidence == "medium"

    def test_title_range_not_confused_with_gt(self):
        """93-94° must not be parsed as > something."""
        c = parse_settlement("Will the high temp be 93-94°?", None)
        assert c.status == "unsupported"
        assert c.operator is None

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
        # (we don't block on unknown variable when threshold is clear)
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
        # Subtitle says "101° or above" → supported with high confidence
        assert c.status == "supported"
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
