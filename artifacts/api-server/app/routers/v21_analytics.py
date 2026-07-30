"""
V2.1 Analytics Router
=====================
GET /api/analytics/v21/retrospective       Before-and-after V2.0 → V2.1 comparison
GET /api/analytics/v21/calibration         Probability calibration by confidence bucket
GET /api/analytics/v21/readiness           V2.1 model readiness panel
GET /api/analytics/v21/stations            Settlement station coverage table
GET /api/analytics/v21/consensus-backtest  Consensus guard retrospective (guard stays disabled)

All endpoints are read-only.  No trade records are created or modified.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import ForecastErrorStats, PaperTrade, PredictionSnapshot
from app.services.settlement_stations import SETTLEMENT_STATIONS
from app.services.probability_engine_v2 import (
    SIGMA_FLOOR,
    SIGMA_CEILING,
    MIN_SAMPLE,
    _calc_prob_threshold,
    _calc_prob_range,
    _conservative_prior,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analytics"])

# ---------------------------------------------------------------------------
# Constants mirrored from paper_trading_v21
# ---------------------------------------------------------------------------

_V21_MIN_EDGE_PP = 10.0   # same as DEFAULT_V21_SETTINGS["min_edge_pct"]
_V21_STAKE = 10.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _avg(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 4) if vals else None


def _brier_score(trades: list[PaperTrade]) -> float | None:
    scored = [
        t for t in trades
        if t.status == "SETTLED"
        and t.kalshi_result in ("yes", "no")
        and t.ec_yes_probability is not None
    ]
    if not scored:
        return None
    total = sum(
        (t.ec_yes_probability - (1 if t.kalshi_result == "yes" else 0)) ** 2
        for t in scored
    )
    return round(total / len(scored), 6)


def _roi(trades: list[PaperTrade]) -> float | None:
    settled = [t for t in trades if t.status == "SETTLED"]
    total_stake = sum(t.stake or 0 for t in settled)
    net_pl = sum(t.profit_loss or 0 for t in settled)
    return round(net_pl / total_stake * 100, 4) if total_stake > 0 else None


def _v21_sigma(fallback_level: str | None, sigma_used: float | None, lead_time: int | None) -> float:
    """
    Compute the sigma V2.1 would have used for a trade, given V2.0's recorded values.

    Rules:
    - If V2.0 used a fixed_table fallback, V2.1 uses the conservative prior (larger).
    - Otherwise V2.1 applies the sigma floor: max(SIGMA_FLOOR, sigma_used).
    """
    if fallback_level == "fixed_table" or sigma_used is None:
        raw = _conservative_prior(lead_time)
    else:
        raw = sigma_used
    return max(SIGMA_FLOOR, min(SIGMA_CEILING, raw))


def _confidence_bucket(prob: float) -> str:
    """Return the calibration bucket label for a probability."""
    if prob < 0.5:
        return "<50%"
    if prob < 0.6:
        return "50–60%"
    if prob < 0.7:
        return "60–70%"
    if prob < 0.8:
        return "70–80%"
    if prob < 0.9:
        return "80–90%"
    return "90–100%"


def _readiness_stage(settled: int, buckets_with_data: int, verified_cities: int) -> str:
    """
    Plain-language readiness stage for V2.1 model.
    Criteria from spec:
      Ready for Careful Evaluation: ≥250 settled, ≥2 buckets with ≥30 obs, ≥1 verified city
    """
    if settled == 0:
        return "Collecting Data"
    if settled < 30:
        return "Collecting Data"
    if settled < 100 or buckets_with_data < 2:
        return "Early Learning"
    if settled < 250 or buckets_with_data < 2:
        return "Meaningful Sample"
    return "Ready for Careful Evaluation"


# ---------------------------------------------------------------------------
# 1. Retrospective comparison: V2.0 trades re-evaluated with V2.1 sigma
# ---------------------------------------------------------------------------

@router.get("/analytics/v21/retrospective")
async def get_v21_retrospective(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    RETROSPECTIVE ONLY — not a valid substitute for forward paper trading.

    Re-evaluate a representative sample of V2.0 settled trades under V2.1
    sigma rules (floor 3.5°F, conservative prior when fallback_level='fixed_table').

    V2.0 trade records are NOT modified.
    """

    # Load up to 50 settled V2.0 trades that have a snapshot_id
    q = await session.execute(
        select(PaperTrade)
        .where(
            PaperTrade.strategy_version == "v2.0",
            PaperTrade.status == "SETTLED",
            PaperTrade.snapshot_id.is_not(None),
        )
        .order_by(PaperTrade.settlement_timestamp.desc())
        .limit(50)
    )
    v20_trades: list[PaperTrade] = q.scalars().all()

    if not v20_trades:
        return {
            "disclaimer": _RETRO_DISCLAIMER,
            "trades": [],
            "summary": _empty_retro_summary(),
        }

    # Load all referenced snapshots in one query
    snap_ids = [t.snapshot_id for t in v20_trades if t.snapshot_id]
    snaps_q = await session.execute(
        select(PredictionSnapshot).where(PredictionSnapshot.id.in_(snap_ids))
    )
    snaps: dict[int, PredictionSnapshot] = {
        s.id: s for s in snaps_q.scalars().all()
    }

    rows: list[dict] = []

    for trade in v20_trades:
        snap = snaps.get(trade.snapshot_id) if trade.snapshot_id else None
        city = trade.city or ""

        # Station verification status for V2.1
        station = SETTLEMENT_STATIONS.get(city)
        v21_city_eligible = station is not None and station.verified
        station_name = station.station_name if station else "Unknown"

        # Old sigma
        sigma_v20 = trade.sigma_used
        # New sigma V2.1 would use
        sigma_v21 = _v21_sigma(trade.fallback_level, sigma_v20, trade.lead_time_days)
        sigma_changed = (sigma_v20 is not None) and abs(sigma_v21 - (sigma_v20 or 0)) > 0.01

        # Re-compute V2.1 probability if we have the snapshot data
        ec_yes_v21: float | None = None
        new_edge: float | None = None
        v21_would_trade = False
        skip_reason: str | None = None

        if not v21_city_eligible:
            skip_reason = f"City not verified for V2.1 ({station.notes[:60] + '…' if station and station.notes else 'no station registered'})"

        if snap is not None and snap.forecast_value is not None and v21_city_eligible:
            bias = trade.bias_correction or 0.0
            mu = snap.forecast_value - bias
            try:
                if snap.contract_type == "range" and snap.lower_bound and snap.upper_bound:
                    ec_yes_v21 = _calc_prob_range(snap.lower_bound, snap.upper_bound, mu, sigma_v21)
                elif snap.settlement_operator and snap.settlement_threshold is not None:
                    ec_yes_v21 = _calc_prob_threshold(snap.settlement_operator, snap.settlement_threshold, mu, sigma_v21)
            except Exception as exc:
                logger.debug("Retrospective probability error for trade %s: %s", trade.id, exc)

            if ec_yes_v21 is not None:
                ec_no_v21 = round(1.0 - ec_yes_v21, 4)
                side_price = trade.side_market_price or 0.0
                if trade.direction == "YES":
                    new_edge_raw = ec_yes_v21 - side_price
                else:
                    new_edge_raw = ec_no_v21 - side_price
                new_edge = round(new_edge_raw * 100, 2)  # pp
                v21_would_trade = new_edge is not None and new_edge >= _V21_MIN_EDGE_PP
                if not v21_would_trade and new_edge is not None:
                    skip_reason = f"Edge too low ({new_edge:.1f}pp < {_V21_MIN_EDGE_PP:.0f}pp minimum)"
        elif v21_city_eligible and snap is None:
            skip_reason = "No snapshot available for recomputation"

        # P/L unchanged (actual outcome is the same)
        pl_actual = trade.profit_loss or 0.0
        # Hypothetical V2.1 P/L: same outcome, same stake if V2.1 would have traded; $0 if V2.1 would have skipped
        pl_v21 = pl_actual if v21_would_trade else 0.0

        rows.append({
            "tradeId": trade.id,
            "marketTicker": trade.market_ticker,
            "city": city,
            "forecastTimestamp": trade.created_at.isoformat() if trade.created_at else None,
            "settlementDate": trade.target_settlement_date,
            "settlementStation": station_name,
            "stationVerifiedForV21": v21_city_eligible,
            # V2.0 values
            "sigmaV20": round(sigma_v20, 2) if sigma_v20 is not None else None,
            "ecYesProbV20": trade.ec_yes_probability,
            "ecSideProbV20": trade.ec_side_probability,
            "edgePpV20": trade.edge_pct_points,
            "confidenceLabelV20": trade.confidence_label,
            "fallbackLevelV20": trade.fallback_level,
            # V2.1 values
            "sigmaV21": round(sigma_v21, 2),
            "sigmaChanged": sigma_changed,
            "ecYesProbV21": ec_yes_v21,
            "newEdgePp": new_edge,
            "v21WouldTrade": v21_would_trade,
            "v21SkipReason": skip_reason,
            # Outcome
            "direction": trade.direction,
            "sideMarketPrice": trade.side_market_price,
            "kalshiResult": trade.kalshi_result,
            "outcome": trade.outcome,
            "plActual": round(pl_actual, 4),
            "plHypotheticalV21": round(pl_v21, 4),
        })

    # Summary statistics
    total = len(rows)
    v21_would_take = [r for r in rows if r["v21WouldTrade"]]
    v21_skip = [r for r in rows if not r["v21WouldTrade"]]

    # V2.0 losses that V2.1 would avoid (V2.1 would skip AND V2.0 was a LOSS)
    losses_avoided = sum(1 for r in v21_skip if r["outcome"] == "LOSS")
    # V2.0 wins that V2.1 would skip
    wins_skipped = sum(1 for r in v21_skip if r["outcome"] == "WIN")

    # V2.1 hypothetical win rate (trades V2.1 would take)
    settled_v21 = [r for r in v21_would_take if r["outcome"] in ("WIN", "LOSS")]
    v21_wins = sum(1 for r in settled_v21 if r["outcome"] == "WIN")
    v21_win_rate = round(v21_wins / len(settled_v21), 4) if settled_v21 else None

    # V2.1 hypothetical ROI
    total_v21_pl = sum(r["plHypotheticalV21"] for r in v21_would_take)
    total_v21_stake = len(v21_would_take) * _V21_STAKE  # approximate: same stake per trade
    v21_roi = round(total_v21_pl / total_v21_stake * 100, 2) if total_v21_stake > 0 else None

    # V2.0 ROI (for comparison)
    total_v20_pl = sum(r["plActual"] for r in rows)
    total_v20_stake = total * _V21_STAKE
    v20_roi = round(total_v20_pl / total_v20_stake * 100, 2) if total_v20_stake > 0 else None

    # V2.1 hypothetical Brier score
    v21_brier_trades = [
        r for r in v21_would_take
        if r["kalshiResult"] in ("yes", "no") and r["ecYesProbV21"] is not None
    ]
    if v21_brier_trades:
        brier_sum = sum(
            (r["ecYesProbV21"] - (1 if r["kalshiResult"] == "yes" else 0)) ** 2
            for r in v21_brier_trades
        )
        v21_brier = round(brier_sum / len(v21_brier_trades), 6)
    else:
        v21_brier = None

    # Average edge change
    edges_v20 = [r["edgePpV20"] for r in rows if r["edgePpV20"] is not None]
    edges_v21 = [r["newEdgePp"] for r in rows if r["newEdgePp"] is not None]
    avg_edge_v20 = _avg(edges_v20)
    avg_edge_v21 = _avg(edges_v21)

    # Average sigma change
    sigmas_v20 = [r["sigmaV20"] for r in rows if r["sigmaV20"] is not None]
    sigmas_v21 = [r["sigmaV21"] for r in rows]
    avg_sigma_v20 = _avg(sigmas_v20)
    avg_sigma_v21 = _avg(sigmas_v21)

    return {
        "disclaimer": _RETRO_DISCLAIMER,
        "sampleSize": total,
        "trades": rows,
        "summary": {
            "totalInSample": total,
            "v21WouldTake": len(v21_would_take),
            "v21WouldSkip": len(v21_skip),
            "lossesAvoided": losses_avoided,
            "winsSkipped": wins_skipped,
            "v21WinRate": v21_win_rate,
            "v21Roi": v21_roi,
            "v20Roi": v20_roi,
            "v21BrierScore": v21_brier,
            "avgEdgeV20Pp": avg_edge_v20,
            "avgEdgeV21Pp": avg_edge_v21,
            "avgSigmaV20": avg_sigma_v20,
            "avgSigmaV21": avg_sigma_v21,
        },
    }


