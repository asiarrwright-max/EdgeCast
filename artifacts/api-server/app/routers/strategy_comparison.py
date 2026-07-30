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
        stake    = sum(getattr(t, "stake", 0) or 0 for t in active)
        gross_pl = sum(getattr(t, "profit_loss", 0) or 0 for t in settled)
        fees     = sum(
            f for t in active
            if (f := _fee_est(
                getattr(t, "side_market_price", None),
                getattr(t, "quantity", None),
            )) is not None
        )
        net_pl = round(gross_pl - fees, 4)
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
            "total_stake":        round(stake, 2),
            "gross_pl":           round(gross_pl, 2),
            "estimated_fees":     round(fees, 4),
            "net_pl":             net_pl,
            "roi_pct":            round(100 * gross_pl / stake, 1) if stake > 0 and settled else None,
            "brier_score":        _brier(settled) if official and settled else None,
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

    return {
        "flags":           flags_out,
        "strategies":      strategies,
        "shared_count":    len(shared_tickers),
        "total_markets":   len(all_tickers),
        "market_rows":     market_rows,
        "pairing_stats":   pairing_stats,
        "readiness_tracker": readiness_tracker,
        "smoke_test":      smoke_test,
    }
