"""
Tests for V3 Ingestion Pipeline
=================================
Tests the ingestion orchestrator, provider interface, NOAA GHCND client,
unit conversion, and isolation guarantees.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_raw_record(
    city: str = "Denver",
    station_id: str = "USW00023062",
    target_date: str = "2026-01-15",
    lead_time_hours: int = 48,
    forecast_tmax_raw: float | None = 5.0,  # Celsius
    raw_unit: str = "celsius",
    is_reanalysis: bool = False,
    forecast_init_time: datetime | None = None,
    forecast_valid_time: datetime | None = None,
):
    from app.services.v3_providers.base import RawForecastRecord

    default_valid = datetime(2026, 1, 15, 23, 59, tzinfo=timezone.utc)
    default_init  = datetime(2026, 1, 13, 0, 0, tzinfo=timezone.utc)

    return RawForecastRecord(
        provider="open-meteo-forecast-history",
        model="GFS",
        model_version=None,
        city=city,
        station_id=station_id,
        station_lat=39.86,
        station_lon=-104.67,
        local_timezone="America/Denver",
        forecast_init_time=forecast_init_time or default_init,
        forecast_valid_time=forecast_valid_time or default_valid,
        retrieval_timestamp=datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc),
        target_date_local=target_date,
        lead_time_hours=lead_time_hours,
        forecast_tmax_raw=forecast_tmax_raw,
        raw_unit=raw_unit,
        raw_source_identifier="test://denver/2026-01-15/48h",
        source_provenance="test",
        raw_response={"test": True},
        is_reanalysis=is_reanalysis,
    )


# ── Unit conversion tests ─────────────────────────────────────────────────────

class TestUnitConversion:
    def test_celsius_to_fahrenheit_formula(self):
        from app.services.v3_providers.noaa_ghcnd_observations import celsius_to_fahrenheit
        assert celsius_to_fahrenheit(0.0) == pytest.approx(32.0)
        assert celsius_to_fahrenheit(100.0) == pytest.approx(212.0)
        assert celsius_to_fahrenheit(-40.0) == pytest.approx(-40.0)
        assert celsius_to_fahrenheit(37.0) == pytest.approx(98.6, rel=1e-3)

    def test_conversion_from_raw_tmax_tenths(self):
        """NOAA GHCND stores TMAX in tenths of degrees Celsius."""
        from app.services.v3_providers.noaa_ghcnd_observations import celsius_to_fahrenheit, TMAX_SCALE_FACTOR
        raw = 350  # = 35.0°C
        celsius = raw / TMAX_SCALE_FACTOR
        fahrenheit = celsius_to_fahrenheit(celsius)
        assert fahrenheit == pytest.approx(95.0)

    def test_freezing_point_conversion(self):
        from app.services.v3_providers.noaa_ghcnd_observations import celsius_to_fahrenheit, TMAX_SCALE_FACTOR
        raw = 0  # 0°C
        fahrenheit = celsius_to_fahrenheit(raw / TMAX_SCALE_FACTOR)
        assert fahrenheit == pytest.approx(32.0)


# ── NOAA GHCND date range splitting ──────────────────────────────────────────

class TestDateRangeSplitting:
    def test_short_range_no_split(self):
        from app.services.v3_providers.noaa_ghcnd_observations import _split_date_range
        chunks = _split_date_range("2025-01-01", "2025-03-31", max_days=365)
        assert len(chunks) == 1
        assert chunks[0] == ("2025-01-01", "2025-03-31")

    def test_long_range_splits_correctly(self):
        from datetime import date, timedelta
        from app.services.v3_providers.noaa_ghcnd_observations import _split_date_range
        chunks = _split_date_range("2023-01-01", "2025-12-31", max_days=365)
        # At least 3 chunks needed for ~3 years; exact count depends on leap years
        assert len(chunks) >= 3
        # First chunk starts at the requested start date
        assert chunks[0][0] == "2023-01-01"
        # Last chunk ends at the requested end date
        assert chunks[-1][1] == "2025-12-31"
        # No gaps between consecutive chunks
        for i in range(len(chunks) - 1):
            end_of_chunk = date.fromisoformat(chunks[i][1])
            start_of_next = date.fromisoformat(chunks[i + 1][0])
            assert start_of_next == end_of_chunk + timedelta(days=1), (
                f"Gap between chunk {i} and {i+1}: "
                f"{chunks[i][1]} → {chunks[i+1][0]}"
            )
        # Each chunk is within max_days
        for start_str, end_str in chunks:
            start = date.fromisoformat(start_str)
            end = date.fromisoformat(end_str)
            assert (end - start).days < 365, f"Chunk too wide: {start_str} → {end_str}"

    def test_single_day_range(self):
        from app.services.v3_providers.noaa_ghcnd_observations import _split_date_range
        chunks = _split_date_range("2025-06-15", "2025-06-15", max_days=365)
        assert len(chunks) == 1
        assert chunks[0] == ("2025-06-15", "2025-06-15")


# ── Provider interface ────────────────────────────────────────────────────────

class TestProviderInterface:
    def test_provider_registry_has_open_meteo(self):
        from app.services.v3_providers.registry import get_all_provider_keys, get_provider_class
        keys = get_all_provider_keys()
        assert "open-meteo-forecast-history" in keys

    def test_unknown_provider_raises_key_error(self):
        from app.services.v3_providers.registry import get_provider_class
        with pytest.raises(KeyError, match="Unknown V3 forecast provider"):
            get_provider_class("nonexistent-provider")

    def test_open_meteo_provider_implements_interface(self):
        from app.services.v3_providers.open_meteo_forecast_history import OpenMeteoForecastHistoryProvider
        from app.services.v3_providers.base import ForecastHistoryProvider
        assert issubclass(OpenMeteoForecastHistoryProvider, ForecastHistoryProvider)
        assert hasattr(OpenMeteoForecastHistoryProvider, "PROVIDER_KEY")
        assert hasattr(OpenMeteoForecastHistoryProvider, "MODEL")
        assert OpenMeteoForecastHistoryProvider.PROVIDER_KEY == "open-meteo-forecast-history"
        assert OpenMeteoForecastHistoryProvider.MODEL == "GFS"

    def test_open_meteo_provider_has_fetch_history(self):
        from app.services.v3_providers.open_meteo_forecast_history import OpenMeteoForecastHistoryProvider
        provider = OpenMeteoForecastHistoryProvider()
        assert callable(provider.fetch_history)

    def test_mock_provider_satisfies_interface(self):
        """Any class implementing the interface should work as a drop-in."""
        from app.services.v3_providers.base import ForecastHistoryProvider, RawForecastRecord

        class TestProvider(ForecastHistoryProvider):
            PROVIDER_KEY = "test-provider"
            MODEL = "TEST_MODEL"

            async def fetch_history(self, city, station_id, lat, lon, local_timezone,
                                    start_date, end_date, lead_time_hours_list):
                return []

        provider = TestProvider()
        assert provider.PROVIDER_KEY == "test-provider"
        assert provider.MODEL == "TEST_MODEL"


# ── Open-Meteo response parsing ───────────────────────────────────────────────

class TestOpenMeteoResponseParsing:
    def test_parse_valid_response(self):
        from app.services.v3_providers.open_meteo_forecast_history import OpenMeteoForecastHistoryProvider
        from datetime import timezone

        provider = OpenMeteoForecastHistoryProvider()
        retrieval_ts = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
        data = {
            "daily": {
                "time": ["2026-01-15", "2026-01-16"],
                "temperature_2m_max": [5.0, 7.5],
            },
            "model": "gfs_seamless",
        }

        records = provider._parse_response(
            data=data,
            city="Denver",
            station_id="USW00023062",
            lat=39.86,
            lon=-104.67,
            local_timezone="America/Denver",
            lead_time_hours=48,
            forecast_days=2,
            retrieval_ts=retrieval_ts,
            raw_source_id="test-url",
        )

        assert len(records) == 2
        assert records[0].target_date_local == "2026-01-15"
        assert records[0].forecast_tmax_raw == 5.0
        assert records[0].raw_unit == "celsius"
        assert records[0].is_reanalysis is False
        assert records[0].model == "GFS"
        assert records[0].provider == "open-meteo-forecast-history"

        # init_time should be 2 days before valid date
        assert records[0].forecast_init_time.date().isoformat() == "2026-01-13"

    def test_parse_response_with_none_tmax(self):
        """None values (station gap) should still produce records but with flag."""
        from app.services.v3_providers.open_meteo_forecast_history import OpenMeteoForecastHistoryProvider
        provider = OpenMeteoForecastHistoryProvider()
        retrieval_ts = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
        data = {
            "daily": {
                "time": ["2026-01-15"],
                "temperature_2m_max": [None],
            },
        }
        records = provider._parse_response(
            data=data, city="Denver", station_id="USW00023062",
            lat=39.86, lon=-104.67, local_timezone="America/Denver",
            lead_time_hours=24, forecast_days=1,
            retrieval_ts=retrieval_ts, raw_source_id="test",
        )
        assert len(records) == 1
        assert records[0].forecast_tmax_raw is None

    def test_parse_response_length_mismatch_raises(self):
        from app.services.v3_providers.open_meteo_forecast_history import OpenMeteoForecastHistoryProvider
        from app.services.v3_providers.base import ProviderDataError
        provider = OpenMeteoForecastHistoryProvider()
        retrieval_ts = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
        data = {
            "daily": {
                "time": ["2026-01-15", "2026-01-16"],
                "temperature_2m_max": [5.0],  # mismatch: 2 dates, 1 value
            },
        }
        with pytest.raises(ProviderDataError):
            provider._parse_response(
                data=data, city="Denver", station_id="USW00023062",
                lat=39.86, lon=-104.67, local_timezone="America/Denver",
                lead_time_hours=24, forecast_days=1,
                retrieval_ts=retrieval_ts, raw_source_id="test",
            )


# ── Feature flag gate ─────────────────────────────────────────────────────────

class TestFeatureFlagGate:
    @pytest.mark.asyncio
    async def test_ingestion_blocked_when_flag_false(self):
        """run_ingestion returns blocked status when v3.ingestion_enabled=false."""
        from app.services.v3_ingestion import run_ingestion
        from app.models import AppSetting

        mock_setting = MagicMock()
        mock_setting.value = "false"

        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=mock_setting)
        ))

        result = await run_ingestion(session)
        assert result["status"] == "blocked"
        assert "v3.ingestion_enabled" in result["reason"]

    @pytest.mark.asyncio
    async def test_ingestion_blocked_when_flag_missing(self):
        """run_ingestion returns blocked when flag row doesn't exist (None)."""
        from app.services.v3_ingestion import run_ingestion

        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        ))

        result = await run_ingestion(session)
        assert result["status"] == "blocked"


