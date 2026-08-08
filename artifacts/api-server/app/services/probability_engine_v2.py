"""
Probability Engine v2 — data-driven Gaussian model.

Differences from v1
-------------------
1. σ comes from learned city-specific forecast-error statistics stored in
   ForecastErrorStats, not a fixed lookup table.  Falls back gracefully to the
   v1 fixed table when n < MIN_SAMPLE.

2. μ is bias-corrected: mean_error from the same stats table is subtracted.
   Correction only applied when n ≥ MIN_SAMPLE to avoid noise.

3. Probability calibration: a per-bucket multiplier from CalibrationAdjustment
   is applied after the Gaussian calculation.  Calibration is only used when
   sample_size ≥ MIN_CALIB_SAMPLE (conservative — avoids over-fitting small
   samples).

4. Does NOT modify probability_engine.py (v1 engine).

Data limitations noted inline:
- "City" is the finest resolution available (no per-station obs data).
- Open-Meteo historical API gives verified observations, NOT archived model
  forecasts.  True backtesting is not possible.
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.probability_engine import (
    AnalysisResult,
    _normal_cdf,
    sigma_for_lead_time,
    market_implied_probability,
    confidence_score,
)
from app.models import CalibrationAdjustment, ForecastErrorStats

logger = logging.getLogger(__name__)

# ── Sigma governance constants ────────────────────────────────────────────────
#
# MIN_SAMPLE (30)
#   A ForecastErrorStats row must have at least this many verified observations
#   before its std_dev is used.  Raised from 5 to 30 because:
#   - With n=5 the sample std_dev is extremely noisy (±40% error typical).
#   - The audit found that 5-sample values of 1.22°F were 3-6× smaller than
#     observed forecast errors (3–10°F), generating illusory "edges" of 90+ pp.
#
# SIGMA_FLOOR (3.5°F daily, 2.0°F hourly)
#   Even with 30+ samples we clamp σ upward to this floor.  Rationale:
#   - NWS settlement stations are often at airports with different microclimates
#     from the Open-Meteo forecast grid point; this adds irreducible location
#     error of 1–3°F on top of model error.
#   - For daily temperature markets a 3.5°F floor means a forecast must be
#     >7°F from a threshold before the model can assign >97.5% confidence.
#
# SIGMA_CEILING (15.0°F)
#   Prevents corrupted or outlier observations from blowing up the distribution.
#   A value above 15°F implies the observations are noisy or the station mapping
#   is wrong; flag for review rather than using.
#
# _CONSERVATIVE_PRIOR_TABLE
#   Used instead of the V1 fixed table when DB has < MIN_SAMPLE observations.
#   Values are 2–3× larger than the V1 table to reflect that:
#   (a) location offsets between forecast grid and settlement station add error,
#   (b) the V1 table was built for mid-range lead times and under-estimates
#       same-day uncertainty in continental cities.
#   Once a city accumulates MIN_SAMPLE observations the learned σ takes over.
#
# Weighting: all observations are currently equally weighted.  Future work can
# add recency weighting or monthly grouping once ≥60 obs/bucket exist.
#
# Outlier handling: observations where |forecast_error| > 3× the running
# std_dev are excluded from ForecastErrorStats aggregation (handled by the
# forecast_verifier service, not here).  Outliers below that threshold remain.
#
# Fallback hierarchy (V2.1 engine):
#   1. city + variable + lead_bucket + month  (n ≥ MIN_SAMPLE)
#   2. city + variable + lead_bucket           (n ≥ MIN_SAMPLE, all months)
#   3. global + variable + lead_bucket         (n ≥ MIN_SAMPLE, all cities)
#   4. _CONSERVATIVE_PRIOR_TABLE               (< MIN_SAMPLE anywhere)
#   σ is then clamped to [SIGMA_FLOOR, SIGMA_CEILING].

MIN_SAMPLE = 30          # was 5; see note above
MIN_CALIB_SAMPLE = 30   # unchanged

SIGMA_FLOOR = 3.5        # °F — daily high/low markets
SIGMA_FLOOR_HOURLY = 2.0 # °F — hourly_threshold markets
SIGMA_CEILING = 15.0     # °F — absolute upper bound

# Conservative prior: used when DB has < MIN_SAMPLE observations.
# Format: (max_lead_time_days, sigma_°F)
_CONSERVATIVE_PRIOR_TABLE: list[tuple[int, float]] = [
    (1,  5.0),   # 0–1 day:   same-day/next-day — more uncertain than NWP suggests
    (3,  6.0),   # 2–3 days
    (7,  7.5),   # 4–7 days
    (14, 9.5),   # 8–14 days
]
_CONSERVATIVE_PRIOR_DEFAULT = 11.0  # > 14 days


def _conservative_prior(lead_time_days: int | None) -> float:
    """Return the conservative-prior σ for the given lead time."""
    ld = lead_time_days if lead_time_days is not None else 99
    for max_days, sigma in _CONSERVATIVE_PRIOR_TABLE:
        if ld <= max_days:
            return sigma
    return _CONSERVATIVE_PRIOR_DEFAULT


# ---------------------------------------------------------------------------
# Extended result dataclass
# ---------------------------------------------------------------------------

@dataclass
class AnalysisResultV2(AnalysisResult):
    """
    Extends v1 AnalysisResult with v2-specific metadata.

    sigma_used         : σ actually used (may differ from v1 table)
    bias_correction    : mean_error subtracted from μ (0.0 if not applied)
    fallback_level     : "city" | "global" | "fixed_table"
    calibration_adj    : multiplier applied to ec_probability (1.0 = no adj)
    raw_ec_probability : ec_probability before calibration adjustment
    """
    sigma_used: float | None = None
    bias_correction: float = 0.0
    fallback_level: str = "fixed_table"
    calibration_adj: float = 1.0
    raw_ec_probability: float | None = None


# ---------------------------------------------------------------------------
# Lead-time bucket helper (mirrors lead_bucket in paper_trading.py)
# ---------------------------------------------------------------------------

def _lead_bucket(days: int | None) -> str:
    if days is None:
        return ">7d"
    if days <= 1:
        return "0-1d"
    if days <= 3:
        return "2-3d"
    if days <= 7:
        return "4-7d"
    return ">7d"


# ---------------------------------------------------------------------------
# V2 helpers — load learned stats from DB
# ---------------------------------------------------------------------------

async def _get_error_stats(
    city: str,
    weather_variable: str,
    lead_time_days: int | None,
    month: int | None,
    session: AsyncSession,
) -> ForecastErrorStats | None:
    """
    Look up ForecastErrorStats for the best matching group.

    Fallback hierarchy:
      1. city + variable + lead_bucket + month   (city, seasonal)
      2. city + variable + lead_bucket            (city, all-season)
      3. any city + variable + lead_bucket        (global, all-season)

    Returns None if no stats exist yet.
    """
    lb = _lead_bucket(lead_time_days)

    # 1. City + variable + lead_bucket + month (seasonal)
    if month is not None:
        q = await session.execute(
            select(ForecastErrorStats).where(
                ForecastErrorStats.city == city,
                ForecastErrorStats.weather_variable == weather_variable,
                ForecastErrorStats.lead_time_bucket == lb,
                ForecastErrorStats.month == month,
                ForecastErrorStats.sample_size >= MIN_SAMPLE,
            ).limit(1)
        )
        row = q.scalar_one_or_none()
        if row:
            return row

    # 2. City + variable + lead_bucket (all-season)
    q = await session.execute(
        select(ForecastErrorStats).where(
            ForecastErrorStats.city == city,
            ForecastErrorStats.weather_variable == weather_variable,
            ForecastErrorStats.lead_time_bucket == lb,
            ForecastErrorStats.month.is_(None),
            ForecastErrorStats.sample_size >= MIN_SAMPLE,
        ).limit(1)
    )
    row = q.scalar_one_or_none()
    if row:
        return row

    # 3. Global fallback — any city, same variable + lead_bucket (all-season)
    q = await session.execute(
        select(ForecastErrorStats).where(
            ForecastErrorStats.weather_variable == weather_variable,
            ForecastErrorStats.lead_time_bucket == lb,
            ForecastErrorStats.month.is_(None),
            ForecastErrorStats.fallback_level == "global",
            ForecastErrorStats.sample_size >= MIN_SAMPLE,
        ).limit(1)
    )
    row = q.scalar_one_or_none()
    return row


async def _sigma_v2(
    city: str,
    weather_variable: str,
    lead_time_days: int | None,
    month: int | None,
    session: AsyncSession,
    *,
    hourly: bool = False,
) -> tuple[float, str]:
    """
    Return (sigma, fallback_level) with floor/ceiling applied.

    Floor/ceiling prevent overconfident or degenerate estimates:
    - Floor = SIGMA_FLOOR (3.5°F daily) / SIGMA_FLOOR_HOURLY (2.0°F)
    - Ceiling = SIGMA_CEILING (15.0°F)

    When DB has < MIN_SAMPLE observations, uses _conservative_prior()
    instead of the v1 fixed table (prior is larger, reflecting location offset).
    """
    floor = SIGMA_FLOOR_HOURLY if hourly else SIGMA_FLOOR

    stats = await _get_error_stats(city, weather_variable, lead_time_days, month, session)
    if stats is not None and stats.std_dev is not None and stats.std_dev > 0:
        level = stats.fallback_level or "city"
        raw = stats.std_dev
    else:
        # Conservative prior — larger than v1 fixed table to account for
        # location offset between Open-Meteo grid point and NWS station
        raw = _conservative_prior(lead_time_days)
        level = "fixed_table"

    # Clamp to approved range
    sigma = max(floor, min(SIGMA_CEILING, raw))
    return sigma, level


async def _bias_v2(
    city: str,
    weather_variable: str,
    lead_time_days: int | None,
    month: int | None,
    session: AsyncSession,
) -> float:
    """
    Return mean forecast error (actual − forecast) to subtract from μ.
    Returns 0.0 when sample < MIN_SAMPLE or stats unavailable.
    """
    stats = await _get_error_stats(city, weather_variable, lead_time_days, month, session)
    if stats is not None and stats.mean_error is not None:
        return stats.mean_error
    return 0.0


async def _calibration_adj_v2(
    ec_prob: float,
    session: AsyncSession,
    *,
    strategy_version: str = "v2.0",
) -> float:
    """
    Return multiplicative calibration adjustment factor.
    Returns 1.0 when sample < MIN_CALIB_SAMPLE or no calibration row found.

    Pass strategy_version="v2.3" for V2.3 trades so they cannot inherit
    any v2.0-era rows even if those rows are added in the future.
    """
    q = await session.execute(
        select(CalibrationAdjustment).where(
            CalibrationAdjustment.strategy_version == strategy_version,
            CalibrationAdjustment.bucket_lo <= ec_prob,
            CalibrationAdjustment.bucket_hi > ec_prob,
            CalibrationAdjustment.sample_size >= MIN_CALIB_SAMPLE,
            CalibrationAdjustment.adjustment_factor.is_not(None),
        ).limit(1)
    )
    row = q.scalar_one_or_none()
    if row and row.adjustment_factor is not None:
        return row.adjustment_factor
    return 1.0


# ---------------------------------------------------------------------------
# Gaussian helpers (same math as v1, but parameterised)
# ---------------------------------------------------------------------------

def _is_integer_threshold(v: float) -> bool:
    """True when v is a whole number (e.g. 90, 100) vs a half-integer (e.g. 100.5)."""
    return v == math.floor(v)


def _calc_prob_threshold(
    operator: str,
    threshold: float,
    mu: float,
    sigma: float,
) -> float:
    """
    P(X_rounded >= threshold) for 'gte', P(X_rounded <= threshold) for 'lte',
    with NWS integer-rounding correction applied per-operator.

    NWS settlement rounds continuous temperatures to the nearest integer degree.
    The effective CDF boundary differs by operator direction:

      GTE  P(round(actual) >= T) = P(actual >= T − 0.5)   boundary shifts DOWN
      LTE  P(round(actual) <= T) = P(actual <  T + 0.5)   boundary shifts UP

    Note: for integer T, P(GTE) + P(LTE) > 1.0 because settlement temperature T
    itself wins both an "at or above T" contract and an "at or below T" contract.

    Half-integer thresholds (e.g. 100.5) are not NWS-rounded; boundary unchanged.
    Unknown operators raise ValueError — no silent fallback.
    """
    if operator == "gte":
        eff = (threshold - 0.5) if _is_integer_threshold(threshold) else threshold
        return round(1.0 - _normal_cdf((eff - mu) / sigma), 4)
    elif operator == "lte":
        eff = (threshold + 0.5) if _is_integer_threshold(threshold) else threshold
        return round(_normal_cdf((eff - mu) / sigma), 4)
    else:
        raise ValueError(
            f"Unsupported operator {operator!r}; expected 'gte' or 'lte'."
        )


def _calc_prob_range(lower: float, upper: float, mu: float, sigma: float) -> float:
    # NWS settlement rounds to the nearest integer.  For integer range bounds,
    # expand the integration interval by ±0.5 to capture the correct probability mass.
    # P(Lo_rounded ≤ X ≤ Hi_rounded) = P(Lo − 0.5 ≤ actual ≤ Hi + 0.5).
    eff_lo = (lower - 0.5) if _is_integer_threshold(lower) else lower
    eff_hi = (upper + 0.5) if _is_integer_threshold(upper) else upper
    z_hi = (eff_hi - mu) / sigma
    z_lo = (eff_lo - mu) / sigma
    return round(max(0.0, _normal_cdf(z_hi) - _normal_cdf(z_lo)), 4)


# ---------------------------------------------------------------------------
# Main v2 entry point
# ---------------------------------------------------------------------------

async def run_analysis_v2(
    *,
    title: str,
    subtitle: str | None,
    city: str | None,
    target_date_str: str | None,
    weather_variable: str | None,
    operator: str | None,
    threshold: float | None,
    parse_confidence: str,
    settlement_status: str,
    unsupported_reason: str | None,
    forecast_high: float | None,
    forecast_low: float | None,
    forecast_retrieved_at: datetime | None,
    yes_bid: float | None,
    yes_ask: float | None,
    contract_type: str = "threshold",
    lower_bound: float | None = None,
    upper_bound: float | None = None,
    forecast_hourly_value: float | None = None,
    session: AsyncSession | None = None,
) -> AnalysisResultV2:
    """
    Full v2 analysis pipeline for one market.  Mirrors the v1 run_analysis
    signature plus a session parameter for loading learned stats from DB.
    """
    from datetime import date

    mkt_prob = market_implied_probability(yes_bid, yes_ask)

    # ── Unsupported settlement contract ──────────────────────────────────────
    if settlement_status != "supported":
        conf = confidence_score(None, parse_confidence, forecast_retrieved_at, mkt_prob)
        explanation = unsupported_reason or "Market structure not supported by this engine."
        if mkt_prob is not None:
            explanation += f" Market-implied probability: {mkt_prob * 100:.1f}% (Kalshi prices only)."
        return AnalysisResultV2(
            ec_probability=None, market_probability=mkt_prob,
            confidence=conf, explanation=explanation,
            forecast_value=None, lead_time_days=None, sigma=None,
            analysis_status=settlement_status, analysis_reason=unsupported_reason,
        )

    # ── Select forecast value ─────────────────────────────────────────────────
    if contract_type == "hourly_threshold":
        forecast_value = forecast_hourly_value
    elif weather_variable == "high":
        forecast_value = forecast_high
    else:
        forecast_value = forecast_low

    if forecast_value is None or target_date_str is None:
        reason = (
            f"No forecast available for {city or 'this city'}"
            + (f" on {target_date_str}" if target_date_str else "") + "."
        )
        conf = confidence_score(None, parse_confidence, forecast_retrieved_at, mkt_prob)
        explanation = reason
        if mkt_prob is not None:
            explanation += f" Market-implied probability: {mkt_prob * 100:.1f}%."
        return AnalysisResultV2(
            ec_probability=None, market_probability=mkt_prob,
            confidence=conf, explanation=explanation,
            forecast_value=None, lead_time_days=None, sigma=None,
            analysis_status="no_forecast", analysis_reason=reason,
        )

    # ── Lead time ─────────────────────────────────────────────────────────────
    # market.target_date may be a full ISO datetime ("2026-07-28T19:00:00Z");
    # date.fromisoformat only accepts "YYYY-MM-DD", so strip any time component.
    try:
        date_only_str = target_date_str.split("T")[0] if target_date_str else None
        target = date.fromisoformat(date_only_str)
        lead_time_days = max(0, (target - date.today()).days)
        month = target.month
    except (ValueError, TypeError):
        lead_time_days = None
        month = None

    # ── Load v2 parameters ───────────────────────────────────────────────────
    effective_city = city or "unknown"
    effective_variable = weather_variable or "high"

    if session is not None:
        sigma, fallback_level = await _sigma_v2(
            effective_city, effective_variable, lead_time_days, month, session,
            hourly=(contract_type == "hourly_threshold"),  # Fix: 2.0°F floor for hourly
        )
        bias = await _bias_v2(
            effective_city, effective_variable, lead_time_days, month, session
        )
    else:
        sigma = sigma_for_lead_time(lead_time_days if lead_time_days is not None else 99)
        fallback_level = "fixed_table"
        bias = 0.0

    # Bias-corrected μ.
    # Convention: mean_error = mean(actual − forecast).
    #   positive mean_error → actual hotter than forecast → GFS under-forecasts.
    #   negative mean_error → actual cooler than forecast → GFS over-forecasts.
    # Subtracting a positive mean_error lowers mu (downward V2.1 adjustment).
    # V3 uses the opposite sign (mu += bias) which is the correct direction;
    # this V2.1 formula is preserved unchanged to avoid recalculating settled records.
    mu = forecast_value - bias

    # ── Gaussian probability ──────────────────────────────────────────────────
    ld = lead_time_days if lead_time_days is not None else 99

    if contract_type == "range":
        assert lower_bound is not None and upper_bound is not None
        raw_prob = _calc_prob_range(lower_bound, upper_bound, mu, sigma)
    else:
        assert operator is not None and threshold is not None
        raw_prob = _calc_prob_threshold(operator, threshold, mu, sigma)

    # ── Calibration ───────────────────────────────────────────────────────────
    if session is not None:
        calib_adj = await _calibration_adj_v2(raw_prob, session, strategy_version="v2.0")
    else:
        calib_adj = 1.0

    if calib_adj != 1.0:
        ec_prob = float(min(0.999, max(0.001, raw_prob * calib_adj)))
        ec_prob = round(ec_prob, 4)
    else:
        ec_prob = raw_prob

    conf = confidence_score(lead_time_days, parse_confidence, forecast_retrieved_at, mkt_prob)

    # ── Plain-language explanation ────────────────────────────────────────────
    bias_note = ""
    if abs(bias) >= 0.1:
        direction = "high" if bias > 0 else "low"
        bias_note = (
            f" Bias correction of {abs(bias):.1f}°F applied "
            f"(model runs {direction}; adjusted μ = {mu:.1f}°F)."
        )

    sigma_note = (
        f" σ = {sigma:.1f}°F ({fallback_level})"
        if fallback_level != "fixed_table"
        else f" σ = {sigma:.1f}°F (v1 table fallback)"
    )

    calib_note = (
        f" Calibration factor applied: ×{calib_adj:.3f}."
        if calib_adj != 1.0
        else ""
    )

    if contract_type == "range":
        var_label = "high" if weather_variable == "high" else "low"
        explanation = (
            f"[v2] Open-Meteo forecasts {forecast_value:.1f}°F {var_label} for "
            f"{city or 'this city'}.{bias_note}{sigma_note}. "
            f"P({lower_bound:.0f}–{upper_bound:.0f}°F) = {ec_prob * 100:.1f}%.{calib_note}"
        )
    elif contract_type == "hourly_threshold":
        op_label = "≥" if operator == "gte" else "≤"
        explanation = (
            f"[v2] Open-Meteo hourly: {forecast_value:.1f}°F for "
            f"{city or 'this city'}.{bias_note}{sigma_note}. "
            f"P(T {op_label} {threshold:.1f}°F) = {ec_prob * 100:.1f}%.{calib_note}"
        )
    else:
        var_label = "high" if weather_variable == "high" else "low"
        op_label = "≥" if operator == "gte" else "≤"
        explanation = (
            f"[v2] Open-Meteo {var_label}: {forecast_value:.1f}°F for "
            f"{city or 'this city'}.{bias_note}{sigma_note}. "
            f"P(T {op_label} {threshold:.0f}°F) = {ec_prob * 100:.1f}%.{calib_note}"
        )

    if mkt_prob is not None:
        gap = (ec_prob - mkt_prob) * 100
        sign = "+" if gap >= 0 else ""
        explanation += (
            f" Kalshi: {mkt_prob * 100:.1f}% (EdgeCast v2 − Market: {sign}{gap:.1f}pp)."
        )

    return AnalysisResultV2(
        ec_probability=ec_prob,
        market_probability=mkt_prob,
        confidence=conf,
        explanation=explanation,
        forecast_value=forecast_value,
        lead_time_days=lead_time_days,
        sigma=sigma,
        analysis_status="supported",
        analysis_reason=None,
        # v2-specific
        sigma_used=sigma,
        bias_correction=bias,
        fallback_level=fallback_level,
        calibration_adj=calib_adj,
        raw_ec_probability=raw_prob,
    )
