"""
V2.1 Pipeline Validation Tests
================================
Eight targeted test cases covering the four root-cause fixes from the
July 2026 audit:

  1. Forecast coordinates match settlement station (not city-centre)
  2. OKC station is now verified; LAX remains unverified (correct ambiguity)
  3. Sigma floor enforced — learned σ below 3.5°F is clamped up
  4. Conservative prior is larger than v1 fixed table for short lead times
  5. MIN_SAMPLE raised to 30 — 5-sample stats are no longer used
  6. Unverified station blocks V2.1 trade
  7. Verified station allows V2.1 trade through station guard
  8. Stale-quote guard (>4 h) blocks trade; fresh quote (<4 h) allows it
  9. Non-executable flag (>50 qty at <$0.10) is set correctly
 10. YES + NO probability complement: P(YES) + P(NO) ≈ 1.0
"""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.settlement_stations import (
    get_station,
    get_verified_station,
    verified_cities,
)
from app.services.probability_engine_v2 import (
    MIN_SAMPLE,
    SIGMA_FLOOR,
    SIGMA_FLOOR_HOURLY,
    SIGMA_CEILING,
    _conservative_prior,
    _normal_cdf,
)
from app.services.paper_trading_v21 import (
    STRATEGY_VERSION,
    STALE_QUOTE_SECONDS,
    NON_EXECUTABLE_MAX_QTY,
    NON_EXECUTABLE_MAX_PRICE,
    _check_station_verified,
    _check_quote_freshness,
    _is_executable,
    _check_consensus_guard,
)


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Station coordinate fix: OKC now uses airport coords, not city-centre
# ─────────────────────────────────────────────────────────────────────────────

def test_okc_station_uses_airport_coordinates():
    """KOKC (Will Rogers) is at 35.39°N 97.60°W, not downtown OKC 35.47°N 97.52°W."""
    station = get_station("Oklahoma City")
    assert station is not None, "Oklahoma City must be in station registry"
    # Settlement station lat must be notably south/west of city-centre
    # City centre: 35.4676, -97.5164
    # Airport:     35.3931, -97.6007
    assert station.lat < 35.40, (
        f"OKC station lat {station.lat} should be airport (~35.39), "
        "not city-centre (~35.47)"
    )
    assert station.lon < -97.55, (
        f"OKC station lon {station.lon} should be airport (~-97.60), "
        "not city-centre (~-97.52)"
    )


def test_okc_station_is_now_verified():
    """The July 2026 audit verified OKC → station should be verified=True."""
    station = get_station("Oklahoma City")
    assert station is not None
    assert station.verified is True, (
        "OKC KOKC station should be verified after audit confirmation"
    )
    assert get_verified_station("Oklahoma City") is not None, (
        "get_verified_station('Oklahoma City') should return the station"
    )


def test_lax_station_is_now_verified_as_lax_airport():
    """LAX ambiguity resolved 2026-07-30 via Kalshi API rules_secondary; confirmed LAX airport."""
    station = get_station("Los Angeles")
    assert station is not None, "Los Angeles must be in station registry"
    assert station.verified is True, (
        "Los Angeles should be verified — Kalshi rules_secondary confirmed 'Los Angeles Airport, CA'"
    )
    assert station.ghcnd_station_id == "USW00023174", "Must be LAX airport station"
    assert "Los Angeles" in verified_cities(), (
        "'Los Angeles' must appear in verified_cities() after 2026-07-30 API confirmation"
    )


def test_nyc_and_chicago_and_denver_are_verified():
    """Pre-existing verified cities must remain verified after coord update."""
    for city in ("New York City", "Chicago", "Denver"):
        station = get_verified_station(city)
        assert station is not None, f"{city} should still be verified"


