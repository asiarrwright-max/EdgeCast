"""
V3 Walk-Forward Validation (Phase 2)
======================================
Implements a strict chronological walk-forward evaluation of the V3
historical-preload model.

Protocol
--------
Records are sorted by target_date (ascending).  For each test index i
starting at MIN_TRAIN_SIZE, the training set is all records with
target_date < records[i].target_date.  Statistics are computed from the
training set using the same shrinkage logic as the live model, then applied
to records[i].  No future data ever touches the training window.

Honest reporting
----------------
Cases where the preload bias adjustment *increased* absolute error are
identified and counted per season and overall.  The report includes these
even if the net effect is positive, so the user can see the full picture.

Metrics
-------
Per record:
  - raw_error          = observed - forecast              (no adjustment)
  - adj_error          = observed - (forecast + bias)     (with adjustment)
  - raw_abs_error      = |raw_error|
  - adj_abs_error      = |adj_error|
  - preload_hurt        = adj_abs_error > raw_abs_error
  - CRPS (raw and adjusted)

Aggregate:
  - MAE (raw, adjusted, delta)
  - RMSE (raw, adjusted)
  - Bias (mean error before and after correction)
  - Calibration (reliability diagram buckets: P(model), P(empirical))
  - Brier score (binary event: observed >= forecast)
  - CRPS (average proper scoring rule)
  - Seasonal breakdown for all metrics
  - Outlier analysis (records where |raw_error| > 2σ)
  - Cases where preload made it worse
  - Sample sizes with 95% CIs on MAE

Calibration methodology
-----------------------
For each test record we compute P(X >= forecast) under the V3 Gaussian
distribution.  This should equal 0.50 for a well-calibrated model (the
forecast is the median).  We also compute P(X >= observed-5) through
P(X >= observed+5) at 2.5°F intervals to trace a reliability curve.

Brier score
-----------
Binary event E_i = (observed_tmax >= threshold_i) where threshold_i =
records[i].forecast_tmax_f (i.e. "did the temperature exceed the raw
forecast?").  Nominal probability from the *adjusted* distribution:
  p_i = P(X >= forecast_i | mu = forecast_i + bias_i, sigma_i)

For a zero-bias model p_i = 0.50.  Better calibration → lower BS.

CRPS
----
For a Gaussian N(mu, sigma):
  CRPS = sigma * (z*(2*Φ(z)-1) + 2*φ(z) - 1/√π)
  where z = (observed - mu) / sigma.
Lower is better; measures full distributional accuracy.
"""
from __future__ import annotations

import math
import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from app.models_v3 import V3HistoricalRecord
from app.services.v3_error_stats import (
    V3StatsConfig,
    SIGMA_FLOOR,
    SIGMA_CEILING,
    MIN_SAMPLE,
    AUTOCORR_DISCOUNT,
    SHRINKAGE_K,
    GLOBAL_PRIOR_BIAS,
    GLOBAL_PRIOR_SIGMA,
    _compute_raw_stats,
    _shrink,
    _clamp_sigma,
    _n_eff,
    _lambda,
)

logger = logging.getLogger(__name__)


# Minimum training records before we start making predictions.
# Matches V2.1 MIN_SAMPLE = 30.
MIN_TRAIN_SIZE = 30


# ---------------------------------------------------------------------------
# Gaussian helpers (local copies — no import from V1/V2.1)
# ---------------------------------------------------------------------------

