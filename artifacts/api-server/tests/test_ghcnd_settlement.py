"""
Tests for the GHCND/settlement-station integration:
  - settlement_stations registry (verified/unverified lookups, known cities)
  - ghcnd_client (CDO API request shape, value parsing, error handling)
  - forecast_verifier._fetch_observation (source routing, label assignment)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── settlement_stations ──────────────────────────────────────────────────────

class TestSettlementStations:

    def test_verified_cities_are_nyc_chicago_denver(self):
        from app.services.settlement_stations import SETTLEMENT_STATIONS, verified_cities
        vc = verified_cities()
        assert "New York City" in vc
        assert "Chicago" in vc
        assert "Denver" in vc

    def test_verified_count_is_three(self):
        """Exactly 3 verified cities as of current data."""
        from app.services.settlement_stations import verified_cities
        assert len(verified_cities()) == 3

    def test_all_cities_count_is_24(self):
        """All 24 Kalshi cities have entries."""
        from app.services.settlement_stations import all_cities
        assert len(all_cities()) == 24

    def test_nyc_station_details(self):
        from app.services.settlement_stations import get_station
        s = get_station("New York City")
        assert s is not None
        assert s.ghcnd_station_id == "USW00094728"
        assert s.verified is True
        assert s.timezone == "America/New_York"
        assert "Central Park" in s.station_name

    def test_chicago_station_details(self):
        from app.services.settlement_stations import get_station
        s = get_station("Chicago")
        assert s is not None
        assert s.ghcnd_station_id == "USW00014819"
        assert s.verified is True
        assert "Midway" in s.station_name

    def test_denver_station_details(self):
        from app.services.settlement_stations import get_station
        s = get_station("Denver")
        assert s is not None
        assert s.ghcnd_station_id == "USW00003017"
        assert s.verified is True

    def test_unverified_city_returns_entry(self):
        from app.services.settlement_stations import get_station
        s = get_station("Boston")
        assert s is not None
        assert s.verified is False

    def test_unknown_city_returns_none(self):
        from app.services.settlement_stations import get_station
        assert get_station("Atlantis") is None

    def test_los_angeles_is_unverified_and_has_ambiguity_note(self):
        """LA has documented station ambiguity — must stay unverified."""
        from app.services.settlement_stations import get_station
        s = get_station("Los Angeles")
        assert s is not None
        assert s.verified is False
        assert s.notes is not None
        assert "AMBIGUITY" in s.notes.upper() or "ambiguit" in s.notes.lower()

    def test_every_entry_has_required_fields(self):
        """Ensure no entry was added with a blank GHCND ID or timezone."""
        from app.services.settlement_stations import SETTLEMENT_STATIONS
        for city, station in SETTLEMENT_STATIONS.items():
            assert station.ghcnd_station_id, f"{city}: blank ghcnd_station_id"
            assert station.timezone, f"{city}: blank timezone"
            assert station.station_name, f"{city}: blank station_name"
            assert station.city == city, f"{city}: city field mismatch"

    def test_no_duplicate_ghcnd_ids_for_verified(self):
        """Verified cities must not share a station ID."""
        from app.services.settlement_stations import SETTLEMENT_STATIONS
        verified = [s for s in SETTLEMENT_STATIONS.values() if s.verified]
        ids = [s.ghcnd_station_id for s in verified]
        assert len(ids) == len(set(ids)), "Duplicate GHCND IDs among verified stations"


# ── ghcnd_client ─────────────────────────────────────────────────────────────

class TestGhcndClient:

    @pytest.mark.asyncio
    async def test_successful_fetch_returns_high_and_low(self):
        from app.services.ghcnd_client import fetch_ghcnd_daily

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"datatype": "TMAX", "value": 82.0, "date": "2024-07-01T00:00:00"},
                {"datatype": "TMIN", "value": 64.0, "date": "2024-07-01T00:00:00"},
            ]
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("app.services.ghcnd_client.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_ghcnd_daily("USW00094728", "2024-07-01", "fake-token")

        assert result["high"] == 82.0
        assert result["low"] == 64.0

    @pytest.mark.asyncio
    async def test_empty_results_returns_none(self):
        from app.services.ghcnd_client import fetch_ghcnd_daily

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"results": []}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("app.services.ghcnd_client.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_ghcnd_daily("USW00094728", "2024-07-01", "fake-token")

        assert result["high"] is None
        assert result["low"] is None

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        import httpx
        from app.services.ghcnd_client import fetch_ghcnd_daily

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "500", request=MagicMock(), response=MagicMock(status_code=500)
            )
        )

        with patch("app.services.ghcnd_client.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_ghcnd_daily("USW00094728", "2024-07-01", "fake-token")

        assert result["high"] is None
        assert result["low"] is None

    @pytest.mark.asyncio
    async def test_network_exception_returns_none(self):
        from app.services.ghcnd_client import fetch_ghcnd_daily

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("network error"))

        with patch("app.services.ghcnd_client.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_ghcnd_daily("USW00094728", "2024-07-01", "fake-token")

        assert result["high"] is None
        assert result["low"] is None

    @pytest.mark.asyncio
    async def test_station_id_prefixed_correctly(self):
        """CDO API requires 'GHCND:' prefix on station IDs."""
        from app.services.ghcnd_client import fetch_ghcnd_daily

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"results": [
            {"datatype": "TMAX", "value": 75.0, "date": "2024-06-01T00:00:00"},
            {"datatype": "TMIN", "value": 55.0, "date": "2024-06-01T00:00:00"},
        ]}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("app.services.ghcnd_client.httpx.AsyncClient", return_value=mock_client):
            await fetch_ghcnd_daily("USW00094728", "2024-06-01", "fake-token")

        _args, kwargs = mock_client.get.call_args
        params = kwargs.get("params", {})
        assert params["stationid"] == "GHCND:USW00094728"

    @pytest.mark.asyncio
    async def test_already_prefixed_station_not_doubled(self):
        """Station IDs already starting with 'GHCND:' must not be double-prefixed."""
        from app.services.ghcnd_client import fetch_ghcnd_daily

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"results": []}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("app.services.ghcnd_client.httpx.AsyncClient", return_value=mock_client):
            await fetch_ghcnd_daily("GHCND:USW00094728", "2024-06-01", "fake-token")

        _args, kwargs = mock_client.get.call_args
        params = kwargs.get("params", {})
        assert params["stationid"] == "GHCND:USW00094728"
        assert not params["stationid"].startswith("GHCND:GHCND:")

    @pytest.mark.asyncio
    async def test_standard_units_requested(self):
        """CDO must be called with units=standard to receive °F values."""
        from app.services.ghcnd_client import fetch_ghcnd_daily

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"results": []}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("app.services.ghcnd_client.httpx.AsyncClient", return_value=mock_client):
            await fetch_ghcnd_daily("USW00094728", "2024-06-01", "fake-token")

        _args, kwargs = mock_client.get.call_args
        params = kwargs.get("params", {})
        assert params.get("units") == "standard"

    @pytest.mark.asyncio
    async def test_token_sent_as_header(self):
        """NOAA CDO token must be in the 'token' request header."""
        from app.services.ghcnd_client import fetch_ghcnd_daily

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"results": []}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("app.services.ghcnd_client.httpx.AsyncClient", return_value=mock_client):
            await fetch_ghcnd_daily("USW00094728", "2024-06-01", "my-secret-token")

        _args, kwargs = mock_client.get.call_args
        headers = kwargs.get("headers", {})
        assert headers.get("token") == "my-secret-token"


# ── forecast_verifier._fetch_observation source routing ─────────────────────

class TestFetchObservationRouting:
    """
    Tests for _fetch_observation — the function that decides whether to call
    GHCND or fall back to ERA5.
    """

    @pytest.mark.asyncio
    async def test_verified_city_with_token_uses_ghcnd(self):
        """NYC + valid token → ghcnd_observation label, station ID populated."""
        from app.services.forecast_verifier import _fetch_observation, SRC_GHCND_VERIFIED

        good_ghcnd = {"high": 85.0, "low": 68.0}

        with patch("app.services.forecast_verifier.fetch_ghcnd_daily", return_value=good_ghcnd):
            temps, src, station_id = await _fetch_observation(
                city="New York City",
                lat=40.7128,
                lon=-74.0060,
                date_str="2024-07-01",
                noaa_token="test-token",
            )

        assert temps["high"] == 85.0
        assert src == SRC_GHCND_VERIFIED
        assert station_id == "USW00094728"

    @pytest.mark.asyncio
    async def test_unverified_city_with_token_uses_ghcnd_unverified_label(self):
        """Boston + valid token → ghcnd_observation_unverified label."""
        from app.services.forecast_verifier import _fetch_observation, SRC_GHCND_UNVERIFIED

        good_ghcnd = {"high": 72.0, "low": 55.0}

        with patch("app.services.forecast_verifier.fetch_ghcnd_daily", return_value=good_ghcnd):
            temps, src, station_id = await _fetch_observation(
                city="Boston",
                lat=42.3601,
                lon=-71.0589,
                date_str="2024-07-01",
                noaa_token="test-token",
            )

        assert src == SRC_GHCND_UNVERIFIED
        assert station_id == "USW00014739"

    @pytest.mark.asyncio
    async def test_no_token_falls_back_to_era5(self):
        """No token → ERA5 fallback regardless of city."""
        from app.services.forecast_verifier import _fetch_observation, SRC_ERA5

        era5_data = {"high": 83.0, "low": 65.0}

        with patch("app.services.forecast_verifier._fetch_era5_temps", return_value=era5_data) as mock_era5, \
             patch("app.services.forecast_verifier.fetch_ghcnd_daily") as mock_ghcnd:
            temps, src, station_id = await _fetch_observation(
                city="New York City",
                lat=40.7128,
                lon=-74.0060,
                date_str="2024-07-01",
                noaa_token="",
            )

        assert src == SRC_ERA5
        assert station_id is None
        mock_ghcnd.assert_not_called()
        mock_era5.assert_called_once()

    @pytest.mark.asyncio
    async def test_ghcnd_empty_result_falls_back_to_era5(self):
        """GHCND returns no data (not yet published) → ERA5 fallback."""
        from app.services.forecast_verifier import _fetch_observation, SRC_ERA5

        empty_ghcnd = {"high": None, "low": None}
        era5_data = {"high": 80.0, "low": 62.0}

        with patch("app.services.forecast_verifier.fetch_ghcnd_daily", return_value=empty_ghcnd), \
             patch("app.services.forecast_verifier._fetch_era5_temps", return_value=era5_data):
            temps, src, station_id = await _fetch_observation(
                city="New York City",
                lat=40.7128,
                lon=-74.0060,
                date_str="2024-07-01",
                noaa_token="test-token",
            )

        assert src == SRC_ERA5
        assert station_id is None
        assert temps["high"] == 80.0

    @pytest.mark.asyncio
    async def test_unknown_city_falls_back_to_era5(self):
        """City with no settlement station entry → ERA5 fallback."""
        from app.services.forecast_verifier import _fetch_observation, SRC_ERA5

        era5_data = {"high": 70.0, "low": 50.0}

        with patch("app.services.forecast_verifier._fetch_era5_temps", return_value=era5_data) as mock_era5, \
             patch("app.services.forecast_verifier.fetch_ghcnd_daily") as mock_ghcnd:
            temps, src, station_id = await _fetch_observation(
                city="Atlantis",
                lat=0.0,
                lon=0.0,
                date_str="2024-07-01",
                noaa_token="test-token",
            )

        assert src == SRC_ERA5
        assert station_id is None
        mock_ghcnd.assert_not_called()

    @pytest.mark.asyncio
    async def test_source_label_constants(self):
        """Verify source label string values are stable (other code may depend on them)."""
        from app.services.forecast_verifier import (
            SRC_GHCND_VERIFIED,
            SRC_GHCND_UNVERIFIED,
            SRC_ERA5,
            SRC_LEGACY,
        )
        assert SRC_GHCND_VERIFIED == "ghcnd_observation"
        assert SRC_GHCND_UNVERIFIED == "ghcnd_observation_unverified"
        assert SRC_ERA5 == "era5_reanalysis"
        assert SRC_LEGACY == "open_meteo_historical"
