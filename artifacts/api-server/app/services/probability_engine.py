"""
Probability Engine v2 — deterministic Gaussian temperature forecast model.

Model
-----
We model the true temperature T as a Gaussian random variable centred
on the Open-Meteo point forecast:

    T ~ N(μ, σ²)

where:
    μ = Open-Meteo forecast value (daily high, daily low, or hourly temp, °F)
    σ = forecast uncertainty in °F, calibrated by lead time (see below)

Contract types
--------------
threshold (daily high / low):
    P(T >= threshold) = 1 − Φ((threshold − μ) / σ)
    P(T <= threshold) = Φ((threshold − μ) / σ)

range (bucket market):
    P(lo ≤ T ≤ hi) = Φ((hi − μ) / σ) − Φ((lo − μ) / σ)

hourly_threshold:
    Same as threshold but μ comes from the hourly forecast for the specific
    settlement hour, not the daily extreme.

Forecast uncertainty (σ) by lead time
--------------------------------------
Values are calibrated against published NWS MOS (Model Output Statistics)
and ECMWF verification statistics for 2m-temperature daily extremes.

    lead ≤ 1 day  : σ = 2.5°F   (NWS Day-1 median absolute error ~2.3°F)
    lead   2 days : σ = 3.5°F
    lead   3 days : σ = 4.3°F
    lead 4-5 days : σ = 5.5°F
    lead 6-7 days : σ = 6.8°F
    lead 8-10 days: σ = 8.2°F
    lead ≥ 11 days: σ = 9.5°F   (approaching climatological spread)

Confidence score
----------------
Possible values (in descending order): Very High, High, Medium, Low, Very Low.

Score starts at 4 (Very High) with deductions:
    lead_time >  7 days       : −2
    lead_time 4–7 days        : −1
    parse_confidence != 'high': −1
    forecast stale (>12 h old): −1
    market prices unavailable : −0.5 → rounded down

Mapping (floor):  4→Very High  3→High  2→Medium  1→Low  0→Very Low
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Sigma table
# ---------------------------------------------------------------------------

_SIGMA_TABLE: list[tuple[int, float]] = [
    (1,  2.5),
    (2,  3.5),
    (3,  4.3),
    (5,  5.5),
    (7,  6.8),
    (10, 8.2),
]
_SIGMA_DEFAULT = 9.5


def sigma_for_lead_time(lead_time_days: int) -> float:
    """Return the forecast σ (°F) for the given lead time in days."""
    for max_days, sigma in _SIGMA_TABLE:
        if lead_time_days <= max_days:
            return sigma
    return _SIGMA_DEFAULT


# ---------------------------------------------------------------------------
# Normal CDF (no external deps)
# ---------------------------------------------------------------------------

def _normal_cdf(z: float) -> float:
    """Standard normal CDF via the complementary error function."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class AnalysisResult:
    """
    Output of the probability engine for a single market.

    ec_probability   : float 0–1, or None if analysis not possible
    market_probability: float 0–1 derived from Kalshi YES prices, or None
    confidence        : 'Very High' | 'High' | 'Medium' | 'Low' | 'Very Low'
    explanation       : plain-language explanation string
    forecast_value    : the temperature forecast used (μ), or None
    lead_time_days    : integer days to settlement, or None
    sigma             : σ used in the calculation, or None
    analysis_status   : 'supported' | 'unsupported' | 'no_forecast' | 'no_data'
    analysis_reason   : reason string for non-'supported' status
    """

    ec_probability: float | None
    market_probability: float | None
    confidence: str
    explanation: str
    forecast_value: float | None
    lead_time_days: int | None
    sigma: float | None
    analysis_status: str
    analysis_reason: str | None


# ---------------------------------------------------------------------------
# Market-implied probability
# ---------------------------------------------------------------------------

def market_implied_probability(
    yes_bid: float | None,
    yes_ask: float | None,
) -> float | None:
    """
    Derive the market-implied YES probability from Kalshi bid/ask prices.

    On Kalshi, YES ask = the cost to buy $1 if YES resolves.
    Best estimate = midpoint of bid and ask when both are available.
    Falls back to whichever side is present.

    Returns a float in [0, 1] or None if no price data is available.
    """
    if yes_bid is not None and yes_ask is not None:
        return round((yes_bid + yes_ask) / 2, 4)
    if yes_ask is not None:
        return round(yes_ask, 4)
    if yes_bid is not None:
        return round(yes_bid, 4)
    return None


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

_CONF_LABELS = ["Very Low", "Low", "Medium", "High", "Very High"]


