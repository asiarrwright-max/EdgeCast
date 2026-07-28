"""
Shared pytest fixtures for EdgeCast tests.
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def sample_kalshi_market_raw():
    """A realistic raw Kalshi market dict (weather series)."""
    return {
        "ticker": "HIGHTEMP-NYC-20250801-GTE85",
        "event_ticker": "HIGHTEMP-NYC-20250801",
        "series_ticker": "HIGHTEMP",
        "title": "Will the high temperature in New York City reach 85°F or above on Aug 1, 2025?",
        "subtitle": "High temp ≥ 85°F",
        "open_time": "2025-07-25T00:00:00Z",
        "close_time": "2025-08-01T23:59:59Z",
        "status": "open",
        "yes_bid": 45,
        "yes_ask": 55,
        "no_bid": 45,
        "no_ask": 55,
        "volume": 1200,
    }


@pytest.fixture
def sample_openmeteo_response():
    """A realistic Open-Meteo /forecast daily response."""
    return {
        "latitude": 40.71,
        "longitude": -74.01,
        "timezone": "America/New_York",
        "daily": {
            "time": ["2025-07-28", "2025-07-29", "2025-07-30"],
            "temperature_2m_max": [88.5, 91.2, 86.0],
            "temperature_2m_min": [72.1, 74.3, 70.5],
            "precipitation_probability_max": [10, 30, 60],
            "wind_speed_10m_max": [8.5, 12.1, 15.3],
        },
    }