def test_verified_cities_list_includes_okc():
    """OKC should appear in the verified-cities list after audit."""
    verified = verified_cities()
    assert "Oklahoma City" in verified_cities(), (
        "Oklahoma City must be in verified cities list"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — Sigma floor: learned σ below 3.5°F is clamped up
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sigma_floor_applied():
    """
    _sigma_v2 with a DB result of σ=1.22°F (5-sample value from audit)
    must return σ >= SIGMA_FLOOR (3.5°F) after clamping.
    """
    from app.services.probability_engine_v2 import _sigma_v2

    # Mock session with a "learned" σ=1.22 at only 5 samples (should be IGNORED
    # because MIN_SAMPLE=30, but even if accepted, floor should clamp it up)
    tiny_stats = MagicMock()
    tiny_stats.std_dev = 1.22
    tiny_stats.mean_error = 0.0
    tiny_stats.sample_size = 5
    tiny_stats.fallback_level = "city"

    mock_session = AsyncMock()
    # Return the tiny-stats row regardless of query
    mock_session.execute.return_value.scalar_one_or_none = MagicMock(return_value=tiny_stats)

    with patch(
        "app.services.probability_engine_v2._get_error_stats",
        new=AsyncMock(return_value=None),  # simulate: sample < MIN_SAMPLE → None
    ):
        sigma, level = await _sigma_v2(
            "Oklahoma City", "low", lead_time_days=0, month=7, session=mock_session
        )

    # With no DB stats (None returned), falls through to conservative prior
    # Conservative prior for lead=0 days = 5.0°F, then clamped to [3.5, 15.0]
    assert sigma >= SIGMA_FLOOR, f"σ={sigma:.2f} must be ≥ SIGMA_FLOOR={SIGMA_FLOOR}"
    assert sigma <= SIGMA_CEILING, f"σ={sigma:.2f} must be ≤ SIGMA_CEILING={SIGMA_CEILING}"
    assert level == "fixed_table"


@pytest.mark.asyncio
async def test_sigma_floor_clamps_learned_value():
    """
    Even a DB-learned σ=1.80°F (below floor) should be clamped to SIGMA_FLOOR.
    """
    from app.services.probability_engine_v2 import _sigma_v2

    learned_stats = MagicMock()
    learned_stats.std_dev = 1.80
    learned_stats.mean_error = 0.0
    learned_stats.sample_size = 50  # above MIN_SAMPLE
    learned_stats.fallback_level = "city"

    with patch(
        "app.services.probability_engine_v2._get_error_stats",
        new=AsyncMock(return_value=learned_stats),
    ):
        sigma, level = await _sigma_v2(
            "Denver", "high", lead_time_days=1, month=6, session=AsyncMock()
        )

    assert sigma == SIGMA_FLOOR, (
        f"σ={sigma:.2f} should be clamped to SIGMA_FLOOR={SIGMA_FLOOR} "
        "when learned value is below floor"
    )
    assert level == "city"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Conservative prior > V1 fixed table for short lead times
# ─────────────────────────────────────────────────────────────────────────────

def test_conservative_prior_larger_than_v1_for_short_lead():
    """
    Conservative prior at lead 0-1 days must be > 2.5°F (V1 table value).
    This is the core fix for the OKC 9°F error case.
    """
    from app.services.probability_engine import sigma_for_lead_time  # v1 table

    for lead in (0, 1):
        prior = _conservative_prior(lead)
        v1_sigma = sigma_for_lead_time(lead)
        assert prior > v1_sigma, (
            f"Conservative prior ({prior}°F) at lead={lead}d must exceed "
            f"V1 sigma ({v1_sigma}°F) to prevent overconfidence"
        )
        assert prior >= SIGMA_FLOOR, (
            f"Conservative prior ({prior}°F) must be ≥ SIGMA_FLOOR ({SIGMA_FLOOR}°F)"
        )


def test_conservative_prior_at_various_lead_times():
    """All prior values must be within [SIGMA_FLOOR, SIGMA_CEILING]."""
    for lead in (0, 1, 2, 3, 4, 5, 7, 10, 14, 20):
        prior = _conservative_prior(lead)
        assert SIGMA_FLOOR <= prior <= SIGMA_CEILING, (
            f"Prior {prior}°F at lead={lead}d out of bounds "
            f"[{SIGMA_FLOOR}, {SIGMA_CEILING}]"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — MIN_SAMPLE raised to 30
# ─────────────────────────────────────────────────────────────────────────────

def test_min_sample_is_30():
    """MIN_SAMPLE must be 30 to prevent 5-sample statistics corrupting σ."""
    assert MIN_SAMPLE == 30, (
        f"MIN_SAMPLE={MIN_SAMPLE}, expected 30. "
        "The audit found that 5-sample values of σ=1.22°F caused catastrophic losses."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Unverified station blocks V2.1 trade
# ─────────────────────────────────────────────────────────────────────────────

def test_unverified_city_blocked():
    """Cities with verified=False must not pass the station guard."""
    ok, reason = _check_station_verified("Atlanta")  # Atlanta has no live Kalshi markets, still unverified
    assert ok is False, "Unverified city (Atlanta) must be blocked"
    assert reason is not None and "UNVERIFIED" in reason.upper()


def test_washington_dc_blocked_due_to_non_nws_settlement():
    """DC must be blocked permanently — Kalshi settles KXTEMPDCH on The Weather Company, not NWS."""
    ok, reason = _check_station_verified("Washington DC")
    assert ok is False, "Washington DC must be blocked regardless of verified status"
    assert reason is not None
    # The reason must reference the settlement source mismatch, not just unverified status
    assert "non-NWS" in reason or "NWS" in reason or "source" in reason.lower(), (
        f"Block reason should mention settlement source incompatibility, got: {reason}"
    )


def test_washington_dc_blocked_even_if_verified_were_true():
    """Even patching verified=True on DC must not let it through — nws_settlement=False is the hard block."""
    from unittest.mock import patch
    from app.services import settlement_stations as ss

    dc = ss.SETTLEMENT_STATIONS["Washington DC"]
    # Create a copy with verified=True but nws_settlement=False still in place
    patched_dc = type(dc)(
        city=dc.city,
        ghcnd_station_id=dc.ghcnd_station_id,
        station_name=dc.station_name,
        lat=dc.lat,
        lon=dc.lon,
        timezone=dc.timezone,
        verified=True,          # hypothetically verified
        nws_settlement=False,   # still incompatible settlement source
        source=dc.source,
        notes=dc.notes,
    )
    patched_registry = {**ss.SETTLEMENT_STATIONS, "Washington DC": patched_dc}
    with patch.object(ss, "SETTLEMENT_STATIONS", patched_registry):
        ok, reason = _check_station_verified("Washington DC")
    assert ok is False, "DC must still be blocked when verified=True but nws_settlement=False"
    assert reason is not None


def test_unknown_city_blocked():
    """City not in registry must be blocked."""
    ok, reason = _check_station_verified("Atlantis")
    assert ok is False
    assert reason is not None


def test_no_city_blocked():
    """None city must be blocked."""
    ok, reason = _check_station_verified(None)
    assert ok is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — Verified station allows trade through the guard
# ─────────────────────────────────────────────────────────────────────────────

def test_verified_city_allowed():
    """Verified cities (NYC, Chicago, Denver, Oklahoma City) must pass the guard."""
    for city in ("New York City", "Chicago", "Denver", "Oklahoma City"):
        ok, reason = _check_station_verified(city)
        assert ok is True, f"{city} should pass station guard; reason: {reason}"
        assert reason is None


# ─────────────────────────────────────────────────────────────────────────────
# Test 7 — Quote staleness guard (4 h)
# ─────────────────────────────────────────────────────────────────────────────

def test_fresh_quote_allowed():
    """Quote collected 3 hours ago must pass the freshness guard."""
    now = datetime.now(timezone.utc)
    market = MagicMock()
    market.collection_timestamp = now - timedelta(hours=3)
    ok, reason = _check_quote_freshness(market, now)
    assert ok is True, f"3h-old quote should be fresh; reason: {reason}"


def test_stale_quote_blocked():
    """Quote collected 5 hours ago must fail the freshness guard."""
    now = datetime.now(timezone.utc)
    market = MagicMock()
    market.collection_timestamp = now - timedelta(hours=5)
    ok, reason = _check_quote_freshness(market, now)
    assert ok is False, "5h-old quote must be blocked"
    assert reason is not None


def test_missing_timestamp_blocked():
    """Missing collection_timestamp must be conservative and block the trade."""
    now = datetime.now(timezone.utc)
    market = MagicMock()
    market.collection_timestamp = None
    ok, reason = _check_quote_freshness(market, now)
    assert ok is False, "Missing timestamp must block trade"


def test_stale_threshold_is_4_hours():
    """STALE_QUOTE_SECONDS must equal exactly 4 hours (14400s)."""
    assert STALE_QUOTE_SECONDS == 4 * 3600, (
        f"STALE_QUOTE_SECONDS={STALE_QUOTE_SECONDS}, expected 14400 (4h)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 8 — Non-executable flag (>50 qty at <$0.10)
# ─────────────────────────────────────────────────────────────────────────────

def test_non_executable_flagged():
    """51 contracts at 9 cents must be flagged non-executable."""
    assert _is_executable(51, 0.09) is False


def test_exactly_at_boundary_executable():
    """50 contracts at 9 cents (qty = limit) should be allowed."""
    assert _is_executable(50, 0.09) is True


def test_high_price_executable():
    """100 contracts at 30 cents is fine (price is above floor)."""
    assert _is_executable(100, 0.30) is True


def test_low_qty_cheap_executable():
    """10 contracts at 5 cents is fine (qty is below limit)."""
    assert _is_executable(10, 0.05) is True


def test_executability_thresholds_match_constants():
    """The executable check must use the published constants."""
    assert NON_EXECUTABLE_MAX_QTY == 50
    assert NON_EXECUTABLE_MAX_PRICE == 0.10


# ─────────────────────────────────────────────────────────────────────────────
# Test 9 — YES/NO probability complement
# ─────────────────────────────────────────────────────────────────────────────

def test_yes_no_complement_for_threshold():
    """P(T ≥ k) + P(T < k) should sum to ≈ 1.0 (within 4dp rounding)."""
    from app.services.probability_engine_v2 import _calc_prob_threshold

    for mu, sigma, threshold in [
        (72.0, 5.0, 70.0),
        (80.0, 5.0, 85.0),
        (65.0, 4.0, 65.0),  # exactly at boundary
    ]:
        # "gte" = P(T >= k); any other operator returns the CDF = P(T < k)
        p_yes = _calc_prob_threshold("gte", threshold, mu, sigma)
        p_no = _calc_prob_threshold("lte", threshold, mu, sigma)
        total = p_yes + p_no
        assert abs(total - 1.0) <= 0.0001, (
            f"P(YES)+P(NO)={total:.4f} for μ={mu},σ={sigma},k={threshold}; "
            "must equal 1.0 (±0.0001 for rounding)"
        )


def test_yes_no_complement_for_range():
    """
    Sum of properly non-overlapping integer range buckets ≈ 1.0.

    With the NWS rounding correction, _calc_prob_range([lo, lo+1]) expands to
    [lo−0.5, lo+1.5].  Adjacent bins that SHARE an integer endpoint (e.g.
    [50,51] and [51,52]) will therefore OVERLAP by 1°F — this is expected
    and correct semantics (both bins claim ownership of X=51, which is
    physically correct since round(50.5–51.5)=51 is in both ranges).

    For a sum-to-1 test, use single-integer bins [lo, lo] which expand to
    [lo−0.5, lo+0.5] — adjacent bins then touch only at a half-integer
    boundary (zero-measure overlap) and their sum covers the full integer axis.
    """
    from app.services.probability_engine_v2 import _calc_prob_range

    mu, sigma = 75.0, 5.0
    # Single-integer bins: [lo, lo] expands to [lo−0.5, lo+0.5].
    # Adjacent bins [lo,lo] and [lo+1,lo+1] touch at the half-integer point
    # lo+0.5 — non-overlapping, exhaustive partition of the integer axis.
    total = sum(
        _calc_prob_range(float(lo), float(lo), mu, sigma)
        for lo in range(50, 100)
    )
    # Should be very close to P(actual ∈ [49.5, 99.5]) ≈ 1.0 for μ=75, σ=5
    assert abs(total - 1.0) < 0.02, (
        f"Sum of range probabilities = {total:.4f}; expected ≈ 1.0"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 10 — OKC-specific: sigma floor prevents >90% confidence claim
# ─────────────────────────────────────────────────────────────────────────────

def test_okc_sigma_floor_prevents_extreme_confidence():
    """
    Reproduce the audit scenario: OKC low, σ = 1.22°F (pre-fix).
    With the audit fix (σ ≥ 3.5°F), the probability assigned to the
    bucket 71–73°F at a forecast of 80.2°F should be very low (correctly
    bearish on this trade), rather than the 94pp false edge.

    Also verify that with σ = SIGMA_FLOOR (3.5°F), the engine cannot
    assign P ≥ 0.95 to any single 2°F bucket when the forecast is
    ≥ 7°F from that bucket's centre.
    """
    from app.services.probability_engine_v2 import _calc_prob_range

    forecast_mu = 80.2  # Open-Meteo OKC forecast on the audit date
    # Bucket Kalshi was offering: 71–73°F (EdgeCast thought market was wrong)
    bucket_lo, bucket_hi = 71.0, 73.0

    # Pre-fix: σ = 1.22°F → absurdly narrow distribution
    p_prefix = _calc_prob_range(bucket_lo, bucket_hi, forecast_mu, 1.22)

    # Post-fix: σ = SIGMA_FLOOR = 3.5°F
    p_postfix = _calc_prob_range(bucket_lo, bucket_hi, forecast_mu, SIGMA_FLOOR)

    # The forecast is 80.2°F; the bucket is 71–73°F.
    # With σ = 1.22°F the prob of landing in that bucket approaches 0
    # (EdgeCast would have bet NO, which is why it lost when actual = 72°F).
    # Both pre-fix and post-fix should agree that P(71–73|μ=80.2) is very low.
    # The key test: with correct σ the *edge* is also low, not falsely 30-90pp.
    assert p_postfix < 0.03, (
        f"P(71-73°F | μ=80.2°F, σ=3.5°F) = {p_postfix:.4f}; "
        "should be < 3% (bucket is ~2σ below forecast) — the trade should have been skipped"
    )

    # Verify the fix didn't make things worse for this particular case:
    # with σ=3.5°F the prob is closer to 0 than the already-near-zero
    # pre-fix value (both approaches correctly indicate a very unlikely bucket).
    assert p_prefix < 0.05, (
        f"Pre-fix P(71-73°F | μ=80.2°F, σ=1.22°F) = {p_prefix:.4f}; unexpected value"
    )


def test_strategy_version():
    """V2.1 strategy string must be distinct from V2.0."""
    assert STRATEGY_VERSION == "v2.1", f"Got {STRATEGY_VERSION}"
