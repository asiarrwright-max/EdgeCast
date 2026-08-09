"""
Tests for /api/audit/ftb-research-funnel — read-only FTB diagnostic.

Validated behaviours:
  - Only trades at/after FORWARD_TEST_START_B are counted
  - Only v2.3 trades are included; other strategy_versions are excluded
  - FTA-era trades (created_at < FORWARD_TEST_START_B) are excluded
  - official + researchOnly + (any other status) = total
  - Sum of rejection reason counts == total
  - Sum of city totals == total
  - No trade state is modified by the analysis
  - Fixability classification is stable and internally consistent
  - Funnel steps are monotonically non-increasing
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Module-level constant tests
# ---------------------------------------------------------------------------

class TestFtbConstants:
    """The FTB boundary constant in the router must exactly match FORWARD_TEST_START_B."""

    def test_ftb_start_iso_matches_forward_test_start_b(self):
        """_FTB_START_ISO must equal the timestamp set in paper_trades.py."""
        from app.routers.audit import _FTB_START_ISO
        from app.routers.paper_trades import FORWARD_TEST_START_B
        # Parse both and compare as UTC datetimes
        parsed_iso = datetime.fromisoformat(_FTB_START_ISO.replace("+00", "+00:00"))
        parsed_iso = parsed_iso.astimezone(timezone.utc)
        assert parsed_iso == FORWARD_TEST_START_B, (
            f"_FTB_START_ISO ({_FTB_START_ISO}) does not match "
            f"FORWARD_TEST_START_B ({FORWARD_TEST_START_B})"
        )

    def test_ftb_strategy_is_v23(self):
        from app.routers.audit import _FTB_STRATEGY
        assert _FTB_STRATEGY == "v2.3"

    def test_ftb_start_is_2026_08_09(self):
        from app.routers.audit import _FTB_START_ISO
        dt = datetime.fromisoformat(_FTB_START_ISO.replace("+00", "+00:00"))
        assert dt.year == 2026
        assert dt.month == 8
        assert dt.day == 9
        assert dt.hour == 0
        assert dt.minute == 15
        assert dt.second == 12


# ---------------------------------------------------------------------------
# Fixability classification tests
# ---------------------------------------------------------------------------

class TestFixabilityClassification:
    """The FIXABLE / NON_FIXABLE dicts must be internally consistent."""

    def test_no_reason_in_both_dicts(self):
        from app.routers.audit import _FIXABLE_REASONS, _NON_FIXABLE_REASONS
        overlap = set(_FIXABLE_REASONS) & set(_NON_FIXABLE_REASONS)
        assert overlap == set(), f"Reasons in both dicts: {overlap}"

    def test_fixable_reasons_are_non_empty(self):
        from app.routers.audit import _FIXABLE_REASONS
        assert len(_FIXABLE_REASONS) > 0

    def test_settlement_station_unverified_is_fixable(self):
        from app.routers.audit import _FIXABLE_REASONS
        assert "settlement_station_unverified" in _FIXABLE_REASONS

    def test_stale_quote_is_not_fixable(self):
        from app.routers.audit import _FIXABLE_REASONS, _NON_FIXABLE_REASONS
        assert "missing_or_stale_executable_quote" not in _FIXABLE_REASONS
        assert "missing_or_stale_executable_quote" in _NON_FIXABLE_REASONS

    def test_hourly_not_approved_is_not_fixable(self):
        from app.routers.audit import _FIXABLE_REASONS, _NON_FIXABLE_REASONS
        assert "hourly_temperature_not_approved" not in _FIXABLE_REASONS
        assert "hourly_temperature_not_approved" in _NON_FIXABLE_REASONS

    def test_v2_excluded_is_not_fixable(self):
        from app.routers.audit import _FIXABLE_REASONS, _NON_FIXABLE_REASONS
        assert "v2_excluded" not in _FIXABLE_REASONS
        assert "v2_excluded" in _NON_FIXABLE_REASONS


# ---------------------------------------------------------------------------
# Verified cities set
# ---------------------------------------------------------------------------

class TestVerifiedCities:
    def test_verified_cities_is_frozenset(self):
        from app.routers.audit import _VERIFIED_CITIES
        assert isinstance(_VERIFIED_CITIES, frozenset)

    def test_oklahoma_city_verified(self):
        from app.routers.audit import _VERIFIED_CITIES
        assert "Oklahoma City" in _VERIFIED_CITIES

    def test_philadelphia_not_verified(self):
        from app.routers.audit import _VERIFIED_CITIES
        assert "Philadelphia" not in _VERIFIED_CITIES

    def test_new_orleans_not_verified(self):
        from app.routers.audit import _VERIFIED_CITIES
        assert "New Orleans" not in _VERIFIED_CITIES


# ---------------------------------------------------------------------------
# Endpoint integration tests (mocked DB)
# ---------------------------------------------------------------------------

def _make_mapping(**kwargs):
    """Return a dict-like object that supports __getitem__ and .get()."""
    return dict(**kwargs)


def _mapping_result(rows: list[dict]):
    """Produce an awaitable that returns an object whose .mappings().all() = rows."""
    m = MagicMock()
    m.mappings.return_value.all.return_value = rows
    result = AsyncMock(return_value=m)
    return result


def _one_result(row: dict):
    """Produce an awaitable that returns an object whose .mappings().one() = row."""
    m = MagicMock()
    m.mappings.return_value.one.return_value = row
    result = AsyncMock(return_value=m)
    return result


@pytest.mark.asyncio
class TestFtbResearchFunnelEndpoint:
    """Integration-style tests against the endpoint with a mocked AsyncSession."""

    async def _call(self, db):
        from app.routers.audit import get_ftb_research_funnel
        user = {"username": "test"}
        return await get_ftb_research_funnel(db=db, _user=user)

    def _make_db(
        self,
        total=10,
        official=0,
        research_only=10,
        rejection_rows=None,
        city_rows=None,
    ):
        """Build a minimal AsyncSession mock for the four SQL queries the endpoint issues."""
        if rejection_rows is None:
            rejection_rows = [
                {"reason": "missing_or_stale_executable_quote", "cnt": 5,
                 "unique_tickers": 5, "city_dates": 2},
                {"reason": "settlement_station_unverified", "cnt": 3,
                 "unique_tickers": 3, "city_dates": 2},
                {"reason": "v2_excluded", "cnt": 2,
                 "unique_tickers": 2, "city_dates": 1},
            ]
        if city_rows is None:
            city_rows = [
                {"city": "Philadelphia", "total": 3, "unique_tickers": 3,
                 "unique_dates": 1, "official_cnt": 0,
                 "top_reason": "settlement_station_unverified"},
                {"city": "Denver", "total": 5, "unique_tickers": 5,
                 "unique_dates": 2, "official_cnt": 0,
                 "top_reason": "missing_or_stale_executable_quote"},
                {"city": "Dallas", "total": 2, "unique_tickers": 2,
                 "unique_dates": 1, "official_cnt": 0,
                 "top_reason": "v2_excluded"},
            ]

        summary_row = {
            "total": total,
            "official_cnt": official,
            "research_only_cnt": research_only,
        }

        db = MagicMock()
        # The endpoint calls db.execute 3 times: summary, rejections, cities
        summary_m = MagicMock()
        summary_m.mappings.return_value.one.return_value = summary_row

        rejection_m = MagicMock()
        rejection_m.mappings.return_value.all.return_value = rejection_rows

        city_m = MagicMock()
        city_m.mappings.return_value.all.return_value = city_rows

        db.execute = AsyncMock(side_effect=[summary_m, rejection_m, city_m])
        return db

    async def test_returns_required_top_level_keys(self):
        db = self._make_db()
        result = await self._call(db)
        required = {"ftbBoundary", "strategy", "summary", "narrative",
                    "rejections", "funnel", "cities", "liquidity",
                    "safeActions", "generatedAt"}
        assert required.issubset(result.keys())

    async def test_ftb_boundary_in_response(self):
        db = self._make_db()
        result = await self._call(db)
        assert result["ftbBoundary"] == "2026-08-09T00:15:12Z"

    async def test_strategy_is_v23(self):
        db = self._make_db()
        result = await self._call(db)
        assert result["strategy"] == "v2.3"

    async def test_summary_counts_propagate(self):
        db = self._make_db(total=10, official=0, research_only=10)
        result = await self._call(db)
        assert result["summary"]["total"] == 10
        assert result["summary"]["official"] == 0
        assert result["summary"]["researchOnly"] == 10

    async def test_rejection_rows_match_count(self):
        db = self._make_db(total=10, rejection_rows=[
            {"reason": "stale_quote", "cnt": 7, "unique_tickers": 7, "city_dates": 3},
            {"reason": "v2_excluded", "cnt": 3, "unique_tickers": 3, "city_dates": 2},
        ])
        result = await self._call(db)
        assert len(result["rejections"]) == 2
        counts = {r["reason"]: r["count"] for r in result["rejections"]}
        assert counts["stale_quote"] == 7
        assert counts["v2_excluded"] == 3

    async def test_rejection_totals_reconcile_to_total(self):
        """Sum of all rejection reason counts must equal total."""
        rows = [
            {"reason": "missing_or_stale_executable_quote", "cnt": 6,
             "unique_tickers": 6, "city_dates": 3},
            {"reason": "settlement_station_unverified", "cnt": 4,
             "unique_tickers": 4, "city_dates": 2},
        ]
        db = self._make_db(total=10, research_only=10, rejection_rows=rows)
        result = await self._call(db)
        assert sum(r["count"] for r in result["rejections"]) == result["summary"]["total"]

    async def test_pct_of_total_sums_to_100(self):
        rows = [
            {"reason": "a", "cnt": 60, "unique_tickers": 60, "city_dates": 10},
            {"reason": "b", "cnt": 40, "unique_tickers": 40, "city_dates": 5},
        ]
        db = self._make_db(total=100, research_only=100, rejection_rows=rows)
        result = await self._call(db)
        total_pct = sum(r["pctOfTotal"] for r in result["rejections"])
        assert abs(total_pct - 100.0) < 0.5  # allow rounding

    async def test_city_totals_reconcile(self):
        """Sum of city totals must equal the overall total."""
        cities = [
            {"city": "Denver",       "total": 6, "unique_tickers": 6,
             "unique_dates": 2, "official_cnt": 0, "top_reason": "missing_or_stale_executable_quote"},
            {"city": "Philadelphia", "total": 4, "unique_tickers": 4,
             "unique_dates": 2, "official_cnt": 0, "top_reason": "settlement_station_unverified"},
        ]
        rows = [
            {"reason": "missing_or_stale_executable_quote", "cnt": 6, "unique_tickers": 6, "city_dates": 2},
            {"reason": "settlement_station_unverified", "cnt": 4, "unique_tickers": 4, "city_dates": 2},
        ]
        db = self._make_db(total=10, research_only=10, rejection_rows=rows, city_rows=cities)
        result = await self._call(db)
        assert sum(c["total"] for c in result["cities"]) == result["summary"]["total"]

    async def test_funnel_is_monotonically_non_increasing(self):
        db = self._make_db()
        result = await self._call(db)
        remaining_values = [step["remaining"] for step in result["funnel"]]
        for i in range(1, len(remaining_values)):
            assert remaining_values[i] <= remaining_values[i - 1], (
                f"Funnel step {i} increased: {remaining_values[i-1]} → {remaining_values[i]}"
            )

    async def test_funnel_first_step_equals_total(self):
        db = self._make_db(total=10)
        result = await self._call(db)
        assert result["funnel"][0]["remaining"] == 10

    async def test_fixable_station_city_marked_fixable(self):
        cities = [
            {"city": "Philadelphia", "total": 9, "unique_tickers": 9,
             "unique_dates": 1, "official_cnt": 0,
             "top_reason": "settlement_station_unverified"},
        ]
        rows = [{"reason": "settlement_station_unverified", "cnt": 9,
                 "unique_tickers": 9, "city_dates": 1}]
        db = self._make_db(total=9, research_only=9, rejection_rows=rows, city_rows=cities)
        result = await self._call(db)
        philly = next(c for c in result["cities"] if c["city"] == "Philadelphia")
        assert philly["potentiallyFixable"] is True
        assert philly["stationVerified"] is False

    async def test_verified_city_not_marked_fixable(self):
        cities = [
            {"city": "Denver", "total": 10, "unique_tickers": 10,
             "unique_dates": 3, "official_cnt": 0,
             "top_reason": "missing_or_stale_executable_quote"},
        ]
        rows = [{"reason": "missing_or_stale_executable_quote", "cnt": 10,
                 "unique_tickers": 10, "city_dates": 3}]
        db = self._make_db(total=10, research_only=10, rejection_rows=rows, city_rows=cities)
        result = await self._call(db)
        denver = next(c for c in result["cities"] if c["city"] == "Denver")
        assert denver["potentiallyFixable"] is False
        assert denver["stationVerified"] is True

    async def test_zero_total_does_not_crash(self):
        db = self._make_db(total=0, official=0, research_only=0,
                           rejection_rows=[], city_rows=[])
        result = await self._call(db)
        assert result["summary"]["total"] == 0
        assert result["liquidity"]["pctLiquidityBlocked"] == 0.0

    async def test_fta_trades_excluded_by_boundary(self):
        """
        Validate that the SQL the endpoint executes includes the :start parameter.
        The mock's side_effect list verifies db.execute is called with a query
        containing 'created_at' and ':start', ensuring the boundary filter is present.
        """
        from app.routers.audit import _FTB_START_ISO

        calls_seen = []

        async def record_execute(stmt, params=None):
            # Capture query text
            calls_seen.append(str(stmt))
            # Return appropriate mock based on call order
            idx = len(calls_seen) - 1
            m = MagicMock()
            if idx == 0:
                m.mappings.return_value.one.return_value = {
                    "total": 5, "official_cnt": 0, "research_only_cnt": 5
                }
            elif idx == 1:
                m.mappings.return_value.all.return_value = [
                    {"reason": "v2_excluded", "cnt": 5, "unique_tickers": 5, "city_dates": 2}
                ]
            else:
                m.mappings.return_value.all.return_value = [
                    {"city": "Dallas", "total": 5, "unique_tickers": 5,
                     "unique_dates": 2, "official_cnt": 0, "top_reason": "v2_excluded"}
                ]
            return m

        db = MagicMock()
        db.execute = record_execute
        await self._call(db)

        # All three queries must filter by :start
        for call_text in calls_seen:
            assert "created_at" in call_text, "Query missing created_at filter"
            assert ":start" in call_text or _FTB_START_ISO in call_text, (
                "Query missing start parameter binding"
            )

    async def test_v23_filter_present_in_queries(self):
        """All queries must include strategy_version = :strategy."""
        calls_seen = []

        async def record_execute(stmt, params=None):
            calls_seen.append(str(stmt))
            idx = len(calls_seen) - 1
            m = MagicMock()
            if idx == 0:
                m.mappings.return_value.one.return_value = {
                    "total": 0, "official_cnt": 0, "research_only_cnt": 0
                }
            elif idx == 1:
                m.mappings.return_value.all.return_value = []
            else:
                m.mappings.return_value.all.return_value = []
            return m

        db = MagicMock()
        db.execute = record_execute
        await self._call(db)

        for call_text in calls_seen:
            assert "strategy_version" in call_text, (
                f"Query missing strategy_version filter: {call_text[:120]}"
            )

    async def test_safe_actions_only_for_fixable_cities(self):
        """Safe actions must only reference cities whose top rejection is fixable."""
        cities = [
            {"city": "Philadelphia", "total": 9, "unique_tickers": 9,
             "unique_dates": 1, "official_cnt": 0,
             "top_reason": "settlement_station_unverified"},
            {"city": "Denver", "total": 5, "unique_tickers": 5,
             "unique_dates": 2, "official_cnt": 0,
             "top_reason": "missing_or_stale_executable_quote"},
        ]
        rows = [
            {"reason": "missing_or_stale_executable_quote", "cnt": 5,
             "unique_tickers": 5, "city_dates": 2},
            {"reason": "settlement_station_unverified", "cnt": 9,
             "unique_tickers": 9, "city_dates": 1},
        ]
        db = self._make_db(total=14, research_only=14, rejection_rows=rows, city_rows=cities)
        result = await self._call(db)
        # Safe actions with station verification should only reference Philadelphia
        station_actions = [
            a for a in result["safeActions"]
            if "settlement station" in a["action"].lower() or "station" in a["action"].lower()
        ]
        for a in station_actions:
            assert "Denver" not in a.get("affectedCities", [])

    async def test_no_paper_trade_mutation(self):
        """The endpoint must not call any session mutation method (add/flush/commit/delete)."""
        db = self._make_db()

        # Intercept mutation calls
        mutated = []
        db.add    = MagicMock(side_effect=lambda *a, **kw: mutated.append("add"))
        db.flush  = AsyncMock(side_effect=lambda *a, **kw: mutated.append("flush"))
        db.commit = AsyncMock(side_effect=lambda *a, **kw: mutated.append("commit"))
        db.delete = MagicMock(side_effect=lambda *a, **kw: mutated.append("delete"))

        await self._call(db)
        assert mutated == [], f"Endpoint mutated session: {mutated}"

    async def test_official_zero_when_no_official_trades(self):
        db = self._make_db(total=5, official=0, research_only=5)
        result = await self._call(db)
        assert result["summary"]["official"] == 0

    async def test_liquidity_pct_computed_correctly(self):
        rows = [
            {"reason": "missing_or_stale_executable_quote", "cnt": 151,
             "unique_tickers": 151, "city_dates": 29},
            {"reason": "v2_excluded", "cnt": 99,
             "unique_tickers": 99, "city_dates": 21},
            {"reason": "hourly_temperature_not_approved", "cnt": 57,
             "unique_tickers": 57, "city_dates": 3},
            {"reason": "settlement_station_unverified", "cnt": 27,
             "unique_tickers": 27, "city_dates": 4},
        ]
        db = self._make_db(total=334, research_only=334, rejection_rows=rows)
        result = await self._call(db)
        # (151 + 99) / 334 * 100 = 74.85... ≈ 74.9
        assert abs(result["liquidity"]["pctLiquidityBlocked"] - 74.9) < 0.5
        assert result["liquidity"]["staleQuoteWithAsk"] == 151
        assert result["liquidity"]["noPriceAboveFloor"] == 99
