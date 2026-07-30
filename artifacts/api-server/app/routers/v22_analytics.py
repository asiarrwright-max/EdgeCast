"""
V2.2 Analytics API
==================
Endpoints for the V2.2 parallel challenger strategy.

GET  /analytics/v22/flags             — V2.2 feature flag states
GET  /analytics/v22/paper-trades      — V2.2 paper trades with three-section summary
GET  /analytics/v22/comparison        — V2.1 vs V2.2 on shared markets
POST /analytics/v22/enable-predictions   — admin: set v2.2.predictions_enabled=true
POST /analytics/v22/enable-paper-trading — admin: set v2.2.paper_trading_enabled=true

Isolation guarantee
--------------------
All endpoints are read-only with respect to V2.1 state.  The only write operations
are the two /enable-* endpoints which set AppSetting flags for V2.2 only.

Shared comparison identifiers
-------------------------------
V2.1 and V2.2 both reference prediction_snapshots rows via paper_trades.snapshot_id.
The V3 predictor writes prediction_snapshots.comparison_group_id for V3 markets.
Cross-strategy join:

    paper_trades (v2.1 or v2.2) → snapshot_id → prediction_snapshots.comparison_group_id
    v3_paper_trades.comparison_group_id  (same UUID)
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import AppSetting, PaperTrade, PredictionSnapshot

logger = logging.getLogger(__name__)
router = APIRouter(tags=["V2.2 Analytics"])

# V2.2 flag names (mirrors V22_FLAG_DEFAULTS in paper_trading_v22)
_V22_FLAGS = {
    "v2.2.predictions_enabled":   "false",
    "v2.2.paper_trading_enabled": "false",
}

STRATEGY_V22 = "v2.2"
STRATEGY_V21 = "v2.1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fee_estimate(side_price: float | None, quantity: float | None) -> float | None:
    """Estimate Kalshi entry fee: max($0.01, 3.5¢ × min(price, 1−price) × qty)."""
    if side_price is None or quantity is None or quantity <= 0:
        return None
    return round(max(0.01, 0.035 * min(side_price, 1.0 - side_price) * quantity), 4)


def _brier_score(trades: list) -> float | None:
    """
    Brier score on settled trades.

    p_yes  = ec_yes_probability
    YES-WIN  → actual_yes = 1   YES-LOSS → actual_yes = 0
    NO-WIN   → actual_yes = 0   NO-LOSS  → actual_yes = 1
    """
    settled = [
        t for t in trades
        if t.status == "SETTLED" and t.outcome in ("WIN", "LOSS")
    ]
    if not settled:
        return None
    scores = []
    for t in settled:
        p_yes = t.ec_yes_probability or 0.5
        if t.direction == "YES":
            actual_yes = 1.0 if t.outcome == "WIN" else 0.0
        else:
            actual_yes = 0.0 if t.outcome == "WIN" else 1.0
        scores.append((p_yes - actual_yes) ** 2)
    return round(sum(scores) / len(scores), 4)


def _section_summary(trades: list, label: str, include_official: bool) -> dict[str, Any]:
    """Compute summary metrics for a subset of trades."""
    active   = [t for t in trades if t.status not in ("V2_EXCLUDED",)]
    settled  = [t for t in active if t.status == "SETTLED"]
    open_    = [t for t in active if t.status == "OPEN"]
    wins     = sum(1 for t in settled if t.outcome == "WIN")
    losses   = sum(1 for t in settled if t.outcome == "LOSS")
    stake    = sum(t.stake or 0 for t in active)
    gross_pl = sum(t.profit_loss or 0 for t in settled)
    fees_list = [
        _fee_estimate(t.side_market_price, t.quantity)
        for t in active if t.status in ("OPEN", "SETTLED")
    ]
    fees_total = round(sum(f for f in fees_list if f is not None), 4)
    net_pl   = round(gross_pl - fees_total, 4)
    roi      = round(100 * gross_pl / stake, 1) if stake > 0 and settled else None
    brier    = _brier_score(settled) if include_official else None

    result: dict[str, Any] = {
        "label": label,
        "count": len(active),
        "open": len(open_),
        "settled": len(settled),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(100 * wins / len(settled), 1) if settled else None,
        "total_stake": round(stake, 2),
        "gross_pl": round(gross_pl, 2),
        "estimated_fees": fees_total,
        "net_pl": net_pl,
        "roi_pct": roi,
    }
    if include_official:
        result["brier_score"] = brier
    if not include_official:
        result["note"] = "Excluded from official win rate, P/L, and ROI"
    return result


def _trade_dict(t: PaperTrade) -> dict[str, Any]:
    return {
        "id":                     t.id,
        "created_at":             t.created_at.isoformat() if t.created_at else None,
        "market_ticker":          t.market_ticker,
        "city":                   t.city,
        "weather_variable":       t.weather_variable,
        "contract_type":          t.contract_type,
        "direction":              t.direction,
        "ec_yes_probability":     t.ec_yes_probability,
        "ec_side_probability":    t.ec_side_probability,
        "market_yes_probability": t.market_yes_probability,
        "side_market_price":      t.side_market_price,
        "edge_pct_points":        t.edge_pct_points,
        "sigma_used":             t.sigma_used,
        "bias_correction":        t.bias_correction,
        "fallback_level":         t.fallback_level,
        "stake":                  t.stake,
        "quantity":               t.quantity,
        "is_executable":          t.is_executable,
        "lead_time_days":         t.lead_time_days,
        "status":                 t.status,
        "outcome":                t.outcome,
        "profit_loss":            t.profit_loss,
        "estimated_fee":          _fee_estimate(t.side_market_price, t.quantity),
    }


# ---------------------------------------------------------------------------
# GET /analytics/v22/flags
# ---------------------------------------------------------------------------

@router.get("/analytics/v22/flags")
async def get_v22_flags(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Return the current value of all V2.2 feature flags."""
    result = await session.execute(
        select(AppSetting).where(AppSetting.key.in_(list(_V22_FLAGS.keys())))
    )
    rows = {r.key: r.value for r in result.scalars().all()}
    flags: dict[str, Any] = {}
    for key, default in _V22_FLAGS.items():
        raw = rows.get(key, default)
        flags[key] = {
            "value": raw,
            "enabled": (raw or "").lower() in ("true", "1", "yes"),
            "default": default,
        }
    return {
        "flags": flags,
        "strategy": STRATEGY_V22,
        "note": (
            "V2.2 is an isolated parallel challenger.  "
            "Both flags default to 'false' and must be explicitly enabled.  "
            "Do NOT enable paper trading before running in predictions-only mode."
        ),
    }


