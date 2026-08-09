"""
tests/test_bet_watch.py
=======================
Tests for the Bet Watch read-only decision-support layer.

Covers the nine requirements from the specification:
  1. Bet Watch does not mutate paper trades.
  2. Bet Watch does not alter Forward Test B eligibility.
  3. PRELIMINARY candidates cannot become OFFICIAL merely because Bet Watch recommends them.
  4. Stale quotes are clearly labeled.
  5. Missing prices cannot generate a BUY/YES/NO recommendation.
  6. Ranking is deterministic for identical inputs.
  7. FTB status accurately reflects existing eligibility logic.
  8. The API and UI agree on the best opportunity.
  9. No recommendation is fabricated when no actionable candidates exist.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── import the helpers under test directly ──────────────────────────────────
from app.routers.bet_watch import (
    _AVOID_AGE_HOURS,
    _FTB_STRATEGY,
    _MIN_INTERESTING_EDGE_PP,
    _NEAR_OFFICIAL_STALE_SECS,
    _STATUS_ORDER,
    _build_summary,
    _changed_since_creation,
    _current_quote_age,
    _extract_forecast,
    _extract_sigma,
    _ftb_status_text,
    _parse_ticker,
    _recommendation_text,
    _row_to_candidate,
    _score,
    _watch_status,
    _what_to_watch,
    _why_this_bet,
    get_bet_watch,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_row(**kwargs) -> MagicMock:
    """
    Create a MagicMock that looks like a PaperTrade row.
    Defaults represent a healthy, OFFICIAL-eligible trade.
    """
    now = _now()
    defaults = dict(
        id=1,
        created_at=now - timedelta(minutes=5),
        market_ticker="KXLOWTNYC-26AUG10-T72",
        event_ticker="KXLOWTNYC-26AUG10",
        city="New York City",
        weather_variable="low",
        contract_type="threshold",
        target_settlement_date="2026-08-11T19:00:00Z",
        strategy_version=_FTB_STRATEGY,
        direction="YES",
        ec_yes_probability=0.70,
        ec_side_probability=0.70,
        market_yes_probability=0.50,
        side_market_price=0.50,
        price_source="YES_ASK",
        edge_pct_points=20.0,
        confidence_score=0.85,
        confidence_label="Very High",
        stake=10.0,
        quantity=20.0,
        status="OPEN",
        eligibility_status="OFFICIAL",
        eligibility_reason=None,
        quote_age_seconds=45.0,
        quote_timestamp=now - timedelta(seconds=45),
        quote_bid=0.48,
        quote_ask=0.50,
        est_available_qty=500.0,
        is_executable=True,
        station_verified=True,
        station_lat=40.78,
        station_lon=-73.97,
        market_close_timestamp=now + timedelta(hours=3),
        minutes_to_market_close=180.0,
        decision_timestamp=now - timedelta(minutes=5),
        decision_explanation=(
            "[v2.3] Open-Meteo low: 68.5°F for New York City. "
            "σ = 5.0°F (v1 table fallback). "
            "P(T ≥ 72°F) = 70.0%. Kalshi: 50.0% "
            "(EdgeCast v2.3 − Market: +20.0pp)."
        ),
        warnings=None,
        quality_flags=None,
        sigma_used=5.0,
        bias_correction=0.0,
        fallback_level="fixed_table",
        calibration_adj=None,
        settlement_timezone="America/New_York",
        comparison_snapshot_id=None,
        collection_batch_id="batch-123",
        expected_settlement_timestamp=None,
    )
    defaults.update(kwargs)
    row = MagicMock()
    for k, v in defaults.items():
        setattr(row, k, v)
    return row


# ═══════════════════════════════════════════════════════════════════════════
# 1 & 2 — Bet Watch does NOT mutate paper_trades or FTB eligibility
# ═══════════════════════════════════════════════════════════════════════════

class TestNoMutation:
    """Requirements 1 & 2: read-only guarantee."""

    @pytest.mark.asyncio
    async def test_endpoint_never_calls_db_write(self):
        """get_bet_watch() must never call session.add, execute with DML, or commit."""
        row = _make_row()
        db = AsyncMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [row]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        db.execute = AsyncMock(return_value=execute_result)

        user = {"sub": "test_user"}
        result = await get_bet_watch(db=db, _user=user)

        # Confirm result arrived
        assert "candidates" in result

        # No write operations permitted
        db.add.assert_not_called()
        db.commit.assert_not_called()
        db.flush.assert_not_called()
        db.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_trading_state_modified_always_false(self):
        """Response must always carry trading_state_modified=False."""
        db = AsyncMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        db.execute = AsyncMock(return_value=execute_result)

        result = await get_bet_watch(db=db, _user={"sub": "u"})
        assert result["trading_state_modified"] is False

    @pytest.mark.asyncio
    async def test_ftb_untouched_always_true(self):
        """Response must always carry ftb_untouched=True."""
        db = AsyncMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        db.execute = AsyncMock(return_value=execute_result)

        result = await get_bet_watch(db=db, _user={"sub": "u"})
        assert result["ftb_untouched"] is True

    def test_row_to_candidate_does_not_mutate_row(self):
        """_row_to_candidate() must not alter any attribute on the input row."""
        row = _make_row()
        original_status = row.eligibility_status
        original_reason = row.eligibility_reason
        original_edge = row.edge_pct_points

        _row_to_candidate(row, age_now=50.0)

        assert row.eligibility_status == original_status
        assert row.eligibility_reason == original_reason
        assert row.edge_pct_points == original_edge


# ═══════════════════════════════════════════════════════════════════════════
# 3 — PRELIMINARY cannot become OFFICIAL
# ═══════════════════════════════════════════════════════════════════════════

class TestPreliminaryCannotBecomeOfficial:
    """Requirement 3."""

    def test_preliminary_watch_status_does_not_change_eligibility_status(self):
        row = _make_row(
            eligibility_status="RESEARCH_ONLY",
            eligibility_reason="settlement_station_unverified",
        )
        age_now = 60.0
        status = _watch_status(row, age_now)
        # Bet Watch may call it WATCHING — but the underlying eligibility_status is unchanged
        assert status != "OFFICIAL-ELIGIBLE"
        assert row.eligibility_status == "RESEARCH_ONLY"  # unchanged

    def test_candidate_dict_ftb_eligible_false_for_research_only(self):
        row = _make_row(
            eligibility_status="RESEARCH_ONLY",
            eligibility_reason="missing_or_stale_executable_quote",
            quote_timestamp=_now() - timedelta(seconds=400),
        )
        cand = _row_to_candidate(row, age_now=400.0)
        assert cand["ftb_eligible"] is False

    @pytest.mark.asyncio
    async def test_preliminary_recommendation_does_not_say_official(self):
        """The recommendation text for a PRELIMINARY candidate must not say 'OFFICIAL'
        in a way that implies it qualifies."""
        row = _make_row(
            eligibility_status="RESEARCH_ONLY",
            eligibility_reason="settlement_station_unverified",
            is_executable=True,
            side_market_price=0.40,
            edge_pct_points=15.0,
        )
        db = AsyncMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [row]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        db.execute = AsyncMock(return_value=execute_result)

        result = await get_bet_watch(db=db, _user={"sub": "u"})
        # recommendation must not claim OFFICIAL status
        rec = result.get("recommendation", "")
        assert "passes all Forward Test B eligibility guards" not in rec


# ═══════════════════════════════════════════════════════════════════════════
# 4 — Stale quotes are clearly labeled
# ═══════════════════════════════════════════════════════════════════════════

class TestStaleLabeledCorrectly:
    """Requirement 4."""

    def test_stale_quote_watch_status(self):
        """A quote older than AVOID_AGE_HOURS should become AVOID / STALE."""
        row = _make_row(
            eligibility_status="RESEARCH_ONLY",
            eligibility_reason="missing_or_stale_executable_quote",
        )
        old_age = (_AVOID_AGE_HOURS + 0.5) * 3600
        status = _watch_status(row, age_now=old_age)
        assert status == "AVOID / STALE"

    def test_freshly_stale_quote_is_near_official(self):
        """A quote that just crossed the 300 s FTB threshold stays NEAR OFFICIAL
        as long as it's younger than NEAR_OFFICIAL_STALE_SECS."""
        row = _make_row(
            eligibility_status="RESEARCH_ONLY",
            eligibility_reason="missing_or_stale_executable_quote",
        )
        status = _watch_status(row, age_now=350.0)   # 5 min 50 s
        assert status == "NEAR OFFICIAL"

    def test_data_freshness_label_stale(self):
        row = _make_row()
        age_now = 25 * 60   # 25 minutes
        cand = _row_to_candidate(row, age_now=float(age_now))
        assert "stale" in cand["data_freshness"].lower()

    def test_data_freshness_label_unknown_when_no_timestamp(self):
        row = _make_row(quote_timestamp=None)
        cand = _row_to_candidate(row, age_now=None)
        assert cand["data_freshness"] == "UNKNOWN"

    def test_ftb_status_text_includes_age_for_stale_quote(self):
        row = _make_row(
            eligibility_status="RESEARCH_ONLY",
            eligibility_reason="missing_or_stale_executable_quote",
        )
        text = _ftb_status_text(row, age_now=11 * 60)
        assert "11" in text or "minute" in text.lower()

    def test_changed_since_creation_notes_freshness_drift(self):
        """If a quote was fresh at creation but is now stale, flag it."""
        row = _make_row(
            quote_age_seconds=45.0,   # was fresh when evaluated
        )
        # Now >300 s old
        changes = _changed_since_creation(row, age_now=400.0)
        any_drift = any("stale" in c.lower() or "aged" in c.lower() for c in changes)
        assert any_drift


