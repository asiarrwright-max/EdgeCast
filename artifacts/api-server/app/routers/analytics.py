"""
Performance Analytics Router
============================
GET /api/analytics/performance?period=7d|30d|all&strategy=v1.0|v2.0|all

Combines paper-trade metrics, time-series analytics, and calibration into a
single read-only endpoint for the Performance Analytics dashboard.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import PaperTrade

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analytics"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _since(period: str) -> datetime | None:
    """Return the UTC cutoff datetime for the given period string."""
    now = datetime.now(timezone.utc)
    if period == "7d":
        return now - timedelta(days=7)
    if period == "30d":
        return now - timedelta(days=30)
    return None  # all time


def _avg(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 4) if vals else None


def _brier(trades: list[PaperTrade]) -> float | None:
    """Brier score over trades that have ec_yes_probability and a yes/no result."""
    scored = [
        t for t in trades
        if t.status == "SETTLED"
        and t.kalshi_result in ("yes", "no")
        and t.ec_yes_probability is not None
    ]
    if not scored:
        return None
    total = sum(
        (t.ec_yes_probability - (1 if t.kalshi_result == "yes" else 0)) ** 2  # type: ignore[operator]
        for t in scored
    )
    return round(total / len(scored), 6)


def _confidence_distribution(trades: list[PaperTrade]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for t in trades:
        label = t.confidence_label or "Unknown"
        dist[label] = dist.get(label, 0) + 1
    return dist


def _mode_label(dist: dict[str, int]) -> str | None:
    if not dist:
        return None
    return max(dist, key=lambda k: dist[k])


def _cumulative_pl(settled: list[PaperTrade]) -> list[dict]:
    by_time = sorted(
        settled,
        key=lambda t: t.settlement_timestamp or datetime.min.replace(tzinfo=timezone.utc),
    )
    cumul = 0.0
    rows: list[dict] = []
    for t in by_time:
        cumul += t.profit_loss or 0.0
        rows.append({
            "date": t.settlement_timestamp.isoformat() if t.settlement_timestamp else None,
            "cumulativePl": round(cumul, 4),
            "tradeId": t.id,
        })
    return rows


def _cumulative_roi(settled: list[PaperTrade]) -> list[dict]:
    """Cumulative ROI (%) after each settled trade, ordered by settlement time."""
    by_time = sorted(
        settled,
        key=lambda t: t.settlement_timestamp or datetime.min.replace(tzinfo=timezone.utc),
    )
    total_stake = 0.0
    total_pl = 0.0
    rows: list[dict] = []
    for t in by_time:
        total_stake += t.stake or 0.0
        total_pl += t.profit_loss or 0.0
        roi = (total_pl / total_stake * 100) if total_stake > 0 else 0.0
        rows.append({
            "date": t.settlement_timestamp.isoformat() if t.settlement_timestamp else None,
            "roi": round(roi, 4),
            "tradeId": t.id,
        })
    return rows


def _rolling_win_rate(settled: list[PaperTrade], window: int = 10) -> list[dict]:
    """Rolling win-rate over the last `window` settled trades."""
    by_time = sorted(
        settled,
        key=lambda t: t.settlement_timestamp or datetime.min.replace(tzinfo=timezone.utc),
    )
    rows: list[dict] = []
    for i, t in enumerate(by_time):
        start = max(0, i - window + 1)
        chunk = by_time[start : i + 1]
        wins = sum(1 for c in chunk if c.outcome == "WIN")
        rows.append({
            "date": t.settlement_timestamp.isoformat() if t.settlement_timestamp else None,
            "winRate": round(wins / len(chunk), 4),
            "tradeNum": i + 1,
        })
    return rows


def _brier_over_time(settled: list[PaperTrade]) -> list[dict]:
    """Running Brier score after each settled trade with a known ec_yes_probability."""
    by_time = sorted(
        settled,
        key=lambda t: t.settlement_timestamp or datetime.min.replace(tzinfo=timezone.utc),
    )
    scored = [
        t for t in by_time
        if t.kalshi_result in ("yes", "no") and t.ec_yes_probability is not None
    ]
    rows: list[dict] = []
    brier_sum = 0.0
    for i, t in enumerate(scored):
        actual = 1 if t.kalshi_result == "yes" else 0
        brier_sum += (t.ec_yes_probability - actual) ** 2  # type: ignore[operator]
        rows.append({
            "date": t.settlement_timestamp.isoformat() if t.settlement_timestamp else None,
            "brierScore": round(brier_sum / (i + 1), 6),
            "tradeCount": i + 1,
        })
    return rows


def _daily_pl(settled: list[PaperTrade]) -> list[dict]:
    daily: dict[str, dict] = {}
    for t in settled:
        if not t.settlement_timestamp:
            continue
        day = t.settlement_timestamp.strftime("%Y-%m-%d")
        if day not in daily:
            daily[day] = {"date": day, "pl": 0.0, "count": 0}
        daily[day]["pl"] += t.profit_loss or 0.0
        daily[day]["count"] += 1
    return [
        {"date": v["date"], "pl": round(v["pl"], 4), "count": v["count"]}
        for v in sorted(daily.values(), key=lambda x: x["date"])
    ]


def _daily_trade_count(trades: list[PaperTrade]) -> list[dict]:
    """Daily count of ALL trades (open + settled) by created_at."""
    daily: dict[str, int] = {}
    for t in trades:
        if not t.created_at:
            continue
        day = t.created_at.strftime("%Y-%m-%d") if hasattr(t.created_at, "strftime") else str(t.created_at)[:10]
        daily[day] = daily.get(day, 0) + 1
    return [
        {"date": d, "count": c}
        for d, c in sorted(daily.items())
    ]


def _strategy_side(
    all_trades: list[PaperTrade],
    v1: str = "v1.0",
    v2: str = "v2.0",
) -> dict[str, Any]:
    """
    Build strategy comparison breakdown across all trades (no date filter).
    Groups markets into:
      shared       – both v1 and v2 entered
      v1Only       – only v1 entered
      v2Only       – only v2 entered
      oppositeSide – both entered, different directions
    """
    v1_map: dict[str, PaperTrade] = {}
    v2_map: dict[str, PaperTrade] = {}
    for t in all_trades:
        if t.strategy_version == v1:
            v1_map[t.market_ticker] = t
        elif t.strategy_version == v2:
            v2_map[t.market_ticker] = t

    all_tickers = set(v1_map) | set(v2_map)
    shared_tickers = set(v1_map) & set(v2_map)
    v1_only = set(v1_map) - set(v2_map)
    v2_only = set(v2_map) - set(v1_map)
    opposite_tickers = {
        tk for tk in shared_tickers
        if v1_map[tk].direction != v2_map[tk].direction
    }

    def _summary(tickers: set[str], trade_map: dict[str, PaperTrade]) -> dict:
        trades_in = [trade_map[tk] for tk in tickers if tk in trade_map]
        settled = [t for t in trades_in if t.status == "SETTLED"]
        wins = [t for t in settled if t.outcome == "WIN"]
        total_stake = sum(t.stake or 0 for t in settled)
        net_pl = sum(t.profit_loss or 0 for t in settled)
        return {
            "total": len(trades_in),
            "settled": len(settled),
            "wins": len(wins),
            "losses": len(settled) - len(wins),
            "winRate": round(len(wins) / len(settled), 4) if settled else None,
            "roi": round(net_pl / total_stake * 100, 4) if total_stake > 0 else None,
            "netPl": round(net_pl, 4),
        }

    def _version_stats(strategy: str, trade_map: dict[str, PaperTrade]) -> dict:
        trades_in = list(trade_map.values())
        settled = [t for t in trades_in if t.status == "SETTLED"]
        wins = [t for t in settled if t.outcome == "WIN"]
        total_stake = sum(t.stake or 0 for t in settled)
        net_pl = sum(t.profit_loss or 0 for t in settled)
        return {
            "strategy": strategy,
            "total": len(trades_in),
            "settled": len(settled),
            "wins": len(wins),
            "losses": len(settled) - len(wins),
            "winRate": round(len(wins) / len(settled), 4) if settled else None,
            "roi": round(net_pl / total_stake * 100, 4) if total_stake > 0 else None,
            "netPl": round(net_pl, 4),
            "brierScore": _brier(trades_in),
        }

    return {
        "v1": _version_stats(v1, v1_map),
        "v2": _version_stats(v2, v2_map),
        "sharedCount": len(shared_tickers),
        "v1OnlyCount": len(v1_only),
        "v2OnlyCount": len(v2_only),
        "oppositeSideCount": len(opposite_tickers),
        # Each segment returns separate stats per strategy version.
        # v1Only.v2 and v2Only.v1 will be None (no trades on that side).
        "sharedV1": _summary(shared_tickers, v1_map),
        "sharedV2": _summary(shared_tickers, v2_map),
        "v1OnlyV1": _summary(v1_only, v1_map),
        "v2OnlyV2": _summary(v2_only, v2_map),
        "oppositeSideV1": _summary(opposite_tickers, v1_map),
        "oppositeSideV2": _summary(opposite_tickers, v2_map),
    }


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/analytics/performance")
async def get_performance_analytics(
    period: str = Query("all", pattern="^(7d|30d|all)$"),
    strategy: str = Query("all", pattern="^(v1\\.0|v2\\.0|all)$"),
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Combined performance analytics for the dashboard.
    Applies a date window (created_at >= cutoff) when period != 'all'.
    """
    cutoff = _since(period)

    # Load all trades for strategy comparison (no date filter)
    all_q = select(PaperTrade)
    all_result = await session.execute(all_q)
    all_trades_unfiltered: list[PaperTrade] = all_result.scalars().all()

    # Date + strategy filter for summary / charts
    q = select(PaperTrade)
    if strategy != "all":
        q = q.where(PaperTrade.strategy_version == strategy)
    if cutoff is not None:
        q = q.where(PaperTrade.created_at >= cutoff)
    result = await session.execute(q)
    trades: list[PaperTrade] = result.scalars().all()

    open_t = [t for t in trades if t.status == "OPEN"]
    settled_t = [t for t in trades if t.status == "SETTLED"]
    void_t = [t for t in trades if t.status == "VOID"]
    wins = [t for t in settled_t if t.outcome == "WIN"]
    losses = [t for t in settled_t if t.outcome == "LOSS"]

    total_staked_settled = sum(t.stake or 0 for t in settled_t)
    net_pl = sum(t.profit_loss or 0 for t in settled_t)
    win_rate = round(len(wins) / len(settled_t), 4) if settled_t else None
    roi = round(net_pl / total_staked_settled * 100, 4) if total_staked_settled > 0 else None

    all_edges = [t.edge_pct_points for t in trades if t.edge_pct_points is not None]
    conf_dist = _confidence_distribution(trades)

    summary = {
        "totalCount": len(trades),
        "settledCount": len(settled_t),
        "openCount": len(open_t),
        "voidCount": len(void_t),
        "wins": len(wins),
        "losses": len(losses),
        "winRate": win_rate,
        "roi": roi,
        "netProfitLoss": round(net_pl, 4),
        "brierScore": _brier(trades),
        "avgEntryEdgePp": _avg(all_edges),
        "avgConfidenceLabel": _mode_label(conf_dist),
        "confidenceDistribution": conf_dist,
        "sampleSizeWarning": len(settled_t) < 20,
        "preliminaryNote": (
            "Results are preliminary — fewer than 30 settled trades."
        ) if len(settled_t) < 30 else None,
    }

    charts = {
        "cumulativeRoi": _cumulative_roi(settled_t),
        "cumulativePl": _cumulative_pl(settled_t),
        "rollingWinRate": _rolling_win_rate(settled_t),
        "dailyPl": _daily_pl(settled_t),
        "dailyTradeCount": _daily_trade_count(trades),
        "brierOverTime": _brier_over_time(settled_t),
    }

    # Strategy comparison always uses unfiltered data (no date window)
    # so the side-by-side table is stable regardless of period
    strategy_comparison = _strategy_side(all_trades_unfiltered)

    return {
        "period": period,
        "strategy": strategy,
        "summary": summary,
        "charts": charts,
        "strategyComparison": strategy_comparison,
    }
