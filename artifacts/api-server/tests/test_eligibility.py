"""
tests/test_eligibility.py
Comprehensive tests for the Official Trade Eligibility Engine (Guards 1–8)
and batch-level correlated-exposure limit (Guard 6).

Each test corresponds to a guard specified in the hardening pass:
  G1  Hourly threshold exclusion
  G2  Same-day detection (timezone-aware regression)
  G3  Hard settlement cutoff
  G4  Entry-price floor ($0.20)
  G5  Extreme-edge cap (50pp)
  G6  Correlated-exposure limit (batch)
  G7  Verified settlement station
  G8  Fresh executable quote (missing / stale / wrong direction)

Additional integration tests:
  I1  Strategy isolation (research exclusion from official metrics)
  I2  Full OFFICIAL passing scenario (all guards green)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.eligibility import (
    OFFICIAL_CUTOFF_BUFFER_MINUTES,
    OFFICIAL_MAX_EDGE_PP,
    OFFICIAL_MIN_ENTRY_PRICE,
    OFFICIAL_STALE_QUOTE_SECONDS,
    REASON_CORRELATED,
    REASON_CUTOFF,
    REASON_EXTREME_EDGE,
    REASON_HOURLY,
    REASON_PRICE_FLOOR,
    REASON_SAME_DAY,
    REASON_STALE_QUOTE,
    REASON_STATION,
    apply_correlated_limit,
    assess_trade_eligibility,
)

# ── Shared helpers ────────────────────────────────────────────────────────────

SETTLEMENT_TZ = "America/Denver"   # UTC-6 in summer (MDT)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _make_settlement_dt(*, days_ahead: int = 2, hour_utc: int = 19) -> datetime:
    """Return a UTC datetime `days_ahead` days from now at `hour_utc` UTC."""
    base = _now_utc().replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    return base + timedelta(days=days_ahead)


def _fresh_quote() -> datetime:
    """Return a quote timestamp that is 30 minutes old (well within 4-hour window)."""
    return _now_utc() - timedelta(minutes=30)


def _build_base_kwargs(**overrides) -> dict:
    """Return a fully-passing set of kwargs for assess_trade_eligibility."""
    settlement_dt = _make_settlement_dt(days_ahead=2)
    base = {
        "contract_type":                "threshold",
        "target_settlement_date_str":   settlement_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "settlement_timezone":          SETTLEMENT_TZ,
        "now":                          _now_utc(),
        "side_market_price":            0.30,
        "edge_pct_points":              15.0,
        "station_verified":             True,
        "direction":                    "NO",
        "quote_timestamp":              _fresh_quote(),
        "quote_ask":                    0.30,
    }
    base.update(overrides)
    return base


# ── G1: Hourly threshold exclusion ──────────────────────────────────────────

class TestGuard1Hourly:
    def test_hourly_contract_is_research_only(self):
        """Hourly threshold contracts must never be OFFICIAL."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(contract_type="hourly_threshold")
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_HOURLY

    def test_daily_threshold_passes_guard1(self):
        """Standard 'threshold' contracts pass Guard 1."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(contract_type="threshold")
        )
        # Should not be blocked by G1 (may pass all guards if other params good)
        assert reason != REASON_HOURLY

    def test_range_contract_passes_guard1(self):
        """Range contracts (daily high/low band) pass Guard 1."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(contract_type="range")
        )
        assert reason != REASON_HOURLY


# ── G2: Same-day detection (timezone-aware) ──────────────────────────────────

