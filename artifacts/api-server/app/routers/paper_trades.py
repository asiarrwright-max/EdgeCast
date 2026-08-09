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
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    ForecastVerification,
    JobRun,
    KalshiMarket,
    PaperTrade,
    PredictionSnapshot,
)
from app.models_v3 import V3PaperTrade, V3PredictionSnapshot
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

# ── Forward-test constants ────────────────────────────────────────────────────

# Exact UTC timestamp of the hardened main commit 76e4e7d going live on the
# API server.  Only trades created at or after this moment count toward
# readiness metrics.  Display as "August 4, 2026" in the UI.
FORWARD_TEST_START = datetime(2026, 8, 4, 22, 21, 44, tzinfo=timezone.utc)
FORWARD_TEST_START_VERSION = "76e4e7d"
FORWARD_TEST_PHASE = "Collecting clean paper-trade data"
FORWARD_TEST_SETTLED_TARGET = 50

# Forward Test B — set after corrections are deployed and runtime-verified.
# None = "Preparing Forward Test B" (not yet activated).
# Must be set to the exact UTC deployment timestamp of the correction commit.
FORWARD_TEST_START_B: datetime | None = datetime(2026, 8, 9, 0, 15, 12, tzinfo=timezone.utc)
FORWARD_TEST_PHASE_B = "Forward Test B active"

# Set to True only after an explicit manual review of ROI, calibration,
# strategy stability, and drawdown — never flip automatically.
MANUAL_READINESS_APPROVAL: bool = False

# Job types written by app/services/collector.py that scan markets and create
# paper-trade candidates.  Only these runs define a "collection batch" window
# for the "Why no official bet?" reason breakdown.
#   "manual"    = operator-triggered via POST /api/jobs/trigger
#   "scheduled" = background scheduler tick
COLLECTION_JOB_TYPES: tuple[str, ...] = ("manual", "scheduled")

# Eligibility reason codes → human-readable labels for the "Why no bet?" panel.
ELIGIBILITY_REASON_LABELS: dict[str, str] = {
    "missing_or_stale_executable_quote": "Stale or missing quote",
    "cutoff_unverified_or_too_close":    "Too close to market close",
    "same_day_not_approved":             "Same-day market",
    "entry_price_below_official_floor":  "Entry price below 20¢",
    "settlement_station_unverified":     "Station unverified",
    "extreme_edge_requires_validation":  "Extreme claimed edge",
    "correlated_outcome_limit":          "Correlated exposure limit",
    "hourly_temperature_not_approved":   "Hourly contract not approved",
}


def _ft_readiness_label(settled: int) -> str:
    """Map official settled trade count → forward-test readiness stage.

    Automatic progression caps at 'Promising but unproven'.
    'Ready for tiny manual testing' and 'Strong forward-test evidence' require
    MANUAL_READINESS_APPROVAL = True — sample size alone is not sufficient.
    """
    if settled < 10:
        return "Not enough data"
    if settled < 50:
        return "Early signal"
    return "Promising but unproven"


def _ft_next_milestone(settled: int) -> str:
    """Human-readable next milestone based on settled count."""
    if settled < 10:
        return "10 settled official trades"
    if settled < 50:
        return "50 settled official trades (minimum review point)"
    return "Manual review required for further advancement"


def _ft_progress_pct(settled: int, target: int = FORWARD_TEST_SETTLED_TARGET) -> float:
    """Progress toward target as a percentage, capped at 100.0."""
    if target <= 0:
        return 100.0
    return round(min(settled / target * 100.0, 100.0), 2)


def _ft_readiness_for_real_money(settled: int) -> str:
    """Plain-language current-readiness string for the status card.

    Only returns a non-'Not ready' value when MANUAL_READINESS_APPROVAL is True
    AND sufficient settled trades exist.  Sample size alone never triggers this.
    """
    if MANUAL_READINESS_APPROVAL and settled >= 100:
        return "Ready for tiny manual testing"
    return "Not ready for real money"


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