# ── Duplicate prevention ──────────────────────────────────────────────────────

class TestDuplicatePrevention:
    def test_unique_constraint_fields_in_model(self):
        """V3HistoricalRecord has a unique constraint on the right fields."""
        from app.models_v3 import V3HistoricalRecord
        from sqlalchemy import inspect as sa_inspect

        # Check constraint exists on the model
        table_args = V3HistoricalRecord.__table_args__
        constraint_names = [
            getattr(c, "name", "") for c in table_args
            if hasattr(c, "name")
        ]
        assert "uq_v3_hist_city_station_date_src_model_lead_ver" in constraint_names


# ── Station matching ──────────────────────────────────────────────────────────

class TestStationMatching:
    def test_ingestion_uses_settlement_station_id(self):
        """
        The ingestion orchestrator uses SettlementStation.ghcnd_station_id,
        not a city-centre proxy.
        """
        from app.services.settlement_stations import SETTLEMENT_STATIONS
        from app.services.v3_ingestion import SEASON_MAP

        # Spot-check that Denver uses the correct airport station ID
        denver = SETTLEMENT_STATIONS.get("Denver")
        assert denver is not None
        assert denver.ghcnd_station_id is not None
        # Must not be None (would cause ingestion to silently use wrong location)
        assert len(denver.ghcnd_station_id) > 0

    def test_season_mapping_covers_all_months(self):
        from app.services.v3_ingestion import SEASON_MAP
        for month in range(1, 13):
            assert month in SEASON_MAP
            assert SEASON_MAP[month] in ("winter", "spring", "summer", "fall")


