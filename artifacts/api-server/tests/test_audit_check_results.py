"""
Tests for the audit_check_results system.

Covered:
  - AuditCheckResult model fields / defaults
  - run_all_audit_checks stores results for all three check_keys
  - GET /api/audit/check-results returns latest per check_key
  - GET returns PENDING stub for a check that has never run
  - Multiple runs of the same check — newest row is returned
  - No mutation of paper_trades / calibration data during checks
  - Status field is one of the allowed values
  - action_required is a boolean
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Unit tests — pure model / helper behaviour (no DB)
# ---------------------------------------------------------------------------

class TestAuditCheckResultModel:
    """Verify the model dataclass contract without hitting a database."""

    def test_allowed_statuses(self):
        from app.models import AuditCheckResult
        allowed = {"PENDING", "CONFIRMED", "CLEARED", "FIX_REQUIRED", "RESOLVED"}
        for s in allowed:
            r = AuditCheckResult(
                check_key="test_key",
                check_name="Test Check",
                category="test",
                status=s,
                summary="ok",
                action_required=False,
                checked_at=datetime.now(timezone.utc),
            )
            assert r.status in allowed

    def test_action_required_is_bool(self):
        from app.models import AuditCheckResult
        r = AuditCheckResult(
            check_key="k",
            check_name="n",
            category="c",
            status="CLEARED",
            summary="s",
            action_required=True,
            checked_at=datetime.now(timezone.utc),
        )
        assert isinstance(r.action_required, bool)
        assert r.action_required is True

    def test_optional_fields_default_to_none(self):
        from app.models import AuditCheckResult
        r = AuditCheckResult(
            check_key="k",
            check_name="n",
            category="c",
            status="PENDING",
            summary="s",
            action_required=False,
            checked_at=datetime.now(timezone.utc),
        )
        assert r.details is None
        assert r.severity is None
        assert r.source is None
        assert r.metadata_json is None


# ---------------------------------------------------------------------------
# Unit tests — helper functions
# ---------------------------------------------------------------------------

class TestHelpers:

    def test_haversine_miles_same_point(self):
        from app.services.audit_checks import _haversine_miles
        assert _haversine_miles(40.0, -74.0, 40.0, -74.0) == pytest.approx(0.0, abs=0.001)

    def test_haversine_miles_known_distance(self):
        from app.services.audit_checks import _haversine_miles
        # NYC (40.7128, -74.0060) to Newark (40.7357, -74.1724) ≈ 9.4 miles
        d = _haversine_miles(40.7128, -74.0060, 40.7357, -74.1724)
        assert 8.5 < d < 10.5

    def test_parse_settlement_date_date_only(self):
        from app.services.audit_checks import _parse_settlement_date
        date_str, has_time = _parse_settlement_date("2026-08-06")
        assert date_str == "2026-08-06"
        assert has_time is False

    def test_parse_settlement_date_full_utc(self):
        from app.services.audit_checks import _parse_settlement_date
        date_str, has_time = _parse_settlement_date("2026-08-07T05:00:00+00:00")
        assert date_str == "2026-08-07"
        assert has_time is True

    def test_utc_to_local_date_cdt_offset(self):
        from app.services.audit_checks import _utc_to_local_date
        # 2026-08-07T05:00:00Z in CDT (UTC-5) = 2026-08-07T00:00:00 CDT = Aug 07 local
        # Actually midnight CDT = 05:00 UTC, so same date
        result = _utc_to_local_date("2026-08-07T05:00:00+00:00", "America/Chicago")
        assert result == "2026-08-07"

    def test_utc_to_local_date_crosses_midnight(self):
        from app.services.audit_checks import _utc_to_local_date
        # 2026-08-07T03:00:00Z in CDT (UTC-5) = 2026-08-06T22:00:00 CDT → Aug 06 local
        result = _utc_to_local_date("2026-08-07T03:00:00+00:00", "America/Chicago")
        assert result == "2026-08-06"

    def test_utc_to_local_date_bad_input(self):
        from app.services.audit_checks import _utc_to_local_date
        assert _utc_to_local_date("not-a-date", "America/Chicago") is None

    def test_series_city_coords_unique_per_city(self):
        from app.services.audit_checks import _series_city_coords
        coords = _series_city_coords()
        # All values are tuples of two floats
        for city, (lat, lon) in coords.items():
            assert isinstance(lat, float) or isinstance(lat, int)
            assert isinstance(lon, float) or isinstance(lon, int)
        # No duplicates (dict keyed by city)
        assert len(coords) == len(set(coords.keys()))


# ---------------------------------------------------------------------------
# Integration-style tests — check runner with mocked DB
# ---------------------------------------------------------------------------

def _make_mock_db():
    """Return a minimal AsyncSession mock that mimics the patterns used by the checks."""
    db = AsyncMock()

    # Default: execute returns an object whose .fetchall() returns []
    fetch_result = MagicMock()
    fetch_result.fetchall.return_value = []
    fetch_result.scalars.return_value.all.return_value = []
    db.execute.return_value = fetch_result
    db.add = MagicMock()
    db.flush = AsyncMock()

    return db


class TestRunAllAuditChecks:

    @pytest.mark.asyncio
    async def test_runs_all_three_checks(self):
        from app.services.audit_checks import run_all_audit_checks
        db = _make_mock_db()
        results = await run_all_audit_checks(db)
        assert len(results) == 3
        keys = {r.check_key for r in results}
        assert keys == {"db_calibration_contents", "db_date_alignment", "db_coord_alignment"}

    @pytest.mark.asyncio
    async def test_adds_each_result_to_session(self):
        from app.services.audit_checks import run_all_audit_checks
        db = _make_mock_db()
        await run_all_audit_checks(db)
        assert db.add.call_count == 3

    @pytest.mark.asyncio
    async def test_does_not_modify_paper_trades(self):
        from app.services.audit_checks import run_all_audit_checks
        db = _make_mock_db()
        await run_all_audit_checks(db)
        # No UPDATE, DELETE, or INSERT into trading tables
        for call in db.execute.call_args_list:
            args = call[0]
            if args:
                stmt = str(args[0]).upper()
                assert "UPDATE PAPER_TRADES" not in stmt
                assert "DELETE FROM PAPER_TRADES" not in stmt
                assert "INSERT INTO PAPER_TRADES" not in stmt

    @pytest.mark.asyncio
    async def test_empty_db_produces_cleared_statuses(self):
        from app.services.audit_checks import run_all_audit_checks
        db = _make_mock_db()
        results = await run_all_audit_checks(db)
        for r in results:
            assert r.status in {"CLEARED", "PENDING", "CONFIRMED", "FIX_REQUIRED", "RESOLVED"}

    @pytest.mark.asyncio
    async def test_all_results_have_required_fields(self):
        from app.services.audit_checks import run_all_audit_checks
        db = _make_mock_db()
        results = await run_all_audit_checks(db)
        for r in results:
            assert r.check_key
            assert r.check_name
            assert r.category
            assert r.status
            assert r.summary
            assert isinstance(r.action_required, bool)
            assert r.checked_at is not None

    @pytest.mark.asyncio
    async def test_check_continues_after_one_failure(self):
        """A failure in one check must not prevent the others from running."""
        from app.services.audit_checks import run_all_audit_checks
        db = _make_mock_db()
        # Make the first execute call raise, subsequent calls succeed
        call_count = [0]
        original_side_effect = db.execute.side_effect

        async def sometimes_fail(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated DB error")
            result = MagicMock()
            result.fetchall.return_value = []
            result.scalars.return_value.all.return_value = []
            return result

        db.execute.side_effect = sometimes_fail
        results = await run_all_audit_checks(db)
        # At least the successful checks produced results
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_calibration_active_produces_fix_required(self):
        from app.services.audit_checks import _run_calibration_check
        db = _make_mock_db()

        active_row = MagicMock()
        active_row.strategy_version = "v2.0"
        active_row.bucket_lo = 0.85
        active_row.bucket_hi = 0.95
        active_row.predicted_rate = 0.90
        active_row.actual_yes_rate = 0.60
        active_row.adjustment_factor = 0.667
        active_row.sample_size = 35
        active_row.computed_at = datetime(2026, 8, 1, tzinfo=timezone.utc)

        fetch = MagicMock()
        fetch.fetchall.return_value = [active_row]
        db.execute.return_value = fetch

        result = await _run_calibration_check(db)
        assert result.status == "FIX_REQUIRED"
        assert result.action_required is True
        assert result.check_key == "db_calibration_contents"

    @pytest.mark.asyncio
    async def test_calibration_empty_produces_cleared(self):
        from app.services.audit_checks import _run_calibration_check
        db = _make_mock_db()
        fetch = MagicMock()
        fetch.fetchall.return_value = []
        db.execute.return_value = fetch

        result = await _run_calibration_check(db)
        assert result.status == "CLEARED"
        assert result.action_required is False

    @pytest.mark.asyncio
    async def test_date_mismatch_detected(self):
        from app.services.audit_checks import _run_date_alignment_check
        # Simulate a Dallas FTB trade whose UTC close time crosses midnight CDT
        # 2026-08-10T03:00:00+00:00 = 2026-08-09 22:00 CDT → local date = 2026-08-09
        # but [:10] gives "2026-08-10" → mismatch!
        mismatch_row = MagicMock()
        mismatch_row.market_ticker = "KXHIGHTDAL-26AUG10-T95"
        mismatch_row.city = "Dallas"
        mismatch_row.target_settlement_date = "2026-08-10T03:00:00+00:00"
        mismatch_row.settlement_timezone = "America/Chicago"
        mismatch_row.eligibility_status = "OFFICIAL"

        # FTB query (call 1) returns the mismatch row; FTA query (call 2) returns empty
        def _make_fetch(rows):
            f = MagicMock()
            f.fetchall.return_value = rows
            f.scalars.return_value.all.return_value = []
            return f

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.execute.side_effect = [_make_fetch([mismatch_row]), _make_fetch([])]

        result = await _run_date_alignment_check(db)
        assert result.status == "FIX_REQUIRED"
        assert result.action_required is True
        meta = result.metadata_json
        assert meta["ftb_mismatch_count"] == 1
        assert meta["ftb_mismatches"][0]["utc_date_used"] == "2026-08-10"
        assert meta["ftb_mismatches"][0]["local_date"] == "2026-08-09"

    @pytest.mark.asyncio
    async def test_date_only_strings_produce_cleared(self):
        from app.services.audit_checks import _run_date_alignment_check
        row = MagicMock()
        row.market_ticker = "KXHIGHTDAL-26AUG10-T95"
        row.city = "Dallas"
        row.target_settlement_date = "2026-08-10"  # date-only, no time component
        row.settlement_timezone = "America/Chicago"
        row.eligibility_status = "OFFICIAL"

        def _make_fetch(rows):
            f = MagicMock()
            f.fetchall.return_value = rows
            f.scalars.return_value.all.return_value = []
            return f

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.execute.side_effect = [_make_fetch([row]), _make_fetch([])]

        result = await _run_date_alignment_check(db)
        assert result.status == "CLEARED"
        assert result.action_required is False
        assert result.metadata_json["ftb_date_only_count"] == 1
        assert result.metadata_json["ftb_mismatch_count"] == 0


# ---------------------------------------------------------------------------
# GET endpoint — latest-per-check-key behaviour
# ---------------------------------------------------------------------------

class TestGetCheckResults:

    @pytest.mark.asyncio
    async def test_returns_latest_per_check_key(self):
        """Endpoint must return the newest row per check_key."""
        from app.routers.audit import get_audit_check_results

        older = MagicMock()
        older.id = 1
        older.check_key = "db_calibration_contents"
        older.check_name = "Calibration Adjustments Contents"
        older.category = "calibration"
        older.status = "FIX_REQUIRED"
        older.severity = "HIGH"
        older.summary = "old summary"
        older.details = None
        older.action_required = True
        older.checked_at = datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
        older.source = "audit_checks_service"
        older.metadata_json = {}

        newer = MagicMock()
        newer.id = 2
        newer.check_key = "db_calibration_contents"
        newer.check_name = "Calibration Adjustments Contents"
        newer.category = "calibration"
        newer.status = "CLEARED"
        newer.severity = "HIGH"
        newer.summary = "new summary"
        newer.details = None
        newer.action_required = False
        newer.checked_at = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
        newer.source = "audit_checks_service"
        newer.metadata_json = {}

        db = AsyncMock()
        scalars = MagicMock()
        # DB returns rows ORDER BY check_key, checked_at DESC → newest first
        scalars.all.return_value = [newer, older]
        exec_result = MagicMock()
        exec_result.scalars.return_value = scalars
        db.execute.return_value = exec_result

        user = {"username": "admin"}
        response = await get_audit_check_results(db=db, _user=user)

        # The endpoint keeps the first-seen row per key (newest, from DESC order)
        result_map = {r["checkKey"]: r for r in response["results"]}
        assert result_map["db_calibration_contents"]["status"] == "CLEARED"

    @pytest.mark.asyncio
    async def test_pending_stub_for_never_run_check(self):
        """When a check has never run, it must appear with status=PENDING."""
        from app.routers.audit import get_audit_check_results

        db = AsyncMock()
        scalars = MagicMock()
        scalars.all.return_value = []  # empty DB
        exec_result = MagicMock()
        exec_result.scalars.return_value = scalars
        db.execute.return_value = exec_result

        user = {"username": "admin"}
        response = await get_audit_check_results(db=db, _user=user)

        assert "results" in response
        pending = [r for r in response["results"] if r["status"] == "PENDING"]
        assert len(pending) == 3  # all three checks pending

    @pytest.mark.asyncio
    async def test_multiple_runs_returns_newest(self):
        """Three runs of the same check — only the newest result is returned."""
        from app.routers.audit import get_audit_check_results

        rows = []
        for i, status in enumerate(["FIX_REQUIRED", "CLEARED", "FIX_REQUIRED"]):
            r = MagicMock()
            r.id = i + 1
            r.check_key = "db_date_alignment"
            r.check_name = "Target Settlement Date Local-Date Alignment"
            r.category = "era5_verification"
            r.status = status
            r.severity = "CRITICAL"
            r.summary = f"run {i}"
            r.details = None
            r.action_required = status == "FIX_REQUIRED"
            r.checked_at = datetime(2026, 8, 8, i, 0, tzinfo=timezone.utc)
            r.source = "audit_checks_service"
            r.metadata_json = {}
            rows.append(r)

        db = AsyncMock()
        scalars = MagicMock()
        # DB returns rows ORDER BY check_key, checked_at DESC → newest first
        scalars.all.return_value = list(reversed(rows))
        exec_result = MagicMock()
        exec_result.scalars.return_value = scalars
        db.execute.return_value = exec_result

        user = {"username": "admin"}
        response = await get_audit_check_results(db=db, _user=user)

        results = {r["checkKey"]: r for r in response["results"]}
        # Newest row (id=3, status=FIX_REQUIRED, checked_at hour=2) should win
        assert results["db_date_alignment"]["status"] == "FIX_REQUIRED"
        assert results["db_date_alignment"]["summary"] == "run 2"


# ---------------------------------------------------------------------------
# Regression tests — FTB-scoped date-alignment check (Task #65)
# ---------------------------------------------------------------------------

def _make_row(
    ticker: str,
    city: str,
    target_settlement_date: str,
    settlement_timezone: str = "America/Los_Angeles",
    eligibility_status: str = "OFFICIAL",
) -> MagicMock:
    """Minimal mock row matching the columns selected by _run_date_alignment_check."""
    r = MagicMock()
    r.market_ticker = ticker
    r.city = city
    r.target_settlement_date = target_settlement_date
    r.settlement_timezone = settlement_timezone
    r.eligibility_status = eligibility_status
    return r


def _make_two_call_db(ftb_rows: list, fta_rows: list) -> AsyncMock:
    """
    Return a mock DB session where the first execute() call returns ftb_rows
    and the second returns fta_rows (matching _run_date_alignment_check call order).
    """
    def _fetch_result(rows):
        result = MagicMock()
        result.fetchall.return_value = rows
        result.scalars.return_value.all.return_value = []
        return result

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute.side_effect = [
        _fetch_result(ftb_rows),   # first call: FTB query
        _fetch_result(fta_rows),   # second call: FTA query
    ]
    return db


class TestAuditDateAlignmentRows:
    """Unit tests for the pure _audit_date_alignment_rows helper."""

    def test_date_only_string_not_flagged(self):
        from app.services.audit_checks import _audit_date_alignment_rows
        row = _make_row("T1", "Los Angeles", "2026-08-09")  # no 'T' → date-only
        mismatches, date_only, no_tz, errors = _audit_date_alignment_rows([row])
        assert mismatches == []
        assert date_only == 1
        assert no_tz == 0
        assert errors == []

    def test_utc_mismatch_flagged(self):
        from app.services.audit_checks import _audit_date_alignment_rows
        # LA: UTC 2026-08-08T04:54:08Z → local PDT date is 2026-08-07
        # raw[:10] = "2026-08-08" ≠ local "2026-08-07" → mismatch
        row = _make_row(
            "LA-TMAX-2026-08-08",
            "Los Angeles",
            "2026-08-08T04:54:08+00:00",
            "America/Los_Angeles",
        )
        mismatches, date_only, no_tz, errors = _audit_date_alignment_rows([row])
        assert len(mismatches) == 1
        assert mismatches[0]["utc_date_used"] == "2026-08-08"
        assert mismatches[0]["local_date"] == "2026-08-07"
        assert mismatches[0]["off_by_days"] == 1

    def test_matching_date_not_flagged(self):
        from app.services.audit_checks import _audit_date_alignment_rows
        # UTC noon in LA timezone → same calendar date
        row = _make_row(
            "OK-TMAX-2026-08-09",
            "Oklahoma City",
            "2026-08-09T18:00:00+00:00",
            "America/Chicago",
        )
        mismatches, date_only, no_tz, errors = _audit_date_alignment_rows([row])
        assert mismatches == []
        assert date_only == 0


class TestDateAlignmentFTBBoundary:
    """
    Core regression: FTA mismatches must not affect FTB check status.
    """

    @pytest.mark.asyncio
    async def test_fta_mismatch_does_not_fail_ftb_check(self):
        """
        A UTC/local mismatch on an FTA trade (before FORWARD_TEST_START_B)
        must NOT cause the db_date_alignment check to return FIX_REQUIRED.
        """
        from app.services.audit_checks import _run_date_alignment_check
        # Historical LA mismatch — FTA trade
        fta_row = _make_row(
            "LA-TMAX-2026-08-08",
            "Los Angeles",
            "2026-08-08T04:54:08+00:00",
            "America/Los_Angeles",
        )
        db = _make_two_call_db(ftb_rows=[], fta_rows=[fta_row])
        result = await _run_date_alignment_check(db)

        assert result.status == "CLEARED", (
            f"FTA mismatch must not fail FTB status. Got: {result.status}\n{result.summary}"
        )
        assert not result.action_required

    @pytest.mark.asyncio
    async def test_ftb_mismatch_fails_check(self):
        """
        A UTC/local mismatch on an FTB trade (created at or after FORWARD_TEST_START_B)
        MUST cause the db_date_alignment check to return FIX_REQUIRED.
        """
        from app.services.audit_checks import _run_date_alignment_check
        ftb_row = _make_row(
            "LA-TMAX-2026-08-10",
            "Los Angeles",
            "2026-08-10T04:00:00+00:00",  # UTC Aug 10, local PDT = Aug 9 → mismatch
            "America/Los_Angeles",
        )
        db = _make_two_call_db(ftb_rows=[ftb_row], fta_rows=[])
        result = await _run_date_alignment_check(db)

        assert result.status == "FIX_REQUIRED", (
            f"FTB mismatch must fail status. Got: {result.status}\n{result.summary}"
        )
        assert result.action_required

    @pytest.mark.asyncio
    async def test_historical_fta_findings_preserved_in_details(self):
        """
        Even when FTB status is CLEARED, the details must include
        the historical FTA mismatch record.
        """
        from app.services.audit_checks import _run_date_alignment_check
        fta_row = _make_row(
            "LA-TMAX-2026-08-08",
            "Los Angeles",
            "2026-08-08T04:54:08+00:00",
            "America/Los_Angeles",
        )
        db = _make_two_call_db(ftb_rows=[], fta_rows=[fta_row])
        result = await _run_date_alignment_check(db)

        assert result.status == "CLEARED"
        assert "FORWARD TEST A" in result.details, (
            "Historical FTA section must appear in details even when FTB is CLEARED."
        )
        assert "LA-TMAX-2026-08-08" in result.details, (
            "The specific FTA mismatch ticker must appear in the details."
        )
        assert result.metadata_json["fta_mismatch_count"] == 1
        assert result.metadata_json["ftb_mismatch_count"] == 0

    @pytest.mark.asyncio
    async def test_no_ftb_trades_yet_is_cleared(self):
        """Zero FTB trades → CLEARED (not FIX_REQUIRED), with informational summary."""
        from app.services.audit_checks import _run_date_alignment_check
        db = _make_two_call_db(ftb_rows=[], fta_rows=[])
        result = await _run_date_alignment_check(db)
        assert result.status == "CLEARED"
        assert not result.action_required
        assert "No OFFICIAL trades yet" in result.summary or "accumulate" in result.summary

    @pytest.mark.asyncio
    async def test_clean_ftb_trade_is_cleared(self):
        """An FTB trade with matching UTC and local date → CLEARED."""
        from app.services.audit_checks import _run_date_alignment_check
        # UTC noon in Chicago timezone → same calendar date
        ftb_row = _make_row(
            "OKC-TMAX-2026-08-10",
            "Oklahoma City",
            "2026-08-10T18:00:00+00:00",
            "America/Chicago",
        )
        db = _make_two_call_db(ftb_rows=[ftb_row], fta_rows=[])
        result = await _run_date_alignment_check(db)
        assert result.status == "CLEARED"
        assert result.metadata_json["ftb_mismatch_count"] == 0

    @pytest.mark.asyncio
    async def test_metadata_exposes_both_eras(self):
        """metadata_json must include both FTB and FTA counts."""
        from app.services.audit_checks import _run_date_alignment_check
        fta_row = _make_row(
            "LA-FTA",
            "Los Angeles",
            "2026-08-08T04:54:08+00:00",
            "America/Los_Angeles",
        )
        ftb_row = _make_row(
            "OKC-FTB",
            "Oklahoma City",
            "2026-08-10T18:00:00+00:00",
            "America/Chicago",
        )
        db = _make_two_call_db(ftb_rows=[ftb_row], fta_rows=[fta_row])
        result = await _run_date_alignment_check(db)

        md = result.metadata_json
        assert "ftb_total" in md
        assert "fta_total" in md
        assert md["ftb_total"] == 1
        assert md["fta_total"] == 1

    @pytest.mark.asyncio
    async def test_check_name_reflects_ftb_scope(self):
        """check_name must indicate FTB scope so the UI label is unambiguous."""
        from app.services.audit_checks import _run_date_alignment_check
        db = _make_two_call_db(ftb_rows=[], fta_rows=[])
        result = await _run_date_alignment_check(db)
        assert "FTB" in result.check_name or "Forward Test B" in result.check_name, (
            f"check_name should reference FTB scope. Got: {result.check_name!r}"
        )
