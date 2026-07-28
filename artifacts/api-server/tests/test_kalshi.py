"""Tests for Kalshi API client parsing and filtering logic."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.kalshi import parse_market, extract_city, _is_weather_market, CITY_COORDS, SERIES_TO_CITY, _detect_market_type


class TestExtractCity:
    def test_extracts_city_from_series_to_city_map(self):
        """Known series tickers resolve via SERIES_TO_CITY."""
        raw = {"ticker": "KXHIGHTDAL-26JUL28-T107", "series_ticker": "KXHIGHTDAL", "title": "", "subtitle": ""}
        city, lat, lon = extract_city(raw)
        assert city == "Dallas"
        assert lat == pytest.approx(32.7767, abs=0.01)

    def test_extracts_nyc_from_kx_series(self):
        raw = {"ticker": "KXLOWTNYC-26JUL28-T72", "series_ticker": "KXLOWTNYC", "title": "", "subtitle": ""}
        city, lat, lon = extract_city(raw)
        assert city == "New York City"

    def test_extracts_chicago_via_substring(self):
        """CHI is embedded in KXTEMPCHIH as a substring."""
        raw = {"ticker": "KXTEMPCHIH-26JUL28-01", "series_ticker": "KXTEMPCHIH", "title": "", "subtitle": ""}
        city, lat, lon = extract_city(raw)
        assert city == "Chicago"

    def test_extracts_houston_via_substring(self):
        raw = {"ticker": "KXHIGHOU-26JUL28-T95", "series_ticker": "KXHIGHOU", "title": "", "subtitle": ""}
        city, lat, lon = extract_city(raw)
        assert city == "Houston"

    def test_extracts_city_from_ticker_part(self):
        """Legacy style ticker with city code as exact dash-separated part."""
        raw = {"ticker": "WEATHER-NYC-20250801-GTE85", "series_ticker": "", "title": "", "subtitle": ""}
        city, lat, lon = extract_city(raw)
        assert city == "New York City"

    def test_extracts_city_from_title(self):
        raw = {"ticker": "UNKNOWN-001", "series_ticker": "", "title": "Will it rain in Miami today?", "subtitle": ""}
        city, lat, lon = extract_city(raw)
        assert city == "Miami"

    def test_returns_none_for_us_wide_series(self):
        """Series in SERIES_TO_CITY mapped to None should return no city."""
        raw = {"ticker": "HIGHUS-001", "series_ticker": "HIGHUS", "title": "US high temp", "subtitle": ""}
        city, lat, lon = extract_city(raw)
        assert city is None

    def test_returns_none_for_unknown_market(self):
        raw = {"ticker": "CRYPTO-BTC-001", "series_ticker": "KXBTCMAX", "title": "Will BTC hit $200k?", "subtitle": ""}
        city, lat, lon = extract_city(raw)
        assert city is None

    def test_city_code_longer_match_wins(self):
        """SATX (4 chars) should be matched before TX (not in map) or AT."""
        raw = {"ticker": "KXHIGHTTSATX-26JUL28-T101", "series_ticker": "KXHIGHTTSATX", "title": "", "subtitle": ""}
        city, lat, lon = extract_city(raw)
        assert city == "San Antonio"


class TestIsWeatherMarket:
    def test_detects_kx_series_from_series_to_city(self):
        assert _is_weather_market({"series_ticker": "KXHIGHTDAL", "title": "", "subtitle": "", "ticker": ""})
        assert _is_weather_market({"series_ticker": "KXRAINNY", "title": "", "subtitle": "", "ticker": ""})

    def test_detects_by_weather_series_list(self):
        assert _is_weather_market({"series_ticker": "KXBOSSNOWM", "title": "", "subtitle": "", "ticker": ""})
        assert _is_weather_market({"series_ticker": "KXLOWTNYC", "title": "", "subtitle": "", "ticker": ""})

    def test_detects_by_title_keyword_temperature(self):
        assert _is_weather_market({
            "series_ticker": "UNKNOWN",
            "ticker": "SOME-EVENT-001",
            "title": "Will the maximum temperature be >90° on Jul 28?",
            "subtitle": "",
        })

    def test_detects_by_title_keyword_snow(self):
        assert _is_weather_market({
            "series_ticker": "",
            "ticker": "SOME-001",
            "title": "Will it snow in Denver this week?",
            "subtitle": "",
        })

    def test_rejects_non_weather_political(self):
        assert not _is_weather_market({
            "series_ticker": "PRES",
            "ticker": "PRES-2024-DEM",
            "title": "Will Democrats win the presidency?",
            "subtitle": "",
        })

    def test_rejects_non_weather_crypto(self):
        assert not _is_weather_market({
            "series_ticker": "KXBTCMAX",
            "ticker": "KXBTCMAX-001",
            "title": "Will Bitcoin hit $200k by year end?",
            "subtitle": "",
        })


class TestDetectMarketType:
    def test_temperature(self):
        raw = {"series_ticker": "KXHIGHTDAL", "title": "Will the maximum temperature be >107°?", "subtitle": "", "ticker": ""}
        assert _detect_market_type(raw) == "temperature"

    def test_rain(self):
        raw = {"series_ticker": "KXRAINNY", "title": "Will it rain in NYC today?", "subtitle": "", "ticker": ""}
        assert _detect_market_type(raw) == "rain"

    def test_snow(self):
        raw = {"series_ticker": "KXSNOWNY", "title": "NYC snowfall this month", "subtitle": "", "ticker": ""}
        assert _detect_market_type(raw) == "snow"

    def test_snow_before_rain(self):
        """Snow should be matched before rain even if both keywords appear."""
        raw = {"series_ticker": "", "title": "Snow and rain in Boston", "subtitle": "", "ticker": ""}
        assert _detect_market_type(raw) == "snow"


class TestParseMarket:
    @pytest.fixture
    def sample_kx_market(self):
        return {
            "ticker": "KXHIGHTDAL-26JUL28-T107",
            "event_ticker": "KXHIGHTDAL-26JUL28",
            "series_ticker": "KXHIGHTDAL",
            "title": "Will the maximum temperature be >107° on Jul 28, 2026?",
            "subtitle": None,
            "open_time": "2026-07-27T06:00:00Z",
            "close_time": "2026-07-28T23:59:00Z",
            "status": "active",
            "yes_bid": 45,
            "yes_ask": 55,
            "no_bid": 45,
            "no_ask": 55,
            "volume": 250,
        }

    def test_city_extracted_from_kx_series(self, sample_kx_market):
        parsed = parse_market(sample_kx_market)
        assert parsed["city"] == "Dallas"

    def test_weather_market_type_detected(self, sample_kx_market):
        parsed = parse_market(sample_kx_market)
        assert parsed["weather_market_type"] == "temperature"

    def test_price_normalisation_from_cents(self, sample_kx_market):
        parsed = parse_market(sample_kx_market)
        assert parsed["yes_bid"] == pytest.approx(0.45, abs=0.001)
        assert parsed["yes_ask"] == pytest.approx(0.55, abs=0.001)

    def test_price_already_fractional(self):
        raw = {
            "ticker": "KXHIGHTDAL-26JUL28-T100",
            "event_ticker": None,
            "series_ticker": "KXHIGHTDAL",
            "title": "Test",
            "subtitle": None,
            "open_time": None,
            "close_time": None,
            "status": "active",
            "yes_bid": 0.45,
            "yes_ask": 0.55,
            "no_bid": 0.45,
            "no_ask": 0.55,
            "volume": 100,
        }
        parsed = parse_market(raw)
        assert parsed["yes_bid"] == pytest.approx(0.45, abs=0.001)

    def test_parses_iso_timestamps(self, sample_kx_market):
        parsed = parse_market(sample_kx_market)
        assert parsed["close_time"] is not None
        assert parsed["close_time"].year == 2026

    def test_parses_unix_timestamps(self):
        raw = {
            "ticker": "KXLOWTNYC-26JUL28-T65",
            "event_ticker": None,
            "series_ticker": "KXLOWTNYC",
            "title": "Min temp NYC",
            "subtitle": None,
            "open_time": 1753920000,
            "close_time": 1754006400,
            "status": "active",
            "yes_bid": None,
            "yes_ask": None,
            "no_bid": None,
            "no_ask": None,
            "volume": 0,
        }
        parsed = parse_market(raw)
        assert parsed["open_time"] is not None
        assert parsed["city"] == "New York City"

    def test_missing_prices_are_none(self):
        raw = {
            "ticker": "KXRAINSEA-26JUL28-Y",
            "event_ticker": None,
            "series_ticker": "KXRAINSEA",
            "title": "Will it rain in Seattle?",
            "subtitle": None,
            "open_time": None,
            "close_time": None,
            "status": "active",
            "yes_bid": None,
            "yes_ask": None,
            "no_bid": None,
            "no_ask": None,
            "volume": None,
        }
        parsed = parse_market(raw)
        assert parsed["yes_bid"] is None
        assert parsed["no_ask"] is None
        assert parsed["city"] == "Seattle"
        assert parsed["weather_market_type"] == "rain"