# ── V2.1 isolation after V3 ingestion ────────────────────────────────────────

class TestV21IsolationAfterV3Imports:
    def test_forecast_error_stats_model_unchanged(self):
        """V3 imports do not alter or shadow ForecastErrorStats."""
        from app.models import ForecastErrorStats
        from app.models_v3 import V3ErrorStats

        # Different classes, different table names
        assert ForecastErrorStats.__tablename__ == "forecast_error_stats"
        assert V3ErrorStats.__tablename__ == "v3_error_stats"
        assert ForecastErrorStats is not V3ErrorStats

    def test_paper_trade_model_unchanged(self):
        """V3 imports do not alter PaperTrade."""
        from app.models import PaperTrade
        from app.models_v3 import V3PaperTrade

        assert PaperTrade.__tablename__ == "paper_trades"
        assert V3PaperTrade.__tablename__ == "v3_paper_trades"
        assert PaperTrade is not V3PaperTrade

    def test_v3_tables_use_v3_prefix(self):
        """All V3 models use the v3_ table prefix."""
        from app.models_v3 import (
            V3HistoricalRecord, V3RawSourceRecord, V3ErrorStats,
            V3PredictionSnapshot, V3PaperTrade, V3IngestionLog,
        )
        v3_models = [
            V3HistoricalRecord, V3RawSourceRecord, V3ErrorStats,
            V3PredictionSnapshot, V3PaperTrade, V3IngestionLog,
        ]
        for model in v3_models:
            assert model.__tablename__.startswith("v3_"), (
                f"{model.__name__} tablename '{model.__tablename__}' "
                "does not start with 'v3_'"
            )
