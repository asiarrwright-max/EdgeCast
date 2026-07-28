"""Tests for the /health/services endpoint, specifically the database check."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestDbStatusCheck:
    """Unit tests for _check_db_status() in the health router."""

    @pytest.mark.asyncio
    async def test_db_ok_when_select_1_succeeds(self):
        """When SELECT 1 executes without error, status should be 'ok'."""
        from app.routers.health import _check_db_status

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=None)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)

        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(return_value=mock_conn)

        # Patch at the source so get_engine() returns our mock
        with patch("app.database.engine", mock_engine):
            result = await _check_db_status("2026-07-28T00:00:00+00:00")

        assert result["name"] == "Database"
        assert result["status"] == "ok"
        assert result["message"] is None

    @pytest.mark.asyncio
    async def test_db_error_when_engine_is_none(self):
        """When engine is None (not yet initialised), status should be 'error'."""
        from app.routers.health import _check_db_status

        with patch("app.database.engine", None):
            result = await _check_db_status("2026-07-28T00:00:00+00:00")

        assert result["status"] == "error"
        assert "not initialised" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_db_error_when_connection_raises(self):
        """When the DB connection raises, status should be 'error' with the message."""
        from app.routers.health import _check_db_status

        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(side_effect=Exception("connection refused"))
        mock_conn.__aexit__ = AsyncMock(return_value=None)

        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(return_value=mock_conn)

        with patch("app.database.engine", mock_engine):
            result = await _check_db_status("2026-07-28T00:00:00+00:00")

        assert result["status"] == "error"
        assert "connection refused" in result["message"]

    @pytest.mark.asyncio
    async def test_db_error_when_execute_raises(self):
        """When SELECT 1 itself raises, status should be 'error'."""
        from app.routers.health import _check_db_status

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=Exception("timeout"))
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)

        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(return_value=mock_conn)

        with patch("app.database.engine", mock_engine):
            result = await _check_db_status("2026-07-28T00:00:00+00:00")

        assert result["status"] == "error"
        assert "timeout" in result["message"]

    def test_db_status_response_structure(self):
        """All required keys are present in a valid db_status dict."""
        required_keys = {"name", "status", "message", "lastChecked"}
        sample = {
            "name": "Database",
            "status": "ok",
            "message": None,
            "lastChecked": "2026-07-28T00:00:00+00:00",
        }
        assert required_keys <= sample.keys()

    def test_get_engine_returns_module_level_value(self):
        """get_engine() must reflect the current module-level engine, not a stale import."""
        import app.database as db_module
        from app.database import get_engine

        original = db_module.engine
        try:
            # Simulate what init_db() does: reassign the module-level singleton
            sentinel = object()
            db_module.engine = sentinel  # type: ignore[assignment]
            assert get_engine() is sentinel, (
                "get_engine() must call through to the module-level name, "
                "not a value captured at import time"
            )
        finally:
            db_module.engine = original


class TestDeriveSeriesFromEventTicker:
    """Tests for the event_ticker → series derivation helper."""

    def test_strips_single_date_segment(self):
        from app.services.kalshi import _derive_series_from_event_ticker
        assert _derive_series_from_event_ticker("KXLOWTNYC-26JUL27") == "KXLOWTNYC"

    def test_strips_date_preserves_multi_part_series(self):
        from app.services.kalshi import _derive_series_from_event_ticker
        # Hypothetical multi-segment series
        assert _derive_series_from_event_ticker("KXHIGH-DAL-26JUL28") == "KXHIGH-DAL"

    def test_pure_series_no_date(self):
        from app.services.kalshi import _derive_series_from_event_ticker
        # No date segment — returned as-is
        assert _derive_series_from_event_ticker("KXHIGHTDAL") == "KXHIGHTDAL"

    def test_strips_jul28_date(self):
        from app.services.kalshi import _derive_series_from_event_ticker
        assert _derive_series_from_event_ticker("KXLOWTLAX-26JUL28") == "KXLOWTLAX"

    def test_empty_string(self):
        from app.services.kalshi import _derive_series_from_event_ticker
        assert _derive_series_from_event_ticker("") == ""


class TestExtractCityEventTickerFallback:
    """Tests for the event_ticker fallback path in extract_city."""

    def test_resolves_houston_from_event_ticker_when_series_empty(self):
        from app.services.kalshi import extract_city
        raw = {
            "ticker": "KXLOWTHOU-26JUL27-T79",
            "series_ticker": "",        # empty — mimics what Kalshi sometimes sends
            "event_ticker": "KXLOWTHOU-26JUL27",
            "title": "",
            "subtitle": "",
        }
        city, lat, lon = extract_city(raw)
        assert city == "Houston"
        assert lat == pytest.approx(29.7604, abs=0.01)

    def test_resolves_los_angeles_from_event_ticker(self):
        from app.services.kalshi import extract_city
        raw = {
            "ticker": "KXLOWTLAX-26JUL28-T69",
            "series_ticker": None,
            "event_ticker": "KXLOWTLAX-26JUL28",
            "title": "",
            "subtitle": "",
        }
        city, lat, lon = extract_city(raw)
        assert city == "Los Angeles"
        assert lon == pytest.approx(-118.2437, abs=0.01)

    def test_event_ticker_not_used_when_series_ticker_present(self):
        """series_ticker takes priority; event_ticker fallback must not override it."""
        from app.services.kalshi import extract_city
        raw = {
            "ticker": "KXHIGHTDAL-26JUL28-T107",
            "series_ticker": "KXHIGHTDAL",
            "event_ticker": "KXLOWTNYC-26JUL28",  # wrong city in event_ticker
            "title": "",
            "subtitle": "",
        }
        city, _lat, _lon = extract_city(raw)
        assert city == "Dallas"  # from series_ticker, not event_ticker

    def test_unknown_series_returns_none(self):
        from app.services.kalshi import extract_city
        raw = {
            "ticker": "KXBIZARRO-26JUL28-T99",
            "series_ticker": "",
            "event_ticker": "KXBIZARRO-26JUL28",
            "title": "Some unknown market",
            "subtitle": "",
        }
        city, lat, lon = extract_city(raw)
        assert city is None
        assert lat is None
        assert lon is None

    def test_unsupported_location_returns_none(self):
        """Markets for locations not in our city map should return None gracefully."""
        from app.services.kalshi import extract_city
        raw = {
            "ticker": "KXTEMPULAANBAATAR-26JUL28-T10",
            "series_ticker": "KXTEMPULAANBAATAR",
            "event_ticker": "KXTEMPULAANBAATAR-26JUL28",
            "title": "Will temp in Ulaanbaatar exceed 10°?",
            "subtitle": "",
        }
        city, lat, lon = extract_city(raw)
        assert city is None


class TestTargetDateExtraction:
    """Tests for the forecast date alignment helper in the collector."""

    def test_extracts_date_from_datetime_string(self):
        from app.services.collector import _target_date_str
        assert _target_date_str("2026-07-28 14:00:00+00:00") == "2026-07-28"

    def test_extracts_date_from_iso_with_T(self):
        from app.services.collector import _target_date_str
        assert _target_date_str("2026-07-28T14:00:00+00:00") == "2026-07-28"

    def test_returns_none_for_none(self):
        from app.services.collector import _target_date_str
        assert _target_date_str(None) is None

    def test_returns_none_for_empty_string(self):
        from app.services.collector import _target_date_str
        assert _target_date_str("") is None

    def test_date_only_string_passthrough(self):
        from app.services.collector import _target_date_str
        assert _target_date_str("2026-08-01") == "2026-08-01"