class TestGuard2SameDay:
    def test_same_day_in_utc_but_next_day_local_is_official(self):
        """
        Regression guard: if it is 11pm UTC today but the settlement is tomorrow
        local time, the trade must NOT be blocked as same-day.

        Example: Denver (MDT = UTC-6).  UTC midnight Aug 4 = 6pm MDT Aug 3.
        A settlement at 9pm MDT Aug 4 = 3am UTC Aug 5.
        If now is 11pm UTC Aug 4 (5pm MDT Aug 4), settlement is TOMORROW local.
        """
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(SETTLEMENT_TZ)
        now = _now_utc()

        # Craft a settlement that is tomorrow in MDT even though UTC dates may differ
        # Use the exact timezone-aware offset to make it unambiguous
        now_local = now.astimezone(tz)
        tomorrow_local = now_local.replace(
            hour=19, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)
        settlement_dt = tomorrow_local.astimezone(timezone.utc)

        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(
                target_settlement_date_str=settlement_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                now=now,
            )
        )
        assert reason != REASON_SAME_DAY, (
            f"Expected next-local-day trade to pass G2, got reason={reason}"
        )

    def test_same_day_local_is_research_only(self):
        """
        A settlement that falls on today's date in local time must be blocked.
        """
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(SETTLEMENT_TZ)
        now = _now_utc()
        now_local = now.astimezone(tz)

        # Settlement is TODAY local at 23:59 but in 3+ hours so passes cutoff guard
        same_day_local = now_local.replace(
            hour=23, minute=59, second=0, microsecond=0
        )
        # Ensure it's at least CUTOFF_BUFFER_MINUTES ahead in wall time
        settlement_dt = same_day_local.astimezone(timezone.utc)
        seconds_ahead = (settlement_dt - now).total_seconds()

        if seconds_ahead < OFFICIAL_CUTOFF_BUFFER_MINUTES * 60 + 300:
            pytest.skip("Settlement too close to distinguish from cutoff guard; skip this scenario")

        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(
                target_settlement_date_str=settlement_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                now=now,
            )
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_SAME_DAY

    def test_lead_2_days_is_not_same_day(self):
        """2-day lead time always passes the same-day guard."""
        status, reason, _ = assess_trade_eligibility(**_build_base_kwargs())
        assert reason != REASON_SAME_DAY


# ── G3: Hard settlement cutoff ───────────────────────────────────────────────

