"""Tests for Kalshi API client parsing logic."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.kalshi import parse_market, extract_city, _is_weather_market, CITY_COORDS


class TestExtractCity:
    def test_extracts_nyc_from_ticker(self):
        raw = {"ticker": "HIGHTEMP-NYC-20250801-GTE85", "title": "", "subtitle": ""}
        city, lat, lon = extract_city(raw)
        assert city == "New York City"
        assert lat == pytest.approx(40.7128, abs=0.01)
        assert lon == pytest.approx(-74.0060, abs=0.01)

    def test_extracts_chi_from_ticker(self):
        raw = {"ticker": "LOWTEMP-CHI-20250801-LTE32", "title": "", "subtitle": ""}
        city, lat, lon = extract_city(raw)
        assert city == "Chicago"

    def test_extracts_city_from_title(self):
        raw = {"ticker": "WEATHER-UNKNOWN-001", "title": "Will it rain in Miami today?", "subtitle": ""}
        city, lat, lon = extract_city(raw)
        assert city == "Miami"

    def test_returns_none_for_unknown_city(self):
        raw = {"ticker": "SOME-UNKNOWN-TICKER", "title": "Unknown market", "subtitle": ""}
        city, lat, lon = extract_city(raw)
        assert city is None
        assert lat is None
        assert lon is None


class TestIsWeatherMarket:
    def test_detects_by_series_ticker(self):
        assert _is_weather_market({"series_ticker": "HIGHTEMP", "title": "", "subtitle": "", "ticker": ""})
        assert _is_weather_market({"series_ticker": "PRECIP", "title": "", "subtitle": "", "ticker": ""})

    def test_detects_by_title_keyword(self):
        assert _is_weather_market({
            "series_ticker": "",
            "ticker": "SOME-EVENT-001",
            "title": "Will it snow in Denver?",
            "subtitle": "",
        })

    def test_rejects_non_weather(self):
        assert not _is_weather_market({
            "series_ticker": "PRES",
            "ticker": "PRES-2024-DEM",
            "title": "Will Democrats win?",
            "subtitle": "",
        })


class TestParseMarket:
    def test_price_normalisation_from_cents(self, sample_kalshi_market_raw):
        parsed = parse_market(sample_kalshi_market_raw)
        # 45 cents → 0.45
        assert parsed["yes_bid"] == pytest.approx(0.45, abs=0.001)
        assert parsed["yes_ask"] == pytest.approx(0.55, abs=0.001)

    def test_price_already_fractional(self):
        raw = {
            "ticker": "HIGHTEMP-NYC-001",
            "event_ticker": "HIGHTEMP-NYC",
            "series_ticker": "HIGHTEMP",
            "title": "Test market",
            "subtitle": None,
            "open_time": None,
            "close_time": None,
            "status": "open",
            "yes_bid": 0.45,
            "yes_ask": 0.55,
            "no_bid": 0.45,
            "no_ask": 0.55,
            "volume": 100,
        }
        parsed = parse_market(raw)
        assert parsed["yes_bid"] == pytest.approx(0.45, abs=0.001)

    def test_parses_iso_timestamps(self, sample_kalshi_market_raw):
        parsed = parse_market(sample_kalshi_market_raw)
        assert parsed["close_time"] is not None
        assert parsed["close_time"].year == 2025

    def test_parses_unix_timestamps(self):
        raw = {
            "ticker": "HIGHTEMP-NYC-002",
            "event_ticker": None,
            "series_ticker": "HIGHTEMP",
            "title": "Test",
            "subtitle": None,
            "open_time": 1753920000,   # unix timestamp
            "close_time": 1754006400,
            "status": "open",
            "yes_bid": None,
            "yes_ask": None,
            "no_bid": None,
            "no_ask": None,
            "volume": 0,
        }
        parsed = parse_market(raw)
        assert parsed["open_time"] is not None

    def test_city_extracted(self, sample_kalshi_market_raw):
        parsed = parse_market(sample_kalshi_market_raw)
        assert parsed["city"] == "New York City"

    def test_missing_prices_are_none(self):
        raw = {
            "ticker": "PRECIP-SEA-001",
            "event_ticker": None,
            "series_ticker": "PRECIP",
            "title": "Will it rain in Seattle?",
            "subtitle": None,
            "open_time": None,
            "close_time": None,
            "status": "open",
            "yes_bid": None,
            "yes_ask": None,
            "no_bid": None,
            "no_ask": None,
            "volume": None,
        }
        parsed = parse_market(raw)
        assert parsed["yes_bid"] is None
        assert parsed["no_ask"] is None
