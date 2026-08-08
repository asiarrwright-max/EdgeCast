"""
V3 Probability Engine (Phase 2)
================================
Gaussian probability engine that combines historical preload bias/sigma with
incremental forward V3 learning.

Phase 2 status
--------------
Forward learning weight is 0.0 — no live V3 observations yet.  The engine
is fully functional for walk-forward validation purposes.  Phase 3 will wire
forward_weight > 0 once live V3 paper-trade observations accumulate.

Isolation guarantee
-------------------
This module does NOT import from probability_engine.py (V1) or
probability_engine_v2.py (V2.1).  It re-implements the same Gaussian CDF
math directly so changes to V1/V2.1 cannot propagate into V3.

All weighting parameters flow through V3StatsConfig, which is recorded in
every V3PredictionSnapshot row so the split is always auditable.
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.v3_error_stats import (
    V3Prior,
    V3StatsConfig,
    SIGMA_FLOOR,
    SIGMA_CEILING,
    get_v3_prior,
)
from app.models_v3 import CURRENT_PRELOAD_VERSION

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gaussian helpers (V3-local copies; do not import from V1/V2.1 engines)
# ---------------------------------------------------------------------------

def _normal_cdf(z: float) -> float:
    """Standard normal CDF via math.erf."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2)))


def _is_integer_threshold(v: float) -> bool:
    """True when v is a whole number (e.g. 90, 100) vs a half-integer (e.g. 100.5)."""
    return v == math.floor(v)


def _calc_prob_threshold(operator: str, threshold: float, mu: float, sigma: float) -> float:
    """
    P(X_rounded >= threshold) for 'gte', P(X_rounded <= threshold) for 'lte',
    with NWS integer-rounding correction applied per-operator.

    NWS settlement rounds continuous temperatures to the nearest integer degree.
    The effective CDF boundary differs by operator direction:

      GTE  P(round(actual) >= T) = P(actual >= T − 0.5)   boundary shifts DOWN
      LTE  P(round(actual) <= T) = P(actual <  T + 0.5)   boundary shifts UP

    Half-integer thresholds (e.g. 100.5) are not NWS-rounded; boundary unchanged.
    Unknown operators raise ValueError — no silent fallback.
    """
    if operator == "gte":
        eff = (threshold - 0.5) if _is_integer_threshold(threshold) else threshold
        return round(1.0 - _normal_cdf((eff - mu) / sigma), 6)
    elif operator == "lte":
        eff = (threshold + 0.5) if _is_integer_threshold(threshold) else threshold
        return round(_normal_cdf((eff - mu) / sigma), 6)
    else:
        raise ValueError(
            f"Unsupported operator {operator!r}; expected 'gte' or 'lte'."
        )


def _calc_prob_range(lower: float, upper: float, mu: float, sigma: float) -> float:
    """P(lower <= X <= upper) under N(mu, sigma), with NWS rounding correction."""
    # NWS settlement rounds to the nearest integer.  For integer range bounds,
    # expand the integration interval by ±0.5.
    eff_lo = (lower - 0.5) if _is_integer_threshold(lower) else lower
    eff_hi = (upper + 0.5) if _is_integer_threshold(upper) else upper
    z_hi = (eff_hi - mu) / sigma
    z_lo = (eff_lo - mu) / sigma
    return round(max(0.0, _normal_cdf(z_hi) - _normal_cdf(z_lo)), 6)


def _clamp(v: float, lo: float = 0.001, hi: float = 0.999) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Input / output dataclasses
# ---------------------------------------------------------------------------

