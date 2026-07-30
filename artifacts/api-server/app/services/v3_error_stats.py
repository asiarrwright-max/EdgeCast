"""
V3 Error Stats — Historical Bias/Sigma Model (Phase 2)
=======================================================
Computes bias and sigma from V3HistoricalRecord rows and stores the results
in V3ErrorStats with full fallback hierarchy and shrinkage/partial pooling.

Design principles
-----------------
* Conservative:  historical preload is a *prior*, not ground truth.  Every
  estimate is shrunk toward a broader level so small seasonal samples cannot
  produce extreme values.
* Transparent:   every V3ErrorStats row records fallback_level and sample sizes
  so callers know how much evidence backed each estimate.
* Isolated:      no V1/V2/V2.1 table is read or written by this module.
* Governed:      sigma is always clamped to [SIGMA_FLOOR, SIGMA_CEILING].

Fallback hierarchy
------------------
  Level 0:  city + model + lead_bucket + season   (most specific)
  Level 1:  city + model + lead_bucket
  Level 2:  city + model
  Level 3:  model + lead_bucket                   (cross-city)
  Level 4:  global conservative prior

Shrinkage
---------
At each level, the local estimate is partially pooled toward the parent level:

    lambda   = n_eff / (n_eff + SHRINKAGE_K)
    bias_out = lambda * bias_local + (1 - lambda) * bias_parent
    sigma_out = max(SIGMA_FLOOR,
                    lambda * sigma_local + (1 - lambda) * sigma_parent)

SHRINKAGE_K = 30: at n_eff=30, lambda = 0.50 (equal weight local/parent).
                  at n_eff=60, lambda = 0.67 (2/3 local).
                  at n_eff=120, lambda = 0.80.

Effective N (autocorrelation discount)
---------------------------------------
Adjacent-day weather errors are correlated (~AR(1) with ρ ≈ 0.4–0.6).
We discount raw sample counts to avoid overstating evidence:

    n_eff = n_raw * AUTOCORR_DISCOUNT     (AUTOCORR_DISCOUNT = 0.6)

This is conservative: an AR(1) correction with ρ = 0.5 gives n_eff ≈ n/3,
so 0.6 is generous while still penalising tight consecutive samples.

Weighting — historical preload vs forward V3 learning
-------------------------------------------------------
In Phase 2 forward learning is always 0.0 (no live V3 observations yet).
V3StatsConfig stores the target weighting for Phase 3; the split is
visible in every V3PredictionSnapshot and analytics response so the
trade-off is never hidden.

All weights are in [0, 1] and must sum to 1.  ``hist_weight=1.0`` is the
safe Phase 2 default.
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_v3 import CURRENT_PRELOAD_VERSION, V3ErrorStats, V3HistoricalRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Governance constants — mirror V2.1 values where applicable
# ---------------------------------------------------------------------------

SIGMA_FLOOR:   float = 3.5   # °F — same as V2.1 probability_engine_v2.SIGMA_FLOOR
SIGMA_CEILING: float = 15.0  # °F — same as V2.1
MIN_SAMPLE:    int   = 30    # raw rows needed for a level to be considered usable
AUTOCORR_DISCOUNT: float = 0.6   # n_eff = n_raw * 0.6 to discount autocorrelation
SHRINKAGE_K:   float = 30.0  # pooling strength; at n_eff == K, lambda = 0.5

# Conservative global prior used as the root of the shrinkage tree.
# Set higher than V2.1 conservative prior because V3 data is GFS only (no
# multi-model averaging) and all timestamps are derived (not API-provided).
GLOBAL_PRIOR_BIAS:  float = 0.0    # °F — no prior directional assumption
GLOBAL_PRIOR_SIGMA: float = 6.0    # °F — conservative floor above SIGMA_FLOOR


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------

@dataclass
class V3StatsConfig:
    """
    All configurable weights for V3 bias/sigma estimation.

    Storing this as a config object keeps parameters visible in analytics
    responses rather than buried in code.  Phase 3 can adjust these without
    schema changes.

    hist_weight + forward_weight must equal 1.0.
    """
    hist_weight:    float = 1.0   # weight given to historical-preload stats
    forward_weight: float = 0.0   # weight given to forward V3 observations
    shrinkage_k:    float = SHRINKAGE_K
    autocorr_discount: float = AUTOCORR_DISCOUNT
    sigma_floor:    float = SIGMA_FLOOR
    sigma_ceiling:  float = SIGMA_CEILING
    min_sample:     int   = MIN_SAMPLE

    def __post_init__(self) -> None:
        total = round(self.hist_weight + self.forward_weight, 6)
        if abs(total - 1.0) > 1e-4:
            raise ValueError(
                f"hist_weight + forward_weight must equal 1.0; got {total}"
            )

    def to_dict(self) -> dict:
        return {
            "hist_weight":       self.hist_weight,
            "forward_weight":    self.forward_weight,
            "shrinkage_k":       self.shrinkage_k,
            "autocorr_discount": self.autocorr_discount,
            "sigma_floor":       self.sigma_floor,
            "sigma_ceiling":     self.sigma_ceiling,
            "min_sample":        self.min_sample,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _n_eff(n_raw: int, discount: float = AUTOCORR_DISCOUNT) -> float:
    """Return the effective sample size after autocorrelation discount."""
    return max(0.0, float(n_raw) * discount)


def _lambda(n_eff: float, k: float = SHRINKAGE_K) -> float:
    """Shrinkage coefficient: 0 = full parent, 1 = full local."""
    if n_eff <= 0:
        return 0.0
    return n_eff / (n_eff + k)


def _shrink(
    local: float,
    parent: float,
    n_eff: float,
    k: float = SHRINKAGE_K,
) -> float:
    lam = _lambda(n_eff, k)
    return lam * local + (1.0 - lam) * parent


def _safe_std(errors: list[float]) -> float | None:
    """Population-corrected std dev of errors; None if < 2 values."""
    if len(errors) < 2:
        return None
    try:
        return statistics.stdev(errors)
    except statistics.StatisticsError:
        return None


def _safe_mean(errors: list[float]) -> float | None:
    if not errors:
        return None
    return statistics.mean(errors)


def _clamp_sigma(sigma: float, floor: float = SIGMA_FLOOR, ceiling: float = SIGMA_CEILING) -> float:
    return max(floor, min(ceiling, sigma))


@dataclass
class _RawStats:
    """Raw (pre-shrinkage) statistics for one group of records."""
    n:           int
    n_eff:       float
    bias:        float | None   # mean(observed - forecast)
    sigma_raw:   float | None
    mae:         float | None
    rmse:        float | None


def _compute_raw_stats(errors: list[float], discount: float = AUTOCORR_DISCOUNT) -> _RawStats:
    """
    Compute raw stats from a list of signed errors (observed - forecast).
    Returns _RawStats; bias/sigma/mae/rmse are None when n < 2.
    """
    n = len(errors)
    ne = _n_eff(n, discount)

    if n == 0:
        return _RawStats(n=0, n_eff=0.0, bias=None, sigma_raw=None, mae=None, rmse=None)

    bias   = _safe_mean(errors)
    sigma  = _safe_std(errors)
    mae    = statistics.mean(abs(e) for e in errors) if errors else None
    rmse   = math.sqrt(statistics.mean(e * e for e in errors)) if errors else None

    return _RawStats(n=n, n_eff=ne, bias=bias, sigma_raw=sigma, mae=mae, rmse=rmse)


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

async def compute_v3_error_stats(
    session: AsyncSession,
    *,
    config: V3StatsConfig | None = None,
    preload_version: str = CURRENT_PRELOAD_VERSION,
) -> dict:
    """
    Build the full V3ErrorStats table from V3HistoricalRecord rows.

    Steps:
    1. Load all 'ok' V3HistoricalRecord rows for the given preload_version.
    2. Group by fallback level (level 0 → level 3) and compute raw stats.
    3. Apply shrinkage top-down (global → level 3 → level 2 → level 1 → level 0).
    4. Delete existing V3ErrorStats rows for this preload_version and insert fresh.
    5. Return a summary dict.

    Returns summary dict with row counts and timing.
    """
    cfg = config or V3StatsConfig()
    started_at = datetime.now(timezone.utc)

    # ── 1. Load records ────────────────────────────────────────────────────
    result = await session.execute(
        select(V3HistoricalRecord).where(
            V3HistoricalRecord.quality_status == "ok",
            V3HistoricalRecord.preload_version == preload_version,
            V3HistoricalRecord.signed_error.is_not(None),
        ).order_by(V3HistoricalRecord.target_date)
    )
    records: Sequence[V3HistoricalRecord] = result.scalars().all()

    if not records:
        return {
            "status": "no_data",
            "rows_inserted": 0,
            "note": "No 'ok' V3 historical records found.",
        }

    # ── 2. Build raw-stats groups ──────────────────────────────────────────
    # Key = (city, model, lead_bucket, season); value = list of signed_error

    from collections import defaultdict

    # Level 0: city + model + lead + season
    g0: dict[tuple, list[float]] = defaultdict(list)
    # Level 1: city + model + lead
    g1: dict[tuple, list[float]] = defaultdict(list)
    # Level 2: city + model
    g2: dict[tuple, list[float]] = defaultdict(list)
    # Level 3: model + lead
    g3: dict[tuple, list[float]] = defaultdict(list)
    # Level 4: global
    g_all: list[float] = []

    for rec in records:
        e = rec.signed_error
        city  = rec.city
        model = rec.forecast_model
        lead  = rec.lead_time_bucket
        season = rec.season  # may be None

        g0[(city, model, lead, season)].append(e)
        g1[(city, model, lead)].append(e)
        g2[(city, model)].append(e)
        g3[(model, lead)].append(e)
        g_all.append(e)

    # ── 3. Compute raw stats at each level ────────────────────────────────

    stats0 = {k: _compute_raw_stats(v, cfg.autocorr_discount) for k, v in g0.items()}
    stats1 = {k: _compute_raw_stats(v, cfg.autocorr_discount) for k, v in g1.items()}
    stats2 = {k: _compute_raw_stats(v, cfg.autocorr_discount) for k, v in g2.items()}
    stats3 = {k: _compute_raw_stats(v, cfg.autocorr_discount) for k, v in g3.items()}

    global_raw = _compute_raw_stats(g_all, cfg.autocorr_discount)
    global_bias  = global_raw.bias  if global_raw.bias  is not None else GLOBAL_PRIOR_BIAS
    global_sigma = global_raw.sigma_raw if global_raw.sigma_raw is not None else GLOBAL_PRIOR_SIGMA
    global_sigma = _clamp_sigma(global_sigma, cfg.sigma_floor, cfg.sigma_ceiling)

    # ── 4. Shrinkage (top-down: level 3 uses global, level 2 uses level 3 etc.) ──

    # Pre-compute shrunk values for levels 3 → 0 as dicts
    shrunk3: dict[tuple, tuple[float, float]] = {}   # (bias, sigma)
    for key, rs in stats3.items():
        if rs.bias is None or rs.sigma_raw is None:
            shrunk3[key] = (global_bias, global_sigma)
            continue
        ne  = rs.n_eff
        b   = _shrink(rs.bias, global_bias, ne, cfg.shrinkage_k)
        s   = _shrink(rs.sigma_raw, global_sigma, ne, cfg.shrinkage_k)
        shrunk3[key] = (b, _clamp_sigma(s, cfg.sigma_floor, cfg.sigma_ceiling))

    shrunk2: dict[tuple, tuple[float, float]] = {}
    for key2, rs in stats2.items():
        city, model = key2
        # Parent is cross-city level 3 — pick the closest lead bucket, or global
        # Use global as parent (level 3 is cross-city/specific-lead, hard to map)
        parent_b, parent_s = global_bias, global_sigma
        if rs.bias is None or rs.sigma_raw is None:
            shrunk2[key2] = (parent_b, parent_s)
            continue
        ne = rs.n_eff
        b  = _shrink(rs.bias, parent_b, ne, cfg.shrinkage_k)
        s  = _shrink(rs.sigma_raw, parent_s, ne, cfg.shrinkage_k)
        shrunk2[key2] = (b, _clamp_sigma(s, cfg.sigma_floor, cfg.sigma_ceiling))

    shrunk1: dict[tuple, tuple[float, float]] = {}
    for key1, rs in stats1.items():
        city, model, lead = key1
        parent_b, parent_s = shrunk2.get((city, model), (global_bias, global_sigma))
        if rs.bias is None or rs.sigma_raw is None:
            shrunk1[key1] = (parent_b, parent_s)
            continue
        ne = rs.n_eff
        b  = _shrink(rs.bias, parent_b, ne, cfg.shrinkage_k)
        s  = _shrink(rs.sigma_raw, parent_s, ne, cfg.shrinkage_k)
        shrunk1[key1] = (b, _clamp_sigma(s, cfg.sigma_floor, cfg.sigma_ceiling))

    shrunk0: dict[tuple, tuple[float, float]] = {}
    for key0, rs in stats0.items():
        city, model, lead, season = key0
        parent_b, parent_s = shrunk1.get((city, model, lead), (global_bias, global_sigma))
        if rs.bias is None or rs.sigma_raw is None:
            shrunk0[key0] = (parent_b, parent_s)
            continue
        ne = rs.n_eff
        b  = _shrink(rs.bias, parent_b, ne, cfg.shrinkage_k)
        s  = _shrink(rs.sigma_raw, parent_s, ne, cfg.shrinkage_k)
        shrunk0[key0] = (b, _clamp_sigma(s, cfg.sigma_floor, cfg.sigma_ceiling))

    # ── 5. Write to DB (delete + insert) ─────────────────────────────────
    await session.execute(
        delete(V3ErrorStats).where(V3ErrorStats.preload_version == preload_version)
    )
    await session.flush()

    rows_to_insert: list[V3ErrorStats] = []
    now = datetime.now(timezone.utc)

    def _add_row(
        *,
        city: str,
        model: str,
        lead: str,
        season: str | None,
        fallback_level: int,
        rs: _RawStats,
        bias_shrunk: float,
        sigma_shrunk: float,
    ) -> None:
        rows_to_insert.append(V3ErrorStats(
            last_computed_at=now,
            preload_version=preload_version,
            city=city,
            model=model,
            lead_time_bucket=lead,
            season=season,
            fallback_level=fallback_level,
            raw_sample_size=rs.n,
            effective_n=round(rs.n_eff, 2),
            bias=round(bias_shrunk, 4),
            sigma_raw=round(rs.sigma_raw, 4) if rs.sigma_raw is not None else None,
            sigma_shrunk=round(sigma_shrunk, 4),
            mae=round(rs.mae, 4) if rs.mae is not None else None,
            rmse=round(rs.rmse, 4) if rs.rmse is not None else None,
        ))

    # Level 0: city + model + lead + season
    for (city, model, lead, season), rs in stats0.items():
        bs, ss = shrunk0.get((city, model, lead, season), (global_bias, global_sigma))
        _add_row(
            city=city, model=model, lead=lead, season=season,
            fallback_level=0, rs=rs, bias_shrunk=bs, sigma_shrunk=ss,
        )

    # Level 1: city + model + lead
    for (city, model, lead), rs in stats1.items():
        bs, ss = shrunk1.get((city, model, lead), (global_bias, global_sigma))
        _add_row(
            city=city, model=model, lead=lead, season=None,
            fallback_level=1, rs=rs, bias_shrunk=bs, sigma_shrunk=ss,
        )

    # Level 2: city + model
    for (city, model), rs in stats2.items():
        bs, ss = shrunk2.get((city, model), (global_bias, global_sigma))
        _add_row(
            city=city, model=model, lead="__all__", season=None,
            fallback_level=2, rs=rs, bias_shrunk=bs, sigma_shrunk=ss,
        )

    # Level 3: model + lead (cross-city)
    for (model, lead), rs in stats3.items():
        bs, ss = shrunk3.get((model, lead), (global_bias, global_sigma))
        _add_row(
            city="__global__", model=model, lead=lead, season=None,
            fallback_level=3, rs=rs, bias_shrunk=bs, sigma_shrunk=ss,
        )

    # Level 4: global conservative prior (always inserted as anchor)
    rows_to_insert.append(V3ErrorStats(
        last_computed_at=now,
        preload_version=preload_version,
        city="__global__",
        model="__all__",
        lead_time_bucket="__all__",
        season=None,
        fallback_level=4,
        raw_sample_size=global_raw.n,
        effective_n=round(global_raw.n_eff, 2),
        bias=round(global_bias, 4),
        sigma_raw=round(global_raw.sigma_raw, 4) if global_raw.sigma_raw else None,
        sigma_shrunk=round(_clamp_sigma(global_sigma, cfg.sigma_floor, cfg.sigma_ceiling), 4),
        mae=round(global_raw.mae, 4) if global_raw.mae else None,
        rmse=round(global_raw.rmse, 4) if global_raw.rmse else None,
    ))

    session.add_all(rows_to_insert)
    await session.commit()

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    logger.info(
        "[V3 ErrorStats] Computed %d rows (level 0=%d, 1=%d, 2=%d, 3=%d, 4=1) "
        "from %d records in %.1fs",
        len(rows_to_insert),
        len(stats0), len(stats1), len(stats2), len(stats3),
        len(records), elapsed,
    )

    return {
        "status": "ok",
        "rows_inserted": len(rows_to_insert),
        "input_records": len(records),
        "elapsed_seconds": round(elapsed, 2),
        "config": cfg.to_dict(),
        "level_counts": {
            "level_0_city_model_lead_season": len(stats0),
            "level_1_city_model_lead":        len(stats1),
            "level_2_city_model":             len(stats2),
            "level_3_model_lead_global":      len(stats3),
            "level_4_global_prior":           1,
        },
        "global_stats": {
            "n":    global_raw.n,
            "bias": round(global_bias, 4),
            "sigma": round(global_sigma, 4),
            "mae":  round(global_raw.mae, 4) if global_raw.mae else None,
            "rmse": round(global_raw.rmse, 4) if global_raw.rmse else None,
        },
    }


# ---------------------------------------------------------------------------
# Lookup — get the best available stats for a single prediction
# ---------------------------------------------------------------------------

@dataclass
class V3Prior:
    """
    The bias and sigma to apply for one prediction, with full provenance.
    Sigma is always >= SIGMA_FLOOR and <= SIGMA_CEILING.
    """
    bias:           float
    sigma:          float
    fallback_level: int    # 0–4; 4 = global conservative prior
    raw_n:          int    # raw sample count backing this estimate
    effective_n:    float  # autocorrelation-discounted sample count
    source_key:     str    # human-readable group that provided the estimate


async def get_v3_prior(
    session: AsyncSession,
    *,
    city: str,
    model: str,
    lead_bucket: str,
    season: str | None,
    preload_version: str = CURRENT_PRELOAD_VERSION,
    config: V3StatsConfig | None = None,
) -> V3Prior:
    """
    Return the best V3Prior for a prediction, walking the fallback hierarchy.

    Hierarchy:
      0: city + model + lead_bucket + season
      1: city + model + lead_bucket (all-season)
      2: city + model (any lead)
      3: model + lead_bucket (cross-city)
      4: global prior

    At each level, a row must have raw_sample_size >= config.min_sample to
    be considered usable.  If no level qualifies, the level-4 global row is
    returned (or the hard-coded conservative prior if even that is missing).
    """
    cfg = config or V3StatsConfig()

    # Build candidate queries in priority order
    candidates: list[tuple[int, dict]] = [
        (0, dict(city=city, model=model, lead_time_bucket=lead_bucket,
                 season=season,       fallback_level=0)),
        (1, dict(city=city, model=model, lead_time_bucket=lead_bucket,
                 season=None,         fallback_level=1)),
        (2, dict(city=city, model=model, lead_time_bucket="__all__",
                 season=None,         fallback_level=2)),
        (3, dict(city="__global__", model=model, lead_time_bucket=lead_bucket,
                 season=None,         fallback_level=3)),
        (4, dict(city="__global__", model="__all__", lead_time_bucket="__all__",
                 season=None,         fallback_level=4)),
    ]

    for level, filters in candidates:
        q = select(V3ErrorStats).where(
            V3ErrorStats.preload_version == preload_version,
            *[
                getattr(V3ErrorStats, k) == v
                if v is not None
                else getattr(V3ErrorStats, k).is_(None)
                for k, v in filters.items()
            ]
        ).limit(1)
        result = await session.execute(q)
        row: V3ErrorStats | None = result.scalar_one_or_none()

        if row is None:
            continue
        if level < 4 and row.raw_sample_size < cfg.min_sample:
            continue  # not enough data at this level — fall through

        # Use sigma_shrunk (already clamped); if missing fall through
        if row.sigma_shrunk is None:
            continue
        if row.bias is None:
            continue

        label_map = {
            0: f"{city}/{model}/{lead_bucket}/{season}",
            1: f"{city}/{model}/{lead_bucket}/all-season",
            2: f"{city}/{model}/all-lead",
            3: f"global/{model}/{lead_bucket}",
            4: "global/prior",
        }
        return V3Prior(
            bias=float(row.bias),
            sigma=float(row.sigma_shrunk),
            fallback_level=level,
            raw_n=row.raw_sample_size,
            effective_n=float(row.effective_n or 0.0),
            source_key=label_map[level],
        )

    # Absolute fallback — conservative prior if DB is empty
    return V3Prior(
        bias=GLOBAL_PRIOR_BIAS,
        sigma=_clamp_sigma(GLOBAL_PRIOR_SIGMA, cfg.sigma_floor, cfg.sigma_ceiling),
        fallback_level=4,
        raw_n=0,
        effective_n=0.0,
        source_key="hardcoded/prior",
    )