def confidence_score(
    lead_time_days: int | None,
    parse_confidence: str,
    forecast_retrieved_at: datetime | None,
    market_probability: float | None,
) -> str:
    """
    Return a confidence label based on four factors.

    Scoring starts at 4 (Very High) and deductions lower it:
        lead > 7 days or unknown : −2
        lead 4–7 days            : −1
        parse_confidence != high : −1
        forecast stale (>12 h)   : −1
        market prices unavailable: −0.5

    Mapping (floor):  4→Very High  3→High  2→Medium  1→Low  0→Very Low
    """
    score = 4.0

    # Lead-time penalty
    if lead_time_days is None:
        score -= 2.0
    elif lead_time_days > 7:
        score -= 2.0
    elif lead_time_days >= 4:
        score -= 1.0

    # Parse confidence penalty
    if parse_confidence != "high":
        score -= 1.0

    # Forecast freshness penalty (stale if retrieved > 12 h ago)
    if forecast_retrieved_at is not None:
        age_hours = (
            datetime.now(timezone.utc) - forecast_retrieved_at
        ).total_seconds() / 3600
        if age_hours > 12:
            score -= 1.0

    # Missing market prices penalty
    if market_probability is None:
        score -= 0.5

    idx = max(0, min(4, math.floor(score)))
    return _CONF_LABELS[idx]


# ---------------------------------------------------------------------------
# Probability calculation
# ---------------------------------------------------------------------------

def calculate_probability(
    operator: str,         # 'gte' or 'lte'
    threshold: float,      # the temperature threshold in °F
    forecast_value: float, # Open-Meteo μ in °F
    lead_time_days: int,
) -> float:
    """
    Return P(market resolves YES) using the Gaussian forecast model.

    'gte': P(T >= threshold)
    'lte': P(T <= threshold)
    """
    sigma = sigma_for_lead_time(lead_time_days)
    z = (threshold - forecast_value) / sigma
    if operator == "gte":
        return round(1.0 - _normal_cdf(z), 4)
    else:  # lte
        return round(_normal_cdf(z), 4)


def calculate_range_probability(
    lower_bound: float,    # inclusive lower temperature bound in °F
    upper_bound: float,    # inclusive upper temperature bound in °F
    forecast_value: float, # Open-Meteo μ in °F
    lead_time_days: int,
) -> float:
    """
    Return P(lower_bound ≤ T ≤ upper_bound) using the Gaussian forecast model.

    P(lo ≤ T ≤ hi) = Φ((hi − μ) / σ) − Φ((lo − μ) / σ)
    """
    sigma = sigma_for_lead_time(lead_time_days)
    z_hi = (upper_bound - forecast_value) / sigma
    z_lo = (lower_bound - forecast_value) / sigma
    p = _normal_cdf(z_hi) - _normal_cdf(z_lo)
    return round(max(0.0, p), 4)


# ---------------------------------------------------------------------------
# Main engine entry point
# ---------------------------------------------------------------------------

