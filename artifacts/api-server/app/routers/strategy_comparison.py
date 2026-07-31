"""
Strategy Comparison API
=======================
GET /analytics/strategy-comparison

Returns a unified side-by-side view of V2.1, V2.2, and V3 predictions/trades
for the Strategy Comparison dashboard page.

Design rules
------------
- V2.1 is labelled "Original learning version".
- V2.2 is labelled "Corrected-bias version".
- V3  is labelled "Historical-preload version".
- Official ROI / win rate / Brier score use EXECUTABLE trades only.
- Non-executable signals are in a separate section and excluded from headline metrics.
- is_paired = True when V2.1, V2.2, AND V3 all reference the same
  comparison_snapshot_id (i.e. all three ran in the same collection cycle
  and used identical quote + forecast inputs).
- Unpaired rows are useful operational history but excluded from
  direct model-versus-model conclusions.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import AppSetting, PaperTrade
from app.models_v3 import V3PaperTrade, V3PredictionSnapshot

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Strategy Comparison"])

# Readiness milestones: shared settled executable trades
_READINESS_MILESTONES = [50, 100, 250, 500]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fee_est(side_price: float | None, quantity: float | None) -> float | None:
    if side_price is None or quantity is None or quantity <= 0:
        return None
    return round(max(0.01, 0.035 * min(side_price, 1.0 - side_price) * quantity), 4)


def _brier(trades: list, *, use_ec_yes: bool = True) -> float | None:
    settled = [t for t in trades if getattr(t, "status", None) == "SETTLED"
               and getattr(t, "outcome", None) in ("WIN", "LOSS")]
    if not settled:
        return None
    scores = []
    for t in settled:
        p = getattr(t, "ec_yes_probability", None)
        p = p if p is not None else 0.5
        direction = getattr(t, "direction", "YES")
        outcome   = getattr(t, "outcome",    "LOSS")
        actual_yes = (1.0 if outcome == "WIN" else 0.0) if direction == "YES" \
                     else (0.0 if outcome == "WIN" else 1.0)
        scores.append((p - actual_yes) ** 2)
    return round(sum(scores) / len(scores), 4)


def _preliminary_leader(
    v21_trades: list,
    v22_trades: list,
    v3_trades:  list,
) -> dict[str, Any]:
    """
    Rank the three strategies using *only* strictly-paired, executable, settled
    trades with net P/L after fees.

    "Strictly paired" = all three strategies share the same non-NULL
    comparison_snapshot_id (same collection cycle, identical frozen inputs).
    By that construction, every strategy has the same trade count N — so a
    strategy with a lucky small sample can never leapfrog another on volume
    grounds alone.

    Composite score (min-max normalised across the three strategies):
        35 % net ROI after fees  (higher = better)
        25 % Brier score         (lower  = better)
        25 % win rate            (higher = better)
        15 % city consistency    (higher = better)
    """
    _TIERS = [
        (500, "strong",       "Strong evidence"),
        (250, "meaningful",   "Meaningful leader"),
        (100, "emerging",     "Emerging leader"),
        (50,  "preliminary",  "Preliminary leader"),
        (10,  "very_early",   "Very early leader"),
        (1,   "very_early",   "Very early leader"),
        (0,   "insufficient", "Not enough data to rank"),
    ]
    _MILESTONES = [10, 50, 100, 250, 500]

    _CAVEATS = [
        "Too early to declare a winner.",
        "Rankings may change as more trades settle.",
        "Only strictly paired, executable, settled trades are included.",
    ]

    # ── Find strictly-paired settled-executable tickers ───────────────────
    def _se_by_ticker(trades):
        return {
            t.market_ticker: t for t in trades
            if t.status == "SETTLED" and t.is_executable is True
        }

    v21_se = _se_by_ticker(v21_trades)
    v22_se = _se_by_ticker(v22_trades)
    v3_se  = _se_by_ticker(v3_trades)

    paired_tickers: list[str] = []
    for ticker in set(v21_se) & set(v22_se) & set(v3_se):
        t21 = v21_se[ticker]
        t22 = v22_se[ticker]
        t3  = v3_se[ticker]
        sid = t21.comparison_snapshot_id
        if sid and sid == t22.comparison_snapshot_id == t3.comparison_snapshot_id:
            paired_tickers.append(ticker)

    n = len(paired_tickers)

    # Confidence tier
    tier_key, tier_label = "insufficient", "Not enough data to rank"
    for threshold, key, label in _TIERS:
        if n >= threshold:
            tier_key, tier_label = key, label
            break

    # Next milestone
    next_milestone = next((m for m in _MILESTONES if m > n), None)
    next_remaining = (next_milestone - n) if next_milestone else 0

    if n == 0:
        return {
            "n_paired_settled_exec":    0,
            "confidence_tier":          tier_key,
            "confidence_label":         tier_label,
            "next_milestone":           next_milestone,
            "next_milestone_remaining": next_remaining,
            "ranked":                   None,
            "caveats":                  _CAVEATS,
        }

    # ── Per-strategy metrics ──────────────────────────────────────────────
    def _strat_metrics(se_map: dict, label: str) -> dict[str, Any]:
        trades = [se_map[t] for t in paired_tickers]
        stake   = sum(getattr(t, "stake", 0) or 0 for t in trades)
        gross   = sum(getattr(t, "profit_loss", 0) or 0 for t in trades)
        wins    = sum(1 for t in trades if getattr(t, "outcome", "") == "WIN")
        fees    = sum(
            f for t in trades
            if (f := _fee_est(
                getattr(t, "side_market_price", None),
                getattr(t, "quantity", None),
            )) is not None
        )
        net_pl        = gross - fees
        net_roi_pct   = round(100 * net_pl / stake, 2) if stake > 0 else None
        win_rate_pct  = round(100 * wins / n, 1)

        # City consistency: fraction of cities traded that have ≥ 1 win
        cities_traded   = {getattr(t, "city", None) for t in trades if getattr(t, "city", None)}
        cities_with_win = {
            getattr(t, "city", None) for t in trades
            if getattr(t, "city", None) and getattr(t, "outcome", "") == "WIN"
        }
        city_n = len(cities_traded)
        city_consistency_pct = (
            round(100 * len(cities_with_win) / city_n, 1) if city_n else None
        )

        return {
            "label":                label,
            "net_roi_pct":          net_roi_pct,
            "win_rate_pct":         win_rate_pct,
            "brier_score":          _brier(trades),
            "city_consistency_pct": city_consistency_pct,
            "city_n":               city_n,
        }

    strats = [
        ("v21", _strat_metrics(v21_se, "V2.1")),
        ("v22", _strat_metrics(v22_se, "V2.2")),
        ("v3",  _strat_metrics(v3_se,  "V3")),
    ]

    # ── Min-max normalisation (across the 3 strategies) ───────────────────
    def _minmax(vals: list, higher_better: bool = True) -> list:
        """Normalise to [0, 1]. Returns 0.5 for all when all equal or all None."""
        valid = [(i, v) for i, v in enumerate(vals) if v is not None]
        if len(valid) < 2:
            return [0.5 if v is not None else None for v in vals]
        lo = min(v for _, v in valid)
        hi = max(v for _, v in valid)
        result: list = [None] * len(vals)
        for i, v in enumerate(vals):
            if v is None:
                result[i] = None
            elif hi == lo:
                result[i] = 0.5
            else:
                norm = (v - lo) / (hi - lo)
                result[i] = norm if higher_better else 1.0 - norm
        return result

    W_ROI, W_BRIER, W_WR, W_CITY = 0.35, 0.25, 0.25, 0.15

    roi_n   = _minmax([m["net_roi_pct"]         for _, m in strats], higher_better=True)
    brier_n = _minmax([m["brier_score"]          for _, m in strats], higher_better=False)
    wr_n    = _minmax([m["win_rate_pct"]         for _, m in strats], higher_better=True)
    city_n_ = _minmax([m["city_consistency_pct"] for _, m in strats], higher_better=True)

    composite_scores: list[float | None] = []
    for i in range(3):
        components = [
            (W_ROI,   roi_n[i]),
            (W_BRIER, brier_n[i]),
            (W_WR,    wr_n[i]),
            (W_CITY,  city_n_[i]),
        ]
        available = [(w, s) for w, s in components if s is not None]
        if not available:
            composite_scores.append(None)
        else:
            total_w = sum(w for w, _ in available)
            composite_scores.append(
                round(sum(w * s for w, s in available) / total_w, 4)
            )

    # ── Reason generation ─────────────────────────────────────────────────
    _METRIC_REASONS = {
        "roi":   ("Better net ROI after fees",            "Lower net ROI after fees"),
        "brier": ("Better probability accuracy (Brier)",  "Higher forecast error (Brier)"),
        "wr":    ("Higher win rate",                      "Lower win rate"),
        "city":  ("More consistent across cities",        "Less consistent across cities"),
    }

    def _reasons(i: int, score_rank: int) -> list[str]:
        """
        score_rank: 0 = leader, 1 = middle, 2 = trailing.
        Picks 1-2 most salient reasons based on normalised metric positions.
        """
        reasons: list[str] = []
        if n < 10:
            reasons.append("Small sample — interpret with caution")

        norms = {
            "roi":   roi_n[i],
            "brier": brier_n[i],
            "wr":    wr_n[i],
            "city":  city_n_[i],
        }

        if score_rank == 0:   # leader
            top = sorted(
                [(k, v) for k, v in norms.items() if v is not None and v > 0.5],
                key=lambda kv: -kv[1],
            )
            for k, _ in top[:2]:
                reasons.append(_METRIC_REASONS[k][0])
            if not [r for r in reasons if "sample" not in r]:
                reasons.append("Marginally ahead on composite score")

        elif score_rank == 2:  # trailing
            bottom = sorted(
                [(k, v) for k, v in norms.items() if v is not None and v < 0.5],
                key=lambda kv: kv[1],
            )
            for k, _ in bottom[:2]:
                reasons.append(_METRIC_REASONS[k][1])
            if not [r for r in reasons if "sample" not in r]:
                reasons.append("Marginally behind on composite score")

        else:                   # middle
            reasons.append("Mid-range performance across metrics")

        return reasons[:3]

    # Sort descending by score; stable tie-break preserves v21 > v22 > v3
    indexed = sorted(
        enumerate(composite_scores),
        key=lambda x: (x[1] is None, -(x[1] or 0)),
    )

    ranked: list[dict[str, Any]] = []
    for score_rank, (i, score) in enumerate(indexed):
        key, m = strats[i]
        ranked.append({
            "rank":                  score_rank + 1,
            "strategy":              key,
            "label":                 m["label"],
            "composite_score":       score,
            "net_roi_pct":           m["net_roi_pct"],
            "win_rate_pct":          m["win_rate_pct"],
            "brier_score":           m["brier_score"],
            "city_consistency_pct":  m["city_consistency_pct"],
            "n":                     n,
            "reasons":               _reasons(i, score_rank),
        })

    # One-sentence headline reason from the leader's non-sample reasons
    leader_reasons = [r for r in ranked[0]["reasons"] if "sample" not in r.lower()]
    headline = leader_reasons[0] if leader_reasons else "Composite scores are very close."

    return {
        "n_paired_settled_exec":    n,
        "confidence_tier":          tier_key,
        "confidence_label":         tier_label,
        "next_milestone":           next_milestone,
        "next_milestone_remaining": next_remaining,
        "headline_reason":          headline,
        "ranked":                   ranked,
        "caveats":                  _CAVEATS,
    }


def _strategy_summary(
    trades: list,
    *,
    label: str,
    description: str,
    executable_only_official: bool = True,
) -> dict[str, Any]:
    """
    Compute per-strategy performance summary.
    ``official`` metrics always use executable trades only.
    """
    def _sec(subset: list, official: bool) -> dict[str, Any]:
        active   = [t for t in subset if getattr(t, "status", "") not in ("V2_EXCLUDED",)]
        settled  = [t for t in active  if getattr(t, "status", "") == "SETTLED"]
        open_    = [t for t in active  if getattr(t, "status", "") == "OPEN"]
        wins     = sum(1 for t in settled if getattr(t, "outcome", "") == "WIN")
        losses   = sum(1 for t in settled if getattr(t, "outcome", "") == "LOSS")

        # ── Stake: split settled vs open so ROI uses the right denominator ──
        # Bug fix: the old code summed over ALL active trades, mixing settled
        # capital (already resolved) with open capital (still deployed).
        # ROI = settled gross P/L / settled stake only.
        settled_stake = sum(getattr(t, "stake", 0) or 0 for t in settled)
        open_stake    = sum(getattr(t, "stake", 0) or 0 for t in open_)

        gross_pl = sum(getattr(t, "profit_loss", 0) or 0 for t in settled)

        # ── Fees: settled trades only ────────────────────────────────────────
        # Bug fix: the old code accumulated fees over all active trades and
        # subtracted them from settled gross P/L, which mixed open-trade
        # estimates into a settled-only P/L figure.
        # estimated_fees = settled fees → subtracted to get net_pl.
        # open_fees      = open fees   → informational, not deducted here.
        def _fee_sum(tlist: list) -> float:
            return sum(
                f for t in tlist
                if (f := _fee_est(
                    getattr(t, "side_market_price", None),
                    getattr(t, "quantity", None),
                )) is not None
            )

        settled_fees = _fee_sum(settled)
        open_fees    = _fee_sum(open_)
        net_pl       = round(gross_pl - settled_fees, 4)

        edges  = [getattr(t, "edge_pct_points", None) for t in active
                  if getattr(t, "edge_pct_points", None) is not None]
        sigmas = [getattr(t, "sigma_used", None) or
                  getattr(t, "final_sigma", None)
                  for t in active]
        sigmas = [s for s in sigmas if s is not None]
        return {
            "count":              len(active),
            "open":               len(open_),
            "settled":            len(settled),
            "wins":               wins,
            "losses":             losses,
            "win_rate_pct":       round(100 * wins / len(settled), 1) if settled else None,
            # Settled P/L block — all three figures are settled-only
            "settled_stake":      round(settled_stake, 2),
            "gross_pl":           round(gross_pl, 2),
            "estimated_fees":     round(settled_fees, 4),  # settled only
            "net_pl":             net_pl,                  # gross_pl − settled_fees
            # Both ROI variants use settled_stake as denominator.
            # Gross ROI = gross_pl / settled_stake (before fees).
            # Net ROI   = net_pl   / settled_stake (after fees); may exceed −100 %.
            "gross_roi_pct":      round(100 * gross_pl / settled_stake, 1)
                                  if settled_stake > 0 and settled else None,
            "net_roi_pct":        round(100 * net_pl   / settled_stake, 1)
                                  if settled_stake > 0 and settled else None,
            "brier_score":        _brier(settled) if official and settled else None,
            # Open capital — informational; never mixed into settled P/L
            "open_stake":         round(open_stake, 2),
            "open_fees":          round(open_fees, 4),
            # Legacy: settled + open (kept for display total)
            "total_stake":        round(settled_stake + open_stake, 2),
            "avg_edge_pp":        round(sum(edges) / len(edges), 2) if edges else None,
            "avg_sigma":          round(sum(sigmas) / len(sigmas), 3) if sigmas else None,
            "is_official":        official,
        }

    exec_trades     = [t for t in trades if getattr(t, "is_executable", None) is True]
    non_exec_trades = [t for t in trades if getattr(t, "is_executable", None) is not True
                       and getattr(t, "status", "") != "V2_EXCLUDED"]
    excluded_trades = [t for t in trades if getattr(t, "status", "") == "V2_EXCLUDED"]

    return {
        "label":       label,
        "description": description,
        "total_predictions": len(trades),
        "executable":       _sec(exec_trades,     official=True),
        "non_executable":   _sec(non_exec_trades, official=False),
        "excluded_count":   len(excluded_trades),
        "official_note": (
            "Official ROI, win rate, and Brier score use EXECUTABLE trades only."
        ),
    }


def _v2_trade_row(t: PaperTrade) -> dict[str, Any]:
    return {
        "ticker":                t.market_ticker,
        "city":                  t.city,
        "weather_variable":      t.weather_variable,
        "contract_type":         t.contract_type,
        "market_prob":           t.market_yes_probability,
        "ec_prob":               t.ec_yes_probability,
        "edge_pp":               t.edge_pct_points,
        "direction":             t.direction,
        "is_executable":         t.is_executable,
        "sigma":                 t.sigma_used,
        "bias":                  t.bias_correction,
        "fallback":              t.fallback_level,
        "status":                t.status,
        "outcome":               t.outcome,
        "profit_loss":           t.profit_loss,
        "comparison_snapshot_id": t.comparison_snapshot_id,
        "collection_batch_id":   t.collection_batch_id,
    }


def _v3_trade_row(t: V3PaperTrade) -> dict[str, Any]:
    return {
        "ticker":                t.market_ticker,
        "city":                  t.city,
        "weather_variable":      t.weather_variable,
        "contract_type":         t.contract_type,
        "market_prob":           t.market_yes_probability,
        "ec_prob":               t.ec_yes_probability,
        "edge_pp":               t.edge_pct_points,
        "direction":             t.direction,
        "is_executable":         t.is_executable,
        "sigma":                 t.final_sigma,
        "bias":                  t.final_bias,
        "fallback":              str(t.fallback_level_used) if t.fallback_level_used is not None else None,
        "status":                t.status,
        "outcome":               t.outcome,
        "profit_loss":           t.profit_loss,
        "hist_sample_count":     t.hist_sample_count,
        "comparison_snapshot_id": t.comparison_snapshot_id,
        "collection_batch_id":   t.collection_batch_id,
    }


def _is_paired(
    v21: PaperTrade | None,
    v22: PaperTrade | None,
    v3:  V3PaperTrade | None,
) -> bool:
    """
    A market row is strictly paired when all three strategies evaluated it in
    the same collection cycle using the same frozen ComparisonSnapshot.
    Requires all three present AND sharing the same non-NULL snapshot id.
    """
    if not (v21 and v22 and v3):
        return False
    sid = v21.comparison_snapshot_id
    if sid is None:
        return False
    return sid == v22.comparison_snapshot_id == v3.comparison_snapshot_id


# ---------------------------------------------------------------------------
# GET /analytics/strategy-comparison
# ---------------------------------------------------------------------------

@router.get("/analytics/strategy-comparison")
async def get_strategy_comparison(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Unified cross-strategy comparison for V2.1, V2.2, and V3.

    Returns:
      flags              — current feature flag states
      strategies         — per-strategy summary (executable + non-executable)
      shared_markets     — markets appearing in ≥2 strategies (joined on ticker)
      all_market_rows    — full market table (all strategies, all tickers)
      pairing_stats      — counts of strictly-paired vs unpaired market rows
      readiness_tracker  — progress toward settled-trade milestones
    """
    # ---- Load flags -------------------------------------------------------
    flag_keys = [
        "v2.2.predictions_enabled", "v2.2.paper_trading_enabled",
        "v3.predictions_enabled",   "v3.paper_trading_enabled",
    ]
    flags_result = await session.execute(
        select(AppSetting).where(AppSetting.key.in_(flag_keys))
    )
    flags_map = {r.key: r.value for r in flags_result.scalars().all()}

    def _flag(key: str) -> bool:
        return (flags_map.get(key, "false") or "false").lower() in ("true", "1", "yes")

    flags_out = {
        "v21": {
            "predictions_enabled": True,    # always on
            "paper_trading_enabled": True,  # always on
        },
        "v22": {
            "predictions_enabled":   _flag("v2.2.predictions_enabled"),
            "paper_trading_enabled": _flag("v2.2.paper_trading_enabled"),
        },
        "v3": {
            "predictions_enabled":   _flag("v3.predictions_enabled"),
            "paper_trading_enabled": _flag("v3.paper_trading_enabled"),
        },
    }

    # ---- Load trades ------------------------------------------------------
    v21_result = await session.execute(
        select(PaperTrade).where(PaperTrade.strategy_version == "v2.1")
        .order_by(PaperTrade.id.desc())
    )
    v21_trades = list(v21_result.scalars().all())

    v22_result = await session.execute(
        select(PaperTrade).where(PaperTrade.strategy_version == "v2.2")
        .order_by(PaperTrade.id.desc())
    )
    v22_trades = list(v22_result.scalars().all())

    v3_result = await session.execute(
        select(V3PaperTrade).order_by(V3PaperTrade.id.desc())
    )
    v3_trades = list(v3_result.scalars().all())

    # ---- Summaries --------------------------------------------------------
    strategies = {
        "v21": _strategy_summary(
            v21_trades,
            label="V2.1",
            description="Original learning version — station-verified, sigma from fixed table with city override.",
        ),
        "v22": _strategy_summary(
            v22_trades,
            label="V2.2",
            description="Corrected-bias version — same as V2.1 but bias sign fixed (mu += bias). "
                        "Bias activates when a forecast_error_stats bucket reaches MIN_SAMPLE = 30.",
        ),
        "v3": _strategy_summary(
            v3_trades,
            label="V3",
            description="Historical-preload version — sigma and bias from walk-forward trained model. "
                        "is_executable=False signals excluded from official ROI.",
        ),
    }

    # ---- Market-level table ----------------------------------------------
    v21_by_ticker: dict[str, PaperTrade]   = {t.market_ticker: t for t in v21_trades}
    v22_by_ticker: dict[str, PaperTrade]   = {t.market_ticker: t for t in v22_trades}
    v3_by_ticker:  dict[str, V3PaperTrade] = {t.market_ticker: t for t in v3_trades}

    all_tickers = sorted(
        set(v21_by_ticker) | set(v22_by_ticker) | set(v3_by_ticker)
    )

    market_rows: list[dict[str, Any]] = []
    paired_count = 0
    unpaired_count = 0

    for ticker in all_tickers:
        v21 = v21_by_ticker.get(ticker)
        v22 = v22_by_ticker.get(ticker)
        v3  = v3_by_ticker.get(ticker)

        city  = (v21 or v22 or v3).city if (v21 or v22 or v3) else None
        wvar  = (v21 or v22 or v3).weather_variable if (v21 or v22 or v3) else None
        ctype = (v21 or v22 or v3).contract_type if (v21 or v22 or v3) else None
        mkt_prob = (
            v21.market_yes_probability if v21 else
            v22.market_yes_probability if v22 else
            v3.market_yes_probability  if v3  else None
        )

        versions_present = (
            (["v2.1"] if v21 else []) +
            (["v2.2"] if v22 else []) +
            (["v3"]   if v3  else [])
        )

        dirs = set(filter(None, [
            v21.direction if v21 else None,
            v22.direction if v22 else None,
            v3.direction  if v3  else None,
        ]))

        paired = _is_paired(v21, v22, v3)
        if len(versions_present) == 3:
            if paired:
                paired_count += 1
            else:
                unpaired_count += 1

        row: dict[str, Any] = {
            "ticker":           ticker,
            "city":             city,
            "weather_variable": wvar,
            "contract_type":    ctype,
            "market_prob":      mkt_prob,
            "versions_present": versions_present,
            "versions_agreed":  len(dirs) <= 1 and len(versions_present) > 1,
            "is_paired":        paired,
        }

        if v21:
            row["v21"] = _v2_trade_row(v21)
        if v22:
            row["v22"] = _v2_trade_row(v22)
        if v3:
            row["v3"] = _v3_trade_row(v3)

        if v21 and v22 and v21.ec_yes_probability is not None and v22.ec_yes_probability is not None:
            row["v21_v22_delta_pp"] = round(
                (v22.ec_yes_probability - v21.ec_yes_probability) * 100, 2
            )
        if v21 and v3 and v21.ec_yes_probability is not None and v3.ec_yes_probability is not None:
            row["v21_v3_delta_pp"] = round(
                (v3.ec_yes_probability - v21.ec_yes_probability) * 100, 2
            )

        market_rows.append(row)

    shared_tickers = [r["ticker"] for r in market_rows if len(r["versions_present"]) >= 2]

    # ---- Pairing stats ---------------------------------------------------
    pairing_stats = {
        "strictly_paired":       paired_count,
        "timing_mismatched":     unpaired_count,
        "note": (
            "Strictly paired = all three strategies evaluated the same ticker "
            "in the same collection cycle using identical quote and forecast "
            "inputs.  Timing-mismatched rows are useful operational history but "
            "excluded from direct model-versus-model conclusions."
        ),
    }

    # ---- Readiness tracker -----------------------------------------------
    # A shared settled executable trade = one ticker where V2.1, V2.2, AND V3
    # are all SETTLED with is_executable=True (regardless of pairing — we track
    # the paired subset separately in pairing_stats).
    settled_exec_v21 = {
        t.market_ticker for t in v21_trades
        if t.status == "SETTLED" and t.is_executable is True
    }
    settled_exec_v22 = {
        t.market_ticker for t in v22_trades
        if t.status == "SETTLED" and t.is_executable is True
    }
    settled_exec_v3 = {
        t.market_ticker for t in v3_trades
        if t.status == "SETTLED" and t.is_executable is True
    }
    shared_settled_exec_n = len(settled_exec_v21 & settled_exec_v22 & settled_exec_v3)

    readiness_tracker = {
        "shared_settled_executable": shared_settled_exec_n,
        "milestones": [
            {
                "target":    m,
                "reached":   shared_settled_exec_n >= m,
                "remaining": max(0, m - shared_settled_exec_n),
                "pct":       round(100 * min(1.0, shared_settled_exec_n / m), 1),
            }
            for m in _READINESS_MILESTONES
        ],
        "note": (
            "Do not treat trade counts as performance evidence until the trades settle. "
            "50 shared settled executable trades is the minimum for directional signal."
        ),
    }

    # ---- Smoke test status (updated) -------------------------------------
    smoke_test = {
        "phase":                     "paper_trading_live",
        "v22_predictions_enabled":   flags_out["v22"]["predictions_enabled"],
        "v22_paper_trading_enabled": flags_out["v22"]["paper_trading_enabled"],
        "v22_paper_trade_count":     len(v22_trades),
        "v22_min_sample_met":        False,
        "expected_prob_delta_pp":    0,
        "note": (
            "All three strategies are live in parallel paper-trading mode. "
            "V2.2 paper trading is ENABLED. The corrected bias formula "
            "activates only when a forecast_error_stats bucket crosses "
            "MIN_SAMPLE = 30 observations — currently not met. "
            "Until that threshold is crossed, V2.1 and V2.2 probabilities are "
            "numerically identical (prob_delta_pp = 0). This is expected. "
            "Strictly paired rows (is_paired=True) used identical quote + "
            "forecast inputs and are valid for model-versus-model comparison."
        ),
    }

    preliminary_leader = _preliminary_leader(v21_trades, v22_trades, v3_trades)

    return {
        "flags":              flags_out,
        "strategies":         strategies,
        "shared_count":       len(shared_tickers),
        "total_markets":      len(all_tickers),
        "market_rows":        market_rows,
        "pairing_stats":      pairing_stats,
        "readiness_tracker":  readiness_tracker,
        "smoke_test":         smoke_test,
        "preliminary_leader": preliminary_leader,
    }