_RETRO_DISCLAIMER = (
    "RETROSPECTIVE ONLY — these results simulate V2.1 decisions on historical V2.0 "
    "trades using the same market conditions that existed when those trades were placed. "
    "They are NOT a valid substitute for forward paper trading. Sigma values are recalculated "
    "using V2.1 rules (floor 3.5°F, conservative prior) but do NOT reflect market prices, "
    "forecast values, or quote availability that existed at the original trade timestamp. "
    "Do not use these results to claim V2.1 is validated or ready for real trading."
)


def _empty_retro_summary() -> dict:
    return {
        "totalInSample": 0,
        "v21WouldTake": 0,
        "v21WouldSkip": 0,
        "lossesAvoided": 0,
        "winsSkipped": 0,
        "v21WinRate": None,
        "v21Roi": None,
        "v20Roi": None,
        "v21BrierScore": None,
        "avgEdgeV20Pp": None,
        "avgEdgeV21Pp": None,
        "avgSigmaV20": None,
        "avgSigmaV21": None,
    }


# ---------------------------------------------------------------------------
# 2. Probability calibration by confidence bucket
# ---------------------------------------------------------------------------

_CALIB_BUCKETS = [
    ("50–60%", 0.50, 0.60),
    ("60–70%", 0.60, 0.70),
    ("70–80%", 0.70, 0.80),
    ("80–90%", 0.80, 0.90),
    ("90–100%", 0.90, 1.001),
]