def run_analysis(
    *,
    title: str,
    subtitle: str | None,
    city: str | None,
    target_date_str: str | None,  # 'YYYY-MM-DD'
    weather_variable: str | None,  # 'high' | 'low' | 'hourly_temperature'
    operator: str | None,           # 'gte' | 'lte' | None (for range)
    threshold: float | None,
    parse_confidence: str,
    settlement_status: str,         # 'supported' | 'unsupported' | 'no_data'
    unsupported_reason: str | None,
    forecast_high: float | None,
    forecast_low: float | None,
    forecast_retrieved_at: datetime | None,
    yes_bid: float | None,
    yes_ask: float | None,
    # Phase 2B new params (all optional for backward compatibility)
    contract_type: str = "threshold",       # 'threshold' | 'range' | 'hourly_threshold'
    lower_bound: float | None = None,       # for range contracts
    upper_bound: float | None = None,       # for range contracts
    forecast_hourly_value: float | None = None,  # for hourly_threshold contracts
) -> AnalysisResult:
    """
    Full analysis pipeline for one market.

    Delegates to the Gaussian model when the settlement contract is
    'supported' and a matching forecast exists.
    """
    from datetime import date

    mkt_prob = market_implied_probability(yes_bid, yes_ask)

    # ---- Unsupported settlement contract -----------------------------------
    if settlement_status != "supported":
        conf = confidence_score(None, parse_confidence, forecast_retrieved_at, mkt_prob)
        explanation = unsupported_reason or "Market structure not supported by this engine."
        if mkt_prob is not None:
            explanation += (
                f" Market-implied probability: {mkt_prob * 100:.1f}% "
                "(from Kalshi prices only)."
            )
        return AnalysisResult(
            ec_probability=None,
            market_probability=mkt_prob,
            confidence=conf,
            explanation=explanation,
            forecast_value=None,
            lead_time_days=None,
            sigma=None,
            analysis_status=settlement_status,
            analysis_reason=unsupported_reason,
        )

    # ---- Select forecast value based on contract type ---------------------
    if contract_type == "hourly_threshold":
        forecast_value = forecast_hourly_value
    elif weather_variable == "high":
        forecast_value = forecast_high
    else:
        forecast_value = forecast_low

    # ---- No forecast available ---------------------------------------------
    if forecast_value is None or target_date_str is None:
        if contract_type == "hourly_threshold":
            reason = (
                f"No hourly temperature forecast available for "
                f"{city or 'this city'}"
                + (f" on {target_date_str}" if target_date_str else "")
                + ". Exact settlement hour unavailable."
            )
        else:
            var_label = "high" if weather_variable == "high" else "low"
            reason = (
                f"No Open-Meteo {var_label} temperature forecast available for "
                f"{city or 'this city'}"
                + (f" on {target_date_str}" if target_date_str else "")
                + "."
            )
        conf = confidence_score(None, parse_confidence, forecast_retrieved_at, mkt_prob)
        explanation = reason
        if mkt_prob is not None:
            explanation += f" Market-implied probability: {mkt_prob * 100:.1f}%."
        return AnalysisResult(
            ec_probability=None,
            market_probability=mkt_prob,
            confidence=conf,
            explanation=explanation,
            forecast_value=None,
            lead_time_days=None,
            sigma=None,
            analysis_status="no_forecast",
            analysis_reason=reason,
        )

    # ---- Lead time ---------------------------------------------------------
    try:
        target = date.fromisoformat(target_date_str)
        lead_time_days = max(0, (target - date.today()).days)
    except ValueError:
        lead_time_days = None

    sigma = sigma_for_lead_time(lead_time_days if lead_time_days is not None else 99)

    # ---- Gaussian probability ----------------------------------------------
    if contract_type == "range":
        assert lower_bound is not None and upper_bound is not None
        ec_prob = calculate_range_probability(
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            forecast_value=forecast_value,
            lead_time_days=lead_time_days if lead_time_days is not None else 99,
        )
    else:
        # threshold or hourly_threshold
        assert operator is not None and threshold is not None
        ec_prob = calculate_probability(
            operator=operator,
            threshold=threshold,
            forecast_value=forecast_value,
            lead_time_days=lead_time_days if lead_time_days is not None else 99,
        )

    conf = confidence_score(lead_time_days, parse_confidence, forecast_retrieved_at, mkt_prob)

    # ---- Plain-language explanation ----------------------------------------
    if contract_type == "range":
        var_label = "high" if weather_variable == "high" else "low"
        explanation_parts = [
            f"Open-Meteo forecasts a daily {var_label} of {forecast_value:.1f}°F for {city or 'this city'}.",
            f"This market resolves YES if the {var_label} temperature is between {lower_bound:.0f}°F and {upper_bound:.0f}°F.",
            f"Using a {sigma:.1f}°F uncertainty (lead time: {lead_time_days if lead_time_days is not None else '?'} day(s)),",
            f"EdgeCast estimates a {ec_prob * 100:.1f}% probability of YES.",
        ]
    elif contract_type == "hourly_threshold":
        op_label = "at or above" if operator == "gte" else "at or below"
        explanation_parts = [
            f"Open-Meteo hourly forecast: {forecast_value:.1f}°F for {city or 'this city'}.",
            f"This market resolves YES if the temperature is {op_label} {threshold:.2f}°F at the settlement hour.",
            f"Using a {sigma:.1f}°F uncertainty (lead time: {lead_time_days if lead_time_days is not None else '?'} day(s)),",
            f"EdgeCast estimates a {ec_prob * 100:.1f}% probability of YES.",
        ]
    else:
        var_label = "high" if weather_variable == "high" else "low"
        op_label = "at or above" if operator == "gte" else "at or below"
        explanation_parts = [
            f"Open-Meteo forecasts a daily {var_label} of {forecast_value:.1f}°F for {city or 'this city'}.",
            f"This market resolves YES if the {var_label} temperature is {op_label} {threshold:.0f}°F.",
            f"Using a {sigma:.1f}°F uncertainty (lead time: {lead_time_days if lead_time_days is not None else '?'} day(s)),",
            f"EdgeCast estimates a {ec_prob * 100:.1f}% probability of YES.",
        ]

    if mkt_prob is not None:
        gap = (ec_prob - mkt_prob) * 100
        sign = "+" if gap >= 0 else ""
        explanation_parts.append(
            f"Kalshi market-implied probability: {mkt_prob * 100:.1f}% "
            f"(EdgeCast − Market: {sign}{gap:.1f}pp)."
        )
    else:
        explanation_parts.append("Kalshi price data not available for this market.")

    return AnalysisResult(
        ec_probability=ec_prob,
        market_probability=mkt_prob,
        confidence=conf,
        explanation=" ".join(explanation_parts),
        forecast_value=forecast_value,
        lead_time_days=lead_time_days,
        sigma=sigma,
        analysis_status="supported",
        analysis_reason=None,
    )