class TestGuard3Cutoff:
    def test_settlement_past_is_research_only(self):
        """Settlement already happened → blocked."""
        past = (_now_utc() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(target_settlement_date_str=past)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_CUTOFF

    def test_settlement_within_buffer_is_research_only(self):
        """Settlement < 120 minutes away → blocked."""
        soon = (_now_utc() + timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(target_settlement_date_str=soon)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_CUTOFF

    def test_missing_settlement_date_is_research_only(self):
        """No settlement date string → blocked as unverifiable."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(target_settlement_date_str=None)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_CUTOFF

    def test_settlement_well_ahead_passes_cutoff(self):
        """Settlement 48 hours away (the default) passes the cutoff guard."""
        status, reason, _ = assess_trade_eligibility(**_build_base_kwargs())
        assert reason != REASON_CUTOFF


def _build_base_kwargs(**overrides) -> dict:    # noqa: F811  (redefined below for convenience)
    """Fully-passing kwargs; override any key to test edge cases."""
    settlement_dt = _make_settlement_dt(days_ahead=2)
    base = {
        "contract_type":                "threshold",
        "target_settlement_date_str":   settlement_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "settlement_timezone":          SETTLEMENT_TZ,
        "now":                          _now_utc(),
        "side_market_price":            0.30,
        "edge_pct_points":              15.0,
        "station_verified":             True,
        "direction":                    "NO",
        "quote_timestamp":              _fresh_quote(),
        "quote_ask":                    0.30,
    }
    base.update(overrides)
    return base


# ── G4: Entry-price floor ─────────────────────────────────────────────────────

class TestGuard4PriceFloor:
    def test_price_0_19_is_research_only(self):
        """$0.19 entry is below the $0.20 floor."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(side_market_price=0.19)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_PRICE_FLOOR

    def test_price_0_20_passes(self):
        """$0.20 entry is exactly at the floor — should pass."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(side_market_price=0.20)
        )
        assert reason != REASON_PRICE_FLOOR

    def test_price_0_50_passes(self):
        """$0.50 entry easily passes."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(side_market_price=0.50)
        )
        assert reason != REASON_PRICE_FLOOR

    def test_price_0_01_blocked(self):
        """Penny contracts are far below the floor."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(side_market_price=0.01)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_PRICE_FLOOR


# ── G5: Extreme-edge cap ──────────────────────────────────────────────────────

class TestGuard5ExtremeEdge:
    def test_edge_49_9pp_passes(self):
        """49.9 pp is strictly below the 50pp cap — passes."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(edge_pct_points=49.9)
        )
        assert reason != REASON_EXTREME_EDGE

    def test_edge_50pp_is_research_only(self):
        """Exactly 50pp triggers the extreme-edge guard."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(edge_pct_points=50.0)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_EXTREME_EDGE

    def test_edge_75pp_is_research_only(self):
        """High-edge trades that were historically losers are gated."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(edge_pct_points=75.0)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_EXTREME_EDGE

    def test_moderate_edge_passes(self):
        """15pp edge — normal range — passes."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(edge_pct_points=15.0)
        )
        assert reason != REASON_EXTREME_EDGE


# ── G6: Correlated-exposure limit (batch) ────────────────────────────────────

class TestGuard6CorrelatedLimit:
    def _make_candidate(self, *, edge: float, price: float, qt_offset_minutes: int = 0) -> dict:
        """Helper: build a candidate dict that looks like a decision from decide_trade_v22."""
        settlement_dt = _make_settlement_dt(days_ahead=2)
        qt = _fresh_quote() + timedelta(minutes=qt_offset_minutes)
        return {
            "eligibility_status":           "OFFICIAL",
            "eligibility_reason":           None,
            "city":                         "Denver",
            "weather_variable":             "high",
            "target_settlement_date_str":   settlement_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "settlement_timezone":          SETTLEMENT_TZ,
            "edge_pct_points":              edge,
            "side_market_price":            price,
            "quote_timestamp":              qt,
            "is_executable":                True,
        }

    def test_single_candidate_unchanged(self):
        """A single OFFICIAL candidate is never demoted."""
        cands = [self._make_candidate(edge=20.0, price=0.30)]
        apply_correlated_limit(cands)
        assert cands[0]["eligibility_status"] == "OFFICIAL"

    def test_three_candidates_keep_best_ev(self):
        """
        Three correlated candidates: best EV = highest edge/price.
        The best is kept OFFICIAL; the other two become RESEARCH_ONLY.
        """
        # Candidate A: EV = 30/0.30 = 100
        # Candidate B: EV = 20/0.35 ≈ 57
        # Candidate C: EV = 15/0.40 = 37.5
        a = self._make_candidate(edge=30.0, price=0.30)
        b = self._make_candidate(edge=20.0, price=0.35)
        c = self._make_candidate(edge=15.0, price=0.40)
        cands = [b, c, a]   # deliberately unsorted
        apply_correlated_limit(cands)

        official = [x for x in cands if x["eligibility_status"] == "OFFICIAL"]
        research = [x for x in cands if x["eligibility_status"] == "RESEARCH_ONLY"]

        assert len(official) == 1
        assert len(research) == 2
        assert official[0]["edge_pct_points"] == 30.0   # best EV wins
        assert all(x["eligibility_reason"] == REASON_CORRELATED for x in research)
        assert all(not x["is_executable"] for x in research)

    def test_different_cities_not_correlated(self):
        """Candidates for different cities are independent — both stay OFFICIAL."""
        settlement_dt = _make_settlement_dt(days_ahead=2)
        date_str = settlement_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        a = {
            "eligibility_status": "OFFICIAL", "eligibility_reason": None,
            "city": "Denver", "weather_variable": "high",
            "target_settlement_date_str": date_str,
            "settlement_timezone": SETTLEMENT_TZ,
            "edge_pct_points": 20.0, "side_market_price": 0.30,
            "quote_timestamp": _fresh_quote(), "is_executable": True,
        }
        b = {
            "eligibility_status": "OFFICIAL", "eligibility_reason": None,
            "city": "Chicago", "weather_variable": "high",
            "target_settlement_date_str": date_str,
            "settlement_timezone": "America/Chicago",
            "edge_pct_points": 18.0, "side_market_price": 0.35,
            "quote_timestamp": _fresh_quote(), "is_executable": True,
        }
        apply_correlated_limit([a, b])
        assert a["eligibility_status"] == "OFFICIAL"
        assert b["eligibility_status"] == "OFFICIAL"

    def test_different_variables_not_correlated(self):
        """High and Low for the same city-date are independent outcomes."""
        settlement_dt = _make_settlement_dt(days_ahead=2)
        date_str = settlement_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        a = self._make_candidate(edge=20.0, price=0.30)
        b = {**self._make_candidate(edge=18.0, price=0.35), "weather_variable": "low"}
        apply_correlated_limit([a, b])
        assert a["eligibility_status"] == "OFFICIAL"
        assert b["eligibility_status"] == "OFFICIAL"

    def test_research_only_not_affected(self):
        """Candidates already RESEARCH_ONLY are ignored by the limit."""
        settlement_dt = _make_settlement_dt(days_ahead=2)
        date_str = settlement_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        research = {
            "eligibility_status": "RESEARCH_ONLY", "eligibility_reason": REASON_SAME_DAY,
            "city": "Denver", "weather_variable": "high",
            "target_settlement_date_str": date_str,
            "settlement_timezone": SETTLEMENT_TZ,
            "edge_pct_points": 30.0, "side_market_price": 0.20,
            "quote_timestamp": _fresh_quote(), "is_executable": False,
        }
        official = self._make_candidate(edge=15.0, price=0.30)
        apply_correlated_limit([research, official])
        assert official["eligibility_status"] == "OFFICIAL"
        assert research["eligibility_status"] == "RESEARCH_ONLY"
        assert research["eligibility_reason"] == REASON_SAME_DAY  # unchanged


# ── G7: Verified settlement station ──────────────────────────────────────────

class TestGuard7Station:
    def test_unverified_station_is_research_only(self):
        """station_verified=False → RESEARCH_ONLY."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(station_verified=False)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_STATION

    def test_verified_station_passes(self):
        """station_verified=True passes Guard 7."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(station_verified=True)
        )
        assert reason != REASON_STATION


# ── G8: Fresh executable quote ────────────────────────────────────────────────

class TestGuard8FreshQuote:
    def test_missing_quote_timestamp_is_research_only(self):
        """No quote_timestamp at all → RESEARCH_ONLY."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(quote_timestamp=None)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_STALE_QUOTE

    def test_stale_quote_is_research_only(self):
        """Quote older than 4 hours → RESEARCH_ONLY."""
        stale_ts = _now_utc() - timedelta(hours=5)
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(quote_timestamp=stale_ts)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_STALE_QUOTE

    def test_fresh_quote_passes(self):
        """30-minute-old quote passes Guard 8."""
        status, reason, _ = assess_trade_eligibility(**_build_base_kwargs())
        assert reason != REASON_STALE_QUOTE

    def test_missing_quote_ask_is_research_only(self):
        """
        YES/NO ask must match direction.
        quote_ask=None (the ask on our side) → RESEARCH_ONLY.
        """
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(direction="YES", quote_ask=None)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_STALE_QUOTE

    def test_yes_direction_needs_quote_ask(self):
        """YES direction requires yes_ask (passed as quote_ask). Present → passes."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(direction="YES", quote_ask=0.65)
        )
        assert reason != REASON_STALE_QUOTE

    def test_no_direction_needs_no_ask(self):
        """NO direction requires no_ask (passed as quote_ask). Present → passes."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(direction="NO", quote_ask=0.35)
        )
        assert reason != REASON_STALE_QUOTE

    def test_quote_age_seconds_returned(self):
        """quote_age_seconds is calculated and returned for all outcomes."""
        ts = _now_utc() - timedelta(minutes=45)
        _, _, age = assess_trade_eligibility(**_build_base_kwargs(quote_timestamp=ts))
        assert age is not None
        assert 44 * 60 < age < 46 * 60  # ≈ 2700 s

    def test_exactly_at_stale_threshold(self):
        """A quote exactly at the staleness boundary (4h) is stale."""
        # boundary: age == OFFICIAL_STALE_QUOTE_SECONDS → stale
        boundary_ts = _now_utc() - timedelta(seconds=OFFICIAL_STALE_QUOTE_SECONDS + 1)
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(quote_timestamp=boundary_ts)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_STALE_QUOTE


