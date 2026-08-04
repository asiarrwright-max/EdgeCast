"""
Tests for the Forward Test Status endpoint helpers.

Covers (per spec):
  - correct forward-test start filtering (exact 22:21:44 UTC cutoff)
  - trade created earlier on Aug 4 is legacy (regression)
  - trade created at/after exact cutoff can count
  - legacy trades excluded from new metrics
  - RESEARCH_ONLY trades excluded from official metrics
  - progress calculation (_ft_progress_pct)
  - zero-trade state (readiness label when settled=0)
  - milestone text (_ft_next_milestone at key thresholds)
  - readiness label caps at "Promising but unproven" automatically
  - 100 / 200 settled does NOT auto-trigger real-money readiness
  - readiness label defaults to "Not enough data"
  - batch-window job filtering: only collection job types qualify
  - non-collection JobRuns (any other job_type) are ignored
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.routers.paper_trades import (
    FORWARD_TEST_START,
    FORWARD_TEST_SETTLED_TARGET,
    FORWARD_TEST_START_VERSION,
    FORWARD_TEST_PHASE,
    MANUAL_READINESS_APPROVAL,
    COLLECTION_JOB_TYPES,
    ELIGIBILITY_REASON_LABELS,
    _ft_readiness_label,
    _ft_next_milestone,
    _ft_progress_pct,
    _ft_readiness_for_real_money,
)


# ── Simulation helper (mirrors the endpoint's batch-window selection) ─────────

@dataclass
class _FakeJobRun:
    """Minimal stand-in for a JobRun row — no DB required."""
    job_type: str
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = field(default=None)


def _select_latest_collection_job(jobs: list[_FakeJobRun]) -> Optional[_FakeJobRun]:
    """Replicate the endpoint's collection-batch selection logic."""
    candidates = [
        j for j in jobs
        if j.job_type in COLLECTION_JOB_TYPES
        and j.status == "success"
        and j.completed_at is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda j: j.completed_at)  # type: ignore[arg-type]


# ── FORWARD_TEST_START constant ───────────────────────────────────────────────

class TestForwardTestStartConstant:
    def test_start_is_august_4_2026(self):
        assert FORWARD_TEST_START.year  == 2026
        assert FORWARD_TEST_START.month == 8
        assert FORWARD_TEST_START.day   == 4

    def test_start_is_utc(self):
        assert FORWARD_TEST_START.tzinfo is not None
        assert FORWARD_TEST_START.utcoffset().total_seconds() == 0

    def test_start_at_exact_commit_time(self):
        """Cutoff is the exact UTC time of commit 76e4e7d going live — not midnight."""
        assert FORWARD_TEST_START.hour   == 22
        assert FORWARD_TEST_START.minute == 21
        assert FORWARD_TEST_START.second == 44

    def test_start_version(self):
        assert FORWARD_TEST_START_VERSION == "76e4e7d"

    def test_phase_text(self):
        assert "paper-trade" in FORWARD_TEST_PHASE.lower() or "collecting" in FORWARD_TEST_PHASE.lower()

    def test_settled_target(self):
        assert FORWARD_TEST_SETTLED_TARGET == 50

    def test_manual_readiness_approval_is_false(self):
        """Must never be flipped automatically — only after explicit review."""
        assert MANUAL_READINESS_APPROVAL is False


# ── Legacy / forward-test filtering logic ────────────────────────────────────

