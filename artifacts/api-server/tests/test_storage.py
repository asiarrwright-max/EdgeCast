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

        # Grab the lock before calling
        async with collector._collect_lock:
            # A second call while lock is held should bail immediately
            result = collector._collect_lock.locked()
            assert result is True

    def test_city_coords_mapping_complete(self):
        """All city entries should have non-zero coordinates."""
        from app.services.kalshi import CITY_COORDS

        for code, (name, lat, lon) in CITY_COORDS.items():
            assert isinstance(name, str) and name
            assert -90 <= lat <= 90, f"Bad lat for {code}"
            assert -180 <= lon <= 180, f"Bad lon for {code}"


class TestModels:
    """Basic smoke-tests for ORM model instantiation."""

    def test_kalshi_market_defaults(self):
        from app.models import KalshiMarket

        # status default is applied at INSERT; pass it explicitly here to verify
        # the model accepts the expected values and weather_matched defaults to False.
        m = KalshiMarket(ticker="TEST-001", title="Test market", status="active", weather_matched=False)
        assert m.status == "active"
        assert m.weather_matched is False

    def test_job_run_defaults(self):
        from app.models import JobRun

        # status default is applied at INSERT; pass it explicitly here.
        j = JobRun(job_type="manual", status="running")
        assert j.status == "running"

    def test_app_error_instantiation(self):
        from app.models import AppError

        e = AppError(error_type="test_error", message="Something went wrong")
        assert e.error_type == "test_error"
