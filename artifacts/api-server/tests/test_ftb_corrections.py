"""
Forward Test B — Correction Package Tests
==========================================
Regression tests for all 8 fixes implemented before Forward Test B.

Fix 1 — NWS integer threshold rounding  (V2 + V3)
Fix 2 — NWS range contract rounding     (V2 + V3)
Fix 3 — ERA5 local-date extraction      (forecast_verifier._local_settlement_date)
Fix 4 — Hourly sigma floor              (run_analysis_v2 / run_analysis_v22)
Fix 5 — Station verification eligibility (get_verified_station bug)
Fix 6 — Philadelphia / San Antonio coordinates (kalshi.SERIES_TO_CITY)
Fix 7 — Calibration reset / V2.3 strategy version isolation
Fix 8 — Forward Test B boundary constants
"""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    """Standard normal CDF — reference implementation for test assertions."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


# ===========================================================================
# Fix 1 + 2 — NWS Rounding Corrections (V2 engine helpers)
# ===========================================================================

class TestNWSRoundingV2:
    """
    _is_integer_threshold, _calc_prob_threshold, _calc_prob_range all live in
    probability_engine_v2.  V2.2 (probability_engine_v22) imports _calc_prob_* from V2,
    so the same corrections apply automatically.
    """

    def test_is_integer_threshold_whole_number(self):
        from app.services.probability_engine_v2 import _is_integer_threshold
        assert _is_integer_threshold(90.0) is True
        assert _is_integer_threshold(100.0) is True
        assert _is_integer_threshold(0.0) is True
        assert _is_integer_threshold(-5.0) is True

    def test_is_integer_threshold_half_integer(self):
        from app.services.probability_engine_v2 import _is_integer_threshold
        assert _is_integer_threshold(100.5) is False
        assert _is_integer_threshold(90.5) is False
        assert _is_integer_threshold(0.5) is False
        assert _is_integer_threshold(74.0 + 0.01) is False  # not exact integer

    # ── Fix 1a: GTE — integer threshold boundary is T − 0.5 ─────────────────

    def test_calc_prob_threshold_gte_integer_uses_t_minus_half(self):
        """
        For integer T, P(round(actual) >= T) = P(actual >= T−0.5).
        Parser 'X° or above' → operator='gte' → boundary T−0.5.
        """
        from app.services.probability_engine_v2 import _calc_prob_threshold
        mu, sigma, T = 90.0, 5.0, 90.0

        result = _calc_prob_threshold("gte", T, mu, sigma)
        expected = round(1.0 - _norm_cdf((T - 0.5 - mu) / sigma), 4)
        assert result == expected, (
            f"GTE integer: expected boundary T−0.5={T-0.5}, "
            f"expected P={expected}, got {result}"
        )

    def test_gte_integer_at_mu_equals_t_exceeds_50_percent(self):
        """
        At mu=T, P(round(actual) >= T) > 50%.
        Settlement temperature T itself wins the 'at or above T' contract,
        so the corrected probability is strictly above the naive 50%.
        """
        from app.services.probability_engine_v2 import _calc_prob_threshold
        T = 90.0
        result = _calc_prob_threshold("gte", T, mu=T, sigma=5.0)
        assert result > 0.50, (
            f"GTE at mu=T: expected >50%, got {result:.4f}. "
            "NWS rounding correction (T−0.5 boundary) must push above 50%."
        )

    def test_gte_integer_rounding_impact_exceeds_2pp(self):
        """
        Audit identified ~5.7pp impact. For T=90, mu=84, sigma=5 the
        T−0.5 correction must produce a measurable increase (>2pp) vs naive.
        """
        from app.services.probability_engine_v2 import _calc_prob_threshold
        mu, sigma, T = 84.0, 5.0, 90.0

        corrected = _calc_prob_threshold("gte", T, mu, sigma)
        naive = round(1.0 - _norm_cdf((T - mu) / sigma), 4)
        impact_pp = (corrected - naive) * 100
        assert impact_pp > 2.0, (
            f"Expected >2pp GTE rounding correction impact, got {impact_pp:.2f}pp"
        )

    def test_calc_prob_threshold_gte_half_integer_unchanged(self):
        """Half-integer GTE thresholds (e.g. 100.5) must NOT be corrected."""
        from app.services.probability_engine_v2 import _calc_prob_threshold
        mu, sigma, T = 100.0, 5.0, 100.5

        result = _calc_prob_threshold("gte", T, mu, sigma)
        expected = round(1.0 - _norm_cdf((T - mu) / sigma), 4)
        assert result == expected, (
            f"Half-integer GTE must not be corrected: expected {expected}, got {result}"
        )

    # ── Fix 1b: LTE — integer threshold boundary is T + 0.5 ─────────────────

    def test_calc_prob_threshold_lte_integer_uses_t_plus_half(self):
        """
        For integer T, P(round(actual) <= T) = P(actual < T+0.5).
        Parser 'X° or below' → operator='lte' → boundary T+0.5, NOT T−0.5.
        """
        from app.services.probability_engine_v2 import _calc_prob_threshold
        mu, sigma, T = 90.0, 5.0, 90.0

        result = _calc_prob_threshold("lte", T, mu, sigma)
        expected = round(_norm_cdf((T + 0.5 - mu) / sigma), 4)
        assert result == expected, (
            f"LTE integer: expected boundary T+0.5={T+0.5}, "
            f"expected P={expected}, got {result}"
        )
        # Must NOT equal the wrong T−0.5 value
        wrong = round(_norm_cdf((T - 0.5 - mu) / sigma), 4)
        assert result != wrong, (
            "LTE must use T+0.5, not T−0.5. Got same value as T−0.5 formula."
        )

    def test_lte_integer_at_mu_equals_t_exceeds_50_percent(self):
        """
        At mu=T, P(round(actual) <= T) > 50%.
        Settlement temperature T wins the 'at or below T' contract too,
        so corrected LTE probability is strictly above 50%.
        """
        from app.services.probability_engine_v2 import _calc_prob_threshold
        T = 90.0
        result = _calc_prob_threshold("lte", T, mu=T, sigma=5.0)
        assert result > 0.50, (
            f"LTE at mu=T: expected >50%, got {result:.4f}. "
            "NWS rounding correction (T+0.5 boundary) must push above 50%."
        )

    def test_calc_prob_threshold_lte_half_integer_unchanged(self):
        """Half-integer LTE thresholds (e.g. 89.5) must NOT be corrected."""
        from app.services.probability_engine_v2 import _calc_prob_threshold
        mu, sigma, T = 90.0, 5.0, 89.5

        result = _calc_prob_threshold("lte", T, mu, sigma)
        expected = round(_norm_cdf((T - mu) / sigma), 4)
        assert result == expected, (
            f"Half-integer LTE must not be corrected: expected {expected}, got {result}"
        )

    # ── Fix 1c: both inclusive contracts win at settlement temperature T ──────

    def test_gte_and_lte_both_above_50_at_mu_equals_t(self):
        """
        At mu=T (integer), BOTH P(GTE) > 50% AND P(LTE) > 50%.
        Settlement temperature T wins both inclusive boundary contracts.
        P(GTE) + P(LTE) > 1.0 is correct — contracts are not mutually exclusive.
        """
        from app.services.probability_engine_v2 import _calc_prob_threshold
        T, sigma = 90.0, 5.0
        p_gte = _calc_prob_threshold("gte", T, mu=T, sigma=sigma)
        p_lte = _calc_prob_threshold("lte", T, mu=T, sigma=sigma)
        assert p_gte > 0.50, f"GTE: expected >50% at mu=T, got {p_gte:.4f}"
        assert p_lte > 0.50, f"LTE: expected >50% at mu=T, got {p_lte:.4f}"
        assert p_gte + p_lte > 1.0, (
            f"P(GTE)+P(LTE)={p_gte+p_lte:.4f} should exceed 1.0 for integer T "
            "because T wins both contracts."
        )

    # ── Fix 1d: unknown operator raises ValueError ────────────────────────────

    def test_unknown_operator_raises_value_error(self):
        """Unknown operators must raise ValueError, not silently fallback."""
        import pytest
        from app.services.probability_engine_v2 import _calc_prob_threshold
        with pytest.raises(ValueError, match="Unsupported operator"):
            _calc_prob_threshold("lt", 90.0, 90.0, 5.0)
        with pytest.raises(ValueError, match="Unsupported operator"):
            _calc_prob_threshold("gt", 90.0, 90.0, 5.0)
        with pytest.raises(ValueError, match="Unsupported operator"):
            _calc_prob_threshold("", 90.0, 90.0, 5.0)

    # ── Fix 2: range contract uses lower−0.5, upper+0.5 boundaries ─────────

    def test_calc_prob_range_integer_bounds_expanded(self):
        """For integer [Lo, Hi], integration uses [Lo−0.5, Hi+0.5]."""
        from app.services.probability_engine_v2 import _calc_prob_range
        mu, sigma, lo, hi = 90.0, 5.0, 88.0, 92.0

        result = _calc_prob_range(lo, hi, mu, sigma)
        eff_lo, eff_hi = lo - 0.5, hi + 0.5
        expected = round(max(0.0, _norm_cdf((eff_hi - mu) / sigma) - _norm_cdf((eff_lo - mu) / sigma)), 4)
        assert result == expected

    def test_calc_prob_range_wider_than_naive(self):
        """Rounding-corrected range must be wider than naive integration."""
        from app.services.probability_engine_v2 import _calc_prob_range
        mu, sigma, lo, hi = 90.0, 5.0, 88.0, 92.0

        corrected = _calc_prob_range(lo, hi, mu, sigma)
        naive = round(max(0.0, _norm_cdf((hi - mu) / sigma) - _norm_cdf((lo - mu) / sigma)), 4)
        assert corrected > naive, "Corrected range probability must exceed naive"

    def test_calc_prob_range_half_integer_bounds_unchanged(self):
        """Half-integer bounds (e.g. 74.5, 94.5) must not be expanded."""
        from app.services.probability_engine_v2 import _calc_prob_range
        mu, sigma, lo, hi = 84.0, 5.0, 74.5, 94.5

        result = _calc_prob_range(lo, hi, mu, sigma)
        expected = round(max(0.0, _norm_cdf((hi - mu) / sigma) - _norm_cdf((lo - mu) / sigma)), 4)
        assert result == expected


# ===========================================================================
# Fix 1 + 2 — NWS Rounding Corrections (V3 engine helpers, independent copies)
# ===========================================================================

class TestNWSRoundingV3:
    """V3 has its own copies of the helpers; must be corrected independently."""

    def test_v3_is_integer_threshold_exists(self):
        from app.services.v3_probability_engine import _is_integer_threshold
        assert _is_integer_threshold(90.0) is True
        assert _is_integer_threshold(90.5) is False

    def test_v3_calc_prob_threshold_gte_integer_corrected(self):
        """V3 GTE: integer threshold uses T−0.5 boundary."""
        from app.services.v3_probability_engine import _calc_prob_threshold
        mu, sigma, T = 90.0, 5.0, 90.0

        result = _calc_prob_threshold("gte", T, mu, sigma)
        expected = round(1.0 - _norm_cdf((T - 0.5 - mu) / sigma), 6)
        assert result == expected

    def test_v3_calc_prob_threshold_lte_integer_uses_t_plus_half(self):
        """V3 LTE: integer threshold uses T+0.5 boundary, NOT T−0.5."""
        from app.services.v3_probability_engine import _calc_prob_threshold
        mu, sigma, T = 90.0, 5.0, 90.0

        result = _calc_prob_threshold("lte", T, mu, sigma)
        expected = round(_norm_cdf((T + 0.5 - mu) / sigma), 6)
        assert result == expected, (
            f"V3 LTE integer: expected T+0.5 boundary ({expected}), got {result}"
        )
        wrong = round(_norm_cdf((T - 0.5 - mu) / sigma), 6)
        assert result != wrong, "V3 LTE must use T+0.5, not T−0.5"

    def test_v3_calc_prob_threshold_gte_half_integer_unchanged(self):
        """V3: half-integer GTE thresholds are not corrected."""
        from app.services.v3_probability_engine import _calc_prob_threshold
        mu, sigma, T = 100.0, 5.0, 100.5

        result = _calc_prob_threshold("gte", T, mu, sigma)
        expected = round(1.0 - _norm_cdf((T - mu) / sigma), 6)
        assert result == expected

    def test_v3_unknown_operator_raises_value_error(self):
        """V3: unknown operators must raise ValueError, not silently fallback."""
        import pytest
        from app.services.v3_probability_engine import _calc_prob_threshold
        with pytest.raises(ValueError, match="Unsupported operator"):
            _calc_prob_threshold("lt", 90.0, 90.0, 5.0)

    def test_v2_v3_same_gte_boundary(self):
        """
        V2 and V3 use the same GTE boundary formula (T−0.5 for integer T).
        V2 rounds to 4dp; V3 to 6dp — compare within 0.0002 to allow for
        the 1-ULP difference that arises when re-rounding 6dp to 4dp.
        """
        from app.services.probability_engine_v2 import _calc_prob_threshold as v2_fn
        from app.services.v3_probability_engine import _calc_prob_threshold as v3_fn
        mu, sigma, T = 88.0, 4.5, 92.0

        v2 = v2_fn("gte", T, mu, sigma)              # 4dp
        v3 = v3_fn("gte", T, mu, sigma)              # 6dp
        assert abs(v2 - v3) < 0.0002, (
            f"V2 GTE={v2} and V3 GTE={v3} differ by more than 0.0002; "
            "both engines must use the same T−0.5 boundary formula."
        )

    def test_v2_v3_same_lte_boundary(self):
        """
        V2 and V3 use the same LTE boundary formula (T+0.5 for integer T).
        Compared within 0.0002 to allow for the V2/V3 precision difference.
        """
        from app.services.probability_engine_v2 import _calc_prob_threshold as v2_fn
        from app.services.v3_probability_engine import _calc_prob_threshold as v3_fn
        mu, sigma, T = 88.0, 4.5, 92.0

        v2 = v2_fn("lte", T, mu, sigma)              # 4dp
        v3 = v3_fn("lte", T, mu, sigma)              # 6dp
        assert abs(v2 - v3) < 0.0002, (
            f"V2 LTE={v2} and V3 LTE={v3} differ by more than 0.0002; "
            "both engines must use the same T+0.5 boundary formula."
        )

    def test_v3_calc_prob_range_integer_bounds_expanded(self):
        from app.services.v3_probability_engine import _calc_prob_range
        mu, sigma, lo, hi = 90.0, 5.0, 88.0, 92.0

        result = _calc_prob_range(lo, hi, mu, sigma)
        eff_lo, eff_hi = lo - 0.5, hi + 0.5
        expected = round(max(0.0, _norm_cdf((eff_hi - mu) / sigma) - _norm_cdf((eff_lo - mu) / sigma)), 6)
        assert result == expected

    def test_v3_calc_prob_range_half_integer_bounds_unchanged(self):
        from app.services.v3_probability_engine import _calc_prob_range
        mu, sigma, lo, hi = 84.0, 5.0, 74.5, 94.5

        result = _calc_prob_range(lo, hi, mu, sigma)
        expected = round(max(0.0, _norm_cdf((hi - mu) / sigma) - _norm_cdf((lo - mu) / sigma)), 6)
        assert result == expected


# ===========================================================================
# Fix 3 — ERA5 Local-Date Extraction (forecast_verifier._local_settlement_date)
# ===========================================================================

class TestLocalSettlementDate:
    """
    _local_settlement_date must convert UTC ISO timestamps to the station-local
    calendar date.  Pure-string function — no DB access needed.
    """

    def _fn(self, raw_date: str, city: str) -> str:
        from app.services.forecast_verifier import _local_settlement_date
        return _local_settlement_date(raw_date, city)

    def test_plain_date_string_returned_unchanged(self):
        """YYYY-MM-DD strings have no time component; return as-is."""
        assert self._fn("2026-08-07", "Los Angeles") == "2026-08-07"
        assert self._fn("2026-08-07", "Denver") == "2026-08-07"

    def test_utc_midnight_next_day_for_la(self):
        """
        LA trade: 2026-08-08T04:54:08Z is still 2026-08-07 in PDT (UTC-7).
        This was the confirmed production bug.
        """
        result = self._fn("2026-08-08T04:54:08Z", "Los Angeles")
        assert result == "2026-08-07", (
            f"Expected 2026-08-07 (PDT local), got {result}"
        )

    def test_utc_morning_for_chicago(self):
        """Chicago CDT = UTC-5. 2026-08-08T02:00:00Z → 2026-08-07 locally."""
        result = self._fn("2026-08-08T02:00:00Z", "Chicago")
        assert result == "2026-08-07", (
            f"Expected 2026-08-07 (CDT local), got {result}"
        )

    def test_utc_afternoon_no_crossover(self):
        """UTC afternoon (e.g. 18:00Z) is same calendar day for eastern cities."""
        # Dallas CDT = UTC-5: 18:00 UTC = 13:00 local = same day
        result = self._fn("2026-08-08T18:00:00Z", "Dallas")
        assert result == "2026-08-08"

    def test_utc_eastern_early_morning(self):
        """New York EDT = UTC-4: 2026-08-08T03:00:00Z = 2026-08-07 23:00 EDT."""
        result = self._fn("2026-08-08T03:00:00Z", "New York City")
        assert result == "2026-08-07"

    def test_utc_eastern_midday_same_day(self):
        """New York EDT = UTC-4: 2026-08-08T14:00:00Z = 10:00 EDT = same day."""
        result = self._fn("2026-08-08T14:00:00Z", "New York City")
        assert result == "2026-08-08"

    def test_empty_string_returns_empty(self):
        result = self._fn("", "Dallas")
        assert result == ""

    def test_short_string_returned_unchanged(self):
        result = self._fn("2026", "Dallas")
        assert result == "2026"

    def test_unknown_city_falls_back_to_utc_date(self):
        """
        For an unknown city, get_station returns None → tz_name defaults to UTC.
        UTC slice should equal the raw UTC date component.
        """
        result = self._fn("2026-08-08T04:00:00Z", "UnknownCityXYZ")
        # Falls back to UTC: date portion is 2026-08-08
        assert result == "2026-08-08"


# ===========================================================================
# Fix 4 — Hourly Sigma Floor (run_analysis_v2 / run_analysis_v22)
# ===========================================================================

class TestHourlySigmaFloor:
    """
    Hourly contracts must call _sigma_v2 with hourly=True so the 2.0°F
    floor (SIGMA_FLOOR_HOURLY) is applied instead of the 3.5°F daily floor.
    """

    @pytest.mark.asyncio
    async def test_run_analysis_v2_passes_hourly_true_for_hourly_contracts(self):
        """
        When contract_type='hourly_threshold', run_analysis_v2 must pass
        hourly=True to _sigma_v2.
        """
        from app.services.probability_engine_v2 import run_analysis_v2

        session = AsyncMock()
        sigma_call_args = {}

        async def mock_sigma(city, var, lead, month, sess, *, hourly=False):
            sigma_call_args["hourly"] = hourly
            return (3.5, "fixed_table")

        async def mock_bias(*args, **kwargs):
            return 0.0

        async def mock_calib(prob, sess, *, strategy_version="v2.0"):
            return 1.0

        with (
            patch("app.services.probability_engine_v2._sigma_v2", mock_sigma),
            patch("app.services.probability_engine_v2._bias_v2", mock_bias),
            patch("app.services.probability_engine_v2._calibration_adj_v2", mock_calib),
        ):
            await run_analysis_v2(
                title="Test",
                subtitle=None,
                city="Dallas",
                target_date_str="2026-09-01",
                weather_variable="high",
                operator="gte",
                threshold=95.0,
                parse_confidence="high",
                settlement_status="supported",
                unsupported_reason=None,
                forecast_high=96.0,
                forecast_low=75.0,
                forecast_retrieved_at=datetime.now(timezone.utc),
                yes_bid=0.85,
                yes_ask=0.87,
                contract_type="hourly_threshold",
                forecast_hourly_value=96.0,
                session=session,
            )

        assert sigma_call_args.get("hourly") is True, (
            f"Expected _sigma_v2 called with hourly=True, got {sigma_call_args}"
        )

    @pytest.mark.asyncio
    async def test_run_analysis_v2_passes_hourly_false_for_daily_contracts(self):
        """Daily contracts must pass hourly=False (default) to _sigma_v2."""
        from app.services.probability_engine_v2 import run_analysis_v2

        session = AsyncMock()
        sigma_call_args = {}

        async def mock_sigma(city, var, lead, month, sess, *, hourly=False):
            sigma_call_args["hourly"] = hourly
            return (3.5, "fixed_table")

        async def mock_bias(*args, **kwargs):
            return 0.0

        async def mock_calib(prob, sess, *, strategy_version="v2.0"):
            return 1.0

        with (
            patch("app.services.probability_engine_v2._sigma_v2", mock_sigma),
            patch("app.services.probability_engine_v2._bias_v2", mock_bias),
            patch("app.services.probability_engine_v2._calibration_adj_v2", mock_calib),
        ):
            await run_analysis_v2(
                title="Test",
                subtitle=None,
                city="Dallas",
                target_date_str="2026-09-01",
                weather_variable="high",
                operator="gte",
                threshold=95.0,
                parse_confidence="high",
                settlement_status="supported",
                unsupported_reason=None,
                forecast_high=96.0,
                forecast_low=75.0,
                forecast_retrieved_at=datetime.now(timezone.utc),
                yes_bid=0.85,
                yes_ask=0.87,
                contract_type="threshold",
                session=session,
            )

        assert sigma_call_args.get("hourly") is False, (
            f"Expected _sigma_v2 called with hourly=False, got {sigma_call_args}"
        )

    @pytest.mark.asyncio
    async def test_run_analysis_v22_passes_hourly_true_for_hourly_contracts(self):
        """V2.3 engine (run_analysis_v22) must also pass hourly=True for hourly contracts."""
        from app.services.probability_engine_v22 import run_analysis_v22

        session = AsyncMock()
        sigma_call_args = {}

        async def mock_sigma(city, var, lead, month, sess, *, hourly=False):
            sigma_call_args["hourly"] = hourly
            return (2.0, "fixed_table")

        async def mock_bias(*args, **kwargs):
            return 0.0

        async def mock_calib(prob, sess, *, strategy_version="v2.3"):
            return 1.0

        with (
            patch("app.services.probability_engine_v22._sigma_v2", mock_sigma),
            patch("app.services.probability_engine_v22._bias_v2", mock_bias),
            patch("app.services.probability_engine_v22._calibration_adj_v2", mock_calib),
        ):
            await run_analysis_v22(
                title="Test",
                subtitle=None,
                city="Dallas",
                target_date_str="2026-09-01",
                weather_variable="high",
                operator="gte",
                threshold=95.0,
                parse_confidence="high",
                settlement_status="supported",
                unsupported_reason=None,
                forecast_high=96.0,
                forecast_low=75.0,
                forecast_retrieved_at=datetime.now(timezone.utc),
                yes_bid=0.85,
                yes_ask=0.87,
                contract_type="hourly_threshold",
                forecast_hourly_value=96.0,
                session=session,
            )

        assert sigma_call_args.get("hourly") is True, (
            f"V2.3: Expected _sigma_v2 called with hourly=True, got {sigma_call_args}"
        )

    def test_sigma_floor_hourly_value(self):
        """SIGMA_FLOOR_HOURLY must be 2.0 (not 3.5, the daily floor)."""
        from app.services.probability_engine_v2 import SIGMA_FLOOR, SIGMA_FLOOR_HOURLY
        assert SIGMA_FLOOR_HOURLY == 2.0
        assert SIGMA_FLOOR == 3.5
        assert SIGMA_FLOOR_HOURLY < SIGMA_FLOOR


# ===========================================================================
# Fix 5 — Station Verification: get_verified_station bug
# ===========================================================================

class TestStationVerification:
    """
    get_verified_station must return None for unverified stations.
    Bug: old code had `else s` (returned the station regardless of .verified).
    Fix: `else None`.
    """

    def test_verified_station_returns_station(self):
        """get_verified_station returns the station object for a verified city."""
        from app.services.settlement_stations import get_verified_station, verified_cities
        verified = verified_cities()
        if not verified:
            pytest.skip("No verified cities in registry")
        city = verified[0]
        s = get_verified_station(city)
        assert s is not None, f"{city} is in verified_cities() but get_verified_station returned None"
        assert s.verified is True

    def test_unverified_station_returns_none(self):
        """
        get_verified_station must return None for unverified stations.
        This is the core bug fix: previously returned the unverified station object.
        """
        from app.services.settlement_stations import get_verified_station, get_station, verified_cities
        verified = set(verified_cities())
        # Find an unverified city
        from app.services.settlement_stations import SETTLEMENT_STATIONS
        unverified = [c for c, s in SETTLEMENT_STATIONS.items() if not s.verified]
        if not unverified:
            pytest.skip("No unverified cities in registry")
        city = unverified[0]
        s = get_verified_station(city)
        assert s is None, (
            f"get_verified_station('{city}') returned {s!r} instead of None. "
            "Bug: else branch returned the station instead of None."
        )

    def test_unknown_city_returns_none(self):
        """get_verified_station for an unknown city must return None."""
        from app.services.settlement_stations import get_verified_station
        assert get_verified_station("NotARealCity123") is None

    def test_multiple_unverified_cities_all_return_none(self):
        """Every unverified city must return None from get_verified_station."""
        from app.services.settlement_stations import get_verified_station, SETTLEMENT_STATIONS
        unverified = [c for c, s in SETTLEMENT_STATIONS.items() if not s.verified]
        for city in unverified:
            s = get_verified_station(city)
            assert s is None, (
                f"get_verified_station('{city}') returned non-None for unverified station"
            )

    def test_eligibility_engine_returns_research_only_for_unverified(self):
        """
        assess_trade_eligibility returns RESEARCH_ONLY when station_verified=False.
        This is the guard that prevents OFFICIAL trades on unverified stations.
        """
        from datetime import datetime, timezone, timedelta
        from app.services.eligibility import assess_trade_eligibility

        now = datetime.now(timezone.utc)
        quote_time = now - timedelta(seconds=60)

        status, reason, _ = assess_trade_eligibility(
            contract_type="threshold",
            target_settlement_date_str="2026-09-01",
            settlement_timezone="America/Chicago",
            now=now,
            side_market_price=0.85,
            edge_pct_points=5.0,
            station_verified=False,   # <── key: unverified station
            direction="yes",
            quote_timestamp=quote_time,
            quote_ask=0.87,
            market_close_timestamp=now + timedelta(hours=5),
        )
        assert status == "RESEARCH_ONLY"
        assert reason == "settlement_station_unverified"


# ===========================================================================
# Fix 6 — Philadelphia / San Antonio Coordinates
# ===========================================================================

class TestCityCoordinates:
    """
    SERIES_TO_CITY must use settlement-station coordinates for Philadelphia
    and San Antonio, not city-centre coordinates.
    """

    def test_philadelphia_uses_kphl_coordinates(self):
        """Philadelphia must use KPHL airport station (39.8721, -75.2411)."""
        from app.services.kalshi import SERIES_TO_CITY
        # Some entries are None (unsupported series); filter them out
        philly_entries = [v for v in SERIES_TO_CITY.values() if v is not None and v[0] == "Philadelphia"]
        assert philly_entries, "Philadelphia not found in SERIES_TO_CITY"
        for city_name, lat, lon in philly_entries:
            assert abs(lat - 39.8721) < 0.001, (
                f"Philadelphia lat should be ~39.8721 (KPHL), got {lat}. "
                "Was city-centre (39.9526) corrected?"
            )
            assert abs(lon - (-75.2411)) < 0.001, (
                f"Philadelphia lon should be ~-75.2411 (KPHL), got {lon}. "
                "Was city-centre (-75.1652) corrected?"
            )

    def test_san_antonio_uses_ksat_coordinates(self):
        """San Antonio must use KSAT airport station (29.5337, -98.4698)."""
        from app.services.kalshi import SERIES_TO_CITY
        # Some entries are None (unsupported series); filter them out
        sa_entries = [v for v in SERIES_TO_CITY.values() if v is not None and v[0] == "San Antonio"]
        assert sa_entries, "San Antonio not found in SERIES_TO_CITY"
        for city_name, lat, lon in sa_entries:
            assert abs(lat - 29.5337) < 0.001, (
                f"San Antonio lat should be ~29.5337 (KSAT), got {lat}. "
                "Was city-centre (29.4241) corrected?"
            )
            assert abs(lon - (-98.4698)) < 0.001, (
                f"San Antonio lon should be ~-98.4698 (KSAT), got {lon}. "
                "Was city-centre (-98.4936) corrected?"
            )

    def test_coordinates_match_settlement_station_registry(self):
        """SERIES_TO_CITY coords for Philly/SA must match settlement_stations.py."""
        from app.services.kalshi import SERIES_TO_CITY
        from app.services.settlement_stations import get_station

        for city_name, registry_name in [
            ("Philadelphia", "Philadelphia"),
            ("San Antonio", "San Antonio"),
        ]:
            # Filter out None entries (unsupported series markers)
            series_entries = [v for v in SERIES_TO_CITY.values() if v is not None and v[0] == city_name]
            station = get_station(registry_name)
            assert station is not None, f"{registry_name} not in settlement_stations"
            for _, lat, lon in series_entries:
                assert abs(lat - station.lat) < 0.0001, (
                    f"{city_name}: SERIES_TO_CITY lat {lat} != station.lat {station.lat}"
                )
                assert abs(lon - station.lon) < 0.0001, (
                    f"{city_name}: SERIES_TO_CITY lon {lon} != station.lon {station.lon}"
                )


# ===========================================================================
# Fix 7 — V2.3 Strategy Version + Calibration Isolation
# ===========================================================================

class TestStrategyVersion:
    """
    paper_trading_v22.py must use STRATEGY_VERSION = "v2.3" so new trades
    are stamped correctly and do not inherit v2.0-era calibration rows.
    """

    def test_paper_trading_v22_strategy_version_is_v23(self):
        """STRATEGY_VERSION must be 'v2.3' after the Forward Test B correction."""
        from app.services.paper_trading_v22 import STRATEGY_VERSION
        assert STRATEGY_VERSION == "v2.3", (
            f"Expected 'v2.3', got '{STRATEGY_VERSION}'. "
            "Historical v2.2 trades are preserved; this only affects new trades."
        )

    @pytest.mark.asyncio
    async def test_probability_engine_v22_explanation_prefix_is_v23(self):
        """Explanation strings from run_analysis_v22 must say [v2.3], not [v2.2]."""
        from app.services.probability_engine_v22 import run_analysis_v22

        result = await run_analysis_v22(
            title="Test",
            subtitle=None,
            city="Dallas",
            target_date_str="2026-09-01",
            weather_variable="high",
            operator="gte",
            threshold=95.0,
            parse_confidence="high",
            settlement_status="supported",
            unsupported_reason=None,
            forecast_high=96.0,
            forecast_low=75.0,
            forecast_retrieved_at=datetime.now(timezone.utc),
            yes_bid=0.85,
            yes_ask=0.87,
            session=None,  # no DB; use fixed-table fallback
        )
        assert "[v2.3]" in result.explanation, (
            f"Expected '[v2.3]' in explanation, got: {result.explanation!r}"
        )
        assert "[v2.2]" not in result.explanation, (
            "Old '[v2.2]' prefix still present in explanation"
        )

    @pytest.mark.asyncio
    async def test_calibration_adj_v2_accepts_strategy_version_param(self):
        """
        _calibration_adj_v2 must accept a strategy_version keyword argument.
        When called with strategy_version='v2.3', it must not query v2.0 rows.
        Simulate no rows found (scalar_one_or_none returns None) → factor = 1.0.
        """
        from app.services.probability_engine_v2 import _calibration_adj_v2

        session = AsyncMock()
        # _calibration_adj_v2 calls session.execute(...) then .scalar_one_or_none()
        # (synchronous call on the execute result). Return None to simulate no rows.
        mock_exec_result = MagicMock()
        mock_exec_result.scalar_one_or_none.return_value = None  # no row found
        session.execute = AsyncMock(return_value=mock_exec_result)

        factor = await _calibration_adj_v2(0.75, session, strategy_version="v2.3")
        assert factor == 1.0, (
            f"V2.3 calibration should be 1.0 (no rows), got {factor}"
        )

    @pytest.mark.asyncio
    async def test_v23_calibration_version_in_db_query(self):
        """
        When run_analysis_v22 calls _calibration_adj_v2, it must pass
        strategy_version='v2.3', not 'v2.0'.
        """
        from app.services.probability_engine_v22 import run_analysis_v22

        session = AsyncMock()
        calib_kwargs_seen = {}

        async def mock_sigma(*args, **kwargs):
            return (3.5, "fixed_table")

        async def mock_bias(*args, **kwargs):
            return 0.0

        async def mock_calib(prob, sess, *, strategy_version="v2.0"):
            calib_kwargs_seen["strategy_version"] = strategy_version
            return 1.0

        with (
            patch("app.services.probability_engine_v22._sigma_v2", mock_sigma),
            patch("app.services.probability_engine_v22._bias_v2", mock_bias),
            patch("app.services.probability_engine_v22._calibration_adj_v2", mock_calib),
        ):
            await run_analysis_v22(
                title="Test",
                subtitle=None,
                city="Dallas",
                target_date_str="2026-09-01",
                weather_variable="high",
                operator="gte",
                threshold=95.0,
                parse_confidence="high",
                settlement_status="supported",
                unsupported_reason=None,
                forecast_high=96.0,
                forecast_low=75.0,
                forecast_retrieved_at=datetime.now(timezone.utc),
                yes_bid=0.85,
                yes_ask=0.87,
                session=session,
            )

        assert calib_kwargs_seen.get("strategy_version") == "v2.3", (
            f"Expected strategy_version='v2.3' passed to _calibration_adj_v2, "
            f"got '{calib_kwargs_seen.get('strategy_version')}'"
        )


# ===========================================================================
# Fix 8 — Forward Test B Boundary Constants
# ===========================================================================

class TestForwardTestBConstants:
    """
    FORWARD_TEST_START_B and FORWARD_TEST_PHASE_B must exist in paper_trades.py.
    After activation, FORWARD_TEST_START_B must be the exact UTC deployment timestamp.
    """

    def test_forward_test_start_b_is_activated(self):
        from app.routers.paper_trades import FORWARD_TEST_START_B
        assert FORWARD_TEST_START_B is not None, (
            "FORWARD_TEST_START_B must be set to the deployment timestamp after corrections "
            "are verified. Got None — Forward Test B has not been activated."
        )
        assert FORWARD_TEST_START_B == datetime(2026, 8, 9, 0, 15, 12, tzinfo=timezone.utc), (
            f"FORWARD_TEST_START_B must be exactly the Publish #1 completion timestamp. "
            f"Got: {FORWARD_TEST_START_B!r}"
        )

    def test_forward_test_phase_b_text(self):
        from app.routers.paper_trades import FORWARD_TEST_PHASE_B
        assert "active" in FORWARD_TEST_PHASE_B.lower(), (
            f"FORWARD_TEST_PHASE_B should indicate active state, got: {FORWARD_TEST_PHASE_B!r}"
        )

    def test_forward_test_a_start_unchanged(self):
        """The original FORWARD_TEST_START (Forward Test A) must not be altered."""
        from app.routers.paper_trades import FORWARD_TEST_START
        assert FORWARD_TEST_START == datetime(2026, 8, 4, 22, 21, 44, tzinfo=timezone.utc), (
            "FORWARD_TEST_START (Forward Test A boundary) must not be changed"
        )


# ===========================================================================
# Historical V2.2 trade preservation (non-regression)
# ===========================================================================

class TestHistoricalPreservation:
    """
    Changing STRATEGY_VERSION to v2.3 must not affect historical paper_trades rows
    that already have strategy_version='v2.2'.  Those rows are immutable DB records.
    This is a code-level verification: the constant only affects NEW inserts.
    """

    def test_v22_strategy_version_not_v21_not_v2(self):
        """Verify the engine did not roll back to an older version string."""
        from app.services.paper_trading_v22 import STRATEGY_VERSION
        assert STRATEGY_VERSION not in ("v2.1", "v2.0", "v2"), (
            f"STRATEGY_VERSION must not roll back to old version, got {STRATEGY_VERSION!r}"
        )

    def test_v21_strategy_version_still_v21(self):
        """V2.1 engine must keep its own STRATEGY_VERSION unchanged."""
        from app.services.paper_trading_v21 import STRATEGY_VERSION as V21_VERSION
        assert V21_VERSION == "v2.1", (
            f"V2.1 STRATEGY_VERSION must remain 'v2.1', got {V21_VERSION!r}"
        )
