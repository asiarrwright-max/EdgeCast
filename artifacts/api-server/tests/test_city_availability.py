"""
Tests for city_availability.py — status computation logic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(city_last_seen: dict[str, datetime]):
    """Return an AsyncSession mock whose kalshi_markets query returns the given data."""
    session = AsyncMock()

    rows = [(city, ts) for city, ts in city_last_seen.items()]

    class _FakeRow:
        def __init__(self, row):
            self._row = row

        def all(self):
            return self._row

    session.execute = AsyncMock(return_value=_FakeRow(rows))
    return session


def _recent(hours_ago: float = 1.0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours_ago)


def _stale(hours_ago: float = 20.0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours_ago)


# ---------------------------------------------------------------------------
# _classify — unit tests (no DB)
# ---------------------------------------------------------------------------

class TestClassify:
    def _station(self, verified: bool, nws_settlement: bool = True):
        s = MagicMock()
        s.verified = verified
        s.nws_settlement = nws_settlement
        return s

    def test_blocked_when_nws_settlement_false(self):
        from app.services.city_availability import _classify
        status, reason = _classify("Washington DC", self._station(False, nws_settlement=False), _recent())
        assert status == "blocked"
        assert reason is not None
        assert "Weather Company" in reason or "NWS" in reason

    def test_blocked_even_if_verified_true(self):
        """blocked overrides verified — nws_settlement=False is the hard stop."""
        from app.services.city_availability import _classify
        status, reason = _classify("Washington DC", self._station(True, nws_settlement=False), _recent())
        assert status == "blocked"

    def test_active_when_verified_and_recent_markets(self):
        from app.services.city_availability import _classify
        status, reason = _classify("Denver", self._station(True), _recent(hours_ago=1))
        assert status == "active"
        assert reason is None

    def test_inactive_when_verified_but_stale_markets(self):
        from app.services.city_availability import _classify
        from app.services.city_availability import MARKET_STALENESS_HOURS
        status, reason = _classify("Denver", self._station(True), _stale(hours_ago=MARKET_STALENESS_HOURS + 1))
        assert status == "inactive"
        assert reason is not None
        assert "markets" in reason.lower()

    def test_inactive_when_unverified_with_recent_markets(self):
        from app.services.city_availability import _classify
        status, reason = _classify("Atlanta", self._station(False), _recent())
        assert status == "inactive"
        assert "verified" in reason.lower()

    def test_inactive_when_unverified_and_no_markets(self):
        from app.services.city_availability import _classify
        status, reason = _classify("Portland", self._station(False), None)
        assert status == "inactive"
        assert reason is not None

    def test_inactive_when_verified_and_no_markets(self):
        from app.services.city_availability import _classify
        status, reason = _classify("Boston", self._station(True), None)
        assert status == "inactive"
        assert "markets" in reason.lower()


# ---------------------------------------------------------------------------
# compute_city_statuses — integration tests with mocked session
# ---------------------------------------------------------------------------

class TestComputeCityStatuses:
    @pytest.mark.asyncio
    async def test_dc_is_always_blocked(self):
        """Washington DC must be blocked regardless of Kalshi market activity."""
        from app.services.city_availability import compute_city_statuses
        session = _make_session({"Washington DC": _recent()})
        statuses = await compute_city_statuses(session)
        dc = next(s for s in statuses if s["city"] == "Washington DC")
        assert dc["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_verified_city_with_recent_market_is_active(self):
        from app.services.city_availability import compute_city_statuses
        session = _make_session({
            "Denver": _recent(hours_ago=2),
            "New York City": _recent(hours_ago=1),
        })
        statuses = await compute_city_statuses(session)
        city_map = {s["city"]: s for s in statuses}
        assert city_map["Denver"]["status"] == "active"
        assert city_map["New York City"]["status"] == "active"

    @pytest.mark.asyncio
    async def test_unverified_city_with_recent_market_is_inactive(self):
        from app.services.city_availability import compute_city_statuses
        session = _make_session({"Atlanta": _recent()})
        statuses = await compute_city_statuses(session)
        atlanta = next(s for s in statuses if s["city"] == "Atlanta")
        assert atlanta["status"] == "inactive"
        assert "verified" in (atlanta["reason"] or "").lower()

    @pytest.mark.asyncio
    async def test_city_without_markets_is_inactive(self):
        from app.services.city_availability import compute_city_statuses
        session = _make_session({})  # No markets for any city
        statuses = await compute_city_statuses(session)
        for s in statuses:
            if s["status"] != "blocked":
                assert s["status"] == "inactive"

    @pytest.mark.asyncio
    async def test_all_cities_present(self):
        from app.services.city_availability import compute_city_statuses
        from app.services.settlement_stations import SETTLEMENT_STATIONS
        session = _make_session({})
        statuses = await compute_city_statuses(session)
        returned_cities = {s["city"] for s in statuses}
        assert returned_cities == set(SETTLEMENT_STATIONS.keys())

    @pytest.mark.asyncio
    async def test_status_fields_present(self):
        from app.services.city_availability import compute_city_statuses
        session = _make_session({"Denver": _recent()})
        statuses = await compute_city_statuses(session)
        for s in statuses:
            assert "city" in s
            assert "status" in s
            assert s["status"] in ("active", "inactive", "blocked")
            assert "verified" in s
            assert "nwsSettlement" in s
            assert "lastMarketSeenAt" in s


# ---------------------------------------------------------------------------
# get_active_cities
# ---------------------------------------------------------------------------

class TestGetActiveCities:
    @pytest.mark.asyncio
    async def test_returns_verified_cities_with_recent_markets(self):
        from app.services.city_availability import get_active_cities
        # Denver, NYC, Chicago are all verified; give them recent markets
        session = _make_session({
            "Denver": _recent(),
            "New York City": _recent(),
            "Chicago": _recent(),
            "Atlanta": _recent(),  # unverified — should NOT be active
        })
        active = await get_active_cities(session)
        assert "Denver" in active
        assert "New York City" in active
        assert "Chicago" in active
        assert "Atlanta" not in active
        assert "Washington DC" not in active  # blocked

    @pytest.mark.asyncio
    async def test_dc_never_active(self):
        from app.services.city_availability import get_active_cities
        session = _make_session({"Washington DC": _recent()})
        active = await get_active_cities(session)
        assert "Washington DC" not in active


# ---------------------------------------------------------------------------
# summarise
# ---------------------------------------------------------------------------

class TestSummarise:
    def test_counts_are_correct(self):
        from app.services.city_availability import summarise
        statuses = [
            {"city": "A", "status": "active"},
            {"city": "B", "status": "active"},
            {"city": "C", "status": "inactive"},
            {"city": "D", "status": "blocked"},
        ]
        s = summarise(statuses)
        assert s["activeCount"] == 2
        assert s["inactiveCount"] == 1
        assert s["blockedCount"] == 1
        assert s["totalCount"] == 4
        assert set(s["activeCities"]) == {"A", "B"}
        assert s["inactiveCities"] == ["C"]
        assert s["blockedCities"] == ["D"]
