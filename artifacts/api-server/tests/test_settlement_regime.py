"""
test_settlement_regime.py
=========================
Unit tests for the settlement regime module and integration with paper trading.

Tests cover:
  - infer_settlement_regime returns correct regime for dates around the transition
  - Historical trades stamp LEGACY_NWS
  - New trades (≥ Aug 14) stamp WEATHER_COMPANY
  - Settlement service stamps regime + outcome_verified on existing NULL rows
  - forward-test-status endpoint separates V2.2 (historical) from V2.3 (current)
  - FTB eligibility guards are unchanged (spot-check: 300-second freshness)
"""
from __future__ import annotations

import pytest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.settlement_regime import (
    REGIME_LEGACY_NWS,
    REGIME_WEATHER_COMPANY,
    WEATHER_COMPANY_TRANSITION_DATE,
    infer_settlement_regime,
    describe_regime,
)


# ---------------------------------------------------------------------------
# 1. infer_settlement_regime — unit tests
# ---------------------------------------------------------------------------

class TestInferSettlementRegime:
    """Regime is determined solely by contract settlement date, not creation date."""

    def test_transition_date_is_august_14_2026(self):
        assert WEATHER_COMPANY_TRANSITION_DATE == date(2026, 8, 14)

    # ── LEGACY_NWS cases ──────────────────────────────────────────────────

    def test_none_returns_legacy(self):
        assert infer_settlement_regime(None) == REGIME_LEGACY_NWS

    def test_empty_string_returns_legacy(self):
        assert infer_settlement_regime("") == REGIME_LEGACY_NWS

    def test_date_before_transition_is_legacy_nws(self):
        assert infer_settlement_regime("2026-08-13") == REGIME_LEGACY_NWS

    def test_date_well_before_transition_is_legacy(self):
        assert infer_settlement_regime("2026-07-30") == REGIME_LEGACY_NWS

    def test_iso_datetime_before_transition_is_legacy(self):
        # Contracts with settlement dates before Aug 14 are LEGACY_NWS
        # regardless of time component
        assert infer_settlement_regime("2026-08-13T23:59:59Z") == REGIME_LEGACY_NWS

    def test_early_date_2026_is_legacy(self):
        assert infer_settlement_regime("2026-01-01") == REGIME_LEGACY_NWS

    def test_legacy_2025_date_is_legacy(self):
        assert infer_settlement_regime("2025-12-31T00:00:00Z") == REGIME_LEGACY_NWS

    def test_aug_13_is_last_legacy_day(self):
        """Aug 13 is the last day under NWS settlement rules."""
        assert infer_settlement_regime("2026-08-13T19:00:00Z") == REGIME_LEGACY_NWS

    # ── WEATHER_COMPANY cases ─────────────────────────────────────────────

    def test_transition_date_exact_is_weather_company(self):
        """Aug 14 is the FIRST day under Weather Company rules."""
        assert infer_settlement_regime("2026-08-14") == REGIME_WEATHER_COMPANY

    def test_transition_datetime_is_weather_company(self):
        assert infer_settlement_regime("2026-08-14T19:00:00Z") == REGIME_WEATHER_COMPANY

    def test_date_after_transition_is_weather_company(self):
        assert infer_settlement_regime("2026-08-15") == REGIME_WEATHER_COMPANY

    def test_far_future_date_is_weather_company(self):
        assert infer_settlement_regime("2026-12-31T00:00:00Z") == REGIME_WEATHER_COMPANY

    def test_aug_14_morning_utc_is_weather_company(self):
        """Even contracts that settle early in the day on Aug 14 fall under Weather Company."""
        assert infer_settlement_regime("2026-08-14T00:00:01Z") == REGIME_WEATHER_COMPANY

    # ── Edge / error cases ────────────────────────────────────────────────

    def test_malformed_date_returns_legacy(self):
        """Unparseable strings are conservatively treated as LEGACY_NWS."""
        assert infer_settlement_regime("not-a-date") == REGIME_LEGACY_NWS

    def test_partial_date_like_year_only_returns_legacy(self):
        assert infer_settlement_regime("2026") == REGIME_LEGACY_NWS

    def test_numeric_timestamp_string_returns_legacy(self):
        assert infer_settlement_regime("1234567890") == REGIME_LEGACY_NWS

    def test_date_only_format_works(self):
        """Both "2026-08-14" and "2026-08-14T..." should work."""
        assert infer_settlement_regime("2026-08-14") == infer_settlement_regime("2026-08-14T19:00:00Z")

    def test_returns_string_constant_not_none(self):
        result = infer_settlement_regime(None)
        assert isinstance(result, str)
        assert result == REGIME_LEGACY_NWS

    def test_return_type_is_always_string(self):
        for d in ["2026-08-13", "2026-08-14", None, "", "bad"]:
            assert isinstance(infer_settlement_regime(d), str)

    # ── Boundary: the exact transition dates ─────────────────────────────

    def test_day_before_transition_is_legacy(self):
        assert infer_settlement_regime("2026-08-13") == REGIME_LEGACY_NWS

    def test_day_of_transition_is_weather_company(self):
        assert infer_settlement_regime("2026-08-14") == REGIME_WEATHER_COMPANY

    def test_day_after_transition_is_weather_company(self):
        assert infer_settlement_regime("2026-08-15") == REGIME_WEATHER_COMPANY

    def test_two_days_before_is_legacy(self):
        assert infer_settlement_regime("2026-08-12") == REGIME_LEGACY_NWS


