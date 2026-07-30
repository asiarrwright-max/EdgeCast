"""
Tests for app/services/v3_predictor.py — V3 Phase 3 predictor.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.v3_predictor import (
    _season_from_date,
    _lead_days,
    _v3_confidence_label,
    _is_city_ok,
)


# ---------------------------------------------------------------------------
# Season helper
# ---------------------------------------------------------------------------

class TestSeasonFromDate:
    def test_december_is_winter(self):
        assert _season_from_date("2024-12-15") == "winter"

    def test_january_is_winter(self):
        assert _season_from_date("2024-01-10") == "winter"

    def test_february_is_winter(self):
        assert _season_from_date("2024-02-28") == "winter"

    def test_march_is_spring(self):
        assert _season_from_date("2024-03-01") == "spring"

    def test_may_is_spring(self):
        assert _season_from_date("2024-05-31") == "spring"

    def test_june_is_summer(self):
        assert _season_from_date("2024-06-01") == "summer"

    def test_august_is_summer(self):
        assert _season_from_date("2024-08-15") == "summer"

    def test_september_is_fall(self):
        assert _season_from_date("2024-09-01") == "fall"

    def test_november_is_fall(self):
        assert _season_from_date("2024-11-30") == "fall"

    def test_malformed_returns_fall(self):
        assert _season_from_date("bad-data") == "fall"

    def test_empty_returns_fall(self):
        assert _season_from_date("") == "fall"

    def test_datetime_string_accepted(self):
        # target_date can be stored as "2026-07-28 14:00:00+00:00"
        assert _season_from_date("2026-07-28 14:00:00+00:00") == "summer"


# ---------------------------------------------------------------------------
# Lead days helper
# ---------------------------------------------------------------------------

class TestLeadDays:
    def test_future_date_positive(self):
        from datetime import datetime, timezone, date
        from unittest.mock import patch as p

        future = date(2099, 1, 1)
        with p("app.services.v3_predictor.datetime") as mock_dt:
            mock_dt.now.return_value.utctimetuple = None
            mock_dt.now.return_value.date.return_value = date(2099, 1, 1)
            mock_dt.strptime = datetime.strptime
            result = _lead_days("2099-01-02")
        assert result >= 0

    def test_malformed_returns_one(self):
        assert _lead_days("not-a-date") == 1

    def test_empty_returns_one(self):
        assert _lead_days("") == 1


# ---------------------------------------------------------------------------
# Confidence label
# ---------------------------------------------------------------------------

class TestV3ConfidenceLabel:
    def test_very_high_above_80pct(self):
        assert _v3_confidence_label(0.85) == "Very High"

    def test_very_high_below_15pct(self):
        assert _v3_confidence_label(0.15) == "Very High"

    def test_high_range(self):
        assert _v3_confidence_label(0.72) == "High"
        assert _v3_confidence_label(0.28) == "High"

    def test_medium_range(self):
        assert _v3_confidence_label(0.62) == "Medium"
        assert _v3_confidence_label(0.38) == "Medium"

    def test_low_range(self):
        assert _v3_confidence_label(0.56) == "Low"
        assert _v3_confidence_label(0.44) == "Low"

    def test_very_low_near_fifty(self):
        assert _v3_confidence_label(0.52) == "Very Low"
        assert _v3_confidence_label(0.50) == "Very Low"


# ---------------------------------------------------------------------------
# City ok helper
# ---------------------------------------------------------------------------

class TestIsCityOk:
    def test_none_city_is_not_ok(self):
        assert _is_city_ok(None) is False

    def test_empty_city_is_not_ok(self):
        assert _is_city_ok("") is False

    def test_city_with_no_station_is_not_ok(self):
        with patch("app.services.v3_predictor.get_station", return_value=None):
            assert _is_city_ok("Unknown City") is False

    def test_unverified_station_is_not_ok(self):
        station = MagicMock()
        station.verified = False
        station.nws_settlement = True
        with patch("app.services.v3_predictor.get_station", return_value=station):
            assert _is_city_ok("Denver") is False

    def test_non_nws_station_is_not_ok(self):
        station = MagicMock()
        station.verified = True
        station.nws_settlement = False
        with patch("app.services.v3_predictor.get_station", return_value=station):
            assert _is_city_ok("Chicago") is False

    def test_verified_nws_station_is_ok(self):
        station = MagicMock()
        station.verified = True
        station.nws_settlement = True
        with patch("app.services.v3_predictor.get_station", return_value=station):
            assert _is_city_ok("Denver") is True

    def test_no_nws_settlement_attr_defaults_to_ok(self):
        """If nws_settlement attribute is absent, getattr default=True, so should pass."""
        station = MagicMock(spec=["verified"])  # no nws_settlement attr
        station.verified = True
        with patch("app.services.v3_predictor.get_station", return_value=station):
            assert _is_city_ok("OKC") is True


# ---------------------------------------------------------------------------
# run_v3_predictions integration
# ---------------------------------------------------------------------------

class TestRunV3Predictions:
    """Smoke tests for the batch runner with a mocked DB session."""

    @pytest.mark.asyncio
    async def test_disabled_flag_returns_early(self):
        from app.services.v3_predictor import run_v3_predictions

        mock_session = AsyncMock()
        with patch(
            "app.services.v3_predictor.get_v3_flag",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await run_v3_predictions(mock_session)

        assert result["status"] == "disabled"
        assert result["created"] == 0

    @pytest.mark.asyncio
    async def test_no_snapshots_returns_zero_created(self):
        from app.services.v3_predictor import run_v3_predictions

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=_empty_execute)

        with patch(
            "app.services.v3_predictor.get_v3_flag",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await run_v3_predictions(mock_session)

        assert result["status"] == "ok"
        assert result["created"] == 0


def _empty_execute(_query):
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    return result
