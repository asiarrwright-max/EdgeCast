"""Tests for Open-Meteo API client parsing."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx


class TestFetchForecast:
    @pytest.mark.asyncio
    async def test_parses_daily_response(self, sample_openmeteo_response):
        """fetch_forecast should return structured day rows from raw API data."""
        from app.services.openmeteo import fetch_forecast

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = sample_openmeteo_response

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            rows = await fetch_forecast("New York City", 40.7128, -74.0060)

        assert len(rows) == 3
        assert rows[0]["city"] == "New York City"
        assert rows[0]["forecast_date"] == "2025-07-28"
        assert rows[0]["temperature_high"] == pytest.approx(88.5, abs=0.1)
        assert rows[0]["temperature_low"] == pytest.approx(72.1, abs=0.1)
        assert rows[0]["precipitation_prob"] == 10
        assert rows[0]["wind_speed"] == pytest.approx(8.5, abs=0.1)

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        """Should return [] and log warning instead of raising."""
        from app.services.openmeteo import fetch_forecast

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            rows = await fetch_forecast("Chicago", 41.8781, -87.6298)

        assert rows == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        """Should return [] on 4xx/5xx responses."""
        from app.services.openmeteo import fetch_forecast

        mock_req = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429 Too Many Requests", request=mock_req, response=mock_resp
        )
        mock_resp.status_code = 429

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            rows = await fetch_forecast("Los Angeles", 34.0522, -118.2437)

        assert rows == []