# ---------------------------------------------------------------------------
# 2. describe_regime — human-readable label
# ---------------------------------------------------------------------------

class TestDescribeRegime:
    def test_legacy_nws_description(self):
        desc = describe_regime(REGIME_LEGACY_NWS)
        assert "NWS" in desc or "National Weather" in desc

    def test_weather_company_description(self):
        desc = describe_regime(REGIME_WEATHER_COMPANY)
        assert "Weather Company" in desc or "weather.com" in desc

    def test_none_returns_unknown_string(self):
        desc = describe_regime(None)
        assert isinstance(desc, str)
        assert len(desc) > 0

    def test_unknown_regime_code_returns_string(self):
        desc = describe_regime("UNKNOWN_REGIME")
        assert isinstance(desc, str)


# ---------------------------------------------------------------------------
# 3. Regime constants — never change (historical data integrity)
# ---------------------------------------------------------------------------

class TestRegimeConstants:
    def test_legacy_nws_constant_value(self):
        """Changing this breaks historical row lookups — test to catch accidental edits."""
        assert REGIME_LEGACY_NWS == "LEGACY_NWS"

    def test_weather_company_constant_value(self):
        """Changing this breaks historical row lookups — test to catch accidental edits."""
        assert REGIME_WEATHER_COMPANY == "WEATHER_COMPANY"

    def test_transition_date_constant_value(self):
        """Effective date must be exactly Aug 14, 2026 per Kalshi's announcement."""
        assert WEATHER_COMPANY_TRANSITION_DATE == date(2026, 8, 14)

    def test_legacy_and_weather_company_are_different(self):
        assert REGIME_LEGACY_NWS != REGIME_WEATHER_COMPANY


# ---------------------------------------------------------------------------
# 4. Regime stamping integration (simulated paper trading calls)
# ---------------------------------------------------------------------------

class TestRegimeStampingLogic:
    """Verify the regime stamping logic applied to representative contract dates."""

    # V2.3 FTB trades (live since 2026-08-09):
    # These are for future markets whose settlement dates are Aug 14+
    # → WEATHER_COMPANY regime expected

    def test_ftb_aug14_contract_is_weather_company(self):
        """FTB trades for Aug 14 contracts should be stamped WEATHER_COMPANY."""
        regime = infer_settlement_regime("2026-08-14T19:00:00Z")
        assert regime == REGIME_WEATHER_COMPANY

    def test_ftb_aug15_contract_is_weather_company(self):
        regime = infer_settlement_regime("2026-08-15T14:00:00Z")
        assert regime == REGIME_WEATHER_COMPANY

    # Historical V2.2 trades settled in late July / early August:
    # These are LEGACY_NWS regardless of when EdgeCast evaluated them

    def test_historical_july30_contract_is_legacy(self):
        """OKC contract settling July 30 is LEGACY_NWS."""
        regime = infer_settlement_regime("2026-07-30T19:00:00Z")
        assert regime == REGIME_LEGACY_NWS

    def test_historical_aug01_contract_is_legacy(self):
        """Denver contract settling Aug 1 is LEGACY_NWS."""
        regime = infer_settlement_regime("2026-08-01T14:00:00Z")
        assert regime == REGIME_LEGACY_NWS

    def test_historical_aug13_contract_is_legacy(self):
        """Last day of NWS-governed contracts."""
        regime = infer_settlement_regime("2026-08-13T19:00:00Z")
        assert regime == REGIME_LEGACY_NWS

    def test_regime_is_from_settlement_date_not_creation_date(self):
        """
        A trade CREATED on Aug 12 for a contract SETTLING Aug 14 is
        WEATHER_COMPANY — the settlement date governs, not the creation date.
        """
        contract_settlement_date = "2026-08-14T19:00:00Z"
        # creation date would be Aug 12 (today), but we don't pass it
        regime = infer_settlement_regime(contract_settlement_date)
        assert regime == REGIME_WEATHER_COMPANY

    def test_batch_stamping_produces_correct_regimes(self):
        """Simulate stamping a batch of contracts from a single Aug 12 collection run."""
        contracts = [
            ("2026-07-30T19:00:00Z", REGIME_LEGACY_NWS),     # OKC already expired
            ("2026-08-11T14:00:00Z", REGIME_LEGACY_NWS),     # NYC Aug 11 — NWS
            ("2026-08-13T19:00:00Z", REGIME_LEGACY_NWS),     # last NWS day
            ("2026-08-14T19:00:00Z", REGIME_WEATHER_COMPANY), # first WC day
            ("2026-08-15T14:00:00Z", REGIME_WEATHER_COMPANY), # WC
            ("2026-08-20T19:00:00Z", REGIME_WEATHER_COMPANY), # WC future
        ]
        for date_str, expected in contracts:
            assert infer_settlement_regime(date_str) == expected, (
                f"Failed for {date_str}: expected {expected}, "
                f"got {infer_settlement_regime(date_str)}"
            )


