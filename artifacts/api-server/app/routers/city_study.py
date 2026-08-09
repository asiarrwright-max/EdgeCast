"""
GET /api/city-study

Read-only city-by-city accuracy and suitability study.
No writes, no model changes, no eligibility changes.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.services.settlement_stations import SETTLEMENT_STATIONS

router = APIRouter(tags=["city-study"])

# ---------------------------------------------------------------------------
# Score weights (must sum to 1.0)
# ---------------------------------------------------------------------------
W_FORECAST   = 0.30   # forecast accuracy
W_TRADING    = 0.25   # historical trading quality
W_LIQUIDITY  = 0.20   # market quality / quote availability
W_SAMPLE     = 0.15   # sample size / data quality
W_STATION    = 0.10   # settlement / station integrity


# ---------------------------------------------------------------------------
# Scoring helpers — all return 0..100
# ---------------------------------------------------------------------------

def _score_mae(mae: float | None) -> float:
    """Lower MAE is better. 0.5°F → 100; 5.5°F+ → 0."""
    if mae is None:
        return 0.0
    return max(0.0, min(100.0, 100.0 - (mae - 0.5) * 20.0))


def _score_win_rate(win_rate_pct: float | None, n: int) -> float:
    """Win rate 0-100, but penalise tiny samples."""
    if win_rate_pct is None or n == 0:
        return 0.0
    raw = win_rate_pct  # already 0-100
    # penalise if fewer than 20 settled trades
    if n < 10:
        return raw * 0.40
    if n < 20:
        return raw * 0.65
    return raw


def _score_liquidity(pct_valid_ask: float | None) -> float:
    if pct_valid_ask is None:
        return 0.0
    return max(0.0, min(100.0, float(pct_valid_ask)))


def _score_sample(settled: int, fv_obs: int) -> float:
    """Log-scaled composite of settled trades and forecast verifications."""
    trade_s  = min(100.0, 35.0 * math.log1p(settled / 10.0)) if settled > 0 else 0.0
    fv_s     = min(100.0, 50.0 * math.log1p(fv_obs / 5.0))   if fv_obs  > 0 else 0.0
    return 0.6 * trade_s + 0.4 * fv_s


def _score_station(verified: bool, nws: bool) -> float:
    if not nws:
        return 0.0          # non-NWS settlement — hard disqualifier
    if verified:
        return 100.0
    return 60.0             # unverified but NWS-compatible


def _sample_grade(settled: int, fv_obs: int) -> str:
    if settled < 10 and fv_obs < 10:
        return "VERY LOW"
    if settled < 30 or fv_obs < 15:
        return "LOW"
    if settled < 80 or fv_obs < 25:
        return "MODERATE"
    if settled < 200 or fv_obs < 50:
        return "GOOD"
    return "STRONG"


def _sample_warnings(city: str, settled: int, fv_obs: int,
                     wins: int, losses: int) -> list[str]:
    warnings: list[str] = []
    if settled < 10:
        warnings.append(f"Fewer than 10 settled observations ({settled}) — results are unreliable.")
    if fv_obs < 30:
        warnings.append(f"Fewer than 30 forecast-verification observations ({fv_obs}).")
    if settled > 0:
        dominant = max(wins, losses)
        if dominant > 0 and dominant / settled >= 0.90:
            warnings.append("One outcome (win or loss) dominates ≥90% of settled trades.")
    return warnings


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------

@router.get("/city-study")
async def get_city_study(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Read-only city-by-city suitability study.
    Pulls all metrics live from the database. No model or trading changes.
    """
    generated_at = datetime.now(timezone.utc).isoformat()

    # ── 1. Overall trading performance ──────────────────────────────────────
    perf_rows = (await db.execute(sql_text("""
        SELECT
          city,
          COUNT(*) FILTER (WHERE outcome IN ('WIN','LOSS'))                    AS settled_total,
          COUNT(*) FILTER (WHERE eligibility_status='OFFICIAL'
                             AND outcome IN ('WIN','LOSS'))                    AS official_settled,
          COUNT(*) FILTER (WHERE eligibility_status='RESEARCH_ONLY'
                             AND outcome IN ('WIN','LOSS'))                    AS research_settled,
          COUNT(*) FILTER (WHERE outcome = 'WIN')                             AS wins,
          COUNT(*) FILTER (WHERE outcome = 'LOSS')                            AS losses,
          ROUND((100.0 * COUNT(*) FILTER (WHERE outcome='WIN')
                 / NULLIF(COUNT(*) FILTER (WHERE outcome IN ('WIN','LOSS')),0)
                )::numeric, 1)                                                AS win_rate_pct,
          ROUND(AVG(ec_side_probability)
                FILTER (WHERE outcome IN ('WIN','LOSS'))::numeric, 3)         AS avg_predicted_prob,
          ROUND(AVG(CASE WHEN outcome='WIN' THEN 1.0 WHEN outcome='LOSS' THEN 0.0 END)
                ::numeric, 3)                                                 AS actual_win_rate,
          ROUND(SUM(profit_loss)
                FILTER (WHERE outcome IN ('WIN','LOSS'))::numeric, 2)         AS total_pnl,
          ROUND(SUM(profit_loss)
                FILTER (WHERE eligibility_status='OFFICIAL'
                         AND outcome IN ('WIN','LOSS'))::numeric, 2)          AS official_pnl,
          ROUND(AVG(edge_pct_points)
                FILTER (WHERE outcome IN ('WIN','LOSS'))::numeric, 1)         AS avg_edge,
          ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY edge_pct_points)
                FILTER (WHERE outcome IN ('WIN','LOSS'))::numeric, 1)         AS median_edge,
          COUNT(DISTINCT target_settlement_date)                              AS unique_market_days,
          COUNT(DISTINCT market_ticker)                                       AS unique_tickers,
          COUNT(DISTINCT strategy_version)                                    AS strategy_versions
        FROM paper_trades
        WHERE city IS NOT NULL AND city != ''
        GROUP BY city
        ORDER BY settled_total DESC, unique_market_days DESC
    """))).fetchall()

    perf: dict[str, dict] = {}
    for r in perf_rows:
        d = dict(r._mapping)
        perf[d["city"]] = d

    # ── 2. Direction + contract-type breakdown ───────────────────────────────
    dir_rows = (await db.execute(sql_text("""
        SELECT city, direction, contract_type,
               COUNT(*) FILTER (WHERE outcome IN ('WIN','LOSS')) AS settled,
               COUNT(*) FILTER (WHERE outcome = 'WIN')           AS wins,
               ROUND(SUM(profit_loss) FILTER (WHERE outcome IN ('WIN','LOSS'))::numeric,2) AS pnl
        FROM paper_trades
        WHERE city IS NOT NULL AND city != '' AND outcome IN ('WIN','LOSS')
        GROUP BY city, direction, contract_type
        ORDER BY city, direction, contract_type
    """))).fetchall()

    dir_breakdown: dict[str, list[dict]] = {}
    for r in dir_rows:
        d = dict(r._mapping)
        dir_breakdown.setdefault(d["city"], []).append(d)

    # ── 3. Model-version win rates ───────────────────────────────────────────
    mv_rows = (await db.execute(sql_text("""
        SELECT city, strategy_version,
               COUNT(*) FILTER (WHERE outcome IN ('WIN','LOSS')) AS settled,
               COUNT(*) FILTER (WHERE outcome='WIN')             AS wins,
               COUNT(*) FILTER (WHERE outcome='LOSS')            AS losses,
               ROUND(100.0*COUNT(*) FILTER (WHERE outcome='WIN')
                     /NULLIF(COUNT(*) FILTER (WHERE outcome IN ('WIN','LOSS')),0),1) AS win_rate,
               ROUND(SUM(profit_loss) FILTER (WHERE outcome IN ('WIN','LOSS'))::numeric,2) AS pnl,
               ROUND(AVG(edge_pct_points) FILTER (WHERE outcome IN ('WIN','LOSS'))::numeric,1) AS avg_edge,
               COUNT(*) AS total_evaluated
        FROM paper_trades
        WHERE city IS NOT NULL AND city != ''
          AND strategy_version IN ('v1.0','v2.0','v2.2','v2.3')
        GROUP BY city, strategy_version
        ORDER BY city, strategy_version
    """))).fetchall()

    model_versions: dict[str, list[dict]] = {}
    for r in mv_rows:
        d = dict(r._mapping)
        model_versions.setdefault(d["city"], []).append(d)

    # ── 4. Forecast verifications (accuracy) ────────────────────────────────
    fv_rows = (await db.execute(sql_text("""
        SELECT city,
               COUNT(*)                                          AS fv_obs,
               ROUND(AVG(ABS(forecast_error))::numeric,2)        AS mae,
               ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP
                     (ORDER BY ABS(forecast_error))::numeric,2)  AS median_ae,
               ROUND(AVG(forecast_error)::numeric,2)             AS mean_bias,
               ROUND(SQRT(AVG(forecast_error^2))::numeric,2)     AS rmse,
               ROUND(STDDEV(forecast_error)::numeric,2)           AS sigma,
               ROUND(100.0*COUNT(*) FILTER
                     (WHERE ABS(forecast_error)<=1.0)/NULLIF(COUNT(*),0),1) AS pct_within_1f,
               ROUND(100.0*COUNT(*) FILTER
                     (WHERE ABS(forecast_error)<=2.0)/NULLIF(COUNT(*),0),1) AS pct_within_2f,
               ROUND(100.0*COUNT(*) FILTER
                     (WHERE ABS(forecast_error)<=3.0)/NULLIF(COUNT(*),0),1) AS pct_within_3f,
               ROUND(AVG(ABS(forecast_error)) FILTER
                     (WHERE lead_time_days < 0.25)::numeric,2)              AS mae_lt6h,
               ROUND(AVG(ABS(forecast_error)) FILTER
                     (WHERE lead_time_days >= 0.25 AND lead_time_days < 0.5)::numeric,2) AS mae_6_12h,
               ROUND(AVG(ABS(forecast_error)) FILTER
                     (WHERE lead_time_days >= 0.5  AND lead_time_days < 1.0)::numeric,2) AS mae_12_24h,
               ROUND(AVG(ABS(forecast_error)) FILTER
                     (WHERE lead_time_days >= 1.0  AND lead_time_days < 2.0)::numeric,2) AS mae_24_48h,
               ROUND(AVG(ABS(forecast_error)) FILTER
                     (WHERE lead_time_days >= 2.0)::numeric,2)              AS mae_48hplus,
               STRING_AGG(DISTINCT source_label, ', ')                       AS sources_seen,
               COUNT(*) FILTER (WHERE weather_variable='high')               AS fv_high,
               COUNT(*) FILTER (WHERE weather_variable='low')                AS fv_low
        FROM forecast_verifications
        WHERE city IS NOT NULL
        GROUP BY city
        ORDER BY fv_obs DESC
    """))).fetchall()

    fv: dict[str, dict] = {}
    for r in fv_rows:
        d = dict(r._mapping)
        # Filter out cities with no usable data (Chicago / DC have 0 actual errors)
        if d["mae"] is not None:
            fv[d["city"]] = d

    # ── 5. Forecast error stats (pre-aggregated) ─────────────────────────────
    fes_rows = (await db.execute(sql_text("""
        SELECT city, weather_variable, lead_time_bucket,
               mean_error, mae, std_dev, sample_size
        FROM forecast_error_stats
        WHERE city != '__global__'
        ORDER BY city, weather_variable, lead_time_bucket
    """))).fetchall()

    fes: dict[str, list[dict]] = {}
    for r in fes_rows:
        d = dict(r._mapping)
        fes.setdefault(d["city"], []).append(d)

    # ── 6. Market quality (Kalshi scans) ────────────────────────────────────
    mq_rows = (await db.execute(sql_text("""
        SELECT city,
               COUNT(*) AS market_scans,
               COUNT(*) FILTER (WHERE yes_ask IS NOT NULL AND yes_ask > 0.01) AS scans_valid_ask,
               COUNT(*) FILTER (WHERE yes_ask IS NULL OR yes_ask <= 0.01)     AS scans_no_quote,
               ROUND(AVG(volume)::numeric,1)                                   AS avg_volume,
               ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY volume)::numeric,1) AS median_volume,
               COUNT(DISTINCT DATE(collection_timestamp))                      AS distinct_market_days,
               COUNT(DISTINCT ticker)                                          AS distinct_tickers
        FROM kalshi_markets
        WHERE city IS NOT NULL AND city != '' AND weather_matched = true
        GROUP BY city
        ORDER BY market_scans DESC
    """))).fetchall()

    mq: dict[str, dict] = {}
    for r in mq_rows:
        d = dict(r._mapping)
        mq[d["city"]] = d

    # ── 7. Quote freshness from paper_trades ────────────────────────────────
    qf_rows = (await db.execute(sql_text("""
        SELECT city,
               COUNT(*) AS total_evaluated,
               COUNT(*) FILTER (WHERE quote_age_seconds IS NOT NULL)          AS with_quote_age,
               COUNT(*) FILTER (WHERE quote_age_seconds <= 300)               AS fresh_lte300s,
               COUNT(*) FILTER (WHERE quote_age_seconds > 300)                AS stale_gt300s,
               ROUND(100.0*COUNT(*) FILTER (WHERE quote_age_seconds <= 300)
                     /NULLIF(COUNT(*) FILTER (WHERE quote_age_seconds IS NOT NULL),0),1)
                                                                              AS pct_fresh_300s,
               ROUND(100.0*COUNT(*) FILTER (WHERE is_executable = true)
                     /NULLIF(COUNT(*),0),1)                                   AS pct_executable,
               ROUND(AVG(est_available_qty)
                     FILTER (WHERE est_available_qty > 0)::numeric,1)         AS avg_qty,
               COUNT(*) FILTER (WHERE eligibility_reason LIKE '%missing_or_stale%') AS rej_stale_quote,
               COUNT(*) FILTER (WHERE eligibility_reason LIKE '%settlement_station_unverified%') AS rej_station,
               COUNT(*) FILTER (WHERE eligibility_reason LIKE '%v2_excluded%') AS rej_v2_excluded,
               COUNT(*) FILTER (WHERE eligibility_reason LIKE '%hourly_temperature%') AS rej_hourly
        FROM paper_trades
        WHERE city IS NOT NULL AND city != ''
        GROUP BY city
        ORDER BY total_evaluated DESC
    """))).fetchall()

    qf: dict[str, dict] = {}
    for r in qf_rows:
        d = dict(r._mapping)
        qf[d["city"]] = d

    # ── 8. FTB-era (v2.3) city data ──────────────────────────────────────────
    ftb_rows = (await db.execute(sql_text("""
        SELECT city,
               COUNT(*) AS total_v23,
               COUNT(*) FILTER (WHERE eligibility_status='OFFICIAL')  AS official_count,
               COUNT(*) FILTER (WHERE eligibility_status='RESEARCH_ONLY') AS research_count,
               COUNT(*) FILTER (WHERE outcome IN ('WIN','LOSS'))       AS settled_v23,
               COUNT(*) FILTER (WHERE outcome='WIN')                   AS wins_v23,
               COUNT(*) FILTER (WHERE eligibility_reason LIKE '%missing_or_stale_executable_quote%') AS rej_stale,
               COUNT(*) FILTER (WHERE eligibility_reason LIKE '%v2_excluded%') AS rej_v2_excl,
               COUNT(*) FILTER (WHERE eligibility_reason LIKE '%hourly_temperature%') AS rej_hourly,
               COUNT(*) FILTER (WHERE eligibility_reason LIKE '%settlement_station_unverified%') AS rej_station,
               MIN(created_at) AS first_v23,
               MAX(created_at) AS last_v23,
               COUNT(DISTINCT DATE(created_at)) AS scan_days,
               COUNT(DISTINCT market_ticker) AS unique_tickers_v23
        FROM paper_trades
        WHERE strategy_version = 'v2.3' AND city IS NOT NULL
        GROUP BY city
        ORDER BY total_v23 DESC
    """))).fetchall()

    ftb: dict[str, dict] = {}
    for r in ftb_rows:
        d = dict(r._mapping)
        ftb[d["city"]] = d

    # ── 9. Build per-city results ────────────────────────────────────────────
    all_cities = sorted(
        set(perf.keys()) | set(fv.keys()) | set(mq.keys()),
        key=lambda c: perf.get(c, {}).get("settled_total", 0) or 0,
        reverse=True,
    )

    cities_out: list[dict] = []
    for city in all_cities:
        p   = perf.get(city, {})
        f   = fv.get(city)
        m   = mq.get(city, {})
        q   = qf.get(city, {})
        fb  = ftb.get(city, {})
        sta = SETTLEMENT_STATIONS.get(city)

        settled     = int(p.get("settled_total") or 0)
        wins        = int(p.get("wins") or 0)
        losses      = int(p.get("losses") or 0)
        win_rate    = float(p.get("win_rate_pct") or 0) if settled > 0 else None
        fv_obs      = int(f["fv_obs"]) if f else 0
        mae         = float(f["mae"]) if f else None

        scans       = int(m.get("market_scans") or 0)
        valid_ask   = int(m.get("scans_valid_ask") or 0)
        pct_valid   = round(100.0 * valid_ask / scans, 1) if scans > 0 else None

        s_forecast  = _score_mae(mae)
        s_trading   = _score_win_rate(win_rate, settled)
        s_liquidity = _score_liquidity(pct_valid)
        s_sample    = _score_sample(settled, fv_obs)
        s_station   = _score_station(
            verified=sta.verified if sta else False,
            nws=sta.nws_settlement if sta else True,
        )

        total_score = round(
            W_FORECAST  * s_forecast  +
            W_TRADING   * s_trading   +
            W_LIQUIDITY * s_liquidity +
            W_SAMPLE    * s_sample    +
            W_STATION   * s_station,
            1,
        )

        # calibration gap for most recent strategy with settled trades
        cal_gap = None
        avg_pred = p.get("avg_predicted_prob")
        actual_wr = p.get("actual_win_rate")
        if avg_pred is not None and actual_wr is not None:
            cal_gap = round(float(avg_pred) - float(actual_wr), 3)

        cities_out.append({
            "city": city,
            # station
            "station_name":    sta.station_name    if sta else None,
            "station_id":      sta.ghcnd_station_id if sta else None,
            "station_verified": sta.verified        if sta else False,
            "nws_compatible":  sta.nws_settlement   if sta else True,
            "station_notes":   sta.notes            if sta else None,
            # trading performance
            "settled_total":   settled,
            "official_settled": int(p.get("official_settled") or 0),
            "research_settled": int(p.get("research_settled") or 0),
            "wins":            wins,
            "losses":          losses,
            "win_rate_pct":    win_rate,
            "avg_predicted_prob": float(p["avg_predicted_prob"]) if p.get("avg_predicted_prob") else None,
            "actual_win_rate": float(p["actual_win_rate"]) if p.get("actual_win_rate") else None,
            "calibration_gap": cal_gap,
            "total_pnl":       float(p["total_pnl"]) if p.get("total_pnl") else None,
            "official_pnl":    float(p["official_pnl"]) if p.get("official_pnl") else None,
            "avg_edge":        float(p["avg_edge"]) if p.get("avg_edge") else None,
            "median_edge":     float(p["median_edge"]) if p.get("median_edge") else None,
            "unique_market_days": int(p.get("unique_market_days") or 0),
            "unique_tickers":  int(p.get("unique_tickers") or 0),
            "approx_opps_per_day": (
                round(int(p.get("unique_tickers") or 0) /
                      max(1, int(p.get("unique_market_days") or 1)), 1)
            ),
            # direction breakdown
            "direction_breakdown": dir_breakdown.get(city, []),
            # model versions
            "model_versions": model_versions.get(city, []),
            # forecast accuracy
            "fv_obs":         fv_obs,
            "mae":            mae,
            "median_ae":      float(f["median_ae"]) if f and f.get("median_ae") else None,
            "mean_bias":      float(f["mean_bias"])  if f and f.get("mean_bias") else None,
            "rmse":           float(f["rmse"])       if f and f.get("rmse") else None,
            "sigma":          float(f["sigma"])      if f and f.get("sigma") else None,
            "pct_within_1f":  float(f["pct_within_1f"]) if f and f.get("pct_within_1f") is not None else None,
            "pct_within_2f":  float(f["pct_within_2f"]) if f and f.get("pct_within_2f") is not None else None,
            "pct_within_3f":  float(f["pct_within_3f"]) if f and f.get("pct_within_3f") is not None else None,
            "mae_by_lead_time": {
                "<6h":   float(f["mae_lt6h"])    if f and f.get("mae_lt6h") else None,
                "6-12h": float(f["mae_6_12h"])   if f and f.get("mae_6_12h") else None,
                "12-24h": float(f["mae_12_24h"]) if f and f.get("mae_12_24h") else None,
                "24-48h": float(f["mae_24_48h"]) if f and f.get("mae_24_48h") else None,
                "48h+":  float(f["mae_48hplus"]) if f and f.get("mae_48hplus") else None,
            } if f else None,
            "forecast_sources": f["sources_seen"] if f else None,
            "fes_detail": fes.get(city, []),
            # market quality
            "market_scans":        int(m.get("market_scans") or 0),
            "scans_valid_ask":     int(m.get("scans_valid_ask") or 0),
            "scans_no_quote":      int(m.get("scans_no_quote") or 0),
            "pct_valid_ask":       pct_valid,
            "avg_volume":          float(m["avg_volume"]) if m.get("avg_volume") else None,
            "median_volume":       float(m["median_volume"]) if m.get("median_volume") else None,
            "distinct_market_days_kalshi": int(m.get("distinct_market_days") or 0),
            # quote freshness
            "total_evaluated":     int(q.get("total_evaluated") or 0),
            "pct_fresh_300s":      float(q["pct_fresh_300s"]) if q.get("pct_fresh_300s") else None,
            "pct_executable":      float(q["pct_executable"]) if q.get("pct_executable") else None,
            "avg_qty":             float(q["avg_qty"]) if q.get("avg_qty") else None,
            "rej_stale_quote":     int(q.get("rej_stale_quote") or 0),
            "rej_station":         int(q.get("rej_station") or 0),
            "rej_v2_excluded":     int(q.get("rej_v2_excluded") or 0),
            "rej_hourly":          int(q.get("rej_hourly") or 0),
            "volume_note":         "Volume and open interest are not stored in paper_trades.",
            # FTB data
            "ftb": {
                "total_v23":        int(fb.get("total_v23") or 0),
                "official_count":   int(fb.get("official_count") or 0),
                "research_count":   int(fb.get("research_count") or 0),
                "settled_v23":      int(fb.get("settled_v23") or 0),
                "wins_v23":         int(fb.get("wins_v23") or 0),
                "rej_stale":        int(fb.get("rej_stale") or 0),
                "rej_v2_excl":      int(fb.get("rej_v2_excl") or 0),
                "rej_hourly":       int(fb.get("rej_hourly") or 0),
                "rej_station":      int(fb.get("rej_station") or 0),
                "scan_days":        int(fb.get("scan_days") or 0),
                "unique_tickers_v23": int(fb.get("unique_tickers_v23") or 0),
            } if fb else None,
            # specialization score
            "score": {
                "total":       total_score,
                "forecast":    round(s_forecast, 1),
                "trading":     round(s_trading, 1),
                "liquidity":   round(s_liquidity, 1),
                "sample":      round(s_sample, 1),
                "station":     round(s_station, 1),
                "weights": {
                    "forecast":   W_FORECAST,
                    "trading":    W_TRADING,
                    "liquidity":  W_LIQUIDITY,
                    "sample":     W_SAMPLE,
                    "station":    W_STATION,
                },
            },
            # evidence quality
            "sample_size_grade":   _sample_grade(settled, fv_obs),
            "sample_warnings":     _sample_warnings(city, settled, fv_obs, wins, losses),
        })

    # Sort by score descending
    cities_out.sort(key=lambda c: c["score"]["total"], reverse=True)

    # ── 10. Best city picks ──────────────────────────────────────────────────
    # Only consider cities with nws_compatible=True for recommendations
    eligible = [c for c in cities_out if c["nws_compatible"]]
    best1 = eligible[0]["city"] if len(eligible) >= 1 else None
    best2 = eligible[1]["city"] if len(eligible) >= 2 else None
    best3 = eligible[2]["city"] if len(eligible) >= 3 else None

    # Build 3-city set balancing different criteria
    # - best trading: highest total score with good trading component
    # - best forecast: highest forecast score
    # - best liquidity: highest liquidity score
    three_city_set = _pick_three_city_set(eligible)

    # ── 11. FTB projection for #1 city ──────────────────────────────────────
    ftb_impact = _ftb_projection(best1, ftb.get(best1, {}), perf.get(best1, {})) if best1 else None

    # ── 12. Recommendation ──────────────────────────────────────────────────
    recommendation, rec_reasons = _build_recommendation(eligible)

    return {
        "generated_at":       generated_at,
        "trading_state_modified": False,
        "ftb_untouched":          True,
        "read_only":              True,
        "cities_analyzed":    [c["city"] for c in cities_out],
        "top_single_city":    best1,
        "top_two_city":       best2,
        "top_three_city_individual": best3,
        "best_3_city_set":    three_city_set,
        "recommendation":     recommendation,
        "recommendation_reasons": rec_reasons,
        "ftb_impact":         ftb_impact,
        "score_weights": {
            "forecast":   W_FORECAST,
            "trading":    W_TRADING,
            "liquidity":  W_LIQUIDITY,
            "sample":     W_SAMPLE,
            "station":    W_STATION,
        },
        "cities": cities_out,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pick_three_city_set(eligible: list[dict]) -> list[str]:
    """
    Pick a 3-city set that balances trading quality, forecast accuracy, and
    liquidity — not just the top-3 by total score.
    """
    if len(eligible) <= 3:
        return [c["city"] for c in eligible]

    # Start with the top city by total score
    chosen = [eligible[0]]

    # Add the city with the best forecast score not yet chosen
    remaining = [c for c in eligible if c not in chosen]
    remaining.sort(key=lambda c: c["score"]["forecast"], reverse=True)
    if remaining:
        chosen.append(remaining[0])

    # Add the city with the best liquidity score not yet chosen
    remaining2 = [c for c in eligible if c not in chosen]
    remaining2.sort(key=lambda c: c["score"]["liquidity"], reverse=True)
    if remaining2:
        chosen.append(remaining2[0])

    return [c["city"] for c in chosen]


def _ftb_projection(city: str, fb: dict, p: dict) -> dict:
    """Project FTB pace for the given city."""
    total_v23        = int(fb.get("total_v23") or 0)
    scan_days        = int(fb.get("scan_days") or 0)
    unique_tickers   = int(fb.get("unique_tickers_v23") or 0)
    rej_stale        = int(fb.get("rej_stale") or 0)

    # Historical opps per day from all strategies
    hist_tickers     = int(p.get("unique_tickers") or 0)
    hist_days        = int(p.get("unique_market_days") or 0)
    hist_opps_day    = round(hist_tickers / max(1, hist_days), 1)

    # Estimate executable per day = stale-rejected fraction could become OFFICIAL
    # Conservative: assume ~30-50% of stale-rejected become OFFICIAL with fresh quotes
    est_official_per_scan_lo = max(1, round(hist_opps_day * 0.15))
    est_official_per_scan_hi = max(2, round(hist_opps_day * 0.40))

    # FTB runs ~5 scans per week (once per day)
    scans_per_week = 5
    weekly_lo = est_official_per_scan_lo * scans_per_week
    weekly_hi = est_official_per_scan_hi * scans_per_week

    def weeks_to(target: int) -> str:
        lo = math.ceil(target / max(1, weekly_hi))
        hi = math.ceil(target / max(1, weekly_lo))
        if lo == hi:
            return f"~{lo} weeks"
        return f"{lo}–{hi} weeks"

    return {
        "city":                 city,
        "v23_total_evaluated":  total_v23,
        "v23_official":         int(fb.get("official_count") or 0),
        "v23_research_only":    int(fb.get("research_count") or 0),
        "v23_scan_days":        scan_days,
        "top_rejection_reasons": _top_ftb_rejections(fb),
        "hist_opps_per_day":    hist_opps_day,
        "est_official_per_week_lo": weekly_lo,
        "est_official_per_week_hi": weekly_hi,
        "est_weeks_to_10_settled":  weeks_to(10),
        "est_weeks_to_25_settled":  weeks_to(25),
        "est_weeks_to_50_settled":  weeks_to(50),
        "note": (
            "Projections assume current scan cadence (~1 per day) and that "
            "stale-quote rejections convert to OFFICIAL at 15–40% when fresh "
            "quotes are available. Use ranges, not point estimates."
        ),
    }


def _top_ftb_rejections(fb: dict) -> list[str]:
    reasons = [
        ("missing_or_stale_executable_quote", int(fb.get("rej_stale") or 0)),
        ("v2_excluded",                       int(fb.get("rej_v2_excl") or 0)),
        ("hourly_temperature_not_approved",   int(fb.get("rej_hourly") or 0)),
        ("settlement_station_unverified",     int(fb.get("rej_station") or 0)),
    ]
    reasons.sort(key=lambda x: x[1], reverse=True)
    return [f"{r} ({n})" for r, n in reasons if n > 0]


def _build_recommendation(eligible: list[dict]) -> tuple[str, list[str]]:
    """
    Returns one of:
      A. SPECIALIZE_ONE_CITY
      B. SPECIALIZE_THREE_CITIES
      C. KEEP_MULTI_CITY
      D. NOT_ENOUGH_DATA
    """
    if not eligible:
        return "D. NOT_ENOUGH_DATA", ["No city has sufficient data to recommend."]

    top = eligible[0]
    top_settled = top["settled_total"]
    top_score   = top["score"]["total"]

    reasons: list[str] = []

    # Not enough data overall?
    if top_settled < 50:
        reasons.append(
            f"{top['city']} has only {top_settled} settled trades — not enough to "
            "confirm it is genuinely better than other cities."
        )
        reasons.append(
            "Specialising on one city now would dramatically reduce trade volume, "
            "making validation even slower."
        )
        return "D. NOT_ENOUGH_DATA", reasons

    # Strong single-city case?
    if (top_score >= 68 and top_settled >= 150 and
            top.get("win_rate_pct") and top["win_rate_pct"] >= 45):
        reasons.append(
            f"{top['city']} leads on total score ({top_score}/100), has {top_settled} "
            f"settled trades, and a {top['win_rate_pct']}% win rate."
        )
        reasons.append(
            "However, single-city specialisation cuts volume by ~80-90%, extending "
            "FTB validation timelines substantially. Review carefully."
        )
        return "B. SPECIALIZE_THREE_CITIES", reasons

    # Default: multi-city
    reasons.append(
        f"Top city ({top['city']}) scores {top_score}/100 with {top_settled} settled trades — "
        "evidence is suggestive but not conclusive for full specialisation."
    )
    reasons.append(
        "A 3-city focus preserves enough volume for timely FTB validation while "
        "concentrating on the highest-quality markets."
    )
    return "C. KEEP_MULTI_CITY", reasons