# ═══════════════════════════════════════════════════════════════════════════
# 5 — Missing price cannot generate a YES/NO recommendation
# ═══════════════════════════════════════════════════════════════════════════

class TestMissingPriceNoRecommendation:
    """Requirement 5."""

    @pytest.mark.asyncio
    async def test_none_price_row_does_not_become_best(self):
        """A row with side_market_price=None must not surface as best_opportunity."""
        row = _make_row(
            side_market_price=None,
            quote_bid=None,
            quote_ask=None,
            eligibility_status="RESEARCH_ONLY",
            eligibility_reason="missing_or_stale_executable_quote",
        )
        db = AsyncMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [row]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        db.execute = AsyncMock(return_value=execute_result)

        result = await get_bet_watch(db=db, _user={"sub": "u"})
        assert result["best_opportunity"] is None

    @pytest.mark.asyncio
    async def test_no_recommendation_when_only_penny_markets(self):
        """Penny-price rows must not generate a confident BUY recommendation."""
        row = _make_row(
            side_market_price=0.01,
            eligibility_status="RESEARCH_ONLY",
            eligibility_reason="v2_excluded",
            is_executable=False,
        )
        db = AsyncMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [row]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        db.execute = AsyncMock(return_value=execute_result)

        result = await get_bet_watch(db=db, _user={"sub": "u"})
        rec = result["recommendation"]
        assert rec == "EdgeCast does not see a bet worth taking right now."

    def test_candidate_dict_carries_none_price_faithfully(self):
        """None price must be preserved in the output — not substituted."""
        row = _make_row(side_market_price=None)
        cand = _row_to_candidate(row, age_now=50.0)
        assert cand["kalshi_price"] is None

    def test_recommendation_text_best_none_returns_no_bet(self):
        rec = _recommendation_text(None, [])
        assert "does not see a bet worth taking" in rec