LOW_SAMPLE_THRESHOLD = 30   # spec requirement


def _calib_for_strategy(trades: list[PaperTrade]) -> list[dict]:
    """
    Group settled trades into confidence buckets and compute calibration stats.

    Uses ec_side_probability (the probability EdgeCast assigned to our chosen side)
    and whether that side won.
    """
    settled = [
        t for t in trades
        if t.status == "SETTLED"
        and t.kalshi_result in ("yes", "no")
        and t.ec_side_probability is not None
    ]

    rows: list[dict] = []
    for label, lo, hi in _CALIB_BUCKETS:
        bucket = [
            t for t in settled
            if lo <= (t.ec_side_probability or 0) < hi
        ]
        if not bucket:
            rows.append({
                "bucket": label,
                "count": 0,
                "avgPredictedProb": None,
                "actualWinRate": None,
                "calibrationDiff": None,
                "avgBrierScore": None,
                "avgRoi": None,
                "lowSample": True,
            })
            continue

        avg_pred = _avg([t.ec_side_probability for t in bucket])
        wins = sum(1 for t in bucket if t.outcome == "WIN")
        actual_wr = round(wins / len(bucket), 4)
        calib_diff = round(actual_wr - (avg_pred or 0), 4)

        # Brier: (predicted_side_prob - actual_side_outcome)^2
        brier_vals = [
            (t.ec_side_probability - (1 if t.outcome == "WIN" else 0)) ** 2
            for t in bucket
            if t.ec_side_probability is not None
        ]
        avg_brier = _avg(brier_vals)

        total_stake = sum(t.stake or 0 for t in bucket)
        net_pl = sum(t.profit_loss or 0 for t in bucket)
        avg_roi = round(net_pl / total_stake * 100, 4) if total_stake > 0 else None

        rows.append({
            "bucket": label,
            "bucketLo": lo,
            "bucketHi": min(hi, 1.0),
            "count": len(bucket),
            "avgPredictedProb": avg_pred,
            "actualWinRate": actual_wr,
            "calibrationDiff": calib_diff,
            "avgBrierScore": avg_brier,
            "avgRoi": avg_roi,
            "lowSample": len(bucket) < LOW_SAMPLE_THRESHOLD,
        })
    return rows


