"""
EdgeCast Audit Router
=====================
Provides read-only diagnostic and research endpoints for:
  1. Strategy Differences — v1 vs v2 side-by-side per market
  2. V1 Loss Audit — probability-bucketed win/loss breakdown
  3. Long-shot Analysis — entry-price-bucketed breakdown
  4. Settlement Correctness Check — per-trade payout verification
  5. V2 Readiness — how much verified historical data v2 has

All endpoints are read-only. No trade data is modified.
"""
from __future__ import annotations

import math
import statistics as _statistics
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import AuditCheckResult, ForecastErrorStats, ForecastVerification, KalshiMarket, PaperTrade, PredictionSnapshot
from app.services.audit_checks import run_all_audit_checks

router = APIRouter(tags=["audit"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct(v: float | None) -> float | None:
    return round(v * 100, 2) if v is not None else None


def _round(v: float | None, n: int = 4) -> float | None:
    return round(v, n) if v is not None else None


def _roi(pl: float, stake: float) -> float | None:
    if not stake:
        return None
    return round(pl / stake * 100, 2)


def _trade_to_comparison_half(t: PaperTrade | None, side: str) -> dict[str, Any]:
    """Render one strategy half of a comparison row."""
    if t is None:
        return {
            f"{side}Traded": False,
            f"{side}Status": None,
            f"{side}Direction": None,
            f"{side}EcYesProb": None,
            f"{side}EcSideProb": None,
            f"{side}MarketPrice": None,
            f"{side}EntryPrice": None,
            f"{side}Edge": None,
            f"{side}Outcome": None,
            f"{side}Pl": None,
            f"{side}Stake": None,
            f"{side}DecisionExplanation": None,
            f"{side}QualityFlags": [],
        }
    return {
        f"{side}Traded": t.status not in ("V2_EXCLUDED",),
        f"{side}Status": t.status,
        f"{side}Direction": t.direction,
        f"{side}EcYesProb": _pct(t.ec_yes_probability),
        f"{side}EcSideProb": _pct(t.ec_side_probability),
        f"{side}MarketPrice": _round(t.market_yes_probability),
        f"{side}EntryPrice": _round(t.side_market_price),
        f"{side}Edge": _round(t.edge_pct_points),
        f"{side}Outcome": t.outcome,
        f"{side}Pl": _round(t.profit_loss),
        f"{side}Stake": _round(t.stake),
        f"{side}DecisionExplanation": t.decision_explanation,
        f"{side}QualityFlags": t.quality_flags or [],
    }


def _difference_reason(v1: PaperTrade | None, v2: PaperTrade | None) -> str:
    """Produce a human-readable reason for why one strategy traded and the other didn't."""
    v1_traded = v1 is not None and v1.status != "V2_EXCLUDED"
    v2_traded = v2 is not None and v2.status not in ("V2_EXCLUDED",)

    if v1_traded and v2_traded:
        if v1.direction != v2.direction:
            return (
                f"Both traded but on opposite sides — "
                f"v1 chose {v1.direction} (edge {v1.edge_pct_points:.1f}pp), "
                f"v2 chose {v2.direction} (edge {v2.edge_pct_points:.1f}pp)"
            )
        return "Both traded on the same side"

    if v1_traded and not v2_traded:
        if v2 is None:
            return "v2 has not yet analyzed this market"
        if v2.status == "V2_EXCLUDED":
            flags = v2.quality_flags or []
            flag_str = ", ".join(flags) if flags else "unknown reason"
            return f"v2 excluded: {flag_str}"
        return f"v2 skipped: {v2.decision_explanation or 'no explanation recorded'}"

    if not v1_traded and v2_traded:
        if v1 is None:
            return "v1 has not yet analyzed this market"
        return f"v1 skipped: {v1.decision_explanation or 'no explanation recorded'}"

    if not v1_traded and not v2_traded:
        reasons = []
        if v1:
            reasons.append(f"v1 skipped ({v1.decision_explanation or 'no detail'})")
        if v2:
            if v2.status == "V2_EXCLUDED":
                flags = ", ".join(v2.quality_flags or []) or "unknown"
                reasons.append(f"v2 excluded ({flags})")
            else:
                reasons.append(f"v2 skipped ({v2.decision_explanation or 'no detail'})")
        return "; ".join(reasons) if reasons else "Neither analyzed this market"

    return "Unknown"


# ---------------------------------------------------------------------------
# 1. Strategy Differences
# ---------------------------------------------------------------------------

@router.get("/audit/strategy-differences")
async def strategy_differences(
    filter: str = "",        # both|only_v1|only_v2|diff_side
    min_prob_diff: float = 0.0,  # percentage points, e.g. 5 or 10
    v2_adj: str = "",        # "adj" = only where v2 used historical data; "fallback" = only fallback
    status: str = "",        # open|settled
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Per-market comparison of v1 and v2 strategy decisions.
    Shows every market where at least one strategy has a record.
    """
    # Fetch all v1 and v2 trades
    v1_rows = (await db.execute(
        select(PaperTrade).where(PaperTrade.strategy_version == "v1.0")
        .order_by(PaperTrade.market_ticker)
    )).scalars().all()

    v2_rows = (await db.execute(
        select(PaperTrade).where(PaperTrade.strategy_version == "v2.0")
        .order_by(PaperTrade.market_ticker)
    )).scalars().all()

    # Fetch market metadata for titles
    tickers = list({t.market_ticker for t in v1_rows} | {t.market_ticker for t in v2_rows})
    market_map: dict[str, KalshiMarket] = {}
    if tickers:
        mrows = (await db.execute(
            select(KalshiMarket).where(KalshiMarket.ticker.in_(tickers))
        )).scalars().all()
        market_map = {m.ticker: m for m in mrows}

    v1_map = {t.market_ticker: t for t in v1_rows}
    v2_map = {t.market_ticker: t for t in v2_rows}

    all_tickers = sorted(set(v1_map.keys()) | set(v2_map.keys()))
    rows = []

    for ticker in all_tickers:
        v1 = v1_map.get(ticker)
        v2 = v2_map.get(ticker)
        market = market_map.get(ticker)

        v1_traded = v1 is not None and v1.status != "V2_EXCLUDED"
        v2_traded = v2 is not None and v2.status not in ("V2_EXCLUDED",)

        # Prob diff (using ec_yes_probability)
        v1_prob = (v1.ec_yes_probability or 0.0) if v1 else None
        v2_prob = (v2.ec_yes_probability or 0.0) if v2 else None
        prob_diff_pp = (
            round(abs(v1_prob - v2_prob) * 100, 2)
            if v1_prob is not None and v2_prob is not None
            else None
        )

        # ── Apply filters ──────────────────────────────────────────────────
        if filter == "both" and not (v1_traded and v2_traded):
            continue
        if filter == "only_v1" and not (v1_traded and not v2_traded):
            continue
        if filter == "only_v2" and not (not v1_traded and v2_traded):
            continue
        if filter == "diff_side":
            if not (v1_traded and v2_traded and v1 and v2 and v1.direction != v2.direction):
                continue

        if min_prob_diff > 0 and (prob_diff_pp is None or prob_diff_pp < min_prob_diff):
            continue

        if v2_adj == "adj" and (v2 is None or v2.fallback_level == "fixed_table" or v2.fallback_level is None):
            continue
        if v2_adj == "fallback" and (v2 is None or v2.fallback_level not in ("fixed_table", None)):
            continue

        if status == "open":
            ok = (v1 and v1.status == "OPEN") or (v2 and v2.status == "OPEN")
            if not ok:
                continue
        if status == "settled":
            ok = (v1 and v1.status == "SETTLED") or (v2 and v2.status == "SETTLED")
            if not ok:
                continue

        row: dict[str, Any] = {
            "ticker": ticker,
            "title": market.title if market else None,
            "city": (v1 or v2).city,
            "settlementDate": (v1 or v2).target_settlement_date,
            "contractType": (v1 or v2).contract_type,
            "probDiffPp": prob_diff_pp,
            "bothTraded": v1_traded and v2_traded,
            "onlyV1": v1_traded and not v2_traded,
            "onlyV2": not v1_traded and v2_traded,
            "diffSide": bool(
                v1_traded and v2_traded and v1 and v2 and v1.direction != v2.direction
            ),
            "differenceReason": _difference_reason(v1, v2),
            # v2 engine metadata
            "v2BiasCorrection": _round(v2.bias_correction) if v2 else None,
            "v2SigmaUsed": _round(v2.sigma_used) if v2 else None,
            "v2FallbackLevel": v2.fallback_level if v2 else None,
            "v2CalibrationAdj": _round(v2.calibration_adj) if v2 else None,
            "v2UsedHistorical": (
                v2.fallback_level not in ("fixed_table", None) if v2 else False
            ),
            **_trade_to_comparison_half(v1, "v1"),
            **_trade_to_comparison_half(v2, "v2"),
        }
        rows.append(row)

    return {"rows": rows, "total": len(rows)}


# ---------------------------------------------------------------------------
# 2. V1 Loss Audit
# ---------------------------------------------------------------------------

PROB_BUCKETS = [
    (0.0, 0.05, "0–5%"),
    (0.05, 0.10, "5–10%"),
    (0.10, 0.20, "10–20%"),
    (0.20, 0.30, "20–30%"),
    (0.30, 0.40, "30–40%"),
    (0.40, 0.50, "40–50%"),
    (0.50, 0.70, "50–70%"),
    (0.70, 1.01, "70–100%"),
]


def _prob_bucket_label(prob: float) -> str:
    for lo, hi, label in PROB_BUCKETS:
        if lo <= prob < hi:
            return label
    return "70–100%"


@router.get("/audit/loss-audit")
async def loss_audit(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Audit every settled v1.0 trade, grouped by EdgeCast side probability at entry.
    Computes expected vs actual wins, longest losing streak, ROI by bucket.
    """
    trades = (await db.execute(
        select(PaperTrade)
        .where(
            PaperTrade.strategy_version == "v1.0",
            PaperTrade.status == "SETTLED",
            PaperTrade.outcome.in_(["WIN", "LOSS"]),
        )
        .order_by(PaperTrade.settlement_timestamp)
    )).scalars().all()

    # Build buckets
    bucket_data: dict[str, dict] = {
        label: {
            "bucket": label,
            "settledCount": 0,
            "wins": 0,
            "losses": 0,
            "totalStake": 0.0,
            "totalPl": 0.0,
            "sumProb": 0.0,
            "sumPrice": 0.0,
        }
        for _, _, label in PROB_BUCKETS
    }

    total_expected = 0.0
    total_actual_wins = 0

    # Longest losing streak
    current_streak = 0
    longest_streak = 0
    streak_trades: list[dict] = []

    for t in trades:
        prob = t.ec_side_probability or 0.0
        label = _prob_bucket_label(prob)
        b = bucket_data[label]
        b["settledCount"] += 1
        b["sumProb"] += prob
        b["sumPrice"] += t.side_market_price or 0.0
        b["totalStake"] += t.stake or 0.0
        b["totalPl"] += t.profit_loss or 0.0

        total_expected += prob

        if t.outcome == "WIN":
            b["wins"] += 1
            total_actual_wins += 1
            current_streak = 0
        else:
            b["losses"] += 1
            current_streak += 1
            if current_streak > longest_streak:
                longest_streak = current_streak

    # Finalise bucket rows
    bucket_rows = []
    for _, _, label in PROB_BUCKETS:
        b = bucket_data[label]
        n = b["settledCount"]
        wins = b["wins"]
        losses = b["losses"]
        stake = b["totalStake"]
        pl = b["totalPl"]
        bucket_rows.append({
            "bucket": label,
            "settledCount": n,
            "wins": wins,
            "losses": losses,
            "actualWinRate": _pct(wins / n) if n else None,
            "avgPredictedProb": _pct(b["sumProb"] / n) if n else None,
            "avgEntryPrice": _round(b["sumPrice"] / n) if n else None,
            "totalStake": _round(stake),
            "profitLoss": _round(pl),
            "roi": _roi(pl, stake),
            "expectedWins": _round(b["sumProb"], 2),
        })

    # Per-trade list for streak calculation
    trade_list = [
        {
            "id": t.id,
            "ticker": t.market_ticker,
            "city": t.city,
            "settlementDate": t.target_settlement_date,
            "direction": t.direction,
            "ecSideProb": _pct(t.ec_side_probability),
            "entryPrice": _round(t.side_market_price),
            "edge": _round(t.edge_pct_points),
            "stake": _round(t.stake),
            "outcome": t.outcome,
            "pl": _round(t.profit_loss),
            "settlementTimestamp": t.settlement_timestamp.isoformat() if t.settlement_timestamp else None,
        }
        for t in trades
    ]

    return {
        "buckets": bucket_rows,
        "summary": {
            "totalSettled": len(trades),
            "totalWins": total_actual_wins,
            "totalLosses": len(trades) - total_actual_wins,
            "overallWinRate": _pct(total_actual_wins / len(trades)) if trades else None,
            "expectedWins": _round(total_expected, 2),
            "actualWins": total_actual_wins,
            "expectedVsActualDiff": _round(total_actual_wins - total_expected, 2),
            "longestLosingStreak": longest_streak,
        },
        "trades": trade_list,
    }


# ---------------------------------------------------------------------------
# 3. Long-shot Analysis
# ---------------------------------------------------------------------------

PRICE_BUCKETS = [
    (0.0, 0.015, "1¢"),
    (0.015, 0.055, "2–5¢"),
    (0.055, 0.105, "6–10¢"),
    (0.105, 0.205, "11–20¢"),
    (0.205, 1.01, ">20¢"),
]


def _price_bucket_label(price: float) -> str:
    for lo, hi, label in PRICE_BUCKETS:
        if lo <= price < hi:
            return label
    return ">20¢"


@router.get("/audit/long-shot")
async def long_shot_analysis(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Break down settled v1 trades by entry price bucket to identify
    whether 0-win results are driven by low-price (long-shot) contracts.
    """
    trades = (await db.execute(
        select(PaperTrade)
        .where(
            PaperTrade.strategy_version == "v1.0",
            PaperTrade.status == "SETTLED",
            PaperTrade.outcome.in_(["WIN", "LOSS"]),
        )
    )).scalars().all()

    bucket_data: dict[str, dict] = {
        label: {
            "bucket": label,
            "settledCount": 0,
            "wins": 0,
            "sumEcProb": 0.0,
            "totalStake": 0.0,
            "totalPl": 0.0,
        }
        for _, _, label in PRICE_BUCKETS
    }

    for t in trades:
        price = t.side_market_price or 0.0
        label = _price_bucket_label(price)
        b = bucket_data[label]
        b["settledCount"] += 1
        b["sumEcProb"] += t.ec_side_probability or 0.0
        b["totalStake"] += t.stake or 0.0
        b["totalPl"] += t.profit_loss or 0.0
        if t.outcome == "WIN":
            b["wins"] += 1

    rows = []
    for _, _, label in PRICE_BUCKETS:
        b = bucket_data[label]
        n = b["settledCount"]
        stake = b["totalStake"]
        pl = b["totalPl"]
        rows.append({
            "bucket": label,
            "settledCount": n,
            "wins": b["wins"],
            "losses": n - b["wins"],
            "avgEcProb": _pct(b["sumEcProb"] / n) if n else None,
            "expectedWins": _round(b["sumEcProb"], 2),
            "actualWins": b["wins"],
            "totalStake": _round(stake),
            "profitLoss": _round(pl),
            "roi": _roi(pl, stake),
        })

    # Identify the dominant price tier by settled count
    if trades:
        dominant = max(rows, key=lambda r: r["settledCount"])
        low_price_count = sum(
            r["settledCount"] for r in rows if r["bucket"] in ("1¢", "2–5¢", "6–10¢")
        )
        low_price_wins = sum(
            r["wins"] for r in rows if r["bucket"] in ("1¢", "2–5¢", "6–10¢")
        )
        conclusion = (
            f"{low_price_count} of {len(trades)} settled trades ({round(low_price_count/len(trades)*100)}%) "
            f"were priced below 11¢. Of those, {low_price_wins} won. "
            + (
                "The 0-win result is heavily driven by concentration in long-shot contracts."
                if low_price_wins == 0 and low_price_count > len(trades) * 0.5
                else "Long-shot concentration is a contributing factor but not the sole explanation."
            )
        )
    else:
        conclusion = "No settled trades to analyse."

    return {"buckets": rows, "total": len(trades), "conclusion": conclusion}


# ---------------------------------------------------------------------------
# 4. Settlement Correctness Check
# ---------------------------------------------------------------------------

@router.get("/audit/settlement-check")
async def settlement_check(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Verify the payout and outcome classification of every settled v1 trade.
    Flags any trade where EdgeCast's recorded outcome contradicts what the
    Kalshi result + direction logic implies.
    """
    trades = (await db.execute(
        select(PaperTrade)
        .where(
            PaperTrade.strategy_version == "v1.0",
            PaperTrade.status.in_(["SETTLED", "VOID"]),
        )
        .order_by(PaperTrade.settlement_timestamp)
    )).scalars().all()

    correctly_settled = 0
    incorrectly_settled = 0
    unresolved = 0
    missing_result = 0
    api_error = 0

    verified: list[dict] = []

    for t in trades:
        kalshi_result = t.kalshi_result
        direction = t.direction
        recorded_outcome = t.outcome
        warnings_text = t.warnings or ""

        # Determine expected outcome
        if not kalshi_result:
            expected_outcome = None
            classification = "missing_result"
            missing_result += 1
        elif kalshi_result == "void":
            expected_outcome = "VOID"
            classification = "correct" if recorded_outcome == "VOID" else "incorrect"
            if classification == "correct":
                correctly_settled += 1
            else:
                incorrectly_settled += 1
        elif kalshi_result in ("yes", "no"):
            if direction == "YES":
                expected_outcome = "WIN" if kalshi_result == "yes" else "LOSS"
            elif direction == "NO":
                expected_outcome = "WIN" if kalshi_result == "no" else "LOSS"
            else:
                expected_outcome = None
                classification = "unresolved"
                unresolved += 1
                verified.append({
                    "id": t.id,
                    "ticker": t.market_ticker,
                    "city": t.city,
                    "settlementDate": t.target_settlement_date,
                    "direction": direction,
                    "kalshiResult": kalshi_result,
                    "recordedOutcome": recorded_outcome,
                    "expectedOutcome": None,
                    "classification": "unresolved",
                    "stake": _round(t.stake),
                    "quantity": _round(t.quantity),
                    "grossPayout": _round(t.gross_payout),
                    "profitLoss": _round(t.profit_loss),
                    "expectedGrossPayout": None,
                    "expectedProfitLoss": None,
                    "payoutCorrect": None,
                    "settlementTimestamp": t.settlement_timestamp.isoformat() if t.settlement_timestamp else None,
                    "warnings": warnings_text[:500] if warnings_text else None,
                    "apiError": "api_error" in warnings_text.lower(),
                })
                continue

            # Verify payout
            qty = t.quantity or 0.0
            stake = t.stake or 0.0
            if expected_outcome == "WIN":
                expected_gross = round(qty * 1.0, 4)
                expected_pl = round(expected_gross - stake, 4)
            elif expected_outcome == "VOID":
                expected_gross = stake
                expected_pl = 0.0
            else:
                expected_gross = 0.0
                expected_pl = round(-stake, 4)

            tol = 0.01
            payout_correct = (
                abs((t.gross_payout or 0.0) - expected_gross) <= tol
                and abs((t.profit_loss or 0.0) - expected_pl) <= tol
            )

            if recorded_outcome == expected_outcome and payout_correct:
                classification = "correct"
                correctly_settled += 1
            elif recorded_outcome == expected_outcome and not payout_correct:
                classification = "payout_mismatch"
                incorrectly_settled += 1
            else:
                classification = "outcome_mismatch"
                incorrectly_settled += 1
        else:
            expected_outcome = None
            classification = "unresolved"
            unresolved += 1
            expected_gross = None
            expected_pl = None
            payout_correct = None

        if "api_error" in warnings_text.lower():
            api_error += 1

        verified.append({
            "id": t.id,
            "ticker": t.market_ticker,
            "city": t.city,
            "settlementDate": t.target_settlement_date,
            "direction": direction,
            "kalshiResult": kalshi_result,
            "recordedOutcome": recorded_outcome,
            "expectedOutcome": expected_outcome,
            "classification": classification,
            "stake": _round(t.stake),
            "quantity": _round(t.quantity),
            "grossPayout": _round(t.gross_payout),
            "profitLoss": _round(t.profit_loss),
            "expectedGrossPayout": _round(expected_gross) if kalshi_result else None,
            "expectedProfitLoss": _round(expected_pl) if kalshi_result else None,
            "payoutCorrect": payout_correct if kalshi_result and kalshi_result != "void" else None,
            "settlementTimestamp": t.settlement_timestamp.isoformat() if t.settlement_timestamp else None,
            "warnings": warnings_text[:500] if warnings_text else None,
            "apiError": "api_error" in warnings_text.lower(),
        })

    return {
        "trades": verified,
        "summary": {
            "total": len(verified),
            "correctlySettled": correctly_settled,
            "incorrectlySettled": incorrectly_settled,
            "unresolved": unresolved,
            "missingResult": missing_result,
            "apiError": api_error,
        },
    }


# ---------------------------------------------------------------------------
# 5. V2 Readiness
# ---------------------------------------------------------------------------

MIN_SAMPLE_SIGMA = 5
MIN_SAMPLE_CALIB = 30


@router.get("/audit/v2-readiness")
async def v2_readiness(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Report on how much verified historical data v2 has accumulated,
    broken down by city × variable × lead-time bucket × month.
    """
    rows = (await db.execute(
        select(ForecastErrorStats).order_by(
            ForecastErrorStats.city,
            ForecastErrorStats.weather_variable,
            ForecastErrorStats.lead_time_bucket,
            ForecastErrorStats.month,
        )
    )).scalars().all()

    # City + variable summary (collapsed)
    city_var_map: dict[str, dict] = {}
    detail_rows = []

    for r in rows:
        key = f"{r.city}::{r.weather_variable}"
        if key not in city_var_map:
            city_var_map[key] = {
                "city": r.city,
                "variable": r.weather_variable,
                "totalObservations": 0,
                "groupCount": 0,
                "sufficientForSigma": 0,
                "sufficientForCalib": 0,
            }
        cv = city_var_map[key]
        cv["totalObservations"] += r.sample_size
        cv["groupCount"] += 1
        if r.sample_size >= MIN_SAMPLE_SIGMA:
            cv["sufficientForSigma"] += 1
        if r.sample_size >= MIN_SAMPLE_CALIB:
            cv["sufficientForCalib"] += 1

        detail_rows.append({
            "city": r.city,
            "variable": r.weather_variable,
            "leadTimeBucket": r.lead_time_bucket,
            "month": r.month,
            "sampleSize": r.sample_size,
            "meanBias": _round(r.mean_error),
            "mae": _round(r.mae),
            "stdDev": _round(r.std_dev),
            "sufficientForSigma": r.sample_size >= MIN_SAMPLE_SIGMA,
            "sufficientForCalib": r.sample_size >= MIN_SAMPLE_CALIB,
            "tier": (
                "full" if r.sample_size >= MIN_SAMPLE_CALIB
                else "sigma_only" if r.sample_size >= MIN_SAMPLE_SIGMA
                else "fallback"
            ),
            "lastComputedAt": r.last_computed_at.isoformat() if r.last_computed_at else None,
        })

    city_var_summary = sorted(city_var_map.values(), key=lambda x: x["city"])

    total_groups = len(rows)
    fallback_groups = sum(1 for r in rows if r.sample_size < MIN_SAMPLE_SIGMA)
    sigma_groups = sum(1 for r in rows if MIN_SAMPLE_SIGMA <= r.sample_size < MIN_SAMPLE_CALIB)
    full_groups = sum(1 for r in rows if r.sample_size >= MIN_SAMPLE_CALIB)

    return {
        "detailRows": detail_rows,
        "cityVariableSummary": city_var_summary,
        "summary": {
            "totalGroups": total_groups,
            "fallbackGroups": fallback_groups,
            "sigmaOnlyGroups": sigma_groups,
            "fullGroups": full_groups,
            "pctFallback": _pct(fallback_groups / total_groups) if total_groups else None,
            "pctReady": _pct(sigma_groups / total_groups) if total_groups else None,
            "pctFull": _pct(full_groups / total_groups) if total_groups else None,
        },
    }


# ---------------------------------------------------------------------------
# 6. V2 Learning Progress
# ---------------------------------------------------------------------------

_LP_MILESTONES = [5, 15, 30, 50, 100]
_LP_MIN_SAMPLE = 5

_READINESS_LABELS: dict[str, str] = {
    "not_collecting": "Not Collecting",
    "collecting": "Collecting",
    "insufficient_sample": "Insufficient Sample",
    "partially_learned": "Partially Learned",
    "learned": "Learned",
    "data_quality_issue": "Data-Quality Issue",
}


def _lp_compute_readiness_status(
    station: Any,
    usable_count: int,
    city_fes_groups: list[ForecastErrorStats],
) -> str:
    """
    Six-state readiness classifier for one city.

    States (in evaluation order):
      data_quality_issue  – no station mapping, or notes contain HIGH AMBIGUITY
      not_collecting      – 0 usable FV records
      collecting          – 1–4 usable FV records
      insufficient_sample – ≥5 obs but no city-level FES group has sample_size ≥ MIN_SAMPLE
      partially_learned   – some city-level FES groups ≥ MIN_SAMPLE, others not
      learned             – all city-level FES groups ≥ MIN_SAMPLE
    """
    if station is None:
        return "data_quality_issue"
    notes = getattr(station, "notes", None) or ""
    if "HIGH AMBIGUITY" in notes.upper():
        return "data_quality_issue"
    if usable_count == 0:
        return "not_collecting"
    if usable_count < _LP_MIN_SAMPLE:
        return "collecting"
    # usable_count >= 5 — inspect city-level FES groups
    city_groups = [g for g in city_fes_groups if g.fallback_level == "city"]
    if not city_groups:
        return "insufficient_sample"
    ready = [g for g in city_groups if g.sample_size >= _LP_MIN_SAMPLE]
    if not ready:
        return "insufficient_sample"
    if len(ready) < len(city_groups):
        return "partially_learned"
    return "learned"


def _lp_milestone_progress(usable_count: int) -> dict[str, Any]:
    reached = [usable_count >= m for m in _LP_MILESTONES]
    next_ms = next((m for m in _LP_MILESTONES if usable_count < m), None)
    return {
        "current": usable_count,
        "milestones": _LP_MILESTONES,
        "reached": reached,
        "nextMilestone": next_ms,
        "neededForNext": (next_ms - usable_count) if next_ms is not None else None,
    }


def _lp_source_quality_label(sources: dict[str, int]) -> str:
    ghcnd = sources.get("ghcnd_observation", 0) + sources.get("ghcnd_observation_unverified", 0)
    era5 = sources.get("era5_reanalysis", 0) + sources.get("open_meteo_historical", 0)
    if ghcnd > 0 and era5 == 0:
        return "ghcnd"
    if era5 > 0 and ghcnd == 0:
        return "era5"
    if ghcnd > 0 and era5 > 0:
        return "mixed"
    return "none"


@router.get("/audit/v2-learning-progress")
async def v2_learning_progress(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    V2 Learning Progress Dashboard.
    Returns a full payload: summary cards, per-city rows, error-group rows,
    and v1 vs v2 activation stats.
    """
    from app.services.settlement_stations import SETTLEMENT_STATIONS

    # ── 1. Load all ForecastVerification rows ──────────────────────────────
    fv_rows: list[ForecastVerification] = (await db.execute(
        select(ForecastVerification).order_by(
            ForecastVerification.city, ForecastVerification.target_date
        )
    )).scalars().all()

    # ── 2. Load all ForecastErrorStats rows ────────────────────────────────
    fes_rows: list[ForecastErrorStats] = (await db.execute(
        select(ForecastErrorStats).order_by(
            ForecastErrorStats.city, ForecastErrorStats.weather_variable,
            ForecastErrorStats.lead_time_bucket,
        )
    )).scalars().all()

    # ── 3. Load v2 PaperTrades ─────────────────────────────────────────────
    v2_trades: list[PaperTrade] = (await db.execute(
        select(PaperTrade).where(PaperTrade.strategy_version == "v2.0")
    )).scalars().all()

    # ── 4. Index by city ───────────────────────────────────────────────────
    fv_by_city: dict[str, list[ForecastVerification]] = defaultdict(list)
    for fv in fv_rows:
        fv_by_city[fv.city].append(fv)

    fes_by_city: dict[str, list[ForecastErrorStats]] = defaultdict(list)
    for fes in fes_rows:
        fes_by_city[fes.city].append(fes)

    v2_by_city: dict[str, list[PaperTrade]] = defaultdict(list)
    for t in v2_trades:
        if t.city:
            v2_by_city[t.city].append(t)

    # ── 5. Per-city rows ───────────────────────────────────────────────────
    city_rows = []
    for city, station in SETTLEMENT_STATIONS.items():
        city_fvs = fv_by_city.get(city, [])
        city_fes = fes_by_city.get(city, [])
        city_v2 = v2_by_city.get(city, [])

        usable = [fv for fv in city_fvs if fv.actual_value is not None]
        usable_count = len(usable)
        total_count = len(city_fvs)

        # Source breakdown
        sources: dict[str, int] = defaultdict(int)
        for fv in usable:
            src = fv.source_label or "unknown"
            sources[src] += 1

        # Latest observation date
        latest_date = max((fv.target_date for fv in usable), default=None)

        readiness = _lp_compute_readiness_status(station, usable_count, city_fes)

        city_fes_city_level = [g for g in city_fes if g.fallback_level == "city"]
        ready_groups = [g for g in city_fes_city_level if g.sample_size >= _LP_MIN_SAMPLE]

        # V2 trade stats
        v2_fallback_count = sum(
            1 for t in city_v2
            if t.fallback_level in ("fixed_table", None)
        )
        v2_total = len(city_v2)

        city_rows.append({
            "city": city,
            "stationVerified": station.verified,
            "stationName": station.station_name,
            "readinessStatus": readiness,
            "readinessLabel": _READINESS_LABELS.get(readiness, readiness),
            "usableObservations": usable_count,
            "totalObservations": total_count,
            "sourceBreakdown": dict(sources),
            "sourceQualityLabel": _lp_source_quality_label(dict(sources)),
            "cityFesGroupCount": len(city_fes_city_level),
            "cityFesReadyCount": len(ready_groups),
            "milestoneProgress": _lp_milestone_progress(usable_count),
            "v2TradesTotal": v2_total,
            "v2TradesFallback": v2_fallback_count,
            "v2TradesHistorical": v2_total - v2_fallback_count,
            "latestObservationDate": latest_date,
            "fesGroups": [
                {
                    "variable": g.weather_variable,
                    "leadTimeBucket": g.lead_time_bucket,
                    "month": g.month,
                    "sampleSize": g.sample_size,
                    "fallbackLevel": g.fallback_level,
                    "mae": _round(g.mae),
                    "stdDev": _round(g.std_dev),
                    "meanBias": _round(g.mean_error),
                }
                for g in city_fes
            ],
        })

    # Sort: data_quality_issue last, then by readiness state order, then alpha
    _STATE_ORDER = {
        "learned": 0,
        "partially_learned": 1,
        "insufficient_sample": 2,
        "collecting": 3,
        "not_collecting": 4,
        "data_quality_issue": 5,
    }
    city_rows.sort(key=lambda r: (_STATE_ORDER.get(r["readinessStatus"], 9), r["city"]))

    # ── 6. Error group rows (all FES) ──────────────────────────────────────
    error_group_rows = []
    for g in fes_rows:
        city_fvs_for_source = fv_by_city.get(g.city, [])
        sources_for_group: dict[str, int] = defaultdict(int)
        for fv in city_fvs_for_source:
            if fv.actual_value is not None and fv.source_label:
                sources_for_group[fv.source_label] += 1
        error_group_rows.append({
            "city": g.city,
            "variable": g.weather_variable,
            "leadTimeBucket": g.lead_time_bucket,
            "month": g.month,
            "sampleSize": g.sample_size,
            "fallbackLevel": g.fallback_level,
            "mae": _round(g.mae),
            "stdDev": _round(g.std_dev),
            "meanBias": _round(g.mean_error),
            "sourceQualityLabel": _lp_source_quality_label(dict(sources_for_group)),
            "lastComputedAt": g.last_computed_at.isoformat() if g.last_computed_at else None,
        })

    # ── 7. Summary cards ───────────────────────────────────────────────────
    status_counts: dict[str, int] = defaultdict(int)
    for row in city_rows:
        status_counts[row["readinessStatus"]] += 1

    total_usable = sum(r["usableObservations"] for r in city_rows)
    total_fes = len(fes_rows)
    global_fes = [g for g in fes_rows if g.city == "__global__"]
    city_fes_only = [g for g in fes_rows if g.city != "__global__"]

    v2_hist = sum(1 for t in v2_trades if t.fallback_level not in ("fixed_table", None))
    v2_fall = sum(1 for t in v2_trades if t.fallback_level in ("fixed_table", None))
    v1_trades: list[PaperTrade] = (await db.execute(
        select(PaperTrade).where(PaperTrade.strategy_version == "v1.0")
    )).scalars().all()

    summary = {
        "totalCities": len(SETTLEMENT_STATIONS),
        "citiesLearned": status_counts.get("learned", 0),
        "citiesPartiallyLearned": status_counts.get("partially_learned", 0),
        "citiesCollecting": status_counts.get("collecting", 0),
        "citiesNotCollecting": status_counts.get("not_collecting", 0),
        "citiesDataQualityIssue": status_counts.get("data_quality_issue", 0),
        "totalUsableObservations": total_usable,
        "totalFesGroups": total_fes,
        "cityFesGroups": len(city_fes_only),
        "globalFesGroups": len(global_fes),
        "v2TotalTrades": len(v2_trades),
        "v2TradesUsingHistorical": v2_hist,
        "v2TradesUsingFallback": v2_fall,
        "v1TotalTrades": len(v1_trades),
    }

    return {
        "summary": summary,
        "cities": city_rows,
        "errorGroups": error_group_rows,
    }


@router.get("/audit/v2-city-detail/{city}")
async def v2_city_detail(
    city: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Full verification history + FES groups + v2 trades for a single city.
    Used for drill-down from the V2 Learning Progress dashboard.
    """
    from app.services.settlement_stations import SETTLEMENT_STATIONS

    station = SETTLEMENT_STATIONS.get(city)

    fv_rows: list[ForecastVerification] = (await db.execute(
        select(ForecastVerification)
        .where(ForecastVerification.city == city)
        .order_by(ForecastVerification.target_date.desc())
    )).scalars().all()

    fes_rows: list[ForecastErrorStats] = (await db.execute(
        select(ForecastErrorStats)
        .where(ForecastErrorStats.city == city)
        .order_by(
            ForecastErrorStats.weather_variable,
            ForecastErrorStats.lead_time_bucket,
        )
    )).scalars().all()

    v2_rows: list[PaperTrade] = (await db.execute(
        select(PaperTrade)
        .where(PaperTrade.strategy_version == "v2.0", PaperTrade.city == city)
        .order_by(PaperTrade.created_at.desc())
    )).scalars().all()

    usable = [fv for fv in fv_rows if fv.actual_value is not None]
    sources: dict[str, int] = defaultdict(int)
    for fv in usable:
        src = fv.source_label or "unknown"
        sources[src] += 1

    city_fes = fes_rows
    readiness = _lp_compute_readiness_status(station, len(usable), city_fes)

    return {
        "city": city,
        "readinessStatus": readiness,
        "readinessLabel": _READINESS_LABELS.get(readiness, readiness),
        "stationInfo": {
            "stationName": station.station_name if station else None,
            "ghcndStationId": station.ghcnd_station_id if station else None,
            "verified": station.verified if station else None,
            "notes": station.notes if station else None,
        },
        "milestoneProgress": _lp_milestone_progress(len(usable)),
        "sourceBreakdown": dict(sources),
        "sourceQualityLabel": _lp_source_quality_label(dict(sources)),
        "verifications": [
            {
                "id": fv.id,
                "targetDate": fv.target_date,
                "weatherVariable": fv.weather_variable,
                "forecastValue": _round(fv.forecast_value),
                "actualValue": _round(fv.actual_value),
                "forecastError": _round(fv.forecast_error),
                "sourceLabel": fv.source_label,
                "ghcndStationId": fv.ghcnd_station_id,
                "leadTimeDays": fv.lead_time_days,
                "month": fv.month,
                "season": fv.season,
                "createdAt": fv.created_at.isoformat() if fv.created_at else None,
            }
            for fv in fv_rows
        ],
        "fesGroups": [
            {
                "variable": g.weather_variable,
                "leadTimeBucket": g.lead_time_bucket,
                "month": g.month,
                "sampleSize": g.sample_size,
                "fallbackLevel": g.fallback_level,
                "mae": _round(g.mae),
                "stdDev": _round(g.std_dev),
                "meanBias": _round(g.mean_error),
                "lastComputedAt": g.last_computed_at.isoformat() if g.last_computed_at else None,
            }
            for g in fes_rows
        ],
        "v2Trades": [
            {
                "id": t.id,
                "ticker": t.market_ticker,
                "direction": t.direction,
                "status": t.status,
                "outcome": t.outcome,
                "fallbackLevel": t.fallback_level,
                "sigmaUsed": _round(t.sigma_used),
                "biasCorrection": _round(t.bias_correction),
                "calibrationAdj": _round(t.calibration_adj),
                "stake": _round(t.stake),
                "pl": _round(t.profit_loss),
                "targetDate": t.target_settlement_date,
                "createdAt": t.created_at.isoformat() if t.created_at else None,
            }
            for t in v2_rows
        ],
    }


# ---------------------------------------------------------------------------
# Audit Check Results — GET (latest per check_key) and POST (trigger run)
# ---------------------------------------------------------------------------

# The three check keys that the system tracks, in display order.
_KNOWN_CHECK_KEYS: list[tuple[str, str, str]] = [
    ("db_calibration_contents",  "Calibration Adjustments Contents",               "calibration"),
    ("db_date_alignment",        "Target Settlement Date Local-Date Alignment",     "era5_verification"),
    ("db_coord_alignment",       "WeatherLocation vs Settlement-Station Coordinates", "coordinates"),
]


def _row_to_dict(r: AuditCheckResult) -> dict:
    return {
        "checkKey":       r.check_key,
        "checkName":      r.check_name,
        "category":       r.category,
        "status":         r.status,
        "severity":       r.severity,
        "summary":        r.summary,
        "details":        r.details,
        "actionRequired": r.action_required,
        "checkedAt":      r.checked_at.isoformat() if r.checked_at else None,
        "source":         r.source,
        "metadataJson":   r.metadata_json,
    }


def _pending_stub(key: str, name: str, category: str) -> dict:
    return {
        "checkKey":       key,
        "checkName":      name,
        "category":       category,
        "status":         "PENDING",
        "severity":       None,
        "summary":        "DB verification has not yet run for this check.",
        "details":        None,
        "actionRequired": False,
        "checkedAt":      None,
        "source":         None,
        "metadataJson":   None,
    }


@router.get("/audit/check-results")
async def get_audit_check_results(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> dict:
    """
    Return the latest audit check result for each known check_key.
    If a check has never run, return a PENDING stub so the UI always
    shows all three checks.
    Read-only. No data is modified.
    """
    rows = (await db.execute(
        select(AuditCheckResult).order_by(
            AuditCheckResult.check_key, desc(AuditCheckResult.checked_at)
        )
    )).scalars().all()

    # Keep only the newest row per check_key
    latest: dict[str, AuditCheckResult] = {}
    for r in rows:
        if r.check_key not in latest:
            latest[r.check_key] = r

    results = []
    for key, name, category in _KNOWN_CHECK_KEYS:
        if key in latest:
            results.append(_row_to_dict(latest[key]))
        else:
            results.append(_pending_stub(key, name, category))

    return {"results": results}


@router.post("/audit/run-db-checks")
async def trigger_audit_db_checks(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> dict:
    """
    Manually trigger the three read-only DB verification checks.
    Stores one new result row per check.  Does NOT modify any trading,
    calibration, or historical data.
    """
    written = await run_all_audit_checks(db)
    return {
        "ran": len(written),
        "results": [_row_to_dict(r) for r in written],
    }