@dataclass
class V3PredictionInput:
    """Everything needed to run a V3 probability calculation."""
    city:             str
    model:            str           # e.g. "GFS"
    lead_bucket:      str           # e.g. "1d"
    season:           str | None    # "winter" | "spring" | "summer" | "fall"

    # The raw NWP forecast value (in °F) before any bias adjustment
    forecast_value:   float

    # Contract specification
    contract_type:    str           # "threshold" | "range" | "hourly_threshold"
    operator:         str | None    # "gte" | "lt" (threshold contracts)
    threshold:        float | None  # °F
    lower_bound:      float | None  # °F (range contracts)
    upper_bound:      float | None  # °F (range contracts)

    # Forward V3 observations (empty in Phase 2)
    forward_errors:   list[float] = None  # type: ignore[assignment]

    # Config
    config:           V3StatsConfig = None  # type: ignore[assignment]
    preload_version:  str = CURRENT_PRELOAD_VERSION

    def __post_init__(self) -> None:
        if self.forward_errors is None:
            self.forward_errors = []
        if self.config is None:
            self.config = V3StatsConfig()


@dataclass
class V3PredictionOutput:
    """Full V3 probability output with decomposition."""
    # Core probability
    ec_probability:       float | None   # final probability [0,1]
    raw_ec_probability:   float | None   # without bias correction

    # Bias/sigma decomposition — every component is recorded
    historical_bias:      float          # from V3ErrorStats (shrunk, °F)
    historical_sigma:     float          # from V3ErrorStats (shrunk, °F)
    forward_bias_adj:     float          # incremental from forward learning (0.0 Phase 2)
    forward_sigma_adj:    float          # (0.0 Phase 2)
    final_bias:           float          # weighted combination
    final_sigma:          float          # weighted combination

    # The bias-corrected forecast input (mu used in Gaussian calc)
    mu_adjusted:          float

    # Provenance
    fallback_level_used:  int            # 0–4
    hist_raw_n:           int
    hist_effective_n:     float
    forward_n:            int            # 0 in Phase 2
    source_key:           str            # human-readable fallback path

    # Config snapshot (for analytics transparency)
    hist_weight:          float
    forward_weight:       float
    config_snapshot:      dict

    # Two-component architecture — bias gate result
    bias_applied:         bool           # True = bias correction was applied to mu
    bias_suppressed_reason: str = ""     # non-empty when bias_applied is False

    # Status
    status:               str = "ok"     # "ok" | "no_stats" | "unsupported"
    note:                 str = ""


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------

