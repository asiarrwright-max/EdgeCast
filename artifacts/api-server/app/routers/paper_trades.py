"""
Paper Trades API router — Phase 3A / 3B.

Endpoints:
  GET  /paper-trades               List trades (with filters)
  GET  /paper-trades/metrics       Aggregated summary statistics
  GET  /paper-trades/analytics     Performance breakdowns + realistic results
  GET  /paper-trades/calibration   Probability calibration report + Brier score
  POST /paper-trades/settle-now    Trigger immediate settlement pass
  GET  /paper-trades/settings      Current paper-trading settings (admin only)
  PUT  /paper-trades/settings      Update settings (admin only)
  GET  /paper-trades/export.csv    CSV export of filtered trade list
  GET  /paper-trades/{id}          Single trade detail

All endpoints require authentication.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import KalshiMarket, PaperTrade, PredictionSnapshot
from app.models_v3 import V3PaperTrade
from app.services.paper_trading import (
    FLAG_DESCRIPTIONS,
    _empty_metrics,
    compute_metrics_from_trades,
    edge_bucket,
    get_calibration_report,
    get_paper_trade_analytics,
    get_paper_trade_metrics,
    get_paper_trade_settings,
    price_bucket,
    save_paper_trade_settings,
)
from app.services.paper_trading_v2 import (
    V2_FLAG_DESCRIPTIONS,
    get_strategy_agreement,
    get_v2_settings,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["paper-trades"])


# ── Serialisation helper ──────────────────────────────────────────────────────

def _trade_to_dict(t: PaperTrade) -> dict[str, Any]:
    flags: list[str] = t.quality_flags or []
    all_flag_descs = {**FLAG_DESCRIPTIONS, **V2_FLAG_DESCRIPTIONS}
    return {
        "id": t.id,
        "createdAt": t.created_at.isoformat() if t.created_at else None,
        "marketTicker": t.market_ticker,
        "eventTicker": t.event_ticker,
        "city": t.city,
        "weatherVariable": t.weather_variable,
        "contractType": t.contract_type,
        "targetSettlementDate": t.target_settlement_date,
        "snapshotId": t.snapshot_id,
        "strategyVersion": t.strategy_version,
        "direction": t.direction,
        "ecYesProbability": t.ec_yes_probability,
        "ecSideProbability": t.ec_side_probability,
        "marketYesProbability": t.market_yes_probability,
        "sideMarketPrice": t.side_market_price,
        "priceSource": t.price_source,
        "edgePctPoints": t.edge_pct_points,
        "confidenceScore": t.confidence_score,
        "confidenceLabel": t.confidence_label,
        "stake": t.stake,
        "quantity": t.quantity,
        "leadTimeDays": t.lead_time_days,
        "status": t.status,
        "kalshiResult": t.kalshi_result,
        "outcome": t.outcome,
        "settlementTimestamp": t.settlement_timestamp.isoformat() if t.settlement_timestamp else None,
        "grossPayout": t.gross_payout,
        "profitLoss": t.profit_loss,
        "returnPct": t.return_pct,
        "decisionExplanation": t.decision_explanation,
        "warnings": t.warnings,
        "qualityFlags": flags,
        "isFlagged": bool(flags),
        "qualityFlagDescriptions": {f: all_flag_descs.get(f, f) for f in flags},
        # v2 engine metadata (None for v1 trades)
        "sigmaUsed": t.sigma_used,
        "biasCorrection": t.bias_correction,
        "fallbackLevel": t.fallback_level,
        "calibrationAdj": t.calibration_adj,
    }


# ── Settings schema ───────────────────────────────────────────────────────────

class PaperTradeSettingsUpdate(BaseModel):
    enabled: bool | None = None
    min_edge_pct: float | None = None
    min_confidence: str | None = None
    stake: float | None = None
    strategy_version: str | None = None


# ── Shared filter logic ───────────────────────────────────────────────────────

def _matches_filters(
    t: PaperTrade,
    status_filter: str | None,
    direction: str | None,
    confidence: str | None,
    city: str | None,
    contract_type: str | None,
    date_from: str | None,
    date_to: str | None,
    strategy_version: str | None,
    edge_bucket_filter: str | None,
    price_bucket_filter: str | None,
    is_flagged: bool | None,
    outcome: str | None,
) -> bool:
    if status_filter and t.status != status_filter.upper():
        return False
    if direction and t.direction != direction.upper():
        return False
    if confidence and (not t.confidence_label or confidence.lower() not in t.confidence_label.lower()):
        return False
    if city and (not t.city or city.lower() not in t.city.lower()):
        return False
    if contract_type and t.contract_type != contract_type:
        return False
    if date_from and (not t.target_settlement_date or t.target_settlement_date < date_from):
        return False
    if date_to and (not t.target_settlement_date or t.target_settlement_date > date_to):
        return False
    if strategy_version and t.strategy_version != strategy_version:
        return False
    if edge_bucket_filter and edge_bucket(t.edge_pct_points) != edge_bucket_filter:
        return False
    if price_bucket_filter and price_bucket(t.side_market_price) != price_bucket_filter:
        return False
    if is_flagged is not None:
        trade_is_flagged = bool(t.quality_flags)
        if trade_is_flagged != is_flagged:
            return False
    if outcome and t.outcome != outcome.upper():
        return False
    return True


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/paper-trades/comparison")
async def get_comparison(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Side-by-side v1 vs v2 metrics comparison.
    Returns both strategy summaries and their calibration reports.
    """
    v1_metrics = await get_paper_trade_metrics(db, strategy_version="v1.0")
    v2_metrics = await get_paper_trade_metrics(db, strategy_version="v2.0")
    v1_calib = await get_calibration_report(db, strategy_version="v1.0")
    v2_calib = await get_calibration_report(db, strategy_version="v2.0")
    v2_settings = await get_v2_settings(db)
    return {
        "v1": {**v1_metrics, "calibration": v1_calib},
        "v2": {**v2_metrics, "calibration": v2_calib},
        "v2Settings": v2_settings,
    }