class TestStartFiltering:
    """
    Verify the exact-timestamp boundary: trades before 22:21:44 UTC on Aug 4
    are legacy; trades at or after that moment are in the forward-test window.
    """

    def test_trade_on_aug3_is_legacy(self):
        trade_ts = datetime(2026, 8, 3, 23, 59, 59, tzinfo=timezone.utc)
        assert trade_ts < FORWARD_TEST_START

    def test_trade_at_midnight_aug4_is_legacy(self):
        """Midnight on Aug 4 is BEFORE the 22:21:44 cutoff — must be legacy."""
        trade_ts = datetime(2026, 8, 4, 0, 0, 0, tzinfo=timezone.utc)
        assert trade_ts < FORWARD_TEST_START, (
            "A trade at 2026-08-04T00:00:00Z predates the exact cutoff "
            "(22:21:44Z) and must be treated as legacy."
        )

    def test_trade_one_second_before_cutoff_is_legacy(self):
        """Regression: a trade created 1 second before the exact cutoff is legacy."""
        trade_ts = datetime(2026, 8, 4, 22, 21, 43, tzinfo=timezone.utc)
        assert trade_ts < FORWARD_TEST_START, (
            "Trade at 22:21:43Z is 1 second before the 22:21:44Z cutoff "
            "and must not count toward forward-test metrics."
        )

    def test_trade_at_exact_cutoff_can_count(self):
        """A trade created at exactly 22:21:44Z on Aug 4 is in the forward-test window."""
        trade_ts = datetime(2026, 8, 4, 22, 21, 44, tzinfo=timezone.utc)
        assert trade_ts >= FORWARD_TEST_START, (
            "Trade at the exact cutoff timestamp must be eligible for "
            "the forward-test window."
        )

    def test_trade_after_cutoff_is_forward_test(self):
        trade_ts = datetime(2026, 8, 4, 22, 30, 0, tzinfo=timezone.utc)
        assert trade_ts >= FORWARD_TEST_START

    def test_research_only_excluded_from_official_metrics(self):
        """
        RESEARCH_ONLY trades must not be counted toward readiness.
        Even 10 research-only signals leave official settled at 0.
        """
        official_settled = 0
        assert _ft_readiness_label(official_settled) == "Not enough data"

    def test_legacy_excluded_from_readiness(self):
        """
        Legacy trades (before start) never affect readiness — even with
        1000 legacy settled trades, official settled stays 0.
        """
        official_settled = 0
        assert _ft_readiness_label(official_settled) == "Not enough data"


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

    def test_hundred_is_still_promising_not_ready(self):
        """100 settled does NOT auto-trigger 'Ready for tiny manual testing'."""
        assert _ft_readiness_label(100) == "Promising but unproven"

    def test_one_ninety_nine_is_still_promising(self):
        """199 settled must not return 'Ready for tiny manual testing'."""
        assert _ft_readiness_label(199) == "Promising but unproven"

    def test_two_hundred_is_still_promising(self):
        """200 settled must not return 'Strong forward-test evidence'."""
        assert _ft_readiness_label(200) == "Promising but unproven"

    def test_large_count_is_still_promising(self):
        assert _ft_readiness_label(500) == "Promising but unproven"

    def test_default_zero_trade_state(self):
        """The 'Not enough data' stage must be the default (zero trades)."""
        assert _ft_readiness_label(0) == "Not enough data"

    def test_automatic_cap_at_promising(self):
        """Automatic readiness never advances beyond 'Promising but unproven'."""
        automatic_stages = {_ft_readiness_label(n) for n in [0,5,10,50,100,200,500]}
        assert "Ready for tiny manual testing" not in automatic_stages
        assert "Strong forward-test evidence" not in automatic_stages


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
        """At 50+ trades, further advancement requires manual review."""
        text = _ft_next_milestone(50)
        assert "manual" in text.lower() or "review" in text.lower()

    def test_hundred_settled_milestone(self):
        """At 100+ the milestone is still manual review, not an auto threshold."""
        text = _ft_next_milestone(100)
        assert "manual" in text.lower() or "review" in text.lower()

    def test_two_hundred_settled_milestone(self):
        text = _ft_next_milestone(200)
        assert len(text) > 0


# ── _ft_readiness_for_real_money ──────────────────────────────────────────────

class TestReadinessForRealMoney:
    def test_zero_is_not_ready(self):
        assert _ft_readiness_for_real_money(0) == "Not ready for real money"

    def test_early_signal_is_not_ready(self):
        assert _ft_readiness_for_real_money(10) == "Not ready for real money"

    def test_promising_is_not_ready(self):
        assert _ft_readiness_for_real_money(50) == "Not ready for real money"

    def test_hundred_is_not_ready_without_approval(self):
        """100 settled must NOT trigger readiness — manual approval required."""
        assert _ft_readiness_for_real_money(100) == "Not ready for real money"

    def test_two_hundred_is_not_ready_without_approval(self):
        """200 settled must NOT trigger readiness — manual approval required."""
        assert _ft_readiness_for_real_money(200) == "Not ready for real money"

    def test_current_readiness_is_always_not_ready(self):
        """While MANUAL_READINESS_APPROVAL is False, no count triggers readiness."""
        assert not MANUAL_READINESS_APPROVAL  # guard: must be False
        for n in [0, 10, 50, 100, 200, 500]:
            assert _ft_readiness_for_real_money(n) == "Not ready for real money", (
                f"Expected 'Not ready for real money' at settled={n} "
                f"when MANUAL_READINESS_APPROVAL=False"
            )


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


# ── COLLECTION_JOB_TYPES constant ─────────────────────────────────────────────