# ═══════════════════════════════════════════════════════════════════════════
# 6 — Ranking is deterministic for identical inputs
# ═══════════════════════════════════════════════════════════════════════════

class TestRankingDeterministic:
    """Requirement 6."""

    def test_score_identical_for_same_row(self):
        row = _make_row()
        age_now = 120.0
        s = _watch_status(row, age_now)
        score1 = _score(row, age_now, s)
        score2 = _score(row, age_now, s)
        assert score1 == score2

    def test_sort_order_stable_across_calls(self):
        now = _now()
        rows = [
            _make_row(
                market_ticker="KXTESTC-26AUG10-T80",
                edge_pct_points=15.0,
                eligibility_status="OFFICIAL",
                quote_timestamp=now - timedelta(seconds=30),
            ),
            _make_row(
                market_ticker="KXTESTB-26AUG10-T75",
                edge_pct_points=12.0,
                eligibility_status="RESEARCH_ONLY",
                eligibility_reason="correlated_outcome_limit",
                quote_timestamp=now - timedelta(seconds=30),
            ),
            _make_row(
                market_ticker="KXTESTA-26AUG10-T70",
                edge_pct_points=8.0,
                eligibility_status="RESEARCH_ONLY",
                eligibility_reason="settlement_station_unverified",
                quote_timestamp=now - timedelta(seconds=30),
            ),
        ]

        def _rank(rs):
            candidates = []
            for r in rs:
                age = _current_quote_age(r)
                cand = _row_to_candidate(r, age)
                candidates.append(cand)
            candidates.sort(
                key=lambda c: (_STATUS_ORDER.get(c["watch_status"], 9), -c.get("_score", 0))
            )
            return [c["ticker"] for c in candidates]

        # Patch _row_to_candidate to preserve _score for this test
        with patch(
            "app.routers.bet_watch._row_to_candidate",
            side_effect=lambda row, age_now: {
                **_row_to_candidate(row, age_now),
                "_score": _score(row, age_now, _watch_status(row, age_now)),
            },
        ):
            order1 = _rank(rows)
            order2 = _rank(rows)
        assert order1 == order2

    def test_higher_edge_beats_lower_edge_same_status(self):
        now = _now()
        age = 60.0
        row_high = _make_row(edge_pct_points=25.0, eligibility_status="OFFICIAL",
                             quote_timestamp=now - timedelta(seconds=60))
        row_low  = _make_row(edge_pct_points=10.0, eligibility_status="OFFICIAL",
                             quote_timestamp=now - timedelta(seconds=60))

        s_high = _watch_status(row_high, age)
        s_low  = _watch_status(row_low,  age)
        score_high = _score(row_high, age, s_high)
        score_low  = _score(row_low,  age, s_low)
        assert score_high > score_low


