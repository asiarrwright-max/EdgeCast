"""
V2.2 Probability Engine — corrected bias sign.
===============================================
Identical to the V2.1 engine (probability_engine_v2.py) except that the
historical bias correction is applied with the **correct** sign:

    V2.1:  mu = forecast_value − mean_error   ← inverted, preserved for record integrity
    V2.2:  mu = forecast_value + mean_error   ← correct

Convention (same in both engines):
    signed_error = actual − forecast
    mean_error   = mean(signed_error)                     (stored in forecast_error_stats)
    positive mean_error → actual > forecast → GFS under-forecasts → raise mu  ✓
    negative mean_error → actual < forecast → GFS over-forecasts  → lower mu  ✓

V2.2 is an isolated parallel challenger.  It **never** reads or writes V2.1 state.
The MIN_SAMPLE guard (30 observations) is identical; bias is 0.0 until that threshold
is crossed regardless of sign.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.probability_engine_v2 import (
    AnalysisResultV2,
    _bias_v2,
    _calc_prob_range,
    _calc_prob_threshold,
    _calibration_adj_v2,
    _sigma_v2,
    confidence_score,
    market_implied_probability,
    sigma_for_lead_time,
)


async def run_analysis_v22(
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
    Full V2.2 analysis pipeline for one market.

    Identical to run_analysis_v2 (V2.1) with three changes:
      1. mu = forecast_value + bias  (CORRECTED sign)
      2. bias_note direction label corrected: "low" when bias > 0
      3. Explanation prefix is "[v2.2]"

    All other logic — sigma, calibration, MIN_SAMPLE guard, fallback table,
    lead-time bucketing — is identical and imported from probability_engine_v2.
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
        date_only_str = target_date_str.split("T")[0] if target_date_str else None
        target = date.fromisoformat(date_only_str)
        lead_time_days = max(0, (target - date.today()).days)
        month = target.month
    except (ValueError, TypeError):
        lead_time_days = None
        month = None

    # ── Load parameters (identical to V2.1) ──────────────────────────────────
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

    # ── Bias-corrected μ  ── V2.2 CORRECTED SIGN ─────────────────────────────
    # Convention: mean_error = mean(actual − forecast).
    #   positive → GFS under-forecasts → raise mu  → ADD mean_error  (V2.2 ✓)
    #   negative → GFS over-forecasts  → lower mu  → ADD mean_error  (V2.2 ✓)
    # V2.1 subtracts mean_error (inverted) — preserved to protect historical
    # record integrity; never change probability_engine_v2.run_analysis_v2.
    mu = forecast_value + bias  # CORRECTED from V2.1's (forecast_value - bias)

    # ── Gaussian probability ──────────────────────────────────────────────────
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

    # ── Plain-language explanation (V2.2 corrected direction labels) ──────────
    bias_note = ""
    if abs(bias) >= 0.1:
        # CORRECTED from V2.1: positive bias means GFS runs low (under-forecasts),
        # so mu is raised.  V2.1 had "high"/"low" swapped here.
        direction = "low" if bias > 0 else "high"
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
            f"[v2.2] Open-Meteo forecasts {forecast_value:.1f}°F {var_label} for "
            f"{city or 'this city'}.{bias_note}{sigma_note}. "
            f"P({lower_bound:.0f}–{upper_bound:.0f}°F) = {ec_prob * 100:.1f}%.{calib_note}"
        )
    elif contract_type == "hourly_threshold":
        op_label = "≥" if operator == "gte" else "≤"
        explanation = (
            f"[v2.2] Open-Meteo hourly: {forecast_value:.1f}°F for "
            f"{city or 'this city'}.{bias_note}{sigma_note}. "
            f"P(T {op_label} {threshold:.1f}°F) = {ec_prob * 100:.1f}%.{calib_note}"
        )
    else:
        var_label = "high" if weather_variable == "high" else "low"
        op_label = "≥" if operator == "gte" else "≤"
        explanation = (
            f"[v2.2] Open-Meteo {var_label}: {forecast_value:.1f}°F for "
            f"{city or 'this city'}.{bias_note}{sigma_note}. "
            f"P(T {op_label} {threshold:.0f}°F) = {ec_prob * 100:.1f}%.{calib_note}"
        )

    if mkt_prob is not None:
        gap = (ec_prob - mkt_prob) * 100
        sign = "+" if gap >= 0 else ""
        explanation += (
            f" Kalshi: {mkt_prob * 100:.1f}% (EdgeCast v2.2 − Market: {sign}{gap:.1f}pp)."
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
        # v2-specific fields (same shape as V2.1 AnalysisResultV2)
        sigma_used=sigma,
        bias_correction=bias,
        fallback_level=fallback_level,
        calibration_adj=calib_adj,
        raw_ec_probability=raw_prob,
    )