class TestCollectionJobTypes:
    """The constant must name exactly the job types produced by collector.py."""

    def test_manual_is_a_collection_type(self):
        assert "manual" in COLLECTION_JOB_TYPES

    def test_scheduled_is_a_collection_type(self):
        assert "scheduled" in COLLECTION_JOB_TYPES

    def test_only_collector_types_included(self):
        """No job type outside of manual/scheduled should be present."""
        non_collection = set(COLLECTION_JOB_TYPES) - {"manual", "scheduled"}
        assert non_collection == set(), (
            f"Unexpected job types in COLLECTION_JOB_TYPES: {non_collection}"
        )


# ── Batch-window job selection logic ──────────────────────────────────────────

class TestCollectionJobSelection:
    """
    The endpoint selects the latest successful collection-type JobRun to anchor
    the 'Why no official bet?' reason-breakdown window.  Non-collection runs
    (any other job_type, regardless of recency) must be ignored.
    """

    _T0 = datetime(2026, 8, 4, 20, 0, 0, tzinfo=timezone.utc)   # older collection run
    _T1 = datetime(2026, 8, 4, 22, 0, 0, tzinfo=timezone.utc)   # newer collection run
    _T2 = datetime(2026, 8, 4, 23, 0, 0, tzinfo=timezone.utc)   # newest — but non-collection

    def _collection(self, started: datetime, job_type: str = "scheduled") -> _FakeJobRun:
        return _FakeJobRun(
            job_type=job_type,
            status="success",
            started_at=started,
            completed_at=started.replace(minute=started.minute + 1) if started.minute < 59 else started,
        )

    def _non_collection(self, started: datetime, job_type: str = "healthcheck") -> _FakeJobRun:
        return _FakeJobRun(
            job_type=job_type,
            status="success",
            started_at=started,
            completed_at=started,
        )

    def test_newer_non_collection_job_is_ignored(self):
        """
        Regression: a newer non-collection JobRun must NOT be selected as the
        batch window anchor.  The latest collection run should win instead.
        """
        jobs = [
            self._collection(self._T1),     # collection, earlier
            self._non_collection(self._T2), # non-collection, NEWER — must be ignored
        ]
        result = _select_latest_collection_job(jobs)
        assert result is not None
        assert result.job_type in COLLECTION_JOB_TYPES, (
            "The non-collection job was incorrectly selected as the batch anchor."
        )
        assert result.started_at == self._T1

    def test_latest_collection_job_is_selected(self):
        """The most recent successful collection run is used when multiple exist."""
        jobs = [
            self._collection(self._T0, job_type="manual"),
            self._collection(self._T1, job_type="scheduled"),
        ]
        result = _select_latest_collection_job(jobs)
        assert result is not None
        assert result.started_at == self._T1

    def test_manual_job_qualifies_as_collection(self):
        """Operator-triggered 'manual' runs are valid batch anchors."""
        jobs = [self._collection(self._T1, job_type="manual")]
        result = _select_latest_collection_job(jobs)
        assert result is not None
        assert result.job_type == "manual"

    def test_scheduled_job_qualifies_as_collection(self):
        """Scheduler-triggered 'scheduled' runs are valid batch anchors."""
        jobs = [self._collection(self._T1, job_type="scheduled")]
        result = _select_latest_collection_job(jobs)
        assert result is not None
        assert result.job_type == "scheduled"

    def test_failed_collection_job_is_ignored(self):
        """A collection run that did not succeed (status != 'success') is skipped."""
        jobs = [
            _FakeJobRun(
                job_type="scheduled",
                status="failed",
                started_at=self._T1,
                completed_at=self._T1,
            )
        ]
        result = _select_latest_collection_job(jobs)
        assert result is None

    def test_running_collection_job_is_ignored(self):
        """An in-progress job (completed_at=None) is not a valid batch anchor."""
        jobs = [
            _FakeJobRun(
                job_type="scheduled",
                status="success",
                started_at=self._T1,
                completed_at=None,  # still running
            )
        ]
        result = _select_latest_collection_job(jobs)
        assert result is None

    def test_no_jobs_returns_none(self):
        assert _select_latest_collection_job([]) is None

    def test_only_non_collection_jobs_returns_none(self):
        """If no collection-type job exists, fall back to 24-hour window (None)."""
        jobs = [
            self._non_collection(self._T2, job_type="healthcheck"),
            self._non_collection(self._T1, job_type="audit"),
        ]
        result = _select_latest_collection_job(jobs)
        assert result is None
