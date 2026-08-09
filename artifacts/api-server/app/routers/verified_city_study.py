"""
GET /api/verified-city-study

Read-only verified-city specialization study.
Filters to cities with verified=True AND nws_settlement=True only.
Unverified or non-NWS cities cannot appear in the shortlist.

No writes. No model changes. No FTB changes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.routers.city_study import (
    W_FORECAST, W_TRADING, W_LIQUIDITY, W_SAMPLE, W_STATION,
    _score_mae, _score_win_rate, _score_liquidity, _score_sample,
    _score_station, _sample_grade, _sample_warnings,
    _ftb_projection, _top_ftb_rejections,
)
from app.services.settlement_stations import SETTLEMENT_STATIONS

router = APIRouter(tags=["verified-city-study"])

# ---------------------------------------------------------------------------
# Verification evidence — directly sourced from SETTLEMENT_STATIONS
# All five target cities were verified from authoritative Kalshi API data
# on 2026-07-30.  No station flags changed in this task; they were already set.
# ---------------------------------------------------------------------------

VERIFICATION_EVIDENCE = {
    "Houston": {
        "verdict":           "VERIFIED",
        "api_field":         "rules_secondary",
        "kalshi_series":     "KXLOWTHOU / KXHIGHTHOU",
        "settlement_text":   (
            "choosing the location \"Houston-Hobby, TX\" "
            "with Daily Climate Report"
        ),
        "nws_station":       "Houston-Hobby, TX (NWS CLIHOU)",
        "ghcnd_station":     "USW00012918",
        "station_name":      "Houston William P. Hobby Airport (KHOU)",
        "query_date":        "2026-07-30",
        "source_log":        (
            "Kalshi KXLOWTHOU rules_secondary (2026-07-30 API query); "
            "previously ambiguous (Hobby vs IAH) — now resolved."
        ),
        "was_previously_unverified": False,
        "flag_changed":      False,
        "notes":             "Kalshi uses Hobby Airport (KHOU), NOT Bush Intercontinental (IAH).",
    },
    "Oklahoma City": {
        "verdict":           "VERIFIED",
        "api_field":         "settlement_audit",
        "kalshi_series":     "KXLOWTOKC / KXHIGHTOKC",
        "settlement_text":   (
            "KXLOWTOKC-26JUL28-B71.5 settlement confirmed against "
            "KOKC station recording"
        ),
        "nws_station":       "Oklahoma City (Will Rogers World Airport, KOKC)",
        "ghcnd_station":     "USW00013967",
        "station_name":      "Will Rogers World Airport (KOKC)",
        "query_date":        "2026-07-30",
        "source_log":        (
            "Settlement audit 2026-07-30: KXLOWTOKC-26JUL28-B71.5 "
            "matched KOKC station recording.  Standard NWS Daily Climate "
            "Station for OKC confirmed."
        ),
        "was_previously_unverified": False,
        "flag_changed":      False,
        "notes":             "Station coordinate offset ~7 mi contributes <2°F; dominant error is model forecast quality.",
    },
    "Dallas": {
        "verdict":           "VERIFIED",
        "api_field":         "rules_secondary",
        "kalshi_series":     "KXHIGHTDAL / KXLOWTDAL",
        "settlement_text":   (
            "choosing the location \"Dallas/Fort Worth, TX\" "
            "with Daily Climate Report"
        ),
        "nws_station":       "Dallas/Fort Worth, TX (NWS CLIDFW)",
        "ghcnd_station":     "USW00003927",
        "station_name":      "Dallas/Fort Worth International Airport (KDFW)",
        "query_date":        "2026-07-30",
        "source_log":        (
            "Kalshi KXHIGHTDAL/KXLOWTDAL rules_secondary (2026-07-30 API query). "
            "A separate DAL Love Field series was not observed — KDFW confirmed primary."
        ),
        "was_previously_unverified": False,
        "flag_changed":      False,
        "notes":             "DFW airport station confirmed.  Love Field (KDAL) series not observed live.",
    },
    "Minneapolis": {
        "verdict":           "VERIFIED",
        "api_field":         "rules_secondary",
        "kalshi_series":     "KXHIGHTMIN / KXLOWTMIN",
        "settlement_text":   (
            "choosing the location \"Minneapolis/St Paul, MN\" "
            "with Daily Climate Report"
        ),
        "nws_station":       "Minneapolis/St Paul, MN (NWS CLIMSP)",
        "ghcnd_station":     "USW00014922",
        "station_name":      "Minneapolis–Saint Paul International Airport (KMSP)",
        "query_date":        "2026-07-30",
        "source_log":        "Kalshi KXHIGHTMIN rules_secondary (2026-07-30 API query).",
        "was_previously_unverified": False,
        "flag_changed":      False,
        "notes":             "WARNING: –5.59°F systematic cold bias in era5_reanalysis forecasts. Station itself is correct.",
    },
    "Miami": {
        "verdict":           "VERIFIED",
        "api_field":         "rules_primary",
        "kalshi_series":     "KXHIGHMIA / KXLOWTMIA",
        "settlement_text":   (
            "the highest temperature recorded at Miami International Airport"
        ),
        "nws_station":       "Miami International Airport (KMIA) — NWS Climatological Report",
        "ghcnd_station":     "USW00012839",
        "station_name":      "Miami International Airport (KMIA)",
        "query_date":        "2026-07-30",
        "source_log":        "Kalshi KXHIGHMIA rules_primary (2026-07-30 API query).",
        "was_previously_unverified": False,
        "flag_changed":      False,
        "notes":             "rules_primary is unambiguous for Miami — station explicit in market definition.",
    },
}

TARGET_CITIES = list(VERIFICATION_EVIDENCE.keys())


# ---------------------------------------------------------------------------
# Shortlist logic — enforces verified+NWS constraint
# ---------------------------------------------------------------------------

def _build_verified_shortlist(
    cities: list[dict],
    max_size: int = 3,
) -> tuple[list[str], str, list[str]]:
    """
    Select up to max_size cities from the verified+NWS set.

    Priority: 1. forecast accuracy  2. trading quality  3. sample size
              4. quote availability  5. independent opportunity count

    Cities that are unverified or non-NWS are rejected before this is called.
    Returns (shortlist_cities, verdict_label, reasons).
    """
    if not cities:
        return [], "NO_ELIGIBLE_CITIES", ["No verified NWS cities have sufficient data."]

    # Sort by composite score (already computed with weighted criteria)
    ranked = sorted(cities, key=lambda c: c["score"]["total"], reverse=True)

    # Require minimum evidence bar: ≥20 settled OR ≥10 forecast verifications
    qualified = [
        c for c in ranked
        if c["settled_total"] >= 20 or c["fv_obs"] >= 10
    ]

    if not qualified:
        return [], "NOT_ENOUGH_DATA", ["No city meets minimum evidence threshold (20 settled or 10 forecast obs)."]

    # Greedy pick: #1 by composite score
    chosen = [qualified[0]]
    remaining = qualified[1:]

    if max_size >= 2 and remaining:
        # #2: best forecast accuracy not yet chosen
        by_forecast = sorted(remaining, key=lambda c: c["score"]["forecast"], reverse=True)
        chosen.append(by_forecast[0])
        remaining = [c for c in remaining if c not in chosen]

    if max_size >= 3 and remaining:
        # #3: best trading quality not yet chosen
        by_trading = sorted(remaining, key=lambda c: c["score"]["trading"], reverse=True)
        chosen.append(by_trading[0])
        remaining = [c for c in remaining if c not in chosen]

    # Trim if any chosen city is clearly below the quality bar
    chosen = [
        c for c in chosen
        if c["score"]["total"] >= 30 or c["settled_total"] >= 50
    ]

    n = len(chosen)
    if n == 0:
        return [], "NOT_ENOUGH_DATA", ["No city met the quality bar for the shortlist."]

    city_names = [c["city"] for c in chosen]

    if n < max_size:
        verdict = f"USE_{n}_VERIFIED_CITIES"
        reasons = [
            f"Only {n} verified city/cities meet the quality bar. "
            "Adding a city purely to fill the {max_size}-city slot would reduce overall reliability."
        ]
    else:
        verdict = "SPECIALIZE_THREE_VERIFIED_CITIES"
        reasons = [
            f"Top three verified cities by composite score, forecast accuracy, and trading quality.",
        ]

    # Attach detail reasons
    for c in chosen:
        reasons.append(
            f"{c['city']}: score {c['score']['total']:.1f}/100, "
            f"win rate {c['win_rate_pct']:.1f}% ({c['settled_total']} settled), "
            f"MAE {c['mae']}°F"
            if c["win_rate_pct"] is not None and c["mae"] is not None
            else f"{c['city']}: score {c['score']['total']:.1f}/100"
        )

    return city_names, verdict, reasons


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/verified-city-study")
async def get_verified_city_study(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Verified-city specialization study.
    Only cities with verified=True AND nws_settlement=True appear in rankings.
    Read-only; no model, trading, or FTB state changes.
    """
    generated_at = datetime.now(timezone.utc).isoformat()

    # Identify verified+NWS city names from station registry
    verified_nws = {
        name for name, sta in SETTLEMENT_STATIONS.items()
        if sta.verified and sta.nws_settlement
    }

    # ── 1. Trading performance (verified cities only) ─────────────────────────
    perf_rows = (await db.execute(sql_text("""
        SELECT city,
               COUNT(*) FILTER (WHERE outcome IN ('WIN','LOSS'))             AS settled_total,
               COUNT(*) FILTER (WHERE eligibility_status='OFFICIAL'
                                 AND outcome IN ('WIN','LOSS'))               AS official_settled,
               COUNT(*) FILTER (WHERE outcome='WIN')                         AS wins,
               COUNT(*) FILTER (WHERE outcome='LOSS')                        AS losses,
               ROUND(100.0*COUNT(*) FILTER (WHERE outcome='WIN')
                     /NULLIF(COUNT(*) FILTER (WHERE outcome IN ('WIN','LOSS')),0),1) AS win_rate_pct,
               ROUND(AVG(CASE WHEN outcome='WIN' THEN 1.0 WHEN outcome='LOSS' THEN 0.0 END)
                     ::numeric,3)                                            AS actual_win_rate,
               ROUND(AVG(ec_side_probability)
                     FILTER (WHERE outcome IN ('WIN','LOSS'))::numeric,3)    AS avg_predicted_prob,
               ROUND(SUM(profit_loss)
                     FILTER (WHERE outcome IN ('WIN','LOSS'))::numeric,2)    AS total_pnl,
               ROUND(AVG(edge_pct_points)
                     FILTER (WHERE outcome IN ('WIN','LOSS'))::numeric,1)    AS avg_edge,
               COUNT(DISTINCT target_settlement_date)                        AS unique_market_days,
               COUNT(DISTINCT market_ticker)                                 AS unique_tickers
        FROM paper_trades
        WHERE city IS NOT NULL AND city != ''
        GROUP BY city
        ORDER BY settled_total DESC
    """))).fetchall()

    perf: dict[str, dict] = {}
    for r in perf_rows:
        d = dict(r._mapping)
        city = d["city"]
        if city in verified_nws:
            perf[city] = d

    # ── 2. Forecast verifications ─────────────────────────────────────────────
    fv_rows = (await db.execute(sql_text("""
        SELECT city,
               COUNT(*)                                          AS fv_obs,
               ROUND(AVG(ABS(forecast_error))::numeric,2)        AS mae,
               ROUND(AVG(forecast_error)::numeric,2)             AS mean_bias,
               ROUND(100.0*COUNT(*) FILTER
                     (WHERE ABS(forecast_error)<=2.0)/NULLIF(COUNT(*),0),1) AS pct_within_2f,
               STRING_AGG(DISTINCT source_label,', ')            AS sources_seen
        FROM forecast_verifications
        WHERE city IS NOT NULL
        GROUP BY city
    """))).fetchall()

    fv: dict[str, dict] = {}
    for r in fv_rows:
        d = dict(r._mapping)
        city = d["city"]
        if city in verified_nws and d["mae"] is not None:
            fv[city] = d

    # ── 3. Market quality ─────────────────────────────────────────────────────
    mq_rows = (await db.execute(sql_text("""
        SELECT city,
               COUNT(*)                                                              AS market_scans,
               COUNT(*) FILTER (WHERE yes_ask IS NOT NULL AND yes_ask > 0.01)       AS scans_valid_ask,
               COUNT(DISTINCT DATE(collection_timestamp))                            AS distinct_days
        FROM kalshi_markets
        WHERE city IS NOT NULL AND weather_matched = true
        GROUP BY city
    """))).fetchall()

    mq: dict[str, dict] = {}
    for r in mq_rows:
        d = dict(r._mapping)
        city = d["city"]
        if city in verified_nws:
            mq[city] = d

    # ── 4. FTB data ───────────────────────────────────────────────────────────
    ftb_rows = (await db.execute(sql_text("""
        SELECT city,
               COUNT(*) AS total_v23,
               COUNT(*) FILTER (WHERE eligibility_status='OFFICIAL')  AS official_count,
               COUNT(*) FILTER (WHERE eligibility_status='RESEARCH_ONLY') AS research_count,
               COUNT(*) FILTER (WHERE outcome IN ('WIN','LOSS'))       AS settled_v23,
               COUNT(*) FILTER (WHERE outcome='WIN')                   AS wins_v23,
               COUNT(*) FILTER (WHERE eligibility_reason LIKE '%missing_or_stale%') AS rej_stale,
               COUNT(*) FILTER (WHERE eligibility_reason LIKE '%v2_excluded%') AS rej_v2_excl,
               COUNT(*) FILTER (WHERE eligibility_reason LIKE '%hourly_temperature%') AS rej_hourly,
               COUNT(*) FILTER (WHERE eligibility_reason LIKE '%settlement_station_unverified%') AS rej_station,
               COUNT(DISTINCT DATE(created_at)) AS scan_days,
               COUNT(DISTINCT market_ticker)    AS unique_tickers_v23
        FROM paper_trades
        WHERE strategy_version='v2.3' AND city IS NOT NULL
        GROUP BY city
    """))).fetchall()

    ftb: dict[str, dict] = {}
    for r in ftb_rows:
        d = dict(r._mapping)
        city = d["city"]
        if city in verified_nws:
            ftb[city] = d

    # ── 5. Build per-city results ─────────────────────────────────────────────
    all_cities = sorted(
        verified_nws,
        key=lambda c: perf.get(c, {}).get("settled_total") or 0,
        reverse=True,
    )

    cities_out: list[dict] = []
    for city in all_cities:
        p   = perf.get(city, {})
        f   = fv.get(city)
        m   = mq.get(city, {})
        fb  = ftb.get(city, {})
        sta = SETTLEMENT_STATIONS[city]

        settled    = int(p.get("settled_total") or 0)
        wins       = int(p.get("wins") or 0)
        losses     = int(p.get("losses") or 0)
        win_rate   = float(p.get("win_rate_pct") or 0) if settled > 0 else None
        fv_obs     = int(f["fv_obs"]) if f else 0
        mae        = float(f["mae"]) if f else None

        scans      = int(m.get("market_scans") or 0)
        valid_ask  = int(m.get("scans_valid_ask") or 0)
        pct_valid  = round(100.0 * valid_ask / scans, 1) if scans > 0 else None

        s_f  = _score_mae(mae)
        s_t  = _score_win_rate(win_rate, settled)
        s_l  = _score_liquidity(pct_valid)
        s_s  = _score_sample(settled, fv_obs)
        s_st = _score_station(verified=True, nws=True)   # all cities here are verified+NWS

        total = round(W_FORECAST*s_f + W_TRADING*s_t + W_LIQUIDITY*s_l + W_SAMPLE*s_s + W_STATION*s_st, 1)

        cal_gap = None
        avg_pred = p.get("avg_predicted_prob")
        actual_wr = p.get("actual_win_rate")
        if avg_pred is not None and actual_wr is not None:
            cal_gap = round(float(avg_pred) - float(actual_wr), 3)

        # FTB opportunity estimate
        hist_tickers  = int(p.get("unique_tickers") or 0)
        hist_days     = int(p.get("unique_market_days") or 0)
        hist_opps_day = round(hist_tickers / max(1, hist_days), 1)
        est_opps_wk_lo = max(1, round(hist_opps_day * 0.15 * 5))
        est_opps_wk_hi = max(2, round(hist_opps_day * 0.40 * 5))

        cities_out.append({
            "city":               city,
            "station_name":       sta.station_name,
            "station_id":         sta.ghcnd_station_id,
            "station_verified":   True,
            "nws_compatible":     True,
            "settled_total":      settled,
            "official_settled":   int(p.get("official_settled") or 0),
            "wins":               wins,
            "losses":             losses,
            "win_rate_pct":       win_rate,
            "avg_predicted_prob": float(avg_pred) if avg_pred else None,
            "calibration_gap":    cal_gap,
            "total_pnl":          float(p["total_pnl"]) if p.get("total_pnl") else None,
            "avg_edge":           float(p["avg_edge"]) if p.get("avg_edge") else None,
            "hist_opps_per_day":  hist_opps_day,
            "est_official_per_week_lo": est_opps_wk_lo,
            "est_official_per_week_hi": est_opps_wk_hi,
            "fv_obs":             fv_obs,
            "mae":                mae,
            "mean_bias":          float(f["mean_bias"]) if f and f.get("mean_bias") else None,
            "pct_within_2f":      float(f["pct_within_2f"]) if f and f.get("pct_within_2f") is not None else None,
            "forecast_sources":   f["sources_seen"] if f else None,
            "market_scans":       scans,
            "pct_valid_ask":      pct_valid,
            "ftb": {
                "total_v23":         int(fb.get("total_v23") or 0),
                "official_count":    int(fb.get("official_count") or 0),
                "research_count":    int(fb.get("research_count") or 0),
                "settled_v23":       int(fb.get("settled_v23") or 0),
                "wins_v23":          int(fb.get("wins_v23") or 0),
                "scan_days":         int(fb.get("scan_days") or 0),
                "unique_tickers_v23": int(fb.get("unique_tickers_v23") or 0),
                "top_rejections":    _top_ftb_rejections(fb),
            } if fb else None,
            "score": {
                "total":     total,
                "forecast":  round(s_f, 1),
                "trading":   round(s_t, 1),
                "liquidity": round(s_l, 1),
                "sample":    round(s_s, 1),
                "station":   100.0,
            },
            "sample_size_grade":  _sample_grade(settled, fv_obs),
            "sample_warnings":    _sample_warnings(city, settled, fv_obs, wins, losses),
        })

    # Sort by composite score
    cities_out.sort(key=lambda c: c["score"]["total"], reverse=True)

    # ── 6. Shortlist ──────────────────────────────────────────────────────────
    shortlist, verdict, reasons = _build_verified_shortlist(cities_out, max_size=3)

    # ── 7. Bet Watch guidance (read-only, no implementation) ──────────────────
    bet_watch_guidance = {
        "primary_cities":       shortlist,
        "informational_cities": [
            c["city"] for c in cities_out
            if c["city"] not in shortlist and c["settled_total"] > 0
        ],
        "best_bet_restriction":
            "Only verified specialization cities should be eligible for the primary "
            "'Best Bet Right Now' recommendation. Non-specialized cities should "
            "appear as WATCHING only.",
        "implementation_status": "NOT IMPLEMENTED — guidance only, per task spec.",
    }

    return {
        "generated_at":             generated_at,
        "trading_state_modified":   False,
        "ftb_untouched":            True,
        "station_flags_changed":    False,
        "read_only":                True,
        "verified_nws_city_count":  len(verified_nws),
        # Verification results
        "verification_results": [
            {
                "city":              city,
                "verdict":           ev["verdict"],
                "api_field":         ev["api_field"],
                "kalshi_series":     ev["kalshi_series"],
                "settlement_text":   ev["settlement_text"],
                "nws_station":       ev["nws_station"],
                "ghcnd_station":     ev["ghcnd_station"],
                "station_name":      ev["station_name"],
                "query_date":        ev["query_date"],
                "source_log":        ev["source_log"],
                "flag_changed":      ev["flag_changed"],
                "notes":             ev["notes"],
            }
            for city, ev in VERIFICATION_EVIDENCE.items()
        ],
        "newly_verified":       [],   # all were already verified
        "still_unverified":     [],   # no target city remains unverified
        "conflicts":            [],   # no conflicts found
        # Rankings
        "cities":               cities_out,
        "cities_in_ranking":    [c["city"] for c in cities_out],
        # Final shortlist
        "shortlist":            shortlist,
        "shortlist_verdict":    verdict,
        "shortlist_reasons":    reasons,
        "bet_watch_guidance":   bet_watch_guidance,
        "score_weights": {
            "forecast":   W_FORECAST,
            "trading":    W_TRADING,
            "liquidity":  W_LIQUIDITY,
            "sample":     W_SAMPLE,
            "station":    W_STATION,
        },
    }
