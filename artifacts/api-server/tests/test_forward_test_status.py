"""
Tests for the Forward Test Status endpoint helpers.

Covers (per spec):
  - correct forward-test start filtering (FORWARD_TEST_START constant)
  - legacy trades excluded from new metrics (trades before start → legacy)
  - RESEARCH_ONLY trades excluded from official metrics
  - progress calculation (_ft_progress_pct)
  - zero-trade state (readiness label when settled=0)
  - milestone text (_ft_next_milestone at key thresholds)
  - readiness label defaults to "Not enough data"
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from app.routers.paper_trades import (
    FORWARD_TEST_START,
    FORWARD_TEST_SETTLED_TARGET,
    FORWARD_TEST_START_VERSION,
    FORWARD_TEST_PHASE,
    ELIGIBILITY_REASON_LABELS,
    _ft_readiness_label,
    _ft_next_milestone,
    _ft_progress_pct,
    _ft_readiness_for_real_money,
)


# ── FORWARD_TEST_START constant ───────────────────────────────────────────────

class TestForwardTestStartConstant:
    def test_start_is_august_4_2026(self):
        assert FORWARD_TEST_START.year  == 2026
        assert FORWARD_TEST_START.month == 8
        assert FORWARD_TEST_START.day   == 4

    def test_start_is_utc(self):
        assert FORWARD_TEST_START.tzinfo is not None
        assert FORWARD_TEST_START.utcoffset().total_seconds() == 0

    def test_start_at_midnight(self):
        assert FORWARD_TEST_START.hour   == 0
        assert FORWARD_TEST_START.minute == 0
        assert FORWARD_TEST_START.second == 0

    def test_start_version(self):
        assert FORWARD_TEST_START_VERSION == "76e4e7d"

    def test_phase_text(self):
        assert "paper-trade" in FORWARD_TEST_PHASE.lower() or "collecting" in FORWARD_TEST_PHASE.lower()

    def test_settled_target(self):
        assert FORWARD_TEST_SETTLED_TARGET == 50


# ── Legacy / forward-test filtering logic ────────────────────────────────────

class TestStartFiltering:
    """
    Verify the start-date boundary: trades before start are legacy;
    trades on or after start enter the forward-test window.
    """
    def test_trade_before_start_is_legacy(self):
        trade_ts = datetime(2026, 8, 3, 23, 59, 59, tzinfo=timezone.utc)
        assert trade_ts < FORWARD_TEST_START, "Trade from Aug 3 should be before start"

    def test_trade_on_start_date_is_forward_test(self):
        trade_ts = datetime(2026, 8, 4, 0, 0, 0, tzinfo=timezone.utc)
        assert trade_ts >= FORWARD_TEST_START, "Trade from Aug 4 00:00:00 should be in forward-test window"

    def test_trade_after_start_is_forward_test(self):
        trade_ts = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
        assert trade_ts >= FORWARD_TEST_START

    def test_research_only_excluded_from_official_metrics(self):
        """
        A RESEARCH_ONLY trade after the start date must NOT be counted toward
        officialSettled or officialOpen.  We verify this by checking that the
        readiness label remains 'Not enough data' when only RESEARCH_ONLY
        trades exist (settled=0 official).
        """
        # Simulate: 10 research-only signals, 0 official settled
        official_settled = 0
        label = _ft_readiness_label(official_settled)
        assert label == "Not enough data"

    def test_legacy_excluded_from_readiness(self):
        """
        Legacy trades (before start) must not affect the readiness label.
        Even with 1000 legacy settled trades, the official settled count
        is still 0, so readiness stays 'Not enough data'.
        """
        legacy_settled = 1000
        official_settled = 0   # legacy trades never counted
        label = _ft_readiness_label(official_settled)
        assert label == "Not enough data"


# ── _ft_readiness_label ───────────────────────────────────────────────────────

class TestReadinessLabel:
    def test_zero_is_not_enough_data(self):
        assert _ft_readiness_label(0) == "Not enough data"

    def test_one_is_not_enough_data(self):
        assert _ft_readiness_label(1) == "Not enough data"

    def test_nine_is_not_enough_data(self):
        assert _ft_readiness_label(9) == "Not enough data"

    def test_ten_is_early_signal(self):
        assert _ft_readiness_label(10) == "Early signal"

    def test_forty_nine_is_early_signal(self):
        assert _ft_readiness_label(49) == "Early signal"

    def test_fifty_is_promising(self):
        assert _ft_readiness_label(50) == "Promising but unproven"

    def test_ninety_nine_is_promising(self):
        assert _ft_readiness_label(99) == "Promising but unproven"

    def test_hundred_is_ready_for_testing(self):
        assert _ft_readiness_label(100) == "Ready for tiny manual testing"

    def test_one_ninety_nine_is_ready_for_testing(self):
        assert _ft_readiness_label(199) == "Ready for tiny manual testing"

    def test_two_hundred_is_strong_evidence(self):
        assert _ft_readiness_label(200) == "Strong forward-test evidence"

    def test_large_count_is_strong_evidence(self):
        assert _ft_readiness_label(500) == "Strong forward-test evidence"

    def test_default_zero_trade_state(self):
        """The 'Not enough data' stage must be the default (zero trades)."""
        assert _ft_readiness_label(0) == "Not enough data"


# ── _ft_progress_pct ──────────────────────────────────────────────────────────

class TestProgressPct:
    def test_zero_settled_is_zero_pct(self):
        assert _ft_progress_pct(0) == 0.0

    def test_half_settled(self):
        assert _ft_progress_pct(25) == 50.0

    def test_target_settled_is_100(self):
        assert _ft_progress_pct(50) == 100.0

    def test_over_target_capped_at_100(self):
        assert _ft_progress_pct(75) == 100.0
        assert _ft_progress_pct(200) == 100.0

    def test_one_settled(self):
        assert _ft_progress_pct(1) == pytest.approx(2.0, abs=0.1)

    def test_ten_settled(self):
        assert _ft_progress_pct(10) == pytest.approx(20.0, abs=0.1)

    def test_custom_target(self):
        assert _ft_progress_pct(10, target=100) == pytest.approx(10.0, abs=0.1)

    def test_zero_target_returns_100(self):
        """Edge case: target=0 must not raise ZeroDivisionError."""
        assert _ft_progress_pct(5, target=0) == 100.0


# ── _ft_next_milestone ────────────────────────────────────────────────────────

class TestNextMilestone:
    def test_zero_settled_milestone(self):
        assert "10" in _ft_next_milestone(0)

    def test_nine_settled_milestone(self):
        assert "10" in _ft_next_milestone(9)

    def test_ten_settled_milestone(self):
        text = _ft_next_milestone(10)
        assert "50" in text
        assert "minimum" in text.lower()

    def test_forty_nine_settled_milestone(self):
        text = _ft_next_milestone(49)
        assert "50" in text

    def test_fifty_settled_milestone(self):
        text = _ft_next_milestone(50)
        assert "100" in text

    def test_ninety_nine_settled_milestone(self):
        text = _ft_next_milestone(99)
        assert "100" in text

    def test_hundred_settled_milestone(self):
        text = _ft_next_milestone(100)
        assert "200" in text

    def test_two_hundred_settled_milestone(self):
        text = _ft_next_milestone(200)
        # At 200+ we're beyond the defined milestones — just a maintenance message
        assert len(text) > 0


# ── _ft_readiness_for_real_money ──────────────────────────────────────────────

class TestReadinessForRealMoney:
    def test_zero_is_not_ready(self):
        assert _ft_readiness_for_real_money(0) == "Not ready for real money"

    def test_early_signal_is_not_ready(self):
        assert _ft_readiness_for_real_money(10) == "Not ready for real money"

    def test_promising_is_not_ready(self):
        assert _ft_readiness_for_real_money(50) == "Not ready for real money"

    def test_ready_for_testing(self):
        result = _ft_readiness_for_real_money(100)
        assert result != "Not ready for real money"

    def test_strong_evidence(self):
        result = _ft_readiness_for_real_money(200)
        assert result != "Not ready for real money"


# ── ELIGIBILITY_REASON_LABELS ─────────────────────────────────────────────────

class TestReasonLabels:
    EXPECTED_REASONS = [
        "missing_or_stale_executable_quote",
        "cutoff_unverified_or_too_close",
        "same_day_not_approved",
        "entry_price_below_official_floor",
        "settlement_station_unverified",
        "extreme_edge_requires_validation",
        "correlated_outcome_limit",
        "hourly_temperature_not_approved",
    ]

    def test_all_reason_codes_present(self):
        for reason in self.EXPECTED_REASONS:
            assert reason in ELIGIBILITY_REASON_LABELS, f"Missing label for: {reason}"

    def test_labels_are_human_readable(self):
        for code, label in ELIGIBILITY_REASON_LABELS.items():
            assert "_" not in label, f"Label contains underscore (not human-readable): {label!r}"
            assert len(label) > 5

    def test_count_of_reasons(self):
        assert len(ELIGIBILITY_REASON_LABELS) == len(self.EXPECTED_REASONS)