# ═══════════════════════════════════════════════════════════════════════════
# 7 — FTB status accurately reflects existing eligibility logic
# ═══════════════════════════════════════════════════════════════════════════

class TestFtbStatusAccurate:
    """Requirement 7."""

    @pytest.mark.parametrize("reason,expected_snippet", [
        ("hourly_temperature_not_approved", "Hourly"),
        ("settlement_station_unverified",   "station"),
        ("missing_or_stale_executable_quote", "5 minutes"),
        ("cutoff_unverified_or_too_close",  "120 minutes"),
        ("same_day_not_approved",           "same-day"),
        ("entry_price_below_official_floor","$0.20"),
        ("extreme_edge_requires_validation","50 pp"),
        ("correlated_outcome_limit",        "correlated"),
        ("v2_excluded",                     "$0.01"),
    ])
    def test_reason_surfaces_in_ftb_status_text(self, reason, expected_snippet):
        row = _make_row(eligibility_status="RESEARCH_ONLY", eligibility_reason=reason)
        text = _ftb_status_text(row, age_now=60.0)
        assert expected_snippet.lower() in text.lower(), (
            f"Expected '{expected_snippet}' in FTB text for reason '{reason}', got: {text}"
        )

    def test_official_ftb_status_text(self):
        row = _make_row(eligibility_status="OFFICIAL", eligibility_reason=None)
        text = _ftb_status_text(row, age_now=30.0)
        assert "OFFICIAL" in text
        assert "passes" in text.lower()

    def test_official_row_has_ftb_eligible_true(self):
        row = _make_row(eligibility_status="OFFICIAL", eligibility_reason=None)
        cand = _row_to_candidate(row, age_now=30.0)
        assert cand["ftb_eligible"] is True

    def test_research_only_row_has_ftb_eligible_false(self):
        row = _make_row(eligibility_status="RESEARCH_ONLY",
                        eligibility_reason="settlement_station_unverified")
        cand = _row_to_candidate(row, age_now=30.0)
        assert cand["ftb_eligible"] is False

    def test_failed_ftb_guards_populated_for_research_only(self):
        row = _make_row(eligibility_status="RESEARCH_ONLY",
                        eligibility_reason="missing_or_stale_executable_quote")
        cand = _row_to_candidate(row, age_now=400.0)
        assert len(cand["failed_ftb_guards"]) > 0
        assert "stale" in cand["failed_ftb_guards"][0].lower()

    def test_official_row_has_no_failed_guards(self):
        row = _make_row(eligibility_status="OFFICIAL", eligibility_reason=None)
        cand = _row_to_candidate(row, age_now=30.0)
        assert cand["failed_ftb_guards"] == []