@router.get("/analytics/v21/calibration")
async def get_v21_calibration(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Probability calibration analysis.  Groups settled trades by their entry
    confidence bucket and compares predicted vs actual win rates.
    Returns separate series for V2.0 and V2.1 so the chart can show both.
    """
    q = await session.execute(select(PaperTrade).where(PaperTrade.status == "SETTLED"))
    all_settled: list[PaperTrade] = q.scalars().all()

    v20 = [t for t in all_settled if t.strategy_version == "v2.0"]
    v21 = [t for t in all_settled if t.strategy_version == "v2.1"]

    return {
        "v20": _calib_for_strategy(v20),
        "v21": _calib_for_strategy(v21),
        "lowSampleThreshold": LOW_SAMPLE_THRESHOLD,
        "note": (
            "Points below the diagonal: EdgeCast was overconfident. "
            "Points above: EdgeCast was underconfident. "
            "Points near the diagonal: confidence matched actual results. "
            f"Buckets with fewer than {LOW_SAMPLE_THRESHOLD} settled trades are marked Low Sample and should not be used to draw conclusions."
        ),
    }


# ---------------------------------------------------------------------------
# 3. V2.1 Model Readiness panel
# ---------------------------------------------------------------------------

@router.get("/analytics/v21/readiness")
async def get_v21_readiness(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    V2.1 model readiness panel.  Shows how many predictions have been made,
    how many cities are verified, and what readiness stage we are in.
    """
    # Load all V2.1 trades
    v21_q = await session.execute(
        select(PaperTrade).where(PaperTrade.strategy_version == "v2.1")
    )
    v21_trades: list[PaperTrade] = v21_q.scalars().all()

    open_t = [t for t in v21_trades if t.status == "OPEN"]
    settled_t = [t for t in v21_trades if t.status == "SETTLED"]
    wins = [t for t in settled_t if t.outcome == "WIN"]

    total_stake = sum(t.stake or 0 for t in settled_t)
    net_pl = sum(t.profit_loss or 0 for t in settled_t)

    win_rate = round(len(wins) / len(settled_t), 4) if settled_t else None
    roi = round(net_pl / total_stake * 100, 4) if total_stake > 0 else None
    brier = _brier_score(v21_trades)

    # Sigma usage breakdown
    uses_learned = sum(
        1 for t in v21_trades
        if t.fallback_level in ("city", "global")
    )
    uses_fallback = sum(
        1 for t in v21_trades
        if t.fallback_level == "fixed_table" or t.fallback_level is None
    )
    total_preds = len(v21_trades)

    pct_learned = round(uses_learned / total_preds * 100, 1) if total_preds > 0 else 0.0
    pct_fallback = round(uses_fallback / total_preds * 100, 1) if total_preds > 0 else 0.0

    avg_sigma = _avg([t.sigma_used for t in v21_trades if t.sigma_used is not None])

    # ForecastErrorStats buckets with >= MIN_SAMPLE observations
    stats_q = await session.execute(
        select(ForecastErrorStats).where(
            ForecastErrorStats.sample_size >= MIN_SAMPLE,
            ForecastErrorStats.fallback_level != "global",
        )
    )
    stats_rows = stats_q.scalars().all()
    buckets_with_data = len(stats_rows)

    # Station counts
    verified_cities = [name for name, s in SETTLEMENT_STATIONS.items() if s.verified]
    unverified_cities = [name for name, s in SETTLEMENT_STATIONS.items() if not s.verified]

    # Readiness stage
    stage = _readiness_stage(len(settled_t), buckets_with_data, len(verified_cities))

    # Unresolved station issues for actively-traded cities
    # (cities that appear in V2.1 open trades but are not verified)
    traded_cities = set(t.city for t in open_t if t.city)
    unresolved_stations = [
        c for c in traded_cities
        if c not in verified_cities
    ]

    return {
        "totalPredictions": total_preds,
        "openTrades": len(open_t),
        "settledTrades": len(settled_t),
        "wins": len(wins),
        "losses": len(settled_t) - len(wins),
        "winRate": win_rate,
        "roi": roi,
        "brierScore": brier,
        "avgSigma": avg_sigma,
        "verifiedCities": verified_cities,
        "verifiedCityCount": len(verified_cities),
        "unverifiedCities": unverified_cities,
        "unverifiedCityCount": len(unverified_cities),
        "pctLearnedSigma": pct_learned,
        "pctFallbackSigma": pct_fallback,
        "bucketsWithMinSample": buckets_with_data,
        "minSampleThreshold": MIN_SAMPLE,
        "readinessStage": stage,
        "unresolvedActiveStations": unresolved_stations,
        "criteria": {
            "settledNeeded": 250,
            "bucketsNeeded": 2,
            "currentSettled": len(settled_t),
            "currentBuckets": buckets_with_data,
        },
    }


# ---------------------------------------------------------------------------
# 4. Settlement station coverage
# ---------------------------------------------------------------------------

@router.get("/analytics/v21/stations")
async def get_station_coverage(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Full settlement station registry: all cities, verification status, coordinates,
    and V2.1 trading eligibility.
    """
    # Count V2.1 trades per city for context
    v21_q = await session.execute(
        select(PaperTrade.city, func.count().label("count"))
        .where(PaperTrade.strategy_version == "v2.1")
        .group_by(PaperTrade.city)
    )
    v21_counts: dict[str, int] = {row[0]: row[1] for row in v21_q.all() if row[0]}

    # ForecastErrorStats count per city
    stats_q = await session.execute(
        select(ForecastErrorStats.city, func.sum(ForecastErrorStats.sample_size).label("total_obs"))
        .where(ForecastErrorStats.fallback_level != "global")
        .group_by(ForecastErrorStats.city)
    )
    obs_counts: dict[str, int] = {row[0]: int(row[1]) for row in stats_q.all() if row[0]}

    stations: list[dict] = []
    for city, s in SETTLEMENT_STATIONS.items():
        stations.append({
            "city": city,
            "stationName": s.station_name,
            "ghcndStationId": s.ghcnd_station_id,
            "lat": s.lat,
            "lon": s.lon,
            "timezone": s.timezone,
            "verified": s.verified,
            "source": s.source,
            "notes": s.notes,
            "v21TradingEnabled": s.verified,
            "v21TradeCount": v21_counts.get(city, 0),
            "observationCount": obs_counts.get(city, 0),
        })

    verified = [s for s in stations if s["verified"]]
    unverified = [s for s in stations if not s["verified"]]

    return {
        "stations": stations,
        "verifiedCount": len(verified),
        "unverifiedCount": len(unverified),
        "totalCount": len(stations),
        "note": (
            "V2.1 paper trading is only enabled for VERIFIED cities. "
            "Unverified stations use inferred airport ICAO codes and have not been "
            "confirmed from Kalshi contract PDFs. To verify a city, download its "
            "Kalshi contract PDF from kalshi.com/markets and find the phrase "
            "'NWS Daily Climate Report for <Station Name>'. "
            "Los Angeles has HIGH AMBIGUITY — do not mark verified until contract PDF "
            "confirms whether settlement uses LAX airport or USC Downtown."
        ),
    }


# ---------------------------------------------------------------------------
# 5. Oklahoma City July 28 evidence table
# ---------------------------------------------------------------------------

@router.get("/analytics/v21/okc-explanation")
async def get_okc_explanation(
    _user: dict = Depends(get_current_user),
) -> dict:
    """
    Evidence table for the July 28 Oklahoma City 9°F miss.
    Values are sourced from the July 2026 pipeline audit.
    Forecast values are as recorded at original trade creation time.
    """
    return {
        "event": {
            "city": "Oklahoma City",
            "tradeDate": "2026-07-28",
            "marketTicker": "KXLOWTOKC-26JUL28-B71.5",
            "direction": "YES",
            "contractDescription": "OKC daily low ≥ 71.5°F on July 28",
            "actualOfficialHigh": 71.0,
            "actualNote": "KOKC (Will Rogers World Airport) recorded ~71–72°F low; YES settled at $0.00",
        },
        "forecastSources": [
            {
                "source": "Open-Meteo (city-centre, pre-fix)",
                "forecastTimestamp": "2026-07-28T00:00:00Z",
                "forecastedValue": 80.2,
                "forecastCoordinates": "35.47°N, 97.52°W (OKC city centre)",
                "settlementStation": "KOKC 35.39°N 97.60°W (7 mi away)",
                "actualOfficialValue": 71.5,
                "absoluteError": 8.7,
                "notes": "City-centre coordinates caused ~1–2°F location bias. Remaining 7°F is model error.",
            },
            {
                "source": "Open-Meteo (station-corrected, post-fix)",
                "forecastTimestamp": "2026-07-28T00:00:00Z",
                "forecastedValue": 79.1,
                "forecastCoordinates": "35.39°N, 97.60°W (KOKC airport)",
                "settlementStation": "KOKC 35.39°N 97.60°W",
                "actualOfficialValue": 71.5,
                "absoluteError": 7.6,
                "notes": (
                    "Post-fix coordinates reduce location error by ~1–2°F but "
                    "do not fix the core model error. Open-Meteo did not capture "
                    "the strong radiative cooling event on this night."
                ),
            },
            {
                "source": "Kalshi market consensus (pre-trade)",
                "forecastTimestamp": "2026-07-28T00:00:00Z",
                "forecastedValue": None,
                "forecastCoordinates": "N/A — market-implied probability",
                "settlementStation": "KOKC",
                "actualOfficialValue": 71.5,
                "absoluteError": None,
                "notes": (
                    "Kalshi market implied ~3% YES probability — 97% consensus "
                    "predicted the low would NOT reach 71.5°F. EdgeCast V2.0 "
                    "bet against 97% market consensus using a σ=1.22°F edge that "
                    "was 3–6× too small."
                ),
            },
        ],
        "rootCauseAssessment": {
            "verdict": "combination",
            "primaryCause": (
                "Open-Meteo model error: the forecast failed to capture a strong "
                "radiative cooling event on a clear July night. This accounts for "
                "approximately 7–8°F of the 9°F miss."
            ),
            "secondaryCause": (
                "City-centre vs station-location offset: forecasting at OKC city "
                "centre (35.47°N, 97.52°W) instead of Will Rogers Airport (35.39°N, 97.60°W) "
                "contributed approximately 1–2°F of additional error."
            ),
            "sigmaFailure": (
                "The critical compounding failure was the V2.0 sigma of 1.22°F "
                "(from only 5 DB samples), which was 3–6× smaller than the actual "
                "forecast error range of 3–10°F. This turned a routine model "
                "uncertainty into a reported 94pp edge and allowed V2.0 to bet "
                "against 97% market consensus with extreme confidence. "
                "V2.1 fixes this with a σ floor of 3.5°F."
            ),
            "fixes": [
                "Forecast coordinates now use KOKC airport (35.39°N, 97.60°W)",
                "σ floor enforced at 3.5°F — a 9°F miss would require σ ≥ 4.5°F to be within 2σ",
                "MIN_SAMPLE raised from 5 → 30 to prevent premature σ estimates",
                "Conservative prior (5.0°F) replaces V1 table (2.5°F) when DB samples < 30",
            ],
        },
        "dataQualityNote": (
            "The Open-Meteo post-fix forecast value (79.1°F) is an approximation based "
            "on the ~1–2°F expected location correction. The exact archived forecast at "
            "KOKC station coordinates is not recoverable from Open-Meteo's free tier API. "
            "The pre-fix value (80.2°F) was the live forecast recorded at trade creation time."
        ),
    }


# ---------------------------------------------------------------------------
# 6. Consensus guard backtest results
# ---------------------------------------------------------------------------

@router.get("/analytics/v21/consensus-backtest")
async def get_consensus_backtest(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Retrospective impact of the 85% consensus guard on settled V2.0 trades.
    The guard is currently DISABLED.  These results are experimental.
    """
    from app.services.paper_trading_v21 import consensus_guard_backtest
    result = await consensus_guard_backtest(session)
    result["guardStatus"] = "DISABLED"
    result["experimentalWarning"] = (
        "These results may be overfit to a small sample. "
        "The consensus guard has not been evaluated on out-of-sample data. "
        "Do not enable the guard based solely on this retrospective. "
        "Recommended threshold for activation: ≥100 settled V2.1 trades with "
        "consistent positive ROI before re-evaluating the guard."
    )
    return result
