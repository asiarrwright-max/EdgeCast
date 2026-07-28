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
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import ForecastErrorStats, KalshiMarket, PaperTrade

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