# ═══════════════════════════════════════════════════════════════════════════
# 8 — API and UI agree on best opportunity
# ═══════════════════════════════════════════════════════════════════════════

class TestApiUiAgreement:
    """
    Requirement 8: the best_opportunity field must equal the first entry in
    candidates when that entry is not AVOID/STALE and has sufficient edge.
    (The UI reads best_opportunity to render the top card; it must match
    what candidates[0] says.)
    """

    @pytest.mark.asyncio
    async def test_best_opportunity_matches_candidates_rank_1(self):
        row = _make_row(
            eligibility_status="OFFICIAL",
            side_market_price=0.50,
            edge_pct_points=20.0,
            is_executable=True,
        )
        db = AsyncMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [row]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        db.execute = AsyncMock(return_value=execute_result)

        result = await get_bet_watch(db=db, _user={"sub": "u"})
        best = result["best_opportunity"]
        top = result["candidates"][0] if result["candidates"] else None

        assert best is not None
        assert top is not None
        assert best["ticker"] == top["ticker"]
        assert best["rank"] == 1

    @pytest.mark.asyncio
    async def test_recommendation_text_matches_best_opportunity_city(self):
        row = _make_row(
            city="Dallas",
            eligibility_status="OFFICIAL",
            side_market_price=0.45,
            edge_pct_points=15.0,
            is_executable=True,
        )
        db = AsyncMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [row]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        db.execute = AsyncMock(return_value=execute_result)

        result = await get_bet_watch(db=db, _user={"sub": "u"})
        assert "Dallas" in result["recommendation"]


# ═══════════════════════════════════════════════════════════════════════════
# 9 — No recommendation fabricated when no actionable candidates
# ═══════════════════════════════════════════════════════════════════════════

class TestNoFabricationWhenEmpty:
    """Requirement 9."""

    @pytest.mark.asyncio
    async def test_empty_db_returns_no_bet_message(self):
        db = AsyncMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        db.execute = AsyncMock(return_value=execute_result)

        result = await get_bet_watch(db=db, _user={"sub": "u"})
        assert result["best_opportunity"] is None
        assert "does not see a bet" in result["recommendation"]

    @pytest.mark.asyncio
    async def test_all_avoid_stale_returns_no_bet(self):
        """When all candidates are AVOID/STALE no best_opportunity is set."""
        row = _make_row(
            side_market_price=0.01,
            eligibility_status="RESEARCH_ONLY",
            eligibility_reason="v2_excluded",
            is_executable=False,
        )
        db = AsyncMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [row]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        db.execute = AsyncMock(return_value=execute_result)

        result = await get_bet_watch(db=db, _user={"sub": "u"})
        assert result["best_opportunity"] is None
        assert "does not see a bet" in result["recommendation"]

    @pytest.mark.asyncio
    async def test_very_small_edge_not_surfaced_as_best(self):
        """Candidates with edge below MIN_INTERESTING_EDGE_PP must not be best."""
        row = _make_row(
            eligibility_status="OFFICIAL",
            edge_pct_points=_MIN_INTERESTING_EDGE_PP - 0.1,
            side_market_price=0.50,
            is_executable=True,
        )
        db = AsyncMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [row]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        db.execute = AsyncMock(return_value=execute_result)

        result = await get_bet_watch(db=db, _user={"sub": "u"})
        assert result["best_opportunity"] is None

    def test_recommendation_text_no_best_returns_no_bet(self):
        rec = _recommendation_text(None, [])
        assert "does not see a bet worth taking" in rec

    def test_summary_text_no_candidates(self):
        summary = _build_summary([])
        assert "No compelling" in summary["text"]
        assert summary["total_evaluated"] == 0
        assert summary["best_ticker"] is None


# ═══════════════════════════════════════════════════════════════════════════
# Additional unit tests for helpers
# ═══════════════════════════════════════════════════════════════════════════

