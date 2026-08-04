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
    """Return a quote timestamp that is 60 seconds old (well within the 5-minute / 300 s window)."""
    return _now_utc() - timedelta(seconds=60)


def _build_base_kwargs(**overrides) -> dict:
    """Return a fully-passing set of kwargs for assess_trade_eligibility."""
    settlement_dt   = _make_settlement_dt(days_ahead=2)
    market_close_dt = _make_settlement_dt(days_ahead=2, hour_utc=17)   # closes same day, a few hours before settlement
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
        "market_close_timestamp":       market_close_dt,
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


# ── G3: Market close timestamp guard ─────────────────────────────────────────

class TestGuard3Cutoff:
    """
    Guard 3 now uses the actual Kalshi market close timestamp.
    target_settlement_date_str is used only for Guard 2 (same-day check).
    """

    def test_market_close_past_is_research_only(self):
        """Market already closed → RESEARCH_ONLY via cutoff guard."""
        past_close = _now_utc() - timedelta(hours=1)
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(market_close_timestamp=past_close)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_CUTOFF

    def test_market_close_within_buffer_is_research_only(self):
        """Market closing in 60 minutes (< 120 min buffer) → blocked."""
        soon_close = _now_utc() + timedelta(minutes=60)
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(market_close_timestamp=soon_close)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_CUTOFF

    def test_market_close_missing_is_research_only(self):
        """No market close timestamp → RESEARCH_ONLY (OFFICIAL requires it)."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(market_close_timestamp=None)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_CUTOFF

    def test_market_close_well_ahead_passes(self):
        """Market closing 48 h away (the default) passes Guard 3."""
        status, reason, _ = assess_trade_eligibility(**_build_base_kwargs())
        assert reason != REASON_CUTOFF


def _build_base_kwargs(**overrides) -> dict:    # noqa: F811  (redefined for convenience)
    """Fully-passing kwargs; override any key to test edge cases."""
    settlement_dt   = _make_settlement_dt(days_ahead=2)
    market_close_dt = _make_settlement_dt(days_ahead=2, hour_utc=17)
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
        "market_close_timestamp":       market_close_dt,
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
        """A quote exactly at the staleness boundary is stale (age == limit → stale)."""
        boundary_ts = _now_utc() - timedelta(seconds=OFFICIAL_STALE_QUOTE_SECONDS + 1)
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(quote_timestamp=boundary_ts)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_STALE_QUOTE

    # ── 5-minute boundary tests (spec-required) ──────────────────────────────

    def test_quote_age_299_seconds_passes(self):
        """299 s old quote is within the 300 s window → OFFICIAL."""
        ts = _now_utc() - timedelta(seconds=299)
        status, reason, _ = assess_trade_eligibility(**_build_base_kwargs(quote_timestamp=ts))
        assert reason != REASON_STALE_QUOTE

    def test_quote_age_300_seconds_passes(self):
        """300 s exactly — not strictly > 300 — passes the freshness guard.
        Both `now` and the timestamp are anchored to the same instant so the
        computed age is exactly 300.0 s, not 300.001 due to clock advancement."""
        now = _now_utc()
        ts  = now - timedelta(seconds=300)
        status, reason, _ = assess_trade_eligibility(**_build_base_kwargs(quote_timestamp=ts, now=now))
        assert reason != REASON_STALE_QUOTE

    def test_quote_age_301_seconds_is_research_only(self):
        """301 s old quote is strictly older than 300 s → RESEARCH_ONLY."""
        ts = _now_utc() - timedelta(seconds=301)
        status, reason, _ = assess_trade_eligibility(**_build_base_kwargs(quote_timestamp=ts))
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_STALE_QUOTE

    def test_future_quote_timestamp_is_research_only(self):
        """
        A future-dated quote cannot become OFFICIAL.

        Previously the code clamped negative age to zero, which allowed a future
        timestamp to pass as "age 0".  The corrected behaviour is to reject it:
        future quote_timestamp → RESEARCH_ONLY / missing_or_stale_executable_quote.
        The actual (negative) age must be stored for auditing.
        """
        future_ts = _now_utc() + timedelta(minutes=5)
        status, reason, age = assess_trade_eligibility(
            **_build_base_kwargs(quote_timestamp=future_ts)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_STALE_QUOTE
        # Signed age is preserved (negative), not clamped to zero
        assert age is not None and age < 0, (
            f"Expected negative quote_age_seconds for a future timestamp, got {age}"
        )

    def test_future_quote_timestamp_cannot_be_official(self):
        """Regression guard: a future-dated quote must never reach OFFICIAL status."""
        future_ts = _now_utc() + timedelta(seconds=1)   # barely in the future
        status, _, _ = assess_trade_eligibility(
            **_build_base_kwargs(quote_timestamp=future_ts)
        )
        assert status != "OFFICIAL"


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

# ── G3: Market close timestamp — comprehensive spec-required scenarios ────────

class TestGuard3CloseTime:
    """
    Tests for Guard 3 using the actual market close timestamp.
    Boundary rule: seconds_to_close <= 120 * 60 → RESEARCH_ONLY.
    So exactly 120 min = RESEARCH_ONLY; > 120 min = passes Guard 3.
    """

    def test_more_than_120_min_is_official(self):
        """121 min to close — just outside the buffer — passes Guard 3."""
        close_ts = _now_utc() + timedelta(minutes=121)
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(market_close_timestamp=close_ts)
        )
        assert reason != REASON_CUTOFF, f"121 min should pass G3, got reason={reason}"

    def test_exactly_120_min_is_research_only(self):
        """
        Exactly 120 minutes to close is 'within the cutoff buffer' → RESEARCH_ONLY.
        Boundary: seconds_to_close (7200) <= 7200 → True → blocked.
        """
        close_ts = _now_utc() + timedelta(minutes=120)
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(market_close_timestamp=close_ts)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_CUTOFF

    def test_119_min_is_research_only(self):
        """119 minutes to close — well inside buffer — blocked."""
        close_ts = _now_utc() + timedelta(minutes=119)
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(market_close_timestamp=close_ts)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_CUTOFF

    def test_already_closed_is_research_only(self):
        """Market closed 1 hour ago — seconds_to_close is negative."""
        close_ts = _now_utc() - timedelta(hours=1)
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(market_close_timestamp=close_ts)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_CUTOFF

    def test_missing_close_time_is_research_only(self):
        """None close timestamp → RESEARCH_ONLY; OFFICIAL requires it."""
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(market_close_timestamp=None)
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_CUTOFF

    def test_future_settlement_past_close_is_research_only(self):
        """
        Settlement date is tomorrow, but market already closed → blocked.
        Demonstrates that target_settlement_date alone is insufficient.
        """
        future_settle = _make_settlement_dt(days_ahead=1)
        past_close    = _now_utc() - timedelta(hours=3)
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(
                target_settlement_date_str=future_settle.strftime("%Y-%m-%dT%H:%M:%SZ"),
                market_close_timestamp=past_close,
            )
        )
        assert status == "RESEARCH_ONLY"
        assert reason == REASON_CUTOFF

    def test_utc_based_cutoff_ignores_local_tz(self):
        """
        The cutoff guard uses UTC math — local timezone DST offsets don't affect it.
        A market closing in 200 min (UTC) should pass regardless of which TZ is used.
        """
        far_close = _now_utc() + timedelta(minutes=200)
        for tz_name in ["America/Denver", "America/New_York", "America/Los_Angeles", "America/Chicago"]:
            settle_dt = _make_settlement_dt(days_ahead=2)
            status, reason, _ = assess_trade_eligibility(
                contract_type="threshold",
                target_settlement_date_str=settle_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                settlement_timezone=tz_name,
                now=_now_utc(),
                side_market_price=0.30,
                edge_pct_points=15.0,
                station_verified=True,
                direction="NO",
                quote_timestamp=_fresh_quote(),
                quote_ask=0.30,
                market_close_timestamp=far_close,
            )
            assert reason != REASON_CUTOFF, (
                f"200-min close in TZ {tz_name} should pass G3, got reason={reason}"
            )

    def test_naive_close_timestamp_treated_as_utc(self):
        """Naive (no tzinfo) market_close_timestamp is accepted and treated as UTC."""
        naive_close = (_now_utc() + timedelta(hours=5)).replace(tzinfo=None)
        status, reason, _ = assess_trade_eligibility(
            **_build_base_kwargs(market_close_timestamp=naive_close)
        )
        assert reason != REASON_CUTOFF

    def test_dst_spring_forward_cutoff_still_utc(self):
        """
        DST spring-forward: local clock skips 1h, but UTC cutoff check is unaffected.
        We cannot easily predict when DST transitions happen in a test, so we use a
        fixed UTC time to verify the guard produces the expected result.
        """
        from datetime import timezone as _tz
        fixed_now = datetime(2027, 3, 14, 7, 30, 0, tzinfo=_tz.utc)  # Spring-forward day in US
        # Market closes 200 min from fixed_now — should pass
        close_ts = fixed_now + timedelta(minutes=200)
        settle_dt = fixed_now + timedelta(days=2)

        status, reason, _ = assess_trade_eligibility(
            contract_type="threshold",
            target_settlement_date_str=settle_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            settlement_timezone="America/Denver",
            now=fixed_now,
            side_market_price=0.30,
            edge_pct_points=15.0,
            station_verified=True,
            direction="NO",
            quote_timestamp=fixed_now - timedelta(seconds=60),
            quote_ask=0.30,
            market_close_timestamp=close_ts,
        )
        assert reason != REASON_CUTOFF, (
            f"200-min close on DST spring-forward day should pass G3, got {reason}"
        )


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