@router.get("/paper-trades/agreement")
async def get_agreement(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Strategy v1 vs v2 agreement/divergence summary."""
    return await get_strategy_agreement(db)


@router.post("/paper-trades/run-verification")
async def run_verification(
    _user: dict = Depends(get_current_user),
):
    """
    Trigger an immediate forecast verification pass (fetch actuals + recompute error stats).
    Normally runs every 24 hours automatically.
    """
    from app.database import AsyncSessionLocal
    from app.services.forecast_verifier import (
        fetch_and_store_verifications,
        recompute_error_stats,
    )
    if AsyncSessionLocal is None:
        return {"error": "Database not initialised"}
    async with AsyncSessionLocal() as session:
        vstats = await fetch_and_store_verifications(session)
        estats = await recompute_error_stats(session)
    return {
        "verifications": vstats,
        "errorStats": estats,
    }


# Maps segment name → filter kwargs for get_paper_trade_metrics.
# Segments handled by special-case code in the endpoint are NOT listed here.
_SEGMENT_FILTERS: dict[str, dict] = {
    # Single-strategy views
    "v21_only":    {"strategy_versions": ["v2.1"], "is_executable": True},
    "v22_only":    {"strategy_versions": ["v2.2"], "is_executable": True},
    # Combined V2.1 + V2.2 views (retained for backward-compat / API consumers)
    "current_v2":  {"strategy_versions": ["v2.1", "v2.2"], "is_executable": True},
    "paired_v2":   {"strategy_versions": ["v2.1", "v2.2"], "is_executable": True, "paired_only": True},
    "legacy":      {"strategy_versions": ["v1.0", "v2.0"]},
    "research":    {"strategy_versions": ["v2.1", "v2.2"], "is_executable": False},
    # "all" / omitted → no extra filters
}
# Segments handled with custom logic (not in _SEGMENT_FILTERS):
#   current_exp   — loads both paper_trades and v3_paper_trades, fully aggregated
#   v3_challenger — queries v3_paper_trades only via SQL
#   paired        — 3-way intersection on comparison_snapshot_id across all three strategies


@router.get("/paper-trades/metrics")
async def get_metrics(
    strategy_version: str | None = Query(default=None),
    segment: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Aggregated summary statistics.

    segment — one of:
      current_exp    — V2.1 + V2.2 + V3 executable (default recommended view).
                       Settled stats come from V2.1+V2.2 only because V3 has no
                       settled trades yet; V3 open counts are augmented separately.
      current_v2     — V2.1 + V2.2 executable only (corrected-bias comparison).
      v3_challenger  — V3 executable only (queries v3_paper_trades).
      paired         — strictly paired V2.1+V2.2 (shared comparison_snapshot_id).
      legacy         — V1.0 + V2.0 historical baseline.
      research       — V2.1 + V2.2 non-executable research signals.
      all / omitted  — unfiltered combined view (legacy + current contaminated).

    strategy_version — further narrow to a single version string.
    """
    from sqlalchemy import text as sql_text

    seg = segment or ""

    # ── Current Experiment: full 3-strategy aggregation ──────────────────────
    if seg == "current_exp":
        v2_trades = (await db.execute(
            select(PaperTrade)
            .where(PaperTrade.status != "V2_EXCLUDED",
                   PaperTrade.strategy_version.in_(["v2.1", "v2.2"]),
                   PaperTrade.is_executable == True)
        )).scalars().all()
        v3_exec_trades = (await db.execute(
            select(V3PaperTrade).where(V3PaperTrade.is_executable == True)
        )).scalars().all()

        combined = list(v2_trades) + list(v3_exec_trades)
        result: dict = compute_metrics_from_trades(combined)

        # Per-strategy reconciliation (open, settled, wins, net P/L, ROI)
        def _compact(m: dict) -> dict:
            return {
                "open":     m["openCount"],
                "settled":  m["settledCount"],
                "wins":     m["wins"],
                "winRate":  m["winRate"],
                "netPl":    m["netProfitLoss"],
                "roi":      m["roi"],
            }
        result["reconciliation"] = {
            "v21":      _compact(compute_metrics_from_trades(
                            [t for t in v2_trades if t.strategy_version == "v2.1"])),
            "v22":      _compact(compute_metrics_from_trades(
                            [t for t in v2_trades if t.strategy_version == "v2.2"])),
            "v3":       _compact(compute_metrics_from_trades(list(v3_exec_trades))),
            "combined": _compact(result),
        }
        result["v3OpenExecCount"] = sum(1 for t in v3_exec_trades if t.status == "OPEN")

    # ── V3 Challenger: queries v3_paper_trades only via Python objects ────────
    elif seg == "v3_challenger":
        v3_exec_trades = (await db.execute(
            select(V3PaperTrade).where(V3PaperTrade.is_executable == True)
        )).scalars().all()
        result = compute_metrics_from_trades(list(v3_exec_trades))
        if result["settledCount"] == 0:
            result["preliminaryNote"] = (
                "V3 has no settled executable trades yet — metrics reflect open positions only."
            )

    # ── Strictly Paired Head-to-Head: 3-way intersection on comparison_snapshot_id ──
    elif seg == "paired":
        v21_rows = (await db.execute(
            select(PaperTrade)
            .where(PaperTrade.status != "V2_EXCLUDED",
                   PaperTrade.strategy_version == "v2.1",
                   PaperTrade.is_executable == True,
                   PaperTrade.comparison_snapshot_id.isnot(None))
        )).scalars().all()
        v22_rows = (await db.execute(
            select(PaperTrade)
            .where(PaperTrade.status != "V2_EXCLUDED",
                   PaperTrade.strategy_version == "v2.2",
                   PaperTrade.is_executable == True,
                   PaperTrade.comparison_snapshot_id.isnot(None))
        )).scalars().all()
        v3_rows = (await db.execute(
            select(V3PaperTrade)
            .where(V3PaperTrade.is_executable == True,
                   V3PaperTrade.comparison_snapshot_id.isnot(None))
        )).scalars().all()

        v21_by_snap = {t.comparison_snapshot_id: t for t in v21_rows}
        v22_by_snap = {t.comparison_snapshot_id: t for t in v22_rows}
        v3_by_snap  = {t.comparison_snapshot_id: t for t in v3_rows}

        all_snaps   = set(v21_by_snap) | set(v22_by_snap) | set(v3_by_snap)
        three_way   = set(v21_by_snap) & set(v22_by_snap) & set(v3_by_snap)
        v2_only     = (set(v21_by_snap) & set(v22_by_snap)) - three_way

        three_way_trades: list = []
        for snap_id in three_way:
            three_way_trades.extend([
                v21_by_snap[snap_id], v22_by_snap[snap_id], v3_by_snap[snap_id],
            ])

        result = compute_metrics_from_trades(three_way_trades) if three_way_trades else _empty_metrics()
        result["pairedStats"] = {
            "totalOpportunitiesWithAnySnapshot": len(all_snaps),
            "threeWayOpportunities":             len(three_way),
            "threeWaySettledPositions":          sum(1 for t in three_way_trades
                                                     if t.status == "SETTLED"),
            "excludedMissingV3":   len(v2_only),
            "excludedMissingV21":  len((set(v22_by_snap) & set(v3_by_snap))  - three_way),
            "excludedMissingV22":  len((set(v21_by_snap) & set(v3_by_snap))  - three_way),
            "note": (
                "No paired opportunities yet. comparison_snapshot_id is populated on "
                "trades collected via the pipeline's batch collection mode."
            ) if not all_snaps else None,
        }

    # ── All remaining segments: query paper_trades via service ────────────────
    else:
        seg_filters = _SEGMENT_FILTERS.get(seg) or {}
        result = await get_paper_trade_metrics(
            db,
            strategy_version=strategy_version,
            strategy_versions=seg_filters.get("strategy_versions"),
            is_executable=seg_filters.get("is_executable"),
            paired_only=bool(seg_filters.get("paired_only", False)),
        )

    # ── Closing-today counts (pipeline ops — always all strategies, all segments) ──
    today = datetime.utcnow().date().isoformat()  # e.g. "2026-08-01"
    from sqlalchemy import text as sql_text

    pt_pending = (await db.execute(
        select(func.count(PaperTrade.id))
        .where(PaperTrade.status == "PENDING_SETTLEMENT")
    )).scalar_one() or 0

    v3_pending = (await db.execute(
        select(func.count(V3PaperTrade.id))
        .where(V3PaperTrade.status == "PENDING_SETTLEMENT")
    )).scalar_one() or 0

    pt_closing = (await db.execute(
        select(func.count(PaperTrade.id))
        .where(
            PaperTrade.status == "OPEN",
            PaperTrade.target_settlement_date.like(f"{today}%"),
        )
    )).scalar_one() or 0

    v3_closing = (await db.execute(
        select(func.count(V3PaperTrade.id))
        .where(
            V3PaperTrade.status == "OPEN",
            V3PaperTrade.target_settlement_date.like(f"{today}%"),
        )
    )).scalar_one() or 0

    # Unique Kalshi markets closing today (UNION deduplicates cross-strategy)
    unique_sql = sql_text("""
        SELECT COUNT(*) FROM (
            SELECT market_ticker FROM paper_trades
            WHERE status = 'OPEN' AND target_settlement_date LIKE :pfx
            UNION
            SELECT market_ticker FROM v3_paper_trades
            WHERE status = 'OPEN' AND target_settlement_date LIKE :pfx
        ) AS combined
    """)
    unique_markets = (await db.execute(unique_sql, {"pfx": f"{today}%"})).scalar_one() or 0

    result["pendingSettlementCount"]   = pt_pending + v3_pending
    result["closingTodayCount"]        = pt_closing + v3_closing
    result["closingTodayTotal"]        = pt_pending + v3_pending + pt_closing + v3_closing
    result["closingTodayUniqueMarkets"] = unique_markets

    return result


@router.get("/paper-trades/segment-summary")
async def get_segment_summary(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Full per-version × executability breakdown across all strategies including V3.
    Used by the Strategy Breakdown table on the Paper Trading page.
    No data is modified; this is a read-only analytics view.
    """
    from sqlalchemy import text as sql_text

    pt_sql = sql_text("""
        SELECT
            strategy_version,
            is_executable,
            COUNT(*) FILTER (WHERE status != 'V2_EXCLUDED')                      AS total,
            COUNT(*) FILTER (WHERE status = 'OPEN')                               AS open_n,
            COUNT(*) FILTER (WHERE status = 'PENDING_SETTLEMENT')                 AS pending_n,
            COUNT(*) FILTER (WHERE status = 'SETTLED')                            AS settled_n,
            COUNT(*) FILTER (WHERE status = 'SETTLED' AND outcome = 'WIN')        AS wins,
            COUNT(*) FILTER (WHERE status = 'SETTLED' AND outcome = 'LOSS')       AS losses,
            COUNT(*) FILTER (WHERE status = 'V2_EXCLUDED')                        AS excluded_n,
            ROUND(COALESCE(SUM(stake)       FILTER (WHERE status = 'SETTLED'), 0)::numeric, 2) AS settled_stake,
            ROUND(COALESCE(SUM(profit_loss) FILTER (WHERE status = 'SETTLED'), 0)::numeric, 2) AS settled_pl,
            ROUND(AVG(edge_pct_points)      FILTER (WHERE status != 'V2_EXCLUDED')::numeric, 2) AS avg_edge,
            ROUND(AVG(
                CASE WHEN status = 'SETTLED'
                          AND kalshi_result IS NOT NULL
                          AND ec_yes_probability IS NOT NULL
                    THEN POWER(ec_yes_probability
                               - CASE WHEN kalshi_result = 'YES' THEN 1.0 ELSE 0.0 END, 2)
                END
            )::numeric, 4) AS brier
        FROM paper_trades
        GROUP BY strategy_version, is_executable
        ORDER BY strategy_version, is_executable NULLS LAST
    """)

    v3_sql = sql_text("""
        SELECT
            is_executable,
            COUNT(*) FILTER (WHERE status = 'OPEN')                               AS open_n,
            COUNT(*) FILTER (WHERE status = 'PENDING_SETTLEMENT')                 AS pending_n,
            COUNT(*) FILTER (WHERE status = 'SETTLED')                            AS settled_n,
            COUNT(*) FILTER (WHERE status = 'SETTLED' AND outcome = 'WIN')        AS wins,
            COUNT(*) FILTER (WHERE status = 'SETTLED' AND outcome = 'LOSS')       AS losses,
            ROUND(COALESCE(SUM(stake)       FILTER (WHERE status = 'SETTLED'), 0)::numeric, 2) AS settled_stake,
            ROUND(COALESCE(SUM(profit_loss) FILTER (WHERE status = 'SETTLED'), 0)::numeric, 2) AS settled_pl,
            ROUND(AVG(edge_pct_points)::numeric, 2)                               AS avg_edge,
            ROUND(AVG(
                CASE WHEN status = 'SETTLED'
                          AND kalshi_result IS NOT NULL
                          AND ec_yes_probability IS NOT NULL
                    THEN POWER(ec_yes_probability
                               - CASE WHEN kalshi_result = 'YES' THEN 1.0 ELSE 0.0 END, 2)
                END
            )::numeric, 4) AS brier
        FROM v3_paper_trades
        GROUP BY is_executable
        ORDER BY is_executable NULLS LAST
    """)

    def _group(version: str, is_exec) -> str:
        if version in ("v1.0", "v2.0"):
            return "legacy"
        if version in ("v2.1", "v2.2"):
            return "current_exec" if is_exec else "current_nonexec"
        return "v3"

    def _row(ver: str, is_exec, r: dict, excluded: int = 0) -> dict:
        settled_n     = int(r.get("settled_n") or 0)
        settled_stake = float(r.get("settled_stake") or 0)
        settled_pl    = float(r.get("settled_pl") or 0)
        wins          = int(r.get("wins") or 0)
        return {
            "version":     ver,
            "group":       _group(ver, is_exec),
            "isExecutable": is_exec,
            "total":       int(r.get("total") or 0),
            "open":        int(r.get("open_n") or 0),
            "pending":     int(r.get("pending_n") or 0),
            "settled":     settled_n,
            "wins":        wins,
            "losses":      int(r.get("losses") or 0),
            "excluded":    excluded,
            "winRate":     round(wins / settled_n, 4) if settled_n > 0 else None,
            "settledStake": settled_stake,
            "settledPl":   settled_pl,
            "settledRoi":  round(settled_pl / settled_stake * 100, 2) if settled_stake > 0 else None,
            "avgEdge":     float(r["avg_edge"]) if r.get("avg_edge") is not None else None,
            "brierScore":  float(r["brier"])    if r.get("brier")    is not None else None,
        }

    pt_rows = (await db.execute(pt_sql)).mappings().all()
    v3_rows = (await db.execute(v3_sql)).mappings().all()

    rows = [_row(r["strategy_version"], r["is_executable"], dict(r),
                 excluded=int(r.get("excluded_n") or 0)) for r in pt_rows]
    for r in v3_rows:
        d = dict(r)
        settled_n = int(d.get("settled_n") or 0)
        d["total"] = int(d.get("open_n") or 0) + int(d.get("pending_n") or 0) + settled_n
        rows.append(_row("v3.0", d["is_executable"], d))

    group_order = {"legacy": 0, "current_exec": 1, "current_nonexec": 2, "v3": 3}
    rows.sort(key=lambda x: (
        group_order.get(x["group"], 9),
        x["version"],
        x["isExecutable"] is None,
        not bool(x["isExecutable"]),
    ))
    return {"rows": rows}


@router.get("/paper-trades/best-bet-today")
async def get_best_bet_today(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Return the single best OFFICIAL open paper trade across V2.2 and V3.

    "Best" = highest expected value = edge_pct_points / side_market_price.
    Only trades with eligibility_status = 'OFFICIAL' AND is_executable = TRUE
    AND status = 'OPEN' qualify.

    Returns {"available": bool, "trade": {...} | null}.
    When no official-quality bets exist, available=False and a clear message
    is returned — research-only signals are never surfaced here.
    """
    from sqlalchemy import text as sql_text

    best_sql = sql_text("""
        SELECT
            'v2.2'              AS strategy_version,
            id,
            market_ticker,
            city,
            weather_variable,
            contract_type,
            target_settlement_date,
            direction,
            ec_side_probability,
            market_yes_probability,
            side_market_price,
            edge_pct_points,
            lead_time_days,
            quote_age_seconds,
            station_verified,
            eligibility_status,
            decision_explanation,
            created_at
        FROM paper_trades
        WHERE eligibility_status = 'OFFICIAL'
          AND is_executable = TRUE
          AND status        = 'OPEN'
          AND strategy_version = 'v2.2'

        UNION ALL

        SELECT
            'v3.0'              AS strategy_version,
            id,
            market_ticker,
            city,
            weather_variable,
            contract_type,
            target_settlement_date,
            direction,
            ec_side_probability,
            market_yes_probability,
            side_market_price,
            edge_pct_points,
            lead_time_days,
            quote_age_seconds,
            station_verified,
            eligibility_status,
            decision_explanation,
            created_at
        FROM v3_paper_trades
        WHERE eligibility_status = 'OFFICIAL'
          AND is_executable = TRUE
          AND status        = 'OPEN'

        ORDER BY (edge_pct_points / GREATEST(side_market_price, 0.001)) DESC
        LIMIT 1
    """)

    row = (await db.execute(best_sql)).mappings().first()

    if row is None:
        return {
            "available": False,
            "message":   "No official-quality paper bet is available right now.",
            "trade":     None,
        }

    d = dict(row)
    edge_pp  = float(d.get("edge_pct_points")    or 0)
    price    = float(d.get("side_market_price")  or 0)
    ec_prob  = float(d.get("ec_side_probability") or 0)
    mkt_prob = float(d.get("market_yes_probability") or 0)

    direction   = d.get("direction", "")
    strat       = d.get("strategy_version", "")
    settle_date = (d.get("target_settlement_date") or "")[:10]
    lead        = d.get("lead_time_days")

    why = (
        f"EdgeCast estimates {ec_prob * 100:.1f}% for the {direction} side "
        f"(market implies {mkt_prob * 100:.1f}%). "
        f"Entry at {price * 100:.0f}¢ gives a claimed edge of {edge_pp:.1f} pp. "
        f"Settlement: {settle_date}. "
        f"Lead time: {lead if lead is not None else 'N/A'} day(s). "
        f"Strategy: {strat}."
    )

    return {
        "available": True,
        "message":   None,
        "trade": {
            "strategyVersion":       strat,
            "id":                    d.get("id"),
            "marketTicker":          d.get("market_ticker"),
            "city":                  d.get("city"),
            "weatherVariable":       d.get("weather_variable"),
            "contractType":          d.get("contract_type"),
            "targetSettlementDate":  settle_date,
            "direction":             direction,
            "ecSideProbability":     ec_prob,
            "marketYesProbability":  mkt_prob,
            "sideMarketPrice":       price,
            "edgePctPoints":         edge_pp,
            "leadTimeDays":          lead,
            "quoteAgeSecs":          d.get("quote_age_seconds"),
            "stationVerified":       d.get("station_verified"),
            "eligibilityStatus":     d.get("eligibility_status"),
            "whyWeLikeThisTrade":    why,
            "decisionExplanation":   d.get("decision_explanation"),
        },
    }


# ── Segment → analytics filter mapping ───────────────────────────────────────
# Maps segment name → kwargs forwarded to get_paper_trade_analytics /
# get_calibration_report.  Omitted keys → no filter applied.
# V3 lives in v3_paper_trades (separate table), so analytics for V3-only
# segments return empty until V3 trades settle and are joined here.
_ANALYTICS_SEGMENT_FILTERS: dict[str, dict] = {
    "current_exp":   {"strategy_versions": ["v2.1", "v2.2"], "is_executable": True},
    "v21_only":      {"strategy_versions": ["v2.1"],          "is_executable": True},
    "v22_only":      {"strategy_versions": ["v2.2"],          "is_executable": True},
    "current_v2":    {"strategy_versions": ["v2.1", "v2.2"], "is_executable": True},
    "paired":        {"strategy_versions": ["v2.1", "v2.2"], "is_executable": True},
    "paired_v2":     {"strategy_versions": ["v2.1", "v2.2"], "is_executable": True},
    "v3_challenger": {"strategy_versions": [],                "is_executable": True},
    "legacy":        {"strategy_versions": ["v1.0", "v2.0"], "is_executable": None},
    "research":      {"strategy_versions": ["v2.1", "v2.2"], "is_executable": False},
    # "all" is intentionally absent → no filter (full contaminated view)
}


@router.get("/paper-trades/analytics")
async def get_analytics(
    segment: str | None = Query(default=None),
    strategy_version: str | None = Query(default=None),
    include_flagged: bool = Query(default=True),
    fee_pct: float = Query(default=0.0, ge=0.0, le=100.0),
    slippage_pct: float = Query(default=0.0, ge=0.0, le=100.0),
    spread_adj: float = Query(default=0.0, ge=0.0, le=100.0),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Performance breakdowns by direction, edge bucket, price bucket, city,
    contract type, and lead time, plus cumulative and daily P/L series.

    segment — one of the same segments accepted by /paper-trades/metrics
      (current_exp, current_v2, v3_challenger, paired, paired_v2, legacy,
       research, all).  When supplied, overrides strategy_version and sets
       appropriate is_executable filter automatically.  Omit or pass "all"
       for the unfiltered view.

    Optional realistic-result adjustments (fee_pct, slippage_pct, spread_adj
    expressed as % of stake) produce adjProfitLoss / adjRoi alongside raw figures.
    These are simplified model approximations — not guaranteed real-world performance.
    """
    seg_filters = _ANALYTICS_SEGMENT_FILTERS.get(segment or "", {})
    return await get_paper_trade_analytics(
        db,
        strategy_version=strategy_version if not seg_filters else None,
        strategy_versions=seg_filters.get("strategy_versions"),
        is_executable=seg_filters.get("is_executable"),
        include_flagged=include_flagged,
        fee_pct=fee_pct,
        slippage_pct=slippage_pct,
        spread_adj=spread_adj,
    )


@router.get("/paper-trades/calibration")
async def get_calibration(
    segment: str | None = Query(default=None),
    strategy_version: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Probability calibration report comparing EdgeCast YES-probability estimates
    against actual Kalshi settlement outcomes, plus overall Brier score.
    Uses only settled (non-void) trades.

    segment — same as /paper-trades/analytics; overrides strategy_version when set.
    """
    seg_filters = _ANALYTICS_SEGMENT_FILTERS.get(segment or "", {})
    return await get_calibration_report(
        db,
        strategy_version=strategy_version if not seg_filters else None,
        strategy_versions=seg_filters.get("strategy_versions"),
        is_executable=seg_filters.get("is_executable"),
    )


@router.post("/paper-trades/settle-now")
async def settle_now(
    _user: dict = Depends(get_current_user),
):
    """
    Trigger an immediate settlement check for all open paper trades.
    Normally runs automatically every 3 hours; this allows a manual pass.
    """
    from app.services.settlement import run_settlement_job
    stats = await run_settlement_job()
    return {
        "checked": stats.get("checked", 0),
        "settled": stats.get("settled", 0),
        "voided": stats.get("voided", 0),
        "pendingSettlement": stats.get("pending_settlement", 0),
        "errors": stats.get("errors", 0),
        "stillOpen": stats.get("still_open", 0),
    }


@router.get("/paper-trades/settings")
async def get_settings(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Return current paper-trading configuration."""
    return await get_paper_trade_settings(db)


@router.put("/paper-trades/settings")
async def update_settings(
    body: PaperTradeSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Update paper-trading settings.
    Changing settings NEVER modifies historical trades.
    Changing strategy_version causes future trades to be created under the new version;
    existing v1 trades are permanently preserved under their original version.
    """
    if body.min_edge_pct is not None and not (0.0 < body.min_edge_pct <= 100.0):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_edge_pct must be between 0 and 100",
        )
    if body.stake is not None and body.stake <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="stake must be positive",
        )
    if body.min_confidence is not None and body.min_confidence not in (
        "Very High", "High", "Medium", "Low", "Very Low"
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_confidence must be one of: Very High, High, Medium, Low, Very Low",
        )
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    return await save_paper_trade_settings(db, updates)


@router.get("/paper-trades")
async def list_paper_trades(
    status_filter: str | None = Query(default=None, alias="status"),
    direction: str | None = Query(default=None),
    confidence: str | None = Query(default=None),
    city: str | None = Query(default=None),
    contract_type: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    strategy_version: str | None = Query(default=None),
    edge_bucket_filter: str | None = Query(default=None, alias="edge_bucket"),
    price_bucket_filter: str | None = Query(default=None, alias="price_bucket"),
    is_flagged: bool | None = Query(default=None),
    outcome: str | None = Query(default=None),
    limit: int = Query(default=200, le=1000),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    List paper trades with optional filters.

    Filter params:
      status           OPEN | SETTLED | VOID | ERROR
      direction        YES | NO
      confidence       confidence label substring
      city             city name substring
      contract_type    threshold | range | hourly_threshold
      date_from/to     ISO date — filter by target_settlement_date
      strategy_version exact match (e.g. v1.0)
      edge_bucket      <10pp | 10-20pp | 20-30pp | 30-40pp | ≥40pp
      price_bucket     1-5¢ | 6-15¢ | 16-30¢ | 31-50¢ | >50¢
      is_flagged       true | false
      outcome          WIN | LOSS | VOID
    """
    q = select(PaperTrade).order_by(PaperTrade.created_at.desc()).limit(limit)
    result = await db.execute(q)
    trades = result.scalars().all()

    filtered = [
        _trade_to_dict(t) for t in trades
        if _matches_filters(
            t, status_filter, direction, confidence, city, contract_type,
            date_from, date_to, strategy_version, edge_bucket_filter,
            price_bucket_filter, is_flagged, outcome,
        )
    ]
    return {"trades": filtered, "total": len(filtered)}


@router.get("/paper-trades/export.csv")
async def export_paper_trades(
    status_filter: str | None = Query(default=None, alias="status"),
    direction: str | None = Query(default=None),
    confidence: str | None = Query(default=None),
    city: str | None = Query(default=None),
    contract_type: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    strategy_version: str | None = Query(default=None),
    edge_bucket_filter: str | None = Query(default=None, alias="edge_bucket"),
    price_bucket_filter: str | None = Query(default=None, alias="price_bucket"),
    is_flagged: bool | None = Query(default=None),
    outcome: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """CSV export of filtered paper trades."""
    q = select(PaperTrade).order_by(PaperTrade.created_at.desc())
    result = await db.execute(q)
    trades = result.scalars().all()

    filtered = [
        t for t in trades
        if _matches_filters(
            t, status_filter, direction, confidence, city, contract_type,
            date_from, date_to, strategy_version, edge_bucket_filter,
            price_bucket_filter, is_flagged, outcome,
        )
    ]

    CSV_FIELDS = [
        "id", "createdAt", "marketTicker", "city", "strategyVersion",
        "direction", "status", "outcome", "contractType", "targetSettlementDate",
        "ecYesProbability", "marketYesProbability", "sideMarketPrice", "edgePctPoints",
        "confidenceLabel", "stake", "quantity", "leadTimeDays",
        "grossPayout", "profitLoss", "returnPct", "settlementTimestamp",
        "kalshiResult", "isFlagged", "qualityFlags",
    ]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for t in filtered:
        row = _trade_to_dict(t)
        # Flatten list fields for CSV
        row["qualityFlags"] = "|".join(row.get("qualityFlags") or [])
        writer.writerow(row)

    buf.seek(0)
    filename = f"paper_trades_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/paper-trades/{trade_id}")
async def get_paper_trade(
    trade_id: int,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Full detail for a single paper trade, including linked snapshot and market data."""
    trade_q = await db.execute(
        select(PaperTrade).where(PaperTrade.id == trade_id)
    )
    trade = trade_q.scalar_one_or_none()
    if trade is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found")

    result = _trade_to_dict(trade)

    # Attach linked market info (prices at time of collection — best approximation of entry)
    market_q = await db.execute(
        select(KalshiMarket).where(KalshiMarket.ticker == trade.market_ticker)
    )
    market = market_q.scalar_one_or_none()
    if market:
        result["market"] = {
            "title": market.title,
            "subtitle": market.subtitle,
            "targetDate": market.target_date,
            "openTime": market.open_time.isoformat() if market.open_time else None,
            "closeTime": market.close_time.isoformat() if market.close_time else None,
            "yesBid": market.yes_bid,
            "yesAsk": market.yes_ask,
            "noBid": market.no_bid,
            "noAsk": market.no_ask,
            "volume": market.volume,
            "weatherMarketType": market.weather_market_type,
            "collectionTimestamp": market.collection_timestamp.isoformat() if market.collection_timestamp else None,
        }
    else:
        result["market"] = None

    # Attach snapshot info
    if trade.snapshot_id:
        snap_q = await db.execute(
            select(PredictionSnapshot).where(PredictionSnapshot.id == trade.snapshot_id)
        )
        snap = snap_q.scalar_one_or_none()
        if snap:
            result["snapshot"] = {
                "id": snap.id,
                "createdAt": snap.created_at.isoformat() if snap.created_at else None,
                "forecastDate": snap.forecast_date,
                "forecastValue": snap.forecast_value,
                "forecastRetrievedAt": snap.forecast_retrieved_at.isoformat() if snap.forecast_retrieved_at else None,
                "leadTimeDays": snap.lead_time_days,
                "ecProbability": snap.ec_probability,
                "marketProbability": snap.market_probability,
                "confidence": snap.confidence,
                "explanation": snap.explanation,
                "settlementVariable": snap.settlement_variable,
                "settlementOperator": snap.settlement_operator,
                "settlementThreshold": snap.settlement_threshold,
                "contractType": snap.contract_type,
                "targetHour": snap.target_hour,
                "targetTimezoneStr": snap.target_timezone_str,
                "lowerBound": snap.lower_bound,
                "upperBound": snap.upper_bound,
                "analysisStatus": snap.analysis_status,
                "analysisReason": snap.analysis_reason,
            }
        else:
            result["snapshot"] = None
    else:
        result["snapshot"] = None

    return result
