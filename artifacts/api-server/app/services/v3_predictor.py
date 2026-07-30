"""
V3 Predictor — Phase 3
======================
Runs the V3 probability engine for every active market that has a recent
V2.1 PredictionSnapshot, creating a V3PredictionSnapshot row each collection cycle.

Called from the collection job (Step 5f), after V2.1 paper trading.
Gated by the ``v3.predictions_enabled`` feature flag.

Design
------
* Reuses forecast_value and contract spec from the V2.1 snapshot — no second
  weather-API call.  V3's value is its calibrated sigma/bias, not a different
  forecast.
* Runs run_v3_prediction() with model="GFS", lead_bucket="1d" — these are the
  only values present in the Phase 3 preload.
* Creates one V3PredictionSnapshot per active market per collection run
  (idempotent for paper-trading purposes via the V3PaperTrade unique constraint).
* Writes comparison_group_id to the V2.1 PredictionSnapshot row so analytics
  can JOIN the two tables for A/B comparison.

Isolation
---------
* Never reads/writes paper_trades (V1/V2/V2.1).
* Never modifies PredictionSnapshot rows except to SET comparison_group_id
  (additive-only, NULL → UUID).
* All V3-specific data goes to v3_prediction_snapshots.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KalshiMarket, PredictionSnapshot
from app.models_v3 import CURRENT_PRELOAD_VERSION, V3PredictionSnapshot
from app.services.settlement_stations import get_station
from app.services.v3_flags import get_v3_flag
from app.services.v3_probability_engine import V3PredictionInput, run_v3_prediction

logger = logging.getLogger(__name__)

# V3 preload was trained exclusively on GFS (via Open-Meteo) 1-day-ahead
# forecasts.  These constants flow into every V3PredictionInput created here.
V3_MODEL       = "GFS"
V3_LEAD_BUCKET = "1d"   # Only bucket populated in the Phase 3 preload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _season_from_date(date_str: str) -> str:
    """Return meteorological season from a 'YYYY-MM-DD' or similar date string."""
    try:
        month = int(str(date_str)[5:7])
    except (ValueError, IndexError):
        return "fall"
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "fall"


def _lead_days(forecast_date_str: str) -> int:
    """Days from today to the forecast date (clamped to >= 0)."""
    today = datetime.now(timezone.utc).date()
    try:
        target = datetime.strptime(str(forecast_date_str)[:10], "%Y-%m-%d").date()
        return max(0, (target - today).days)
    except ValueError:
        return 1


def _v3_confidence_label(ec_prob: float) -> str:
    """Confidence label from how far ec_prob deviates from the 50% coin-flip."""
    edge = abs(ec_prob - 0.5)
    if edge >= 0.30:
        return "Very High"
    if edge >= 0.20:
        return "High"
    if edge >= 0.12:
        return "Medium"
    if edge >= 0.06:
        return "Low"
    return "Very Low"


def _is_city_ok(city: str | None) -> bool:
    """
    Return True if the city has a registered, verified, NWS-settlement station.
    Same guard used by V2.1 — prevents V3 from trading markets where the
    settlement source doesn't match the GFS/NWS calibration data.
    """
    if not city:
        return False
    try:
        station = get_station(city)
        if station is None:
            return False
        if not station.verified:
            return False
        if not getattr(station, "nws_settlement", True):
            return False
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

async def run_v3_predictions(session: AsyncSession) -> dict:
    """
    Create V3PredictionSnapshot rows for all active markets.

    Returns:
        {
          "status":               "ok" | "disabled",
          "processed":            int,   # markets considered
          "created":              int,   # V3PredictionSnapshot rows inserted
          "skipped_no_market":    int,   # no active KalshiMarket found
          "skipped_unverified":   int,   # city/station not verified
          "skipped_unsupported":  int,   # analysis_status != "supported" or hourly
          "errors":               int,   # exceptions caught
        }
    """
    stats: dict = {
        "processed": 0, "created": 0,
        "skipped_no_market": 0, "skipped_unverified": 0,
        "skipped_unsupported": 0, "errors": 0,
    }

    if not await get_v3_flag(session, "v3.predictions_enabled"):
        logger.info("V3 predictions disabled (flag v3.predictions_enabled=false) — skipping.")
        return {"status": "disabled", **stats}

    # ── Latest V2.1 PredictionSnapshot per ticker ────────────────────────────
    all_snaps_q = await session.execute(
        select(PredictionSnapshot).order_by(
            PredictionSnapshot.market_ticker,
            PredictionSnapshot.id.desc(),
        )
    )
    all_snaps = all_snaps_q.scalars().all()

    seen_tickers: set[str] = set()
    latest_snaps: list[PredictionSnapshot] = []
    for s in all_snaps:
        if s.market_ticker not in seen_tickers:
            seen_tickers.add(s.market_ticker)
            latest_snaps.append(s)

    # ── Active markets ────────────────────────────────────────────────────────
    markets_q = await session.execute(
        select(KalshiMarket).where(KalshiMarket.status == "active")
    )
    market_map: dict[str, KalshiMarket] = {
        m.ticker: m for m in markets_q.scalars().all()
    }

    # ── Process each market ───────────────────────────────────────────────────
    for snap in latest_snaps:
        market = market_map.get(snap.market_ticker)
        if market is None:
            stats["skipped_no_market"] += 1
            continue

        stats["processed"] += 1

        # Station verification guard (identical to V2.1)
        if not _is_city_ok(market.city):
            stats["skipped_unverified"] += 1
            continue

        # Skip unsupported / missing-forecast / hourly contracts
        # V3 is calibrated on daily TMAX; hourly contracts use a different variable
        if (snap.analysis_status != "supported"
                or snap.forecast_value is None
                or snap.contract_type == "hourly_threshold"):
            stats["skipped_unsupported"] += 1
            continue

        try:
            forecast_date = str(snap.forecast_date or "")[:10]
            season        = _season_from_date(forecast_date)

            inputs = V3PredictionInput(
                city=market.city or "",
                model=V3_MODEL,
                lead_bucket=V3_LEAD_BUCKET,
                season=season,
                forecast_value=float(snap.forecast_value),
                contract_type=snap.contract_type or "threshold",
                operator=snap.settlement_operator,
                threshold=snap.settlement_threshold,
                lower_bound=getattr(snap, "lower_bound", None),
                upper_bound=getattr(snap, "upper_bound", None),
            )

            output = await run_v3_prediction(inputs, session)

            # claimed_edge: best of (YES edge, NO edge) vs market mid-price
            ec_yes = output.ec_probability
            claimed_edge_val: float | None = None
            if ec_yes is not None and snap.market_probability is not None:
                mkt_yes = float(snap.market_probability)
                yes_edge = ec_yes - mkt_yes
                no_edge  = (1.0 - ec_yes) - (1.0 - mkt_yes)
                claimed_edge_val = round(max(yes_edge, no_edge), 4)

            confidence = _v3_confidence_label(ec_yes) if ec_yes is not None else None

            # Comparison group UUID — set on both V3 and V2.1 rows for JOIN support
            comp_id = str(uuid.uuid4())

            v3_snap = V3PredictionSnapshot(
                market_ticker=snap.market_ticker,
                comparison_group_id=comp_id,
                forecast_date=forecast_date,
                forecast_value=float(snap.forecast_value),
                forecast_retrieved_at=snap.forecast_retrieved_at,
                lead_time_days=_lead_days(forecast_date),
                forecast_model=V3_MODEL,
                forecast_source="open_meteo",
                settlement_variable=snap.settlement_variable,
                settlement_operator=snap.settlement_operator,
                settlement_threshold=snap.settlement_threshold,
                contract_type=snap.contract_type,
                historical_bias_adj=output.historical_bias,
                historical_sigma=output.historical_sigma,
                forward_learning_adj=output.forward_bias_adj,
                final_bias=output.final_bias,
                final_sigma=output.final_sigma,
                hist_sample_count=output.hist_raw_n,
                effective_hist_n=output.hist_effective_n,
                v3_forward_count=output.forward_n,
                fallback_level_used=output.fallback_level_used,
                config_version=CURRENT_PRELOAD_VERSION,
                ec_probability=ec_yes,
                market_probability=snap.market_probability,
                confidence=confidence,
                claimed_edge=claimed_edge_val,
                trade_decision="PENDING",
                decision_reason=None,
                analysis_status=output.status,
                bias_applied=output.bias_applied,
                bias_suppressed_reason=output.bias_suppressed_reason or None,
            )
            session.add(v3_snap)

            # Write comparison_group_id to the V2.1 snapshot if still unset
            if snap.comparison_group_id is None:
                snap.comparison_group_id = comp_id

            stats["created"] += 1

        except Exception as exc:
            stats["errors"] += 1
            logger.warning(
                "V3 prediction error for %s: %s",
                snap.market_ticker, exc,
                exc_info=True,
            )

    await session.commit()
    logger.info(
        "V3 predictions: %d processed, %d created, %d no-market, "
        "%d unverified, %d unsupported, %d errors",
        stats["processed"], stats["created"],
        stats["skipped_no_market"], stats["skipped_unverified"],
        stats["skipped_unsupported"], stats["errors"],
    )
    return {"status": "ok", **stats}