def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _normal_pdf(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def _crps_gaussian(observed: float, mu: float, sigma: float) -> float:
    """
    Continuous Ranked Probability Score for N(mu, sigma).
    Lower is better.  Equals MAE for deterministic (sigma→0) forecasts.
    Formula: sigma * (z*(2Φ(z)-1) + 2φ(z) - 1/√π)
    """
    if sigma <= 0:
        return abs(observed - mu)
    z   = (observed - mu) / sigma
    cdf = _normal_cdf(z)
    pdf = _normal_pdf(z)
    return sigma * (z * (2.0 * cdf - 1.0) + 2.0 * pdf - 1.0 / math.sqrt(math.pi))


def _prob_gte(threshold: float, mu: float, sigma: float) -> float:
    """P(X >= threshold) under N(mu, sigma)."""
    z = (threshold - mu) / sigma
    return 1.0 - _normal_cdf(z)


# ---------------------------------------------------------------------------
# In-memory training-set stats (no DB call in walk-forward loop)
# ---------------------------------------------------------------------------

def _check_wf_bias_gate(
    bias_shrunk: float,
    rs: "_RawStats",
    cfg: V3StatsConfig,
) -> tuple[bool, str]:
    """
    Inline bias gate for walk-forward validation (no DB access).
    Returns (gate_passed, suppressed_reason).
    suppressed_reason is "" when gate_passed is True.
    """
    from app.services.v3_error_stats import BIAS_MIN_EFFECTIVE_N, BIAS_MIN_T_STAT, BIAS_MIN_MAGNITUDE
    import math as _math
    ne = rs.n_eff
    bias_min_ne  = getattr(cfg, "bias_min_effective_n", BIAS_MIN_EFFECTIVE_N)
    bias_min_t   = getattr(cfg, "bias_min_t_stat",      BIAS_MIN_T_STAT)
    bias_min_mag = getattr(cfg, "bias_min_magnitude",   BIAS_MIN_MAGNITUDE)
    if ne < bias_min_ne:
        return False, f"n_eff={ne:.1f} < {bias_min_ne}"
    if rs.sigma_raw is None or rs.sigma_raw <= 0:
        return False, "sigma unavailable"
    t = abs(bias_shrunk) / (rs.sigma_raw / _math.sqrt(ne))
    if t < bias_min_t:
        return False, f"|t|={t:.2f} < {bias_min_t}"
    if abs(bias_shrunk) < bias_min_mag:
        return False, f"|bias|={abs(bias_shrunk):.3f}°F < {bias_min_mag}°F"
    return True, ""


def _compute_wf_prior(
    training: list[V3HistoricalRecord],
    target: V3HistoricalRecord,
    cfg: V3StatsConfig,
) -> tuple[float, float, int, str, bool, str]:
    """
    Compute (bias, sigma, fallback_level, source_key, bias_gate_passed,
    bias_suppressed_reason) from the training set for a single test record.

    Returns the most-specific level with n >= cfg.min_sample, or falls back
    toward the global prior.

    Two-component architecture:
    - sigma is always the shrunk estimate (calibration signal).
    - bias gate is evaluated at the selected fallback level; if it fails,
      bias_gate_passed=False and the caller should use mu = raw forecast.
    """
    city   = target.city
    model  = target.forecast_model
    lead   = target.lead_time_bucket
    season = target.season

    # Helper: filter training errors for a given group
    def errors(
        c: str | None = None,
        m: str | None = None,
        l: str | None = None,
        s: str | None = None,
    ) -> list[float]:
        out = []
        for r in training:
            if r.signed_error is None:
                continue
            if c is not None and r.city != c:
                continue
            if m is not None and r.forecast_model != m:
                continue
            if l is not None and r.lead_time_bucket != l:
                continue
            if s is not None and r.season != s:
                continue
            out.append(r.signed_error)
        return out

    # Global baseline
    g_all = errors()
    g_rs  = _compute_raw_stats(g_all, cfg.autocorr_discount)
    g_bias  = g_rs.bias  if g_rs.bias  is not None else GLOBAL_PRIOR_BIAS
    g_sigma = g_rs.sigma_raw if g_rs.sigma_raw is not None else GLOBAL_PRIOR_SIGMA
    g_sigma = _clamp_sigma(g_sigma, cfg.sigma_floor, cfg.sigma_ceiling)

    # Level 3: model + lead (cross-city)
    e3 = errors(m=model, l=lead)
    rs3 = _compute_raw_stats(e3, cfg.autocorr_discount)
    if rs3.bias is not None and rs3.sigma_raw is not None:
        b3 = _shrink(rs3.bias, g_bias, rs3.n_eff, cfg.shrinkage_k)
        s3 = _shrink(rs3.sigma_raw, g_sigma, rs3.n_eff, cfg.shrinkage_k)
        s3 = _clamp_sigma(s3, cfg.sigma_floor, cfg.sigma_ceiling)
    else:
        b3, s3 = g_bias, g_sigma

    # Level 2: city + model
    e2 = errors(c=city, m=model)
    rs2 = _compute_raw_stats(e2, cfg.autocorr_discount)
    if rs2.bias is not None and rs2.sigma_raw is not None:
        b2 = _shrink(rs2.bias, g_bias, rs2.n_eff, cfg.shrinkage_k)
        s2 = _shrink(rs2.sigma_raw, g_sigma, rs2.n_eff, cfg.shrinkage_k)
        s2 = _clamp_sigma(s2, cfg.sigma_floor, cfg.sigma_ceiling)
    else:
        b2, s2 = g_bias, g_sigma

    # Level 1: city + model + lead
    e1 = errors(c=city, m=model, l=lead)
    rs1 = _compute_raw_stats(e1, cfg.autocorr_discount)
    if rs1.bias is not None and rs1.sigma_raw is not None:
        b1 = _shrink(rs1.bias, b2, rs1.n_eff, cfg.shrinkage_k)
        s1 = _shrink(rs1.sigma_raw, s2, rs1.n_eff, cfg.shrinkage_k)
        s1 = _clamp_sigma(s1, cfg.sigma_floor, cfg.sigma_ceiling)
    else:
        b1, s1 = b2, s2

    # Level 0: city + model + lead + season
    e0 = errors(c=city, m=model, l=lead, s=season)
    rs0 = _compute_raw_stats(e0, cfg.autocorr_discount)
    if rs0.bias is not None and rs0.sigma_raw is not None:
        b0 = _shrink(rs0.bias, b1, rs0.n_eff, cfg.shrinkage_k)
        s0 = _shrink(rs0.sigma_raw, s1, rs0.n_eff, cfg.shrinkage_k)
        s0 = _clamp_sigma(s0, cfg.sigma_floor, cfg.sigma_ceiling)
    else:
        b0, s0 = b1, s1

    # Walk the hierarchy — return the most specific level with enough data.
    # Also evaluate the bias gate at the selected level.
    min_n = cfg.min_sample

    def _gate(bias_shrunk: float, rs: "_RawStats") -> tuple[bool, str]:
        return _check_wf_bias_gate(bias_shrunk, rs, cfg)

    if rs0.n >= min_n:
        gp, gr = _gate(b0, rs0)
        return b0, s0, 0, f"{city}/{model}/{lead}/{season}", gp, gr
    if rs1.n >= min_n:
        gp, gr = _gate(b1, rs1)
        return b1, s1, 1, f"{city}/{model}/{lead}/all-season", gp, gr
    if rs2.n >= min_n:
        gp, gr = _gate(b2, rs2)
        return b2, s2, 2, f"{city}/{model}/all-lead", gp, gr
    if rs3.n >= min_n:
        gp, gr = _gate(b3, rs3)
        return b3, s3, 3, f"global/{model}/{lead}", gp, gr
    # Use whatever we have at level 4 with shrinkage toward global
    if g_rs.n >= 1:
        gp, gr = _gate(g_bias, g_rs)
        return g_bias, g_sigma, 4, "global/prior", gp, gr
    return GLOBAL_PRIOR_BIAS, GLOBAL_PRIOR_SIGMA, 4, "hardcoded/prior", False, "no training data"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class WalkForwardRecord:
    """One test-set prediction and its outcome."""
    target_date:       str
    city:              str
    season:            str | None
    forecast_tmax_f:   float
    observed_tmax_f:   float
    bias_used:         float       # the shrunk bias (stored even when not applied)
    sigma_used:        float       # always applied
    mu_adjusted:       float       # = forecast + bias_used if bias_applied, else = forecast
    fallback_level:    int
    source_key:        str
    training_n:        int         # raw training records at time of prediction

    # Two-component architecture fields
    bias_applied:      bool        # True = bias was applied to mu; False = sigma only
    bias_suppressed_reason: str    # non-empty when bias_applied is False

    raw_error:         float       # observed - forecast (no sigma or bias)
    adj_error:         float       # observed - mu_adjusted (reflects bias gate decision)
    raw_abs:           float
    adj_abs:           float
    preload_hurt:      bool        # abs(adj_error) > abs(raw_error) — only meaningful when bias_applied
    crps_raw:          float       # CRPS with raw forecast as mean
    crps_adj:          float       # CRPS with mu_adjusted as mean (sigma always used)

    # P(observed >= forecast) — binary Brier event
    prob_adj:          float       # P(X >= forecast | mu_adj, sigma)
    brier_obs:         float       # (prob_adj - outcome)^2


@dataclass
class CalibrationBucket:
    bucket_lo:   float
    bucket_hi:   float
    count:       int
    empirical:   float   # fraction where event occurred
    mean_prob:   float


@dataclass
class WalkForwardSummary:
    """Aggregate metrics for one group (all, per-season, per-city)."""
    label:           str
    n:               int

    # MAE
    mae_raw:         float
    mae_adj:         float
    mae_delta:       float     # mae_adj - mae_raw; negative = improvement
    mae_ci95:        tuple[float, float]  # 95% CI on mae_adj

    # RMSE
    rmse_raw:        float
    rmse_adj:        float

    # Bias
    mean_error_raw:  float    # mean of (observed - forecast)
    mean_error_adj:  float    # mean of (observed - forecast - bias_used)
                               # ideal = 0.0

    # Calibration
    sigma_coverage_68pct: float  # fraction within ±1 sigma (ideal ≈ 0.68)
    sigma_coverage_95pct: float  # fraction within ±2 sigma (ideal ≈ 0.95)

    # CRPS
    crps_raw:        float
    crps_adj:        float

    # Brier score (binary: observed >= raw forecast)
    brier_score:     float    # mean((p_adj - outcome)^2)

    # Preload impact
    preload_hurt_n:  int      # records where adjustment made it worse
    preload_hurt_pct: float   # percentage

    # Bias gate: how often bias was actually applied (two-component architecture)
    bias_applied_n:  int      # records where bias correction was applied to mu
    bias_applied_pct: float   # percentage

    # Fallback distribution
    fallback_dist:   dict[int, int]    # level → count


@dataclass
class OutlierRecord:
    target_date: str
    city:        str
    season:      str | None
    forecast_f:  float
    observed_f:  float
    raw_error:   float
    adj_error:   float
    bias_used:   float
    sigma_used:  float
    z_score_raw: float   # raw_error / sigma_used
    preload_hurt: bool


@dataclass
class WalkForwardReport:
    """Full walk-forward validation output."""
    generated_at:    str
    config:          dict
    total_records:   int
    train_cutoff_n:  int   # = MIN_TRAIN_SIZE
    test_n:          int

    # Overall metrics
    overall:         WalkForwardSummary

    # Per-city summaries
    by_city:         dict[str, WalkForwardSummary]

    # Per-season summaries
    by_season:       dict[str, WalkForwardSummary]

    # Calibration
    calibration:     list[CalibrationBucket]

    # Outlier analysis: top N worst raw errors
    outliers:        list[OutlierRecord]

    # Records where preload made it worse
    preload_hurt_examples: list[OutlierRecord]

    # Verdict
    verdict:         str   # "improved" | "no_clear_improvement" | "mixed" | "insufficient_data"
    verdict_note:    str

    # Full per-record detail (can be large — included for API consumers who want it)
    records:         list[dict]


# ---------------------------------------------------------------------------
# Core validation function
# ---------------------------------------------------------------------------

def run_walk_forward_validation(
    all_records: Sequence[V3HistoricalRecord],
    *,
    config: V3StatsConfig | None = None,
    top_outliers: int = 20,
    top_hurt: int = 20,
) -> WalkForwardReport:
    """
    Run walk-forward validation entirely in-memory.

    Parameters
    ----------
    all_records : sequence of V3HistoricalRecord with quality_status == 'ok'
                  and signed_error populated.  Sorted by target_date ascending.
    config :      V3StatsConfig; defaults to conservative Phase 2 defaults.
    top_outliers: how many worst-raw-error records to include in the report.
    top_hurt:     how many preload-hurts-worst records to include.

    Returns
    -------
    WalkForwardReport
    """
    cfg = config or V3StatsConfig()
    started_at = datetime.now(timezone.utc)

    # ── Validate and sort ─────────────────────────────────────────────────
    usable = [
        r for r in all_records
        if (r.quality_status == "ok"
            and r.signed_error is not None
            and r.forecast_tmax_f is not None
            and r.observed_tmax_f is not None)
    ]
    usable.sort(key=lambda r: r.target_date)

    total_n = len(usable)
    if total_n < MIN_TRAIN_SIZE + 5:
        return _insufficient_report(total_n, cfg)

    # ── Walk-forward loop ─────────────────────────────────────────────────
    wf_records: list[WalkForwardRecord] = []

    for i in range(MIN_TRAIN_SIZE, total_n):
        test_rec = usable[i]
        training = usable[:i]  # strictly before index i

        bias, sigma, fallback_level, source_key, bias_gate_passed, bias_suppress_reason = (
            _compute_wf_prior(training, test_rec, cfg)
        )

        forecast = test_rec.forecast_tmax_f
        observed = test_rec.observed_tmax_f

        # Two-component architecture: sigma always applied; bias only when gate passes
        mu_adj = forecast + (bias if bias_gate_passed else 0.0)

        raw_err = observed - forecast
        adj_err = observed - mu_adj     # = raw_err when bias suppressed
        raw_abs = abs(raw_err)
        adj_abs = abs(adj_err)

        crps_raw = _crps_gaussian(observed, forecast, sigma)
        crps_adj = _crps_gaussian(observed, mu_adj,   sigma)

        # Brier event: did observed exceed the raw forecast?
        prob_adj = _prob_gte(forecast, mu_adj, sigma)
        outcome  = 1.0 if observed >= forecast else 0.0
        brier    = (prob_adj - outcome) ** 2

        wf_records.append(WalkForwardRecord(
            target_date            = test_rec.target_date,
            city                   = test_rec.city,
            season                 = test_rec.season,
            forecast_tmax_f        = forecast,
            observed_tmax_f        = observed,
            bias_used              = bias,
            sigma_used             = sigma,
            mu_adjusted            = mu_adj,
            fallback_level         = fallback_level,
            source_key             = source_key,
            training_n             = len(training),
            bias_applied           = bias_gate_passed,
            bias_suppressed_reason = bias_suppress_reason,
            raw_error              = raw_err,
            adj_error              = adj_err,
            raw_abs                = raw_abs,
            adj_abs                = adj_abs,
            preload_hurt           = adj_abs > raw_abs,
            crps_raw               = crps_raw,
            crps_adj               = crps_adj,
            prob_adj               = prob_adj,
            brier_obs              = brier,
        ))

    test_n = len(wf_records)
    if test_n == 0:
        return _insufficient_report(total_n, cfg)

    # ── Aggregate metrics ─────────────────────────────────────────────────
    all_summary  = _summarize("overall", wf_records, cfg)

    by_city: dict[str, WalkForwardSummary] = {}
    for city, group in _group_by(wf_records, lambda r: r.city).items():
        by_city[city] = _summarize(city, group, cfg)

    by_season: dict[str, WalkForwardSummary] = {}
    for season, group in _group_by(wf_records, lambda r: r.season or "unknown").items():
        by_season[season] = _summarize(season, group, cfg)

    # ── Calibration ───────────────────────────────────────────────────────
    calibration = _build_calibration(wf_records)

    # ── Outliers ──────────────────────────────────────────────────────────
    sigma_all = statistics.mean(r.sigma_used for r in wf_records) if wf_records else 1.0
    outliers = _build_outlier_list(wf_records, sigma_ref=sigma_all, n=top_outliers)

    hurt_records = [r for r in wf_records if r.preload_hurt]
    hurt_records.sort(key=lambda r: r.adj_abs - r.raw_abs, reverse=True)
    preload_hurt_examples = _build_outlier_list(hurt_records, sigma_ref=sigma_all, n=top_hurt)

    # ── Verdict ───────────────────────────────────────────────────────────
    verdict, verdict_note = _determine_verdict(all_summary, wf_records)

    # ── Per-record detail for API ──────────────────────────────────────────
    records_detail = [
        {
            "date":                   r.target_date,
            "city":                   r.city,
            "season":                 r.season,
            "forecast_f":             round(r.forecast_tmax_f, 2),
            "observed_f":             round(r.observed_tmax_f, 2),
            "raw_error":              round(r.raw_error, 3),
            "adj_error":              round(r.adj_error, 3),
            "bias_used":              round(r.bias_used, 3),
            "sigma_used":             round(r.sigma_used, 3),
            "fallback_level":         r.fallback_level,
            "training_n":             r.training_n,
            "bias_applied":           r.bias_applied,
            "bias_suppressed_reason": r.bias_suppressed_reason,
            "preload_hurt":           r.preload_hurt,
            "crps_raw":               round(r.crps_raw, 4),
            "crps_adj":               round(r.crps_adj, 4),
        }
        for r in wf_records
    ]

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    logger.info(
        "[V3 WalkForward] %d test records. MAE raw=%.2f adj=%.2f delta=%.2f°F "
        "CRPS raw=%.2f adj=%.2f  Preload hurt %d/%d (%.0f%%). Verdict: %s  (%.1fs)",
        test_n,
        all_summary.mae_raw, all_summary.mae_adj, all_summary.mae_delta,
        all_summary.crps_raw, all_summary.crps_adj,
        all_summary.preload_hurt_n, test_n, all_summary.preload_hurt_pct,
        verdict, elapsed,
    )

    return WalkForwardReport(
        generated_at   = started_at.isoformat(),
        config         = cfg.to_dict(),
        total_records  = total_n,
        train_cutoff_n = MIN_TRAIN_SIZE,
        test_n         = test_n,
        overall        = all_summary,
        by_city        = by_city,
        by_season      = by_season,
        calibration    = calibration,
        outliers       = outliers,
        preload_hurt_examples = preload_hurt_examples,
        verdict        = verdict,
        verdict_note   = verdict_note,
        records        = records_detail,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _group_by(records: list[WalkForwardRecord], key_fn) -> dict:
    out: dict[str, list[WalkForwardRecord]] = defaultdict(list)
    for r in records:
        out[key_fn(r)].append(r)
    return dict(out)


def _ci95_mean(values: list[float]) -> tuple[float, float]:
    """95% confidence interval for the mean via t-distribution approximation."""
    n = len(values)
    if n < 2:
        m = values[0] if values else 0.0
        return (m, m)
    m   = statistics.mean(values)
    sd  = statistics.stdev(values)
    # 1.96 is approximate; for n>=30 this is accurate
    t   = 1.96 if n >= 30 else 2.0 + 4.0 / (n - 1)
    se  = sd / math.sqrt(n)
    return (round(m - t * se, 4), round(m + t * se, 4))


def _summarize(
    label: str,
    records: list[WalkForwardRecord],
    cfg: V3StatsConfig,
) -> WalkForwardSummary:
    n = len(records)
    if n == 0:
        return WalkForwardSummary(
            label=label, n=0,
            mae_raw=0.0, mae_adj=0.0, mae_delta=0.0, mae_ci95=(0.0, 0.0),
            rmse_raw=0.0, rmse_adj=0.0,
            mean_error_raw=0.0, mean_error_adj=0.0,
            sigma_coverage_68pct=0.0, sigma_coverage_95pct=0.0,
            crps_raw=0.0, crps_adj=0.0,
            brier_score=0.0,
            preload_hurt_n=0, preload_hurt_pct=0.0,
            bias_applied_n=0, bias_applied_pct=0.0,
            fallback_dist={},
        )

    raw_errs = [r.raw_error for r in records]
    adj_errs = [r.adj_error for r in records]
    raw_abs  = [r.raw_abs   for r in records]
    adj_abs  = [r.adj_abs   for r in records]

    mae_raw = statistics.mean(raw_abs)
    mae_adj = statistics.mean(adj_abs)
    rmse_raw = math.sqrt(statistics.mean(e**2 for e in raw_errs))
    rmse_adj = math.sqrt(statistics.mean(e**2 for e in adj_errs))

    mean_err_raw = statistics.mean(raw_errs)
    mean_err_adj = statistics.mean(adj_errs)

    crps_raw = statistics.mean(r.crps_raw for r in records)
    crps_adj = statistics.mean(r.crps_adj for r in records)
    brier    = statistics.mean(r.brier_obs for r in records)

    # Sigma coverage
    within_1s = sum(1 for r in records if abs(r.adj_error) <= r.sigma_used)
    within_2s = sum(1 for r in records if abs(r.adj_error) <= 2.0 * r.sigma_used)
    cov68 = within_1s / n
    cov95 = within_2s / n

    hurt_n   = sum(1 for r in records if r.preload_hurt)
    hurt_pct = 100.0 * hurt_n / n

    bias_applied_n   = sum(1 for r in records if r.bias_applied)
    bias_applied_pct = 100.0 * bias_applied_n / n

    fallback_dist: dict[int, int] = defaultdict(int)
    for r in records:
        fallback_dist[r.fallback_level] += 1

    return WalkForwardSummary(
        label               = label,
        n                   = n,
        mae_raw             = round(mae_raw, 4),
        mae_adj             = round(mae_adj, 4),
        mae_delta           = round(mae_adj - mae_raw, 4),
        mae_ci95            = _ci95_mean(adj_abs),
        rmse_raw            = round(rmse_raw, 4),
        rmse_adj            = round(rmse_adj, 4),
        mean_error_raw      = round(mean_err_raw, 4),
        mean_error_adj      = round(mean_err_adj, 4),
        sigma_coverage_68pct= round(cov68, 4),
        sigma_coverage_95pct= round(cov95, 4),
        crps_raw            = round(crps_raw, 4),
        crps_adj            = round(crps_adj, 4),
        brier_score         = round(brier, 4),
        preload_hurt_n      = hurt_n,
        preload_hurt_pct    = round(hurt_pct, 1),
        bias_applied_n      = bias_applied_n,
        bias_applied_pct    = round(bias_applied_pct, 1),
        fallback_dist       = dict(fallback_dist),
    )


def _build_calibration(records: list[WalkForwardRecord]) -> list[CalibrationBucket]:
    """
    Build a reliability diagram: group predictions by P(X >= forecast)
    into 10 equal-width probability buckets [0.0, 0.1), [0.1, 0.2), …
    and compute empirical frequency within each bucket.

    Ideal calibration: empirical ≈ mean predicted probability in each bucket.
    """
    buckets = [(i / 10.0, (i + 1) / 10.0) for i in range(10)]
    result  = []
    for lo, hi in buckets:
        members = [r for r in records if lo <= r.prob_adj < hi]
        if not members:
            continue
        observed_count = sum(1 for r in members if r.observed_tmax_f >= r.forecast_tmax_f)
        empirical = observed_count / len(members)
        mean_prob = statistics.mean(r.prob_adj for r in members)
        result.append(CalibrationBucket(
            bucket_lo = lo,
            bucket_hi = hi,
            count     = len(members),
            empirical = round(empirical, 4),
            mean_prob = round(mean_prob, 4),
        ))
    return result


def _build_outlier_list(
    records: list[WalkForwardRecord],
    sigma_ref: float,
    n: int,
) -> list[OutlierRecord]:
    records_sorted = sorted(records, key=lambda r: r.raw_abs, reverse=True)
    return [
        OutlierRecord(
            target_date  = r.target_date,
            city         = r.city,
            season       = r.season,
            forecast_f   = round(r.forecast_tmax_f, 2),
            observed_f   = round(r.observed_tmax_f, 2),
            raw_error    = round(r.raw_error, 3),
            adj_error    = round(r.adj_error, 3),
            bias_used    = round(r.bias_used, 3),
            sigma_used   = round(r.sigma_used, 3),
            z_score_raw  = round(r.raw_error / r.sigma_used, 3) if r.sigma_used else 0.0,
            preload_hurt = r.preload_hurt,
        )
        for r in records_sorted[:n]
    ]


def _determine_verdict(
    summary: WalkForwardSummary,
    records: list[WalkForwardRecord],
) -> tuple[str, str]:
    """
    Determine a verdict based on evidence across multiple metrics.
    Errs toward honest reporting — uses 'no_clear_improvement' unless
    multiple independent metrics all agree improvement occurred.
    """
    if summary.n < 50:
        return "insufficient_data", (
            f"Only {summary.n} test records — too few for a reliable verdict. "
            f"At least 50 are needed."
        )

    signals: list[int] = []  # +1 = improvement, -1 = worse, 0 = neutral

    # MAE
    if summary.mae_delta < -0.1:
        signals.append(1)
    elif summary.mae_delta > 0.1:
        signals.append(-1)
    else:
        signals.append(0)

    # CRPS
    crps_delta = summary.crps_adj - summary.crps_raw
    if crps_delta < -0.05:
        signals.append(1)
    elif crps_delta > 0.05:
        signals.append(-1)
    else:
        signals.append(0)

    # Bias correction effectiveness
    # adj mean error closer to 0 than raw mean error?
    raw_bias_mag = abs(summary.mean_error_raw)
    adj_bias_mag = abs(summary.mean_error_adj)
    if adj_bias_mag < raw_bias_mag - 0.1:
        signals.append(1)
    elif adj_bias_mag > raw_bias_mag + 0.1:
        signals.append(-1)
    else:
        signals.append(0)

    pos = sum(1 for s in signals if s > 0)
    neg = sum(1 for s in signals if s < 0)

    mae_ci_lo, mae_ci_hi = summary.mae_ci95

    lines = [
        f"MAE: raw={summary.mae_raw:.2f}°F → adj={summary.mae_adj:.2f}°F "
        f"(Δ={summary.mae_delta:+.2f}°F, 95% CI [{mae_ci_lo:.2f}, {mae_ci_hi:.2f}]°F).",
        f"RMSE: raw={summary.rmse_raw:.2f}°F → adj={summary.rmse_adj:.2f}°F.",
        f"Bias: raw mean error={summary.mean_error_raw:+.2f}°F → "
        f"adj mean error={summary.mean_error_adj:+.2f}°F.",
        f"CRPS: raw={summary.crps_raw:.3f} → adj={summary.crps_adj:.3f}.",
        f"Brier score (binary event P>=forecast): {summary.brier_score:.4f}.",
        f"Coverage: 68%={summary.sigma_coverage_68pct:.1%} "
        f"(ideal 68%), 95%={summary.sigma_coverage_95pct:.1%} (ideal 95%).",
        f"Preload made it worse on {summary.preload_hurt_n}/{summary.n} records "
        f"({summary.preload_hurt_pct:.0f}%).",
    ]

    if pos >= 2 and neg == 0:
        verdict = "improved"
        lines.insert(0, (
            "The historical preload consistently improved calibration and accuracy."
        ))
    elif neg >= 2 and pos == 0:
        verdict = "no_clear_improvement"
        lines.insert(0, (
            "The historical preload did not improve accuracy. "
            "Bias correction moved predictions in the wrong direction more often than not."
        ))
    elif pos > neg:
        verdict = "mixed"
        lines.insert(0, (
            "The historical preload shows modest improvement on most metrics "
            "but not all. See per-metric breakdown below."
        ))
    else:
        verdict = "no_clear_improvement"
        lines.insert(0, (
            "The historical preload produced no consistent improvement across metrics."
        ))

    return verdict, " ".join(lines)


def _insufficient_report(total_n: int, cfg: V3StatsConfig) -> WalkForwardReport:
    empty_summary = WalkForwardSummary(
        label="overall", n=0,
        mae_raw=0.0, mae_adj=0.0, mae_delta=0.0, mae_ci95=(0.0, 0.0),
        rmse_raw=0.0, rmse_adj=0.0,
        mean_error_raw=0.0, mean_error_adj=0.0,
        sigma_coverage_68pct=0.0, sigma_coverage_95pct=0.0,
        crps_raw=0.0, crps_adj=0.0,
        brier_score=0.0,
        preload_hurt_n=0, preload_hurt_pct=0.0,
        bias_applied_n=0, bias_applied_pct=0.0,
        fallback_dist={},
    )
    return WalkForwardReport(
        generated_at   = datetime.now(timezone.utc).isoformat(),
        config         = cfg.to_dict(),
        total_records  = total_n,
        train_cutoff_n = MIN_TRAIN_SIZE,
        test_n         = 0,
        overall        = empty_summary,
        by_city        = {},
        by_season      = {},
        calibration    = [],
        outliers       = [],
        preload_hurt_examples = [],
        verdict        = "insufficient_data",
        verdict_note   = (
            f"Only {total_n} total 'ok' records found. "
            f"Walk-forward requires at least {MIN_TRAIN_SIZE + 5} records."
        ),
        records        = [],
    )
