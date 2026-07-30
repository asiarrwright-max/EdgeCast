"""
Tests for V3 Look-Ahead Validator (v3_lookahead.py)
=====================================================
Exhaustive tests for every validation rule.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.services.v3_lookahead import (
    TOLERANCE_HOURS,
    LookaheadResult,
    RejectionReason,
    validate_record,
)
from app.services.v3_providers.base import RawForecastRecord


# ── Helpers ───────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
_TARGET_DATE = "2026-07-30"
_LEAD_HOURS = 48  # 2-day lead


def _make_record(
    forecast_init_time: datetime | None = None,
    forecast_valid_time: datetime | None = None,
    retrieval_timestamp: datetime | None = None,
    lead_time_hours: int = _LEAD_HOURS,
    is_reanalysis: bool = False,
    forecast_tmax_raw: float | None = 30.0,
    target_date_local: str = _TARGET_DATE,
) -> RawForecastRecord:
    # Valid times for a default 48h lead:
    # valid_time = 2026-07-30 23:59 UTC
    # init_time  = 2026-07-28 00:00 UTC (exactly lead_hours before valid)
    default_valid = datetime(2026, 7, 30, 23, 59, tzinfo=timezone.utc)
    default_init  = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)

    return RawForecastRecord(
        provider="open-meteo-forecast-history",
        model="GFS",
        model_version=None,
        city="Denver",
        station_id="USW00023062",
        station_lat=39.86,
        station_lon=-104.67,
        local_timezone="America/Denver",
        forecast_init_time=forecast_init_time if forecast_init_time is not None else default_init,
        forecast_valid_time=forecast_valid_time if forecast_valid_time is not None else default_valid,
        retrieval_timestamp=retrieval_timestamp or _NOW,
        target_date_local=target_date_local,
        lead_time_hours=lead_time_hours,
        forecast_tmax_raw=forecast_tmax_raw,
        raw_unit="celsius",
        raw_source_identifier="test://denver/2026-07-30/48h",
        source_provenance="test record",
        raw_response={},
        is_reanalysis=is_reanalysis,
    )


# ── Rule 1: MISSING_INIT_TIME ─────────────────────────────────────────────────

class TestMissingInitTime:
    def test_none_init_time_rejected(self):
        rec = _make_record(forecast_init_time=None)
        # Override the default assignment by setting after creation
        rec = RawForecastRecord(
            **{**rec.__dict__, "forecast_init_time": None}
        )
        result = validate_record(rec)
        assert result.is_valid is False
        assert result.rejection_reason == RejectionReason.MISSING_INIT_TIME

    def test_valid_init_time_passes(self):
        rec = _make_record()
        result = validate_record(rec)
        # May fail other checks but not MISSING_INIT_TIME
        if not result.is_valid:
            assert result.rejection_reason != RejectionReason.MISSING_INIT_TIME


# ── Rule 2: FUTURE_INIT_TIME ─────────────────────────────────────────────────

class TestFutureInitTime:
    def test_init_time_after_retrieval_rejected(self):
        future_init = _NOW + timedelta(hours=2)
        rec = _make_record(forecast_init_time=future_init)
        result = validate_record(rec)
        assert result.is_valid is False
        assert result.rejection_reason == RejectionReason.FUTURE_INIT_TIME

    def test_init_time_one_second_after_retrieval_rejected(self):
        """init_time 1s after retrieval_time must be rejected."""
        future_init = _NOW + timedelta(seconds=1)
        rec = _make_record(forecast_init_time=future_init)
        result = validate_record(rec)
        assert result.is_valid is False
        assert result.rejection_reason == RejectionReason.FUTURE_INIT_TIME

    def test_init_time_well_before_retrieval_passes_rule2(self):
        early_init = _NOW - timedelta(hours=48)
        valid_time = _NOW + timedelta(hours=2)  # valid in the future is ok
        rec = _make_record(forecast_init_time=early_init, forecast_valid_time=valid_time)
        result = validate_record(rec)
        if not result.is_valid:
            assert result.rejection_reason != RejectionReason.FUTURE_INIT_TIME


# ── Rule 3: REANALYSIS_NOT_ALLOWED ───────────────────────────────────────────

class TestReanalysisNotAllowed:
    def test_reanalysis_record_rejected(self):
        rec = _make_record(is_reanalysis=True)
        result = validate_record(rec)
        assert result.is_valid is False
        assert result.rejection_reason == RejectionReason.REANALYSIS_NOT_ALLOWED

    def test_non_reanalysis_record_passes_rule3(self):
        rec = _make_record(is_reanalysis=False)
        result = validate_record(rec)
        if not result.is_valid:
            assert result.rejection_reason != RejectionReason.REANALYSIS_NOT_ALLOWED


# ── Rule 4: LOOKAHEAD_VIOLATION ───────────────────────────────────────────────

class TestLookaheadViolation:
    def test_init_time_too_recent_rejected(self):
        """
        For a 48h lead: max allowed init = valid_time - 48h + 12h tolerance = valid_time - 36h.
        Use init = valid_time - 10h (26h inside the boundary) → LOOKAHEAD_VIOLATION.

        Retrieval timestamp is set after valid_time so init_time is not considered "future".
        """
        valid_time  = datetime(2026, 7, 30, 23, 59, tzinfo=timezone.utc)
        retrieval   = datetime(2026, 7, 31, 12, 0,  tzinfo=timezone.utc)  # after valid
        # Violation: init = valid_time - 10h — still well before retrieval, but too late
        violating_init = valid_time - timedelta(hours=10)
        rec = _make_record(
            forecast_init_time=violating_init,
            forecast_valid_time=valid_time,
            retrieval_timestamp=retrieval,
        )
        result = validate_record(rec)
        assert result.is_valid is False
        assert result.rejection_reason == RejectionReason.LOOKAHEAD_VIOLATION

    def test_init_time_exactly_at_max_allowed_passes(self):
        """
        init_time = valid_time - lead + tolerance → exactly at boundary → should pass.
        Retrieval timestamp after valid_time so boundary is tested in isolation.
        """
        valid_time = datetime(2026, 7, 30, 23, 59, tzinfo=timezone.utc)
        retrieval  = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
        # Max allowed = valid_time - 48h + 12h = valid_time - 36h
        max_init = valid_time - timedelta(hours=(_LEAD_HOURS - TOLERANCE_HOURS))
        rec = _make_record(
            forecast_init_time=max_init,
            forecast_valid_time=valid_time,
            retrieval_timestamp=retrieval,
        )
        result = validate_record(rec)
        assert result.is_valid is True, (
            f"Expected valid at boundary, got: {result.rejection_reason} — {result.notes}"
        )

    def test_init_time_well_before_valid_time_passes(self):
        """Standard case: model run 48h before valid date."""
        valid_time = datetime(2026, 7, 30, 23, 59, tzinfo=timezone.utc)
        init_time  = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)
        rec = _make_record(
            forecast_init_time=init_time,
            forecast_valid_time=valid_time,
        )
        result = validate_record(rec)
        assert result.is_valid is True, f"Expected valid, got: {result.rejection_reason}"

    def test_strict_mode_tighter_boundary(self):
        """With strict=True, no tolerance is applied.
        Retrieval timestamp after valid_time so boundary is isolated.
        """
        valid_time = datetime(2026, 7, 30, 23, 59, tzinfo=timezone.utc)
        retrieval  = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
        # With strict=True, max init = valid - 48h (no +12h tolerance)
        # Use init = valid - 40h (would pass with 12h tolerance, fails without)
        borderline_init = valid_time - timedelta(hours=40)
        rec = _make_record(
            forecast_init_time=borderline_init,
            forecast_valid_time=valid_time,
            retrieval_timestamp=retrieval,
        )
        # Should pass with tolerance
        result_tolerant = validate_record(rec, strict=False)
        assert result_tolerant.is_valid is True

        # Should fail without tolerance
        result_strict = validate_record(rec, strict=True)
        assert result_strict.is_valid is False
        assert result_strict.rejection_reason == RejectionReason.LOOKAHEAD_VIOLATION


# ── Rule 5: VALID_TIME_INCONSISTENCY ─────────────────────────────────────────

class TestValidTimeInconsistency:
    def test_valid_time_before_init_time_rejected(self):
        """
        valid_time < init_time must cause rejection.

        Note: Rule 4 (LOOKAHEAD_VIOLATION) fires before Rule 5
        (VALID_TIME_INCONSISTENCY) for this configuration because the
        lookahead check subsumes the inconsistency when valid_time <
        init_time.  We assert the record is rejected for any rule.
        """
        init_time  = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
        valid_time = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)  # before init!
        rec = _make_record(
            forecast_init_time=init_time,
            forecast_valid_time=valid_time,
        )
        result = validate_record(rec)
        assert result.is_valid is False
        # Either LOOKAHEAD_VIOLATION or VALID_TIME_INCONSISTENCY is correct —
        # Rule 4 is checked first and fires here because valid_time < init_time.
        assert result.rejection_reason in (
            RejectionReason.LOOKAHEAD_VIOLATION,
            RejectionReason.VALID_TIME_INCONSISTENCY,
        )


# ── Rule 6 (soft): MISSING_FORECAST_VALUE ────────────────────────────────────

class TestMissingForecastValue:
    def test_none_forecast_not_rejected_but_flagged(self):
        """Missing forecast value is a soft flag, not a hard rejection."""
        rec = _make_record(forecast_tmax_raw=None)
        result = validate_record(rec)
        # is_valid should be True (not rejected)
        assert result.is_valid is True
        assert RejectionReason.MISSING_FORECAST_VALUE in result.flags

    def test_present_forecast_has_no_soft_flag(self):
        rec = _make_record(forecast_tmax_raw=30.0)
        result = validate_record(rec)
        assert RejectionReason.MISSING_FORECAST_VALUE not in result.flags


# ── Happy path (all rules pass) ───────────────────────────────────────────────

class TestHappyPath:
    def test_valid_record_passes_all_checks(self):
        """Standard well-formed archived GFS forecast record passes everything."""
        valid_time = datetime(2026, 7, 30, 23, 59, tzinfo=timezone.utc)
        init_time  = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)  # 48h before valid
        rec = _make_record(
            forecast_init_time=init_time,
            forecast_valid_time=valid_time,
            retrieval_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc),
            is_reanalysis=False,
            forecast_tmax_raw=30.0,
        )
        result = validate_record(rec)
        assert result.is_valid is True
        assert result.rejection_reason is None
        assert result.flags == []

    def test_1day_lead_passes(self):
        valid_time = datetime(2026, 7, 29, 23, 59, tzinfo=timezone.utc)
        init_time  = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)  # 24h before valid
        rec = _make_record(
            forecast_init_time=init_time,
            forecast_valid_time=valid_time,
            lead_time_hours=24,
        )
        result = validate_record(rec)
        assert result.is_valid is True

    def test_7day_lead_passes(self):
        valid_time = datetime(2026, 8, 4, 23, 59, tzinfo=timezone.utc)
        init_time  = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)  # 168h before valid
        rec = _make_record(
            forecast_init_time=init_time,
            forecast_valid_time=valid_time,
            lead_time_hours=168,
        )
        result = validate_record(rec)
        assert result.is_valid is True


# ── Timezone-awareness ────────────────────────────────────────────────────────

class TestTimezoneAwareness:
    def test_naive_datetime_treated_as_utc(self):
        """Naive datetimes should be treated as UTC (not cause a crash)."""
        naive_init  = datetime(2026, 7, 28, 0, 0)  # no tzinfo
        naive_valid = datetime(2026, 7, 30, 23, 59)
        naive_retr  = datetime(2026, 7, 28, 10, 0)

        rec = _make_record(
            forecast_init_time=naive_init,
            forecast_valid_time=naive_valid,
            retrieval_timestamp=naive_retr,
        )
        # Should not raise
        result = validate_record(rec)
        assert isinstance(result, LookaheadResult)