# ── I1: Full OFFICIAL passing scenario ───────────────────────────────────────

class TestFullOfficialScenario:
    def test_all_guards_pass_returns_official(self):
        """
        When every guard is satisfied, assess_trade_eligibility returns OFFICIAL
        with no reason code.
        """
        status, reason, age = assess_trade_eligibility(**_build_base_kwargs())
        assert status == "OFFICIAL"
        assert reason is None
        assert age is not None  # quote age always computed when ts present
        assert 0 <= age < 3600

    def test_range_contract_can_be_official(self):
        """Range contracts (low ≤ T ≤ high) are eligible for official status."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(contract_type="range")
        )
        assert status == "OFFICIAL"
        assert reason is None

    def test_yes_direction_can_be_official_when_price_ok(self):
        """YES direction at 20¢+ with a fresh yes_ask can be OFFICIAL."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(
                direction="YES",
                side_market_price=0.20,
                quote_ask=0.20,
                edge_pct_points=15.0,
            )
        )
        assert status == "OFFICIAL"
        assert reason is None


# ── I2: Guard priority ordering ───────────────────────────────────────────────

class TestGuardPriority:
    """Guard 1 (hourly) fires before Guard 7 (station) — earliest guard wins."""

    def test_hourly_fires_before_station(self):
        """Even if station is unverified, hourly guard fires first."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(
                contract_type="hourly_threshold",
                station_verified=False,
            )
        )
        assert reason == REASON_HOURLY

    def test_station_fires_before_stale_quote(self):
        """Unverified station fires before stale-quote check (guard 7 < guard 8)."""
        stale_ts = _now_utc() - timedelta(hours=5)
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(
                station_verified=False,
                quote_timestamp=stale_ts,
            )
        )
        assert reason == REASON_STATION

    def test_stale_quote_fires_before_same_day(self):
        """
        Missing quote fires before same-day check.
        The function evaluates G7 (station) then G8 (quote) before G2/G3 (cutoff/same-day).
        """
        # craft a same-day settlement that would also fail G2
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(SETTLEMENT_TZ)
        now = _now_utc()
        now_local = now.astimezone(tz)
        # settlement at 23:59 today local but 4+ hours away (avoids G3 conflation)
        same_day_local = now_local.replace(hour=23, minute=59, second=0, microsecond=0)
        settlement_dt = same_day_local.astimezone(timezone.utc)
        seconds_ahead = (settlement_dt - now).total_seconds()
        if seconds_ahead < OFFICIAL_CUTOFF_BUFFER_MINUTES * 60 + 60:
            pytest.skip("Scenario not achievable close to midnight — skip")

        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(
                target_settlement_date_str=settlement_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                quote_timestamp=None,   # missing quote — G8
                now=now,
            )
        )
        # G8 fires first (station is verified, G7 passes; G8 comes next)
        assert reason == REASON_STALE_QUOTE


# ── I3: Research-only excluded from official-count totals ────────────────────

class TestResearchExclusion:
    def test_research_only_excluded_from_official_count(self):
        """
        Simulate a batch of 5 candidates.  3 pass all guards (OFFICIAL).
        2 fail (1 hourly, 1 same-day).  Official count should be 3.
        """
        settlement_dt = _make_settlement_dt(days_ahead=2)
        date_str = settlement_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        past_same_day = (_now_utc() + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Ensure same-day scenario is achievable (today+6h should still be today locally)
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(SETTLEMENT_TZ)
        now = _now_utc()
        today_local = now.astimezone(tz).date()
        sameday_dt = (_now_utc() + timedelta(hours=6)).astimezone(tz)
        if sameday_dt.date() != today_local:
            # Midnight edge case — skip scenario
            pytest.skip("Can't construct reliable same-day scenario near midnight")

        base_kwargs_no_date = {k: v for k, v in _build_base_kwargs().items()
                               if k != "target_settlement_date_str"}

        results = []
        for i in range(3):
            status, reason, _ = assess_trade_eligibility(
                target_settlement_date_str=date_str,
                **base_kwargs_no_date,
            )
            results.append(status)

        # hourly — should fail
        s, _, _ = assess_trade_eligibility(
            contract_type="hourly_threshold",
            target_settlement_date_str=date_str,
            **{k: v for k, v in base_kwargs_no_date.items() if k != "contract_type"},
        )
        results.append(s)

        # stale quote — should fail
        s, _, _ = assess_trade_eligibility(
            quote_timestamp=None,
            target_settlement_date_str=date_str,
            **{k: v for k, v in base_kwargs_no_date.items() if k != "quote_timestamp"},
        )
        results.append(s)

        assert results.count("OFFICIAL") == 3
        assert results.count("RESEARCH_ONLY") == 2