class TestWatchStatusMapping:
    """Watch status assignment covers all defined reason codes."""

    def test_official_maps_to_official_eligible(self):
        row = _make_row(eligibility_status="OFFICIAL")
        assert _watch_status(row, 30.0) == "OFFICIAL-ELIGIBLE"

    def test_v2_excluded_maps_to_avoid_stale(self):
        row = _make_row(eligibility_status="RESEARCH_ONLY", eligibility_reason="v2_excluded",
                        side_market_price=0.01)
        assert _watch_status(row, 30.0) == "AVOID / STALE"

    def test_penny_price_maps_to_avoid_stale_regardless_of_reason(self):
        row = _make_row(eligibility_status="RESEARCH_ONLY",
                        eligibility_reason="entry_price_below_official_floor",
                        side_market_price=0.005)
        assert _watch_status(row, 30.0) == "AVOID / STALE"

    def test_correlated_maps_to_near_official(self):
        row = _make_row(eligibility_status="RESEARCH_ONLY",
                        eligibility_reason="correlated_outcome_limit",
                        side_market_price=0.60, is_executable=True)
        assert _watch_status(row, 30.0) == "NEAR OFFICIAL"

    def test_station_unverified_maps_to_watching(self):
        row = _make_row(eligibility_status="RESEARCH_ONLY",
                        eligibility_reason="settlement_station_unverified",
                        side_market_price=0.40)
        assert _watch_status(row, 30.0) == "WATCHING"

    def test_hourly_maps_to_preliminary(self):
        row = _make_row(eligibility_status="RESEARCH_ONLY",
                        eligibility_reason="hourly_temperature_not_approved",
                        side_market_price=0.40)
        assert _watch_status(row, 30.0) == "PRELIMINARY"

    def test_age_over_limit_always_avoid_stale(self):
        """Any row whose quote is older than AVOID_AGE_HOURS becomes AVOID/STALE."""
        old_age = (_AVOID_AGE_HOURS + 1.0) * 3600
        row = _make_row(eligibility_status="OFFICIAL")  # even OFFICIAL rows
        assert _watch_status(row, old_age) == "AVOID / STALE"


class TestTickerParser:
    def test_threshold_ticker(self):
        info = _parse_ticker("KXHIGHTCHI-26AUG10-T88")
        assert info["boundary_value"] == pytest.approx(88.0)
        assert "≥" in info["contract_boundary"]

    def test_range_ticker(self):
        info = _parse_ticker("KXLOWTNYC-26AUG10-B71.5")
        assert info["boundary_value"] == pytest.approx(71.5)

    def test_unrecognised_ticker(self):
        info = _parse_ticker("UNKNOWN-TICKER")
        assert info["contract_boundary"] is None

    def test_empty_ticker(self):
        info = _parse_ticker("")
        assert info["contract_boundary"] is None


class TestForecastExtraction:
    def test_extracts_open_meteo_high(self):
        exp = "[v2.3] Open-Meteo high: 99.2°F for Dallas. σ = 4.0°F."
        assert _extract_forecast(exp) == pytest.approx(99.2)

    def test_extracts_open_meteo_low(self):
        exp = "[v2.3] Open-Meteo low: 68.5°F for NYC. σ = 5.0°F."
        assert _extract_forecast(exp) == pytest.approx(68.5)

    def test_extracts_sigma(self):
        exp = "[v2.3] Open-Meteo low: 68.5°F. σ = 5.0°F."
        assert _extract_sigma(exp) == pytest.approx(5.0)

    def test_none_when_no_match(self):
        assert _extract_forecast(None) is None
        assert _extract_forecast("no data here") is None
        assert _extract_sigma(None) is None


class TestSummaryBuilder:
    def test_actionable_count(self):
        cands = [
            {"watch_status": "OFFICIAL-ELIGIBLE", "ticker": "T1", "side": "YES"},
            {"watch_status": "NEAR OFFICIAL",     "ticker": "T2", "side": "NO"},
            {"watch_status": "WATCHING",          "ticker": "T3", "side": "YES"},
            {"watch_status": "AVOID / STALE",     "ticker": "T4", "side": "NO"},
        ]
        s = _build_summary(cands)
        assert s["actionable"] == 2
        assert s["near_official"] == 1
        assert s["watching"] == 1
        assert s["avoid_stale"] == 1
        assert s["total_evaluated"] == 4

    def test_best_ticker_is_first_candidate(self):
        cands = [
            {"watch_status": "OFFICIAL-ELIGIBLE", "ticker": "BEST", "side": "YES"},
            {"watch_status": "NEAR OFFICIAL",     "ticker": "SECOND", "side": "NO"},
        ]
        s = _build_summary(cands)
        assert s["best_ticker"] == "BEST"
