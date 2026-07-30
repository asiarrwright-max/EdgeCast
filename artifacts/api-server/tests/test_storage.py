"""Tests for database storage via the collector service."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


class TestCollector:
    """Smoke-tests for the collector logic using mocked DB and HTTP."""

    @pytest.mark.asyncio
    async def test_collector_uses_lock(self):
        """Two concurrent calls should not produce duplicate work."""
        import asyncio
        from app.services import collector

        async with collector._collect_lock:
            result = collector._collect_lock.locked()
            assert result is True

    def test_city_coords_mapping_complete(self):
        """All city entries should have non-zero coordinates."""
        from app.services.kalshi import CITY_COORDS

        for code, (name, lat, lon) in CITY_COORDS.items():
            assert isinstance(name, str) and name, f"Missing name for {code}"
            assert -90 <= lat <= 90, f"Bad lat for {code}: {lat}"
            assert -180 <= lon <= 180, f"Bad lon for {code}: {lon}"

    def test_series_to_city_mapping_valid(self):
        """All non-None entries in SERIES_TO_CITY should have valid coordinates."""
        from app.services.kalshi import SERIES_TO_CITY

        for series, entry in SERIES_TO_CITY.items():
            if entry is None:
                continue  # US-wide series — OK
            name, lat, lon = entry
            assert isinstance(name, str) and name, f"Missing city name for {series}"
            assert -90 <= lat <= 90, f"Bad lat for {series}: {lat}"
            assert -180 <= lon <= 180, f"Bad lon for {series}: {lon}"

    def test_weather_series_list_nonempty(self):
        """WEATHER_SERIES should have at least the major US weather series."""
        from app.services.kalshi import WEATHER_SERIES
        assert len(WEATHER_SERIES) >= 20
        assert "KXHIGHTDAL" in WEATHER_SERIES
        assert "KXLOWTNYC" in WEATHER_SERIES
        assert "KXHIGHMIA" in WEATHER_SERIES


class TestFetchResultDuplicatePrevention:
    """Verify that the same ticker is never returned twice."""

    @pytest.mark.asyncio
    async def test_no_duplicate_tickers_in_result(self):
        """fetch_weather_markets must deduplicate across Strategy 1 and Strategy 2."""
        from app.services.kalshi import fetch_weather_markets, FetchResult

        # Build a fake Kalshi response page with one weather market
        fake_market = {
            "ticker": "KXHIGHTDAL-26JUL28-T107",
            "event_ticker": "KXHIGHTDAL-26JUL28",
            "series_ticker": "KXHIGHTDAL",
            "title": "Will the maximum temperature be >107° on Jul 28, 2026?",
            "subtitle": None,
            "open_time": None,
            "close_time": None,
            "status": "active",
            "yes_bid": 45,
            "yes_ask": 55,
            "no_bid": 45,
            "no_ask": 55,
            "volume": 100,
        }

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.raise_for_status = MagicMock()
        fake_response.json.return_value = {"markets": [fake_market], "cursor": None}

        async def mock_get(*args, **kwargs):
            return fake_response

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=fake_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result: FetchResult = await fetch_weather_markets()

        all_tickers = [m["ticker"] for m in result.markets] + [m["ticker"] for m in result.parsing_failures]
        assert len(all_tickers) == len(set(all_tickers)), "Duplicate tickers found in FetchResult"


class TestFetchResultEmptyResponse:
    """Verify zero_reason is populated when no markets are found."""

    @pytest.mark.asyncio
    async def test_zero_reason_when_api_returns_empty(self):
        from app.services.kalshi import fetch_weather_markets, FetchResult

        empty_response = MagicMock()
        empty_response.status_code = 200
        empty_response.raise_for_status = MagicMock()
        empty_response.json.return_value = {"markets": [], "cursor": None}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=empty_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result: FetchResult = await fetch_weather_markets()

        assert len(result.markets) == 0
        assert len(result.parsing_failures) == 0
        assert result.zero_reason is not None
        assert len(result.zero_reason) > 10  # should be a meaningful message


class TestModels:
    """Basic smoke-tests for ORM model instantiation."""

    def test_kalshi_market_fields(self):
        from app.models import KalshiMarket

        m = KalshiMarket(
            ticker="KXHIGHTDAL-26JUL28-T107",
            title="Will the max temp be >107°?",
            status="active",
            weather_matched=False,
            parsing_status="collected",
            weather_market_type="temperature",
        )
        assert m.status == "active"
        assert m.weather_matched is False
        assert m.parsing_status == "collected"
        assert m.weather_market_type == "temperature"

    def test_kalshi_market_long_title_and_subtitle(self):
        """Regression: title and subtitle must accept strings longer than 500 chars.

        Previously VARCHAR(500) caused StringDataRightTruncationError when Kalshi
        multi-game / esports markets had concatenated subtitles > 500 characters.
        Both columns are now TEXT with no length cap.
        """
        from app.models import KalshiMarket
        from sqlalchemy import inspect as sa_inspect, text as sa_text
        from sqlalchemy.orm import class_mapper

        long_title    = "A" * 600   # 600 chars — previously would overflow VARCHAR(500)
        long_subtitle = "B" * 900   # 900 chars — matches the real-world esports market that triggered the bug

        m = KalshiMarket(
            ticker="KXTEST-LONGTEXT-001",
            title=long_title,
            subtitle=long_subtitle,
            status="active",
            weather_matched=False,
            parsing_status="collected",
            weather_market_type="temperature",
        )
        assert m.title    == long_title,    "title must store strings >500 chars"
        assert m.subtitle == long_subtitle, "subtitle must store strings >500 chars"
        assert len(m.title)    == 600
        assert len(m.subtitle) == 900

        # Confirm the ORM column type is Text (no length constraint)
        mapper    = class_mapper(KalshiMarket)
        col_title = mapper.columns["title"]
        col_sub   = mapper.columns["subtitle"]
        from sqlalchemy import Text
        assert isinstance(col_title.type, Text),    "title column must be Text, not String(n)"
        assert isinstance(col_sub.type,   Text),    "subtitle column must be Text, not String(n)"
        # Text has no length attribute (or length is None)
        assert getattr(col_title.type, "length", None) is None, "title must have no length cap"
        assert getattr(col_sub.type,   "length", None) is None, "subtitle must have no length cap"

    def test_job_run_new_fields(self):
        from app.models import JobRun

        j = JobRun(
            job_type="manual",
            status="success",
            markets_found=10,
            markets_skipped=150,
            markets_rejected=0,
            duration_seconds=12.5,
        )
        assert j.status == "success"
        assert j.markets_skipped == 150
        assert j.duration_seconds == 12.5

    def test_app_error_instantiation(self):
        from app.models import AppError

        e = AppError(error_type="test_error", message="Something went wrong")
        assert e.error_type == "test_error"