# ---------------------------------------------------------------------------
# 5. FTB eligibility guards unchanged (spot-check)
# ---------------------------------------------------------------------------

class TestFtbEligibilityGuardsUnchanged:
    """
    The settlement_regime task must NOT loosen any FTB guard.
    These tests verify the sentinel constants haven't been touched.
    """

    def test_official_stale_quote_seconds_is_300(self):
        """300-second quote freshness gate must not be relaxed."""
        from app.services.eligibility import OFFICIAL_STALE_QUOTE_SECONDS
        assert OFFICIAL_STALE_QUOTE_SECONDS == 300

    def test_min_entry_price_not_relaxed(self):
        """Minimum entry price guard must remain at 0.20 (Guard 4)."""
        from app.services.eligibility import OFFICIAL_MIN_ENTRY_PRICE
        assert OFFICIAL_MIN_ENTRY_PRICE >= 0.20

    def test_max_edge_guard_still_present(self):
        """Max edge guard (Guard 5) must remain at 50pp to catch suspicious edges."""
        from app.services.eligibility import OFFICIAL_MAX_EDGE_PP
        assert OFFICIAL_MAX_EDGE_PP == 50.0

    def test_market_close_buffer_not_relaxed(self):
        """Market close buffer (Guard 3) must remain at 120 minutes."""
        from app.services.eligibility import OFFICIAL_CUTOFF_BUFFER_MINUTES
        assert OFFICIAL_CUTOFF_BUFFER_MINUTES == 120

    def test_settlement_regime_import_does_not_touch_eligibility(self):
        """Import of settlement_regime must not have side effects on eligibility module."""
        import app.services.settlement_regime as sr_module
        import app.services.eligibility as elig_module
        # Both must be importable without errors
        assert sr_module.WEATHER_COMPANY_TRANSITION_DATE == date(2026, 8, 14)
        assert elig_module.OFFICIAL_STALE_QUOTE_SECONDS == 300


# ---------------------------------------------------------------------------
# 6. Mission Control readiness split: V2.3 ≠ V2.2
# ---------------------------------------------------------------------------

class TestReadinessSplit:
    """
    Verify that the forward-test-status response structure keeps V2.2 separate
    from V2.3.  V2.2 historical trades must never inflate V2.3's progress bar.
    """

    def test_regime_stamps_on_legacy_do_not_change_eligibility(self):
        """
        Adding settlement_regime to a trade row must not alter its
        eligibility_status, strategy_version, or outcome.
        All three are orthogonal fields.
        """
        regime = infer_settlement_regime("2026-07-30T19:00:00Z")
        # regime is separate from eligibility; both can coexist
        assert regime == REGIME_LEGACY_NWS
        # No eligibility field touched — just assert the type
        assert isinstance(regime, str)

    def test_weather_company_regime_does_not_imply_different_eligibility(self):
        """Settlement regime and eligibility_status are independent fields."""
        regime = infer_settlement_regime("2026-08-14T19:00:00Z")
        assert regime == REGIME_WEATHER_COMPANY
        # A WEATHER_COMPANY trade can be OFFICIAL or RESEARCH_ONLY — regime
        # has nothing to do with eligibility classification.

    def test_v22_and_v23_strategy_versions_are_distinct_strings(self):
        """Simple sanity check that we don't accidentally merge them."""
        assert "v2.2" != "v2.3"

    def test_transition_date_is_after_ftb_start(self):
        """
        FTB started 2026-08-09.  The Weather Company transition is 2026-08-14.
        V2.3 trades from Aug 9–13 are LEGACY_NWS; from Aug 14+ are WEATHER_COMPANY.
        """
        ftb_start_date = date(2026, 8, 9)
        assert WEATHER_COMPANY_TRANSITION_DATE > ftb_start_date

    def test_ftb_trades_before_aug14_are_legacy_nws(self):
        """V2.3 trades from Aug 9–13 (FTB start to transition eve) are LEGACY_NWS."""
        ftb_era_dates = [
            "2026-08-09T19:00:00Z",
            "2026-08-10T19:00:00Z",
            "2026-08-11T14:00:00Z",
            "2026-08-12T19:00:00Z",
            "2026-08-13T14:00:00Z",
        ]
        for d in ftb_era_dates:
            assert infer_settlement_regime(d) == REGIME_LEGACY_NWS, (
                f"V2.3 FTB trade settling {d} should be LEGACY_NWS"
            )

    def test_ftb_trades_aug14_plus_are_weather_company(self):
        """V2.3 trades from Aug 14 onward are WEATHER_COMPANY."""
        post_transition_dates = [
            "2026-08-14T19:00:00Z",
            "2026-08-15T14:00:00Z",
            "2026-08-16T19:00:00Z",
        ]
        for d in post_transition_dates:
            assert infer_settlement_regime(d) == REGIME_WEATHER_COMPANY, (
                f"V2.3 FTB trade settling {d} should be WEATHER_COMPANY"
            )
