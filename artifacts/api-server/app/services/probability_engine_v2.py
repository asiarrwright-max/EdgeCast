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

# Minimum verified observations before using learned stats
MIN_SAMPLE = 5
# Minimum settled trades per bucket before applying calibration
MIN_CALIB_SAMPLE = 30


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
) -> tuple[float, str]:
    """
    Return (sigma, fallback_level).

    Uses std_dev from ForecastErrorStats when sample ≥ MIN_SAMPLE.
    Falls back to v1 fixed table otherwise.
    """
    stats = await _get_error_stats(city, weather_variable, lead_time_days, month, session)
    if stats is not None and stats.std_dev is not None and stats.std_dev > 0:
        level = stats.fallback_level or "city"
        return stats.std_dev, level

    # Fall back to v1 fixed σ table
    sigma = sigma_for_lead_time(lead_time_days if lead_time_days is not None else 99)
    return sigma, "fixed_table"


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
) -> float:
    """
    Return multiplicative calibration adjustment factor.
    Returns 1.0 when sample < MIN_CALIB_SAMPLE or no calibration row found.
    """
    q = await session.execute(
        select(CalibrationAdjustment).where(
            CalibrationAdjustment.strategy_version == "v2.0",
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

def _calc_prob_threshold(
    operator: str,
    threshold: float,
    mu: float,
    sigma: float,
) -> float:
    z = (threshold - mu) / sigma
    if operator == "gte":
        return round(1.0 - _normal_cdf(z), 4)
    return round(_normal_cdf(z), 4)


def _calc_prob_range(lower: float, upper: float, mu: float, sigma: float) -> float:
    z_hi = (upper - mu) / sigma
    z_lo = (lower - mu) / sigma
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
    try:
        target = date.fromisoformat(target_date_str)
        lead_time_days = max(0, (target - date.today()).days)
        month = target.month
    except ValueError:
        lead_time_days = None
        month = None

    # ── Load v2 parameters ───────────────────────────────────────────────────
    effective_city = city or "unknown"
    effective_variable = weather_variable or "high"

    if session is not None:
        sigma, fallback_level = await _sigma_v2(
            effective_city, effective_variable, lead_time_days, month, session
        )
        bias = await _bias_v2(
            effective_city, effective_variable, lead_time_days, month, session
        )
    else:
        sigma = sigma_for_lead_time(lead_time_days if lead_time_days is not None else 99)
        fallback_level = "fixed_table"
        bias = 0.0

    # Bias-corrected μ: subtract mean_error (positive = model runs high)
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
        calib_adj = await _calibration_adj_v2(raw_prob, session)
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