# ---------------------------------------------------------------------------
# GET /analytics/v22/paper-trades
# ---------------------------------------------------------------------------

@router.get("/analytics/v22/paper-trades")
async def get_v22_paper_trades(
    limit: int = 200,
    status: str | None = None,
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    V2.2 paper trades with three-section performance summary.

    Sections:
      executable      — is_executable=True  → OFFICIAL performance metrics
      non_executable  — is_executable=False → signal accuracy only, excluded from ROI
      excluded_log    — V2_EXCLUDED rows    → research only

    Official headline ROI and win rate use the ``executable`` section only.
    """
    q = select(PaperTrade).where(
        PaperTrade.strategy_version == STRATEGY_V22
    ).order_by(PaperTrade.id.desc())
    if status:
        q = q.where(PaperTrade.status == status.upper())
    q = q.limit(max(1, min(limit, 1000)))

    result = await session.execute(q)
    all_trades = result.scalars().all()

    # Split into three categories
    active_trades = [t for t in all_trades if t.status != "V2_EXCLUDED"]
    executable     = [t for t in active_trades if t.is_executable is True]
    non_executable = [t for t in active_trades if t.is_executable is not True]
    excluded_log   = [t for t in all_trades if t.status == "V2_EXCLUDED"]

    exec_summary = _section_summary(
        executable, "Executable paper trades (is_executable=True)", include_official=True
    )
    non_exec_summary = _section_summary(
        non_executable,
        "Non-executable signals (is_executable=False) — excluded from official ROI",
        include_official=False,
    )

    return {
        "strategy": STRATEGY_V22,
        "count": len(active_trades),
        "official_performance_note": (
            "Official ROI, win rate, and Brier score use EXECUTABLE trades only. "
            "Non-executable signals are recorded for signal accuracy research but "
            "never contribute to headline metrics."
        ),
        "executable": exec_summary,
        "non_executable": non_exec_summary,
        "excluded_log": {
            "label": "V2_EXCLUDED log entries (research only)",
            "count": len(excluded_log),
            "note": "Markets assessed but excluded before trade creation (V2 base exclusion flags).",
        },
        "trades": [_trade_dict(t) for t in all_trades],
    }


# ---------------------------------------------------------------------------
# GET /analytics/v22/comparison  — V2.1 vs V2.2 on shared markets
# ---------------------------------------------------------------------------

@router.get("/analytics/v22/comparison")
async def get_v22_comparison(
    limit: int = 100,
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Side-by-side V2.1 vs V2.2 probability comparison on markets where both
    strategies placed a trade, joined via prediction_snapshots.snapshot_id.

    The bias_correction column shows the applied correction for each strategy.
    V2.1 always shows 0 (formula never fired — MIN_SAMPLE not yet met).
    V2.2 will also show 0 until MIN_SAMPLE is reached; once crossed, its
    corrections will differ from V2.1 in direction.
    """
    # V2.2 active trades with their snapshot
    v22_q = await session.execute(
        select(PaperTrade, PredictionSnapshot)
        .join(PredictionSnapshot, PaperTrade.snapshot_id == PredictionSnapshot.id)
        .where(
            PaperTrade.strategy_version == STRATEGY_V22,
            PaperTrade.status != "V2_EXCLUDED",
        )
        .order_by(PaperTrade.id.desc())
        .limit(max(1, min(limit, 500)))
    )
    v22_rows = v22_q.all()

    if not v22_rows:
        return {
            "count": 0, "comparisons": [],
            "note": "No V2.2 trades yet. Enable v2.2.paper_trading_enabled to start.",
        }

    # For each V2.2 trade, look for a matching V2.1 trade on the same ticker
    tickers = [trade.market_ticker for trade, _ in v22_rows]
    v21_q = await session.execute(
        select(PaperTrade).where(
            PaperTrade.strategy_version == STRATEGY_V21,
            PaperTrade.market_ticker.in_(tickers),
            PaperTrade.status != "V2_EXCLUDED",
        )
    )
    v21_by_ticker: dict[str, PaperTrade] = {
        t.market_ticker: t for t in v21_q.scalars().all()
    }

    comparisons = []
    for v22_trade, snap in v22_rows:
        v21_trade = v21_by_ticker.get(v22_trade.market_ticker)
        comparisons.append({
            "market_ticker":                v22_trade.market_ticker,
            "city":                         v22_trade.city,
            "weather_variable":             v22_trade.weather_variable,
            "contract_type":                v22_trade.contract_type,
            "comparison_group_id":          snap.comparison_group_id,
            "v22": {
                "direction":            v22_trade.direction,
                "ec_yes_probability":   v22_trade.ec_yes_probability,
                "market_yes_prob":      v22_trade.market_yes_probability,
                "edge_pct_points":      v22_trade.edge_pct_points,
                "bias_correction":      v22_trade.bias_correction,
                "sigma_used":           v22_trade.sigma_used,
                "fallback_level":       v22_trade.fallback_level,
                "is_executable":        v22_trade.is_executable,
                "status":               v22_trade.status,
                "outcome":              v22_trade.outcome,
                "profit_loss":          v22_trade.profit_loss,
            },
            "v21": {
                "direction":            v21_trade.direction if v21_trade else None,
                "ec_yes_probability":   v21_trade.ec_yes_probability if v21_trade else None,
                "market_yes_prob":      v21_trade.market_yes_probability if v21_trade else None,
                "edge_pct_points":      v21_trade.edge_pct_points if v21_trade else None,
                "bias_correction":      v21_trade.bias_correction if v21_trade else None,
                "sigma_used":           v21_trade.sigma_used if v21_trade else None,
                "fallback_level":       v21_trade.fallback_level if v21_trade else None,
                "is_executable":        v21_trade.is_executable if v21_trade else None,
                "status":               v21_trade.status if v21_trade else None,
                "outcome":              v21_trade.outcome if v21_trade else None,
                "profit_loss":          v21_trade.profit_loss if v21_trade else None,
            } if v21_trade else None,
            "bias_delta": (
                None if (v22_trade.bias_correction is None or
                         v21_trade is None or v21_trade.bias_correction is None)
                else round(
                    (v22_trade.bias_correction or 0) - (v21_trade.bias_correction or 0), 4
                )
            ),
            "prob_delta_pp": (
                None if (v22_trade.ec_yes_probability is None or
                         v21_trade is None or v21_trade.ec_yes_probability is None)
                else round(
                    ((v22_trade.ec_yes_probability or 0) - (v21_trade.ec_yes_probability or 0)) * 100,
                    2,
                )
            ),
            "same_direction": (
                v21_trade is not None and
                v22_trade.direction == v21_trade.direction
            ),
        })

    same_dir = sum(1 for c in comparisons if c["same_direction"])
    return {
        "count": len(comparisons),
        "same_direction_count": same_dir,
        "different_direction_count": len(comparisons) - same_dir,
        "note": (
            "prob_delta_pp = V2.2 ec_yes − V2.1 ec_yes.  "
            "While MIN_SAMPLE (30 obs) is not yet met, bias_correction=0 in both "
            "strategies and all prob_delta_pp values will be 0.  "
            "Differences appear only after a city/variable bucket crosses the threshold."
        ),
        "comparisons": comparisons,
    }


# ---------------------------------------------------------------------------
# POST /analytics/v22/enable-predictions  (admin)
# ---------------------------------------------------------------------------

@router.post("/analytics/v22/enable-predictions")
async def enable_v22_predictions(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Enable V2.2 predictions (v2.2.predictions_enabled = true).

    V2.2 predictions run in read-only mode — they compute probabilities but
    do NOT create paper trades.  Enable paper trading separately after
    reviewing prediction outputs.
    """
    flag_key = "v2.2.predictions_enabled"
    result = await session.execute(
        select(AppSetting).where(AppSetting.key == flag_key)
    )
    row = result.scalar_one_or_none()
    if row is None:
        session.add(AppSetting(key=flag_key, value="true"))
    else:
        row.value = "true"
    await session.commit()
    return {"flag": flag_key, "value": "true", "message": "V2.2 predictions enabled."}


# ---------------------------------------------------------------------------
# POST /analytics/v22/enable-paper-trading  (admin)
# ---------------------------------------------------------------------------

@router.post("/analytics/v22/enable-paper-trading")
async def enable_v22_paper_trading(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Enable V2.2 paper trading (v2.2.paper_trading_enabled = true).

    WARNING: Run in predictions-only mode first to verify outputs before
    enabling paper trading.  This creates real PaperTrade rows (strategy_version='v2.2').
    """
    flag_key = "v2.2.paper_trading_enabled"
    result = await session.execute(
        select(AppSetting).where(AppSetting.key == flag_key)
    )
    row = result.scalar_one_or_none()
    if row is None:
        session.add(AppSetting(key=flag_key, value="true"))
    else:
        row.value = "true"
    await session.commit()
    return {
        "flag": flag_key, "value": "true",
        "message": "V2.2 paper trading enabled. Trades will be created on the next collection run.",
        "warning": "Ensure predictions-only smoke testing is complete before enabling.",
    }