async def run_v3_prediction(
    inputs: V3PredictionInput,
    session: AsyncSession,
) -> V3PredictionOutput:
    """
    Compute a V3 probability for one market/contract.

    Step 1 — load historical prior from V3ErrorStats via get_v3_prior().
    Step 2 — compute forward-learning adjustment from inputs.forward_errors
             (empty in Phase 2 → adjustment = 0.0).
    Step 3 — combine using config weights.
    Step 4 — apply Gaussian probability calculation with bias-corrected mu.
    Step 5 — return full V3PredictionOutput with all decomposition fields.

    This function is NOT called in production during Phase 2.  It is used
    exclusively by the walk-forward validator and unit tests.
    """
    cfg = inputs.config

    # ── Step 1: Historical prior ──────────────────────────────────────────
    prior: V3Prior = await get_v3_prior(
        session,
        city=inputs.city,
        model=inputs.model,
        lead_bucket=inputs.lead_bucket,
        season=inputs.season,
        preload_version=inputs.preload_version,
        config=cfg,
    )
    hist_bias  = prior.bias
    hist_sigma = prior.sigma   # already clamped by get_v3_prior

    # ── Step 2: Forward learning (Phase 2: always empty) ─────────────────
    fwd_errors = inputs.forward_errors or []
    fwd_n = len(fwd_errors)
    fwd_bias_adj  = 0.0
    fwd_sigma_adj = 0.0

    if fwd_n > 0 and cfg.forward_weight > 0.0:
        import statistics
        fwd_bias_adj  = statistics.mean(fwd_errors)
        fwd_sigma_adj = statistics.stdev(fwd_errors) if fwd_n >= 2 else hist_sigma

    # ── Step 3: Sigma component (always applied) ──────────────────────────
    # Sigma is the primary calibration signal.  It is ALWAYS used regardless
    # of whether the bias gate passes.  Forward learning blends variance.
    if cfg.forward_weight == 0.0 or fwd_n == 0:
        final_sigma = hist_sigma
    else:
        var_hist    = hist_sigma ** 2
        var_fwd     = (fwd_sigma_adj or hist_sigma) ** 2
        final_sigma = math.sqrt(
            cfg.hist_weight * var_hist + cfg.forward_weight * var_fwd
        )
    final_sigma = max(cfg.sigma_floor, min(cfg.sigma_ceiling, final_sigma))

    # ── Step 3b: Bias component (gated) ───────────────────────────────────
    # Bias correction is applied ONLY when the prior's bias gate passed.
    # When suppressed, mu stays at the raw forecast and only sigma changes.
    bias_applied          = prior.bias_gate_passed
    bias_suppressed_reason = prior.bias_suppressed_reason

    if bias_applied:
        if cfg.forward_weight == 0.0 or fwd_n == 0:
            final_bias = hist_bias
        else:
            final_bias = cfg.hist_weight * hist_bias + cfg.forward_weight * fwd_bias_adj
    else:
        final_bias = 0.0  # bias suppressed — preserve raw forecast

    # ── Step 4: Gaussian probability ──────────────────────────────────────
    mu_raw      = inputs.forecast_value
    mu_adjusted = inputs.forecast_value + final_bias  # = mu_raw when bias suppressed

    if inputs.contract_type == "range":
        if inputs.lower_bound is None or inputs.upper_bound is None:
            return _error_output(inputs, prior, "Missing bounds for range contract")
        raw_prob = _calc_prob_range(inputs.lower_bound, inputs.upper_bound, mu_raw, final_sigma)
        adj_prob = _calc_prob_range(inputs.lower_bound, inputs.upper_bound, mu_adjusted, final_sigma)
    else:
        if inputs.operator is None or inputs.threshold is None:
            return _error_output(inputs, prior, "Missing operator/threshold")
        raw_prob = _calc_prob_threshold(inputs.operator, inputs.threshold, mu_raw, final_sigma)
        adj_prob = _calc_prob_threshold(inputs.operator, inputs.threshold, mu_adjusted, final_sigma)

    return V3PredictionOutput(
        ec_probability          = _clamp(adj_prob),
        raw_ec_probability      = _clamp(raw_prob),
        historical_bias         = hist_bias,
        historical_sigma        = hist_sigma,
        forward_bias_adj        = fwd_bias_adj,
        forward_sigma_adj       = fwd_sigma_adj,
        final_bias              = final_bias,
        final_sigma             = final_sigma,
        mu_adjusted             = mu_adjusted,
        fallback_level_used     = prior.fallback_level,
        hist_raw_n              = prior.raw_n,
        hist_effective_n        = prior.effective_n,
        forward_n               = fwd_n,
        source_key              = prior.source_key,
        hist_weight             = cfg.hist_weight,
        forward_weight          = cfg.forward_weight,
        config_snapshot         = cfg.to_dict(),
        bias_applied            = bias_applied,
        bias_suppressed_reason  = bias_suppressed_reason,
        status                  = "ok",
        note                    = "",
    )


def _error_output(
    inputs: V3PredictionInput,
    prior: V3Prior,
    note: str,
) -> V3PredictionOutput:
    """Return a status='unsupported' output when inputs are invalid."""
    return V3PredictionOutput(
        ec_probability          = None,
        raw_ec_probability      = None,
        historical_bias         = prior.bias,
        historical_sigma        = prior.sigma,
        forward_bias_adj        = 0.0,
        forward_sigma_adj       = 0.0,
        final_bias              = 0.0,
        final_sigma             = prior.sigma,
        mu_adjusted             = inputs.forecast_value,
        fallback_level_used     = prior.fallback_level,
        hist_raw_n              = prior.raw_n,
        hist_effective_n        = prior.effective_n,
        forward_n               = 0,
        source_key              = prior.source_key,
        hist_weight             = inputs.config.hist_weight,
        forward_weight          = inputs.config.forward_weight,
        config_snapshot         = inputs.config.to_dict(),
        bias_applied            = False,
        bias_suppressed_reason  = f"unsupported: {note}",
        status                  = "unsupported",
        note                    = note,
    )