def _selected_side_values(
    direction: str,
    ec_side_probability: float,
    market_yes_probability: float,   # retained for callers; NOT used in selectedSideMarketProbability
    side_market_price: float,
    edge_pct_points: float,
) -> dict:
    """
    Compute selected-side display values from stored trade columns.

    ec_side_probability    — model probability already rotated to the chosen side
    market_yes_probability — raw Kalshi YES market probability; kept as a legacy/raw field only
    side_market_price      — executable ask on the chosen side (yes_ask for YES; no_ask for NO)
    edge_pct_points        — edge in pp (direction-corrected)

    selectedSideMarketProbability == selectedSideAsk == side_market_price.
    In a Kalshi binary market the executable ask IS the market-implied probability (e.g. 0.50 ask
    = 50 ¢ per $1 = 50 % market probability).  Do NOT compute as 1 − market_yes_probability;
    that uses the raw mid-market YES price which differs from the actual executable ask.
    """
    return {
        "selectedSide":                  direction,
        "selectedSideModelProbability":  ec_side_probability,
        "selectedSideAsk":               side_market_price,
        "selectedSideMarketProbability": round(side_market_price, 6),  # = ask, not 1 − yes_prob
        "selectedSideEdgePctPoints":     edge_pct_points,
    }


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

    # PostgreSQL forbids ORDER BY expressions directly on a UNION ALL.
    # Wrap in a subquery so the outer ORDER BY references a plain column alias.
    best_sql = sql_text("""
        SELECT *
        FROM (
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
                created_at,
                edge_pct_points / GREATEST(side_market_price, 0.001) AS ev_ratio
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
                created_at,
                edge_pct_points / GREATEST(side_market_price, 0.001) AS ev_ratio
            FROM v3_paper_trades
            WHERE eligibility_status = 'OFFICIAL'
              AND is_executable = TRUE
              AND status        = 'OPEN'
        ) AS candidates
        ORDER BY ev_ratio DESC
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
    direction   = d.get("direction", "")
    strat       = d.get("strategy_version", "")
    settle_date = (d.get("target_settlement_date") or "")[:10]
    lead        = d.get("lead_time_days")

    # ec_side_probability is already rotated to the chosen side — use as-is for both YES and NO.
    ec_side_prob   = float(d.get("ec_side_probability") or 0)
    mkt_yes_prob   = float(d.get("market_yes_probability") or 0)
    side_ask       = float(d.get("side_market_price") or 0)   # YES ask for YES trades; NO ask for NO trades
    edge_pp        = float(d.get("edge_pct_points") or 0)

    # selectedSideMarketProbability = selectedSideAsk = the executable ask on the chosen side.
    # In a Kalshi binary market the ask price IS the market-implied probability.
    # Never compute as 1 − market_yes_probability; that uses the raw mid-market YES price.
    selected_side_mkt_prob = side_ask   # == side_market_price

    why = (
        f"EdgeCast estimates {ec_side_prob * 100:.1f}% for the {direction} side "
        f"(market ask implies {selected_side_mkt_prob * 100:.1f}¢ on the {direction} side). "
        f"Entry at {side_ask * 100:.0f}¢ gives a claimed edge of {edge_pp:.1f} pp. "
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
            # ── Selected-side fields (correct for both YES and NO) ──
            "selectedSide":                  direction,
            "selectedSideModelProbability":  ec_side_prob,
            "selectedSideAsk":               side_ask,
            "selectedSideMarketProbability": selected_side_mkt_prob,
            "selectedSideEdgePctPoints":     edge_pp,
            # ── Legacy fields kept for frontend compatibility ──
            "ecSideProbability":     ec_side_prob,
            "marketYesProbability":  mkt_yes_prob,   # raw YES prob; not used in calculations above
            "sideMarketPrice":       side_ask,
            "edgePctPoints":         edge_pp,
            # ── Other fields ──
            "leadTimeDays":          lead,
            "quoteAgeSecs":          d.get("quote_age_seconds"),
            "stationVerified":       d.get("station_verified"),
            "eligibilityStatus":     d.get("eligibility_status"),
            "whyWeLikeThisTrade":    why,
            "decisionExplanation":   d.get("decision_explanation"),
        },
    }


@router.get("/paper-trades/forward-test-status")
async def get_forward_test_status(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Return the forward-test progress card data.

    Only trades with eligibility_status = 'OFFICIAL' created on or after
    FORWARD_TEST_START count toward readiness metrics.  RESEARCH_ONLY and
    legacy (pre-start) trades are tracked separately and never mixed in.
    No historical rows are altered by this endpoint.
    """
    start = FORWARD_TEST_START

    # ── Helper: count rows matching all criteria ───────────────────────────────
    async def _ct(model, *criteria):
        q = select(func.count()).select_from(model)
        for c in criteria:
            q = q.where(c)
        return (await db.execute(q)).scalar_one() or 0

    # ── V2.2 post-hardening counts (paper_trades table) ───────────────────────
    # V2.2/V2.3 combined — v2.3 is the corrected continuation of v2.2 (same engine,
    # corrected constants); both count toward the same forward-test strategy slot.
    _v2x_versions = ["v2.2", "v2.3"]
    v22_off_settled = await _ct(
        PaperTrade,
        PaperTrade.created_at >= start,
        PaperTrade.eligibility_status == "OFFICIAL",
        PaperTrade.status == "SETTLED",
        PaperTrade.strategy_version.in_(_v2x_versions),
    )
    v22_off_open = await _ct(
        PaperTrade,
        PaperTrade.created_at >= start,
        PaperTrade.eligibility_status == "OFFICIAL",
        PaperTrade.status == "OPEN",
        PaperTrade.strategy_version.in_(_v2x_versions),
    )
    v22_research = await _ct(
        PaperTrade,
        PaperTrade.created_at >= start,
        PaperTrade.eligibility_status == "RESEARCH_ONLY",
        PaperTrade.strategy_version.in_(_v2x_versions),
    )

    # ── V3 post-hardening counts (v3_paper_trades table) ─────────────────────
    v3_off_settled = await _ct(
        V3PaperTrade,
        V3PaperTrade.created_at >= start,
        V3PaperTrade.eligibility_status == "OFFICIAL",
        V3PaperTrade.status == "SETTLED",
    )
    v3_off_open = await _ct(
        V3PaperTrade,
        V3PaperTrade.created_at >= start,
        V3PaperTrade.eligibility_status == "OFFICIAL",
        V3PaperTrade.status == "OPEN",
    )
    v3_research = await _ct(
        V3PaperTrade,
        V3PaperTrade.created_at >= start,
        V3PaperTrade.eligibility_status == "RESEARCH_ONLY",
    )

    # ── Combined totals ───────────────────────────────────────────────────────
    total_off_settled = v22_off_settled + v3_off_settled
    total_off_open    = v22_off_open    + v3_off_open
    total_research    = v22_research    + v3_research

    # ── Legacy trades (both tables, created before forward-test start) ─────────
    legacy_pt = await _ct(PaperTrade,    PaperTrade.created_at    < start)
    legacy_v3 = await _ct(V3PaperTrade,  V3PaperTrade.created_at  < start)
    legacy_excluded = legacy_pt + legacy_v3

    # ── "Why no official bet?" — batch-aware reason breakdown ────────────────
    # Prefer the most recent completed collection job as the window so we
    # show what happened *right now*, not cumulative all-time counts.
    # Fall back to the past 24 hours if no recent job is found.
    now_utc = datetime.now(tz=timezone.utc)
    _BATCH_STALE_HOURS = 25  # treat a job older than this as stale

    # Filter to collection-only job types (defined at module level).
    # See: COLLECTION_JOB_TYPES constant below the forward-test constants block.

    latest_job = (
        await db.execute(
            select(JobRun)
            .where(JobRun.job_type.in_(COLLECTION_JOB_TYPES))
            .where(JobRun.status == "success")
            .where(JobRun.completed_at.is_not(None))
            .order_by(JobRun.completed_at.desc())
            .limit(1)
        )
    ).scalars().first()

    if (
        latest_job is not None
        and (now_utc - latest_job.started_at).total_seconds() < _BATCH_STALE_HOURS * 3600
    ):
        reason_window_start = latest_job.started_at
        reason_window_label = "Latest collection batch"
    else:
        reason_window_start = now_utc - timedelta(hours=24)
        reason_window_label = "Past 24 hours"

    async def _reason_counts(model, window_start: datetime) -> dict[str, int]:
        rows = (
            await db.execute(
                select(model.eligibility_reason, func.count().label("n"))
                .where(model.created_at >= window_start)
                .where(model.eligibility_status == "RESEARCH_ONLY")
                .group_by(model.eligibility_reason)
            )
        ).all()
        return {row[0]: row[1] for row in rows if row[0]}

    rc_pt = await _reason_counts(PaperTrade, reason_window_start)
    rc_v3 = await _reason_counts(V3PaperTrade, reason_window_start)
    windowed_reasons: dict[str, int] = {}
    for code, n in list(rc_pt.items()) + list(rc_v3.items()):
        windowed_reasons[code] = windowed_reasons.get(code, 0) + n

    why_no_bet = {
        code: {"label": label, "count": windowed_reasons.get(code, 0)}
        for code, label in ELIGIBILITY_REASON_LABELS.items()
    }

    return {
        "phase":                   FORWARD_TEST_PHASE,
        "forwardTestStartDate":    FORWARD_TEST_START.strftime("%Y-%m-%d"),
        "startingCodeVersion":     FORWARD_TEST_START_VERSION,
        "officialSettledCount":    total_off_settled,
        "officialOpenCount":       total_off_open,
        "researchOnlyCount":       total_research,        # cumulative since start
        "legacyExcludedCount":     legacy_excluded,
        "progressPct":             _ft_progress_pct(total_off_settled),
        "progressTarget":          FORWARD_TEST_SETTLED_TARGET,
        "readinessLabel":          _ft_readiness_label(total_off_settled),
        "nextMilestone":           _ft_next_milestone(total_off_settled),
        "currentReadiness":        _ft_readiness_for_real_money(total_off_settled),
        "manualReadinessApproval": MANUAL_READINESS_APPROVAL,
        "whyNoOfficialBet":        why_no_bet,
        "reasonBreakdownWindow":   reason_window_label,
        "byStrategy": {
            "v22": {
                "officialSettled": v22_off_settled,
                "officialOpen":    v22_off_open,
                "researchOnly":    v22_research,
            },
            "v3": {
                "officialSettled": v3_off_settled,
                "officialOpen":    v3_off_open,
                "researchOnly":    v3_research,
            },
        },
        "explanation": (
            "EdgeCast is currently collecting clean forward-test results. "
            "Only OFFICIAL trades created after the hardened rules were deployed "
            "count toward readiness. Older trades remain available as research "
            "history but are excluded from the new score."
        ),
        "forwardTestB": {
            "phase":      FORWARD_TEST_PHASE_B,
            "startDate":  FORWARD_TEST_START_B.strftime("%Y-%m-%d") if FORWARD_TEST_START_B else None,
            "activated":  FORWARD_TEST_START_B is not None,
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


@router.get("/paper-trades/forward-test-diagnostics")
async def get_forward_test_diagnostics(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Forward-test calibration diagnostic report (READ-ONLY).

    Returns probability-band calibration, Brier Score, Log Loss, ECE, MACE,
    false-confidence losses (model ≥85%, outcome = LOSS), and settlement
    integrity flags for settled OFFICIAL trades since the forward-test start.

    Nothing is written or modified.
    """
    import math
    import re
    from datetime import timezone as _tz
    from sqlalchemy import tuple_ as _tuple

    # ── Probability bands ─────────────────────────────────────────────────────
    PROB_BANDS: list[tuple[str, float, float]] = [
        ("<50%",    0.00, 0.50),
        ("50–59%",  0.50, 0.60),
        ("60–69%",  0.60, 0.70),
        ("70–79%",  0.70, 0.80),
        ("80–84%",  0.80, 0.85),
        ("85–89%",  0.85, 0.90),
        ("90–94%",  0.90, 0.95),
        ("95–100%", 0.95, 1.01),
    ]

    # ── 1. Fetch paper_trades settled OFFICIAL + prediction snapshots ──────────
    pt_stmt = (
        select(PaperTrade, PredictionSnapshot)
        .outerjoin(
            PredictionSnapshot,
            PredictionSnapshot.id == PaperTrade.snapshot_id,
        )
        .where(
            PaperTrade.created_at >= FORWARD_TEST_START,
            PaperTrade.eligibility_status == "OFFICIAL",
            PaperTrade.status == "SETTLED",
        )
    )
    pt_rows = (await db.execute(pt_stmt)).all()

    # ── 2. Fetch v3_paper_trades settled OFFICIAL + v3 prediction snapshots ───
    v3_stmt = (
        select(V3PaperTrade, V3PredictionSnapshot)
        .outerjoin(
            V3PredictionSnapshot,
            V3PredictionSnapshot.id == V3PaperTrade.v3_snapshot_id,
        )
        .where(
            V3PaperTrade.created_at >= FORWARD_TEST_START,
            V3PaperTrade.eligibility_status == "OFFICIAL",
            V3PaperTrade.status == "SETTLED",
        )
    )
    v3_rows = (await db.execute(v3_stmt)).all()

    if not pt_rows and not v3_rows:
        return {
            "sampleWarning": "No settled OFFICIAL forward-test trades yet.",
            "asOf": datetime.now(timezone.utc).isoformat(),
            "forwardTestStart": FORWARD_TEST_START.isoformat(),
            "settledCount": 0,
            "wins": 0, "losses": 0, "winRatePct": 0.0,
            "totalStake": 0.0, "totalPl": 0.0, "roiPct": 0.0,
            "avgPredictedProbPct": 0.0, "avgEntryPrice": 0.0, "avgClaimedEdgePp": 0.0,
            "brierScore": None, "logLoss": None,
            "expectedCalibrationErrorPct": None, "meanAbsCalibrationErrorPct": None,
            "calibrationBands": [], "byStrategy": [], "byDirection": [],
            "byEdgeBucket": [], "byEntryPriceBucket": [],
            "falseConfidenceLosses": [], "settlementIntegrityFlags": [],
            "chartPoints": [],
        }

    # ── 3. ERA5 / GHCND actual values ─────────────────────────────────────────
    combos: set[tuple[str, str, str]] = set()
    for pt, _ in pt_rows:
        combos.add((pt.city, pt.weather_variable, str(pt.target_settlement_date)[:10]))
    for v3, _ in v3_rows:
        combos.add((v3.city, v3.weather_variable, str(v3.target_settlement_date)[:10]))

    fv_map: dict[tuple[str, str, str], Any] = {}
    if combos:
        fv_stmt = select(ForecastVerification).where(
            _tuple(
                ForecastVerification.city,
                ForecastVerification.weather_variable,
                ForecastVerification.target_date,
            ).in_(list(combos)),
            ForecastVerification.source_label.in_(
                ["ghcnd_observation", "ghcnd_observation_unverified", "era5_reanalysis"]
            ),
        )
        SOURCE_RANK = {
            "ghcnd_observation": 3,
            "ghcnd_observation_unverified": 2,
            "era5_reanalysis": 1,
        }
        for fv in (await db.execute(fv_stmt)).scalars():
            key = (fv.city, fv.weather_variable, fv.target_date)
            existing = fv_map.get(key)
            if existing is None or (
                SOURCE_RANK.get(fv.source_label, 0) > SOURCE_RANK.get(existing.source_label, 0)
            ):
                fv_map[key] = fv

    # ── 4. Kalshi rules (settlement station text) ─────────────────────────────
    all_tickers = {pt.market_ticker for pt, _ in pt_rows} | {v3.market_ticker for v3, _ in v3_rows}
    km_stmt = select(KalshiMarket.ticker, KalshiMarket.raw_data).where(
        KalshiMarket.ticker.in_(list(all_tickers))
    )
    km_map: dict[str, Any] = {
        row[0]: (row[1] or {}) for row in (await db.execute(km_stmt)).all()
    }

    def _kalshi_station(ticker: str) -> str | None:
        rules = km_map.get(ticker, {}).get("rules_primary", "") or ""
        m = re.search(r"recorded at (.+?) for", rules)
        return m.group(1).strip() if m else None

    def _model_station(explanation: str | None) -> tuple[str | None, str | None]:
        if not explanation:
            return None, None
        m = re.search(r"Station:\s*([^(]+)\s*\(([^)]+)\)", explanation)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return None, None

    # ── 5. Build normalised trade dicts ───────────────────────────────────────
    def _norm_pt(pt: PaperTrade, snap: PredictionSnapshot | None) -> dict[str, Any]:
        date = str(pt.target_settlement_date)[:10]
        fv = fv_map.get((pt.city, pt.weather_variable, date))
        sname, sid = _model_station(pt.decision_explanation)
        forecast_val = snap.forecast_value if snap else None
        era5_actual  = fv.actual_value if fv else None
        dec_err = (
            round(forecast_val - era5_actual, 4)
            if forecast_val is not None and era5_actual is not None
            else None
        )
        lower = snap.lower_bound if snap else None
        upper = snap.upper_bound if snap else None
        threshold = snap.settlement_threshold if snap else None
        op = snap.settlement_operator if snap else None
        era5_pred = _era5_predicted(era5_actual, lower, upper, threshold, op)
        return {
            "table": "paper_trades", "id": pt.id,
            "marketTicker": pt.market_ticker, "city": pt.city,
            "weatherVariable": pt.weather_variable, "contractType": pt.contract_type,
            "targetSettlementDate": date, "strategyVersion": pt.strategy_version,
            "direction": pt.direction,
            "ecSideProb": pt.ec_side_probability or 0.0,
            "sideMarketPrice": pt.side_market_price or 0.0,
            "edgePctPoints": pt.edge_pct_points or 0.0,
            "leadTimeDays": pt.lead_time_days or 0,
            "sigmaUsed": pt.sigma_used,
            "fallbackLevel": pt.fallback_level,
            "confidenceLabel": pt.confidence_label,
            "stake": pt.stake or 0.0, "profitLoss": pt.profit_loss or 0.0,
            "outcome": pt.outcome, "kalshiResult": pt.kalshi_result,
            "decisionTimestamp": pt.decision_timestamp.isoformat() if pt.decision_timestamp else None,
            "settlementTimestamp": pt.settlement_timestamp.isoformat() if pt.settlement_timestamp else None,
            "stationLat": pt.station_lat, "stationLon": pt.station_lon,
            "modelStationName": sname, "modelStationId": sid,
            "kalshiSettlementStation": _kalshi_station(pt.market_ticker),
            "forecastValue": forecast_val,
            "lowerBound": lower, "upperBound": upper,
            "settlementThreshold": threshold, "settlementOperator": op,
            "era5Actual": era5_actual, "decisionForecastError": dec_err,
            "era5PredictedResult": era5_pred,
            "era5SourceLabel": fv.source_label if fv else None,
            "era5GhcndStationId": fv.ghcnd_station_id if fv else None,
        }

    def _norm_v3(v3: V3PaperTrade, snap: V3PredictionSnapshot | None) -> dict[str, Any]:
        date = str(v3.target_settlement_date)[:10]
        fv = fv_map.get((v3.city, v3.weather_variable, date))
        sname, sid = _model_station(v3.decision_explanation)
        forecast_val = snap.forecast_value if snap else None
        era5_actual  = fv.actual_value if fv else None
        dec_err = (
            round(forecast_val - era5_actual, 4)
            if forecast_val is not None and era5_actual is not None
            else None
        )
        era5_pred = _era5_predicted(era5_actual, None, None, None, None)
        return {
            "table": "v3_paper_trades", "id": v3.id,
            "marketTicker": v3.market_ticker, "city": v3.city,
            "weatherVariable": v3.weather_variable, "contractType": v3.contract_type,
            "targetSettlementDate": date, "strategyVersion": v3.strategy_version or "v3.0",
            "direction": v3.direction,
            "ecSideProb": v3.ec_side_probability or 0.0,
            "sideMarketPrice": v3.side_market_price or 0.0,
            "edgePctPoints": v3.edge_pct_points or 0.0,
            "leadTimeDays": v3.lead_time_days or 0,
            "sigmaUsed": v3.historical_sigma,
            "fallbackLevel": str(v3.fallback_level_used) if v3.fallback_level_used is not None else None,
            "confidenceLabel": None,
            "stake": v3.stake or 0.0, "profitLoss": v3.profit_loss or 0.0,
            "outcome": v3.outcome, "kalshiResult": v3.kalshi_result,
            "decisionTimestamp": v3.decision_timestamp.isoformat() if v3.decision_timestamp else None,
            "settlementTimestamp": v3.settlement_timestamp.isoformat() if v3.settlement_timestamp else None,
            "stationLat": v3.station_lat, "stationLon": v3.station_lon,
            "modelStationName": sname, "modelStationId": sid,
            "kalshiSettlementStation": _kalshi_station(v3.market_ticker),
            "forecastValue": forecast_val,
            "lowerBound": None, "upperBound": None,
            "settlementThreshold": None, "settlementOperator": None,
            "era5Actual": era5_actual, "decisionForecastError": dec_err,
            "era5PredictedResult": era5_pred,
            "era5SourceLabel": fv.source_label if fv else None,
            "era5GhcndStationId": fv.ghcnd_station_id if fv else None,
        }

    def _era5_predicted(
        actual: float | None,
        lower: float | None, upper: float | None,
        threshold: float | None, op: str | None,
    ) -> str | None:
        if actual is None:
            return None
        if lower is not None and upper is not None:
            return "yes" if lower <= actual < upper + 1 else "no"
        if threshold is not None and op:
            if op == "gte":
                return "yes" if actual >= threshold else "no"
            if op == "lte":
                return "yes" if actual <= threshold else "no"
        return None

    trades: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for pt, snap in pt_rows:
        k = f"pt:{pt.id}"
        if k not in seen_ids:
            seen_ids.add(k)
            trades.append(_norm_pt(pt, snap))
    for v3, snap in v3_rows:
        k = f"v3:{v3.id}"
        if k not in seen_ids:
            seen_ids.add(k)
            trades.append(_norm_v3(v3, snap))

    n = len(trades)
    if n == 0:
        return {"settledCount": 0, "sampleWarning": "No settled OFFICIAL forward-test trades.", "calibrationBands": []}

    # ── 6. Metric helpers ─────────────────────────────────────────────────────
    def _is_win(t: dict) -> bool:
        return t["outcome"] == "WIN"

    def _grp(rows: list[dict]) -> dict[str, Any] | None:
        if not rows:
            return None
        wins = sum(1 for t in rows if _is_win(t))
        pl   = sum(t["profitLoss"] for t in rows)
        stk  = sum(t["stake"] for t in rows)
        wr   = wins / len(rows) * 100 if rows else 0.0
        avg_prob  = sum(t["ecSideProb"] for t in rows) / len(rows)
        avg_entry = sum(t["sideMarketPrice"] for t in rows) / len(rows)
        avg_edge  = sum(t["edgePctPoints"] for t in rows) / len(rows)
        roi = pl / stk * 100 if stk > 0 else 0.0
        cal = wr / 100 - avg_prob
        bs = sum((t["ecSideProb"] - (1.0 if _is_win(t) else 0.0)) ** 2 for t in rows) / len(rows)
        ll = -sum(
            math.log(max(0.001, t["ecSideProb"])) if _is_win(t)
            else math.log(max(0.001, 1.0 - t["ecSideProb"]))
            for t in rows
        ) / len(rows)
        return {
            "n": len(rows), "wins": wins, "losses": len(rows) - wins,
            "winRatePct": round(wr, 2), "avgPredictedProbPct": round(avg_prob * 100, 2),
            "avgEntryPrice": round(avg_entry, 6), "avgClaimedEdgePp": round(avg_edge, 2),
            "totalPl": round(pl, 4), "totalStake": round(stk, 4),
            "roiPct": round(roi, 2), "calibrationErrorPp": round(cal * 100, 2),
            "brierScore": round(bs, 4), "logLoss": round(ll, 4),
        }

    def _band_row(label: str, rows: list[dict]) -> dict[str, Any]:
        g = _grp(rows)
        return {
            "band": label,
            "numBets": g["n"] if g else 0,
            "wins": g["wins"] if g else 0,
            "losses": g["losses"] if g else 0,
            "observedWinRatePct": g["winRatePct"] if g else None,
            "avgPredictedProbPct": g["avgPredictedProbPct"] if g else None,
            "calibrationErrorPp": g["calibrationErrorPp"] if g else None,
            "avgEntryPrice": g["avgEntryPrice"] if g else None,
            "avgClaimedEdgePp": g["avgClaimedEdgePp"] if g else None,
            "totalPl": g["totalPl"] if g else None,
            "roiPct": g["roiPct"] if g else None,
        }

    def _group_row(label: str, rows: list[dict]) -> dict[str, Any]:
        g = _grp(rows) or {}
        return {"label": label, **{k: g.get(k) for k in
            ["n", "wins", "losses", "winRatePct", "avgPredictedProbPct",
             "totalPl", "roiPct", "brierScore", "logLoss", "calibrationErrorPp"]}}

    # ── 7. Calibration bands ──────────────────────────────────────────────────
    cal_bands = [
        _band_row(label, [t for t in trades if lo <= t["ecSideProb"] < hi])
        for label, lo, hi in PROB_BANDS
    ]

    # ── 8. Overall metrics ────────────────────────────────────────────────────
    overall = _grp(trades) or {}
    ece = sum(
        (len(rows) / n) * abs((sum(1 for t in rows if _is_win(t)) / len(rows)) - (sum(t["ecSideProb"] for t in rows) / len(rows)))
        for _, lo, hi in PROB_BANDS
        if (rows := [t for t in trades if lo <= t["ecSideProb"] < hi])
    )
    non_empty_bands = [
        abs((sum(1 for t in rows if _is_win(t)) / len(rows)) - (sum(t["ecSideProb"] for t in rows) / len(rows)))
        for _, lo, hi in PROB_BANDS
        if (rows := [t for t in trades if lo <= t["ecSideProb"] < hi])
    ]
    mace = sum(non_empty_bands) / len(non_empty_bands) if non_empty_bands else 0.0

    # ── 9. Group breakdowns ───────────────────────────────────────────────────
    by_strategy = [
        _group_row("v2.2",  [t for t in trades if t["strategyVersion"] == "v2.2"]),
        _group_row("v3.0",  [t for t in trades if (t["strategyVersion"] or "").startswith("v3")]),
    ]
    by_direction = [
        _group_row("YES",   [t for t in trades if t["direction"] == "YES"]),
        _group_row("NO",    [t for t in trades if t["direction"] == "NO"]),
    ]
    EDGE_BANDS = [("<5pp",0,5),("5–9.9pp",5,10),("10–14.9pp",10,15),
                  ("15–19.9pp",15,20),("20–29.9pp",20,30),("30+pp",30,999)]
    PRICE_BANDS = [("<0.50",0,0.50),("0.50–0.59",0.50,0.60),("0.60–0.69",0.60,0.70),
                   ("0.70–0.79",0.70,0.80),("0.80+",0.80,2.0)]
    by_edge = [
        _group_row(lbl, [t for t in trades if lo <= t["edgePctPoints"] < hi])
        for lbl, lo, hi in EDGE_BANDS
    ]
    by_entry_price = [
        _group_row(lbl, [t for t in trades if lo <= t["sideMarketPrice"] < hi])
        for lbl, lo, hi in PRICE_BANDS
    ]

    # ── 10. False-confidence losses ───────────────────────────────────────────
    def _thresh_str(t: dict) -> str:
        if t["lowerBound"] is not None:
            return f"{t['lowerBound']}–{t['upperBound']}°F range"
        if t["settlementThreshold"] is not None:
            return f"{t['settlementOperator']} {t['settlementThreshold']}°F"
        return ""

    def _hypothesis(t: dict) -> str:
        if t.get("integrityFlag"):
            return (
                "ERA5 and NWS disagree on the actual value — likely a station/measurement "
                "source mismatch. Verify against the NWS Daily CLI report before drawing conclusions."
            )
        cat = t.get("lossCategory", "")
        fe = t.get("decisionForecastError")
        if cat == "Forecast miss" and fe is not None:
            return (
                f"Model forecast ({t['forecastValue']}°F) diverged from ERA5 actual "
                f"({t['era5Actual']}°F) by {abs(fe):.1f}°F. "
                "ERA5 grid vs point-station difference, or the NWP forecast itself missed."
            )
        if cat == "Threshold too close":
            return (
                "Actual value fell within the range. The fixed sigma floor may "
                "underestimate uncertainty for temperatures near typical daily ranges."
            )
        return "Insufficient data for automatic classification."

    def _loss_category(t: dict) -> str:
        era5_pred = t.get("era5PredictedResult")
        if era5_pred and era5_pred != t["kalshiResult"]:
            return "Station/Settlement mismatch"
        fe = t.get("decisionForecastError")
        dist = t.get("distanceFromThreshold")
        if fe is not None and abs(fe) > 3:
            return "Forecast miss"
        if dist is not None and dist < 1.5:
            return "Threshold too close"
        if fe is not None:
            return "Forecast miss"
        return "Unknown"

    def _dist_from_threshold(t: dict) -> float | None:
        actual = t.get("era5Actual")
        if actual is None:
            return None
        lower, upper = t.get("lowerBound"), t.get("upperBound")
        if lower is not None and upper is not None:
            return round(min(abs(actual - lower), abs(actual - (upper + 1))), 3)
        thr = t.get("settlementThreshold")
        if thr is not None:
            return round(abs(actual - thr), 3)
        return None

    false_confidence_losses: list[dict] = []
    for t in trades:
        if t["ecSideProb"] >= 0.85 and t["outcome"] == "LOSS":
            integrity_flag = (
                "ERA5_KALSHI_DISAGREE"
                if t.get("era5PredictedResult") and t["era5PredictedResult"] != t["kalshiResult"]
                else None
            )
            t["integrityFlag"] = integrity_flag
            t["lossCategory"] = _loss_category(t)
            t["distanceFromThreshold"] = _dist_from_threshold(t)
            false_confidence_losses.append({
                "marketTicker": t["marketTicker"],
                "city": t["city"],
                "weatherVariable": t["weatherVariable"],
                "contractType": t["contractType"],
                "direction": t["direction"],
                "strategyVersion": t["strategyVersion"],
                "modelProbabilityPct": round(t["ecSideProb"] * 100, 2),
                "marketEntryPrice": t["sideMarketPrice"],
                "claimedEdgePp": t["edgePctPoints"],
                "forecastValueF": t.get("forecastValue"),
                "era5ActualF": t.get("era5Actual"),
                "decisionForecastError": t.get("decisionForecastError"),
                "thresholdOrRange": _thresh_str(t),
                "distanceFromThreshold": t["distanceFromThreshold"],
                "sigmaUsed": t.get("sigmaUsed"),
                "lossCategory": t["lossCategory"],
                "integrityFlag": integrity_flag,
                "hypothesis": _hypothesis(t),
            })

    # ── 11. Settlement integrity flags ────────────────────────────────────────
    integrity_flags: list[dict] = []
    for t in trades:
        era5_pred = t.get("era5PredictedResult")
        if era5_pred and era5_pred != t.get("kalshiResult"):
            integrity_flags.append({
                "marketTicker": t["marketTicker"],
                "city": t["city"],
                "weatherVariable": t["weatherVariable"],
                "targetDate": t["targetSettlementDate"],
                "direction": t["direction"],
                "outcome": t["outcome"],
                "kalshiResult": t["kalshiResult"],
                "era5ActualF": t.get("era5Actual"),
                "era5PredictedResult": era5_pred,
                "flag": "ERA5_KALSHI_DISAGREE",
                "detail": (
                    f"ERA5 actual {t['era5Actual']}°F → would give kalshi={era5_pred}, "
                    f"but Kalshi settled {t['kalshiResult']}"
                ),
                "sourceLabel": t.get("era5SourceLabel"),
            })

    # ── 12. Chart points ──────────────────────────────────────────────────────
    chart_points = [
        {
            "predictedProbPct": round(t["ecSideProb"] * 100, 2),
            "isWin": _is_win(t),
            "strategyVersion": t["strategyVersion"],
        }
        for t in trades
    ]

    return {
        "sampleWarning": (
            f"Early forward-test results — {n} settled OFFICIAL trades. "
            "Calibration conclusions are preliminary."
        ),
        "asOf": datetime.now(timezone.utc).isoformat(),
        "forwardTestStart": FORWARD_TEST_START.isoformat(),
        "settledCount": n,
        "wins": overall.get("wins", 0),
        "losses": overall.get("losses", 0),
        "winRatePct": overall.get("winRatePct", 0.0),
        "totalStake": overall.get("totalStake", 0.0),
        "totalPl": overall.get("totalPl", 0.0),
        "roiPct": overall.get("roiPct", 0.0),
        "avgPredictedProbPct": overall.get("avgPredictedProbPct", 0.0),
        "avgEntryPrice": overall.get("avgEntryPrice", 0.0),
        "avgClaimedEdgePp": overall.get("avgClaimedEdgePp", 0.0),
        "brierScore": overall.get("brierScore"),
        "logLoss": overall.get("logLoss"),
        "expectedCalibrationErrorPct": round(ece * 100, 2),
        "meanAbsCalibrationErrorPct": round(mace * 100, 2),
        "calibrationBands": cal_bands,
        "byStrategy": by_strategy,
        "byDirection": by_direction,
        "byEdgeBucket": by_edge,
        "byEntryPriceBucket": by_entry_price,
        "falseConfidenceLosses": false_confidence_losses,
        "settlementIntegrityFlags": integrity_flags,
        "chartPoints": chart_points,
    }


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
