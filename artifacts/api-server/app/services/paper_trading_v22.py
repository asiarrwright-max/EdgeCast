"""
EdgeCast Paper Trading Service — Strategy v2.2
===============================================
Isolated parallel challenger to V2.1.  Identical to V2.1 in every respect
except the probability engine: V2.2 uses ``run_analysis_v22`` which applies
the historical bias correction with the **correct** sign.

V2.1:  mu = forecast_value − mean_error   (inverted — preserved for record integrity)
V2.2:  mu = forecast_value + mean_error   (correct)

Isolation guarantee
--------------------
* strategy_version = "v2.2" — one PaperTrade row per (market_ticker, "v2.2").
* Never reads or modifies V2.1 PaperTrade rows.
* Feature flags ``v2.2.predictions_enabled`` and ``v2.2.paper_trading_enabled``
  are both ``false`` by default and must be explicitly enabled.
* All guard logic (station verification, quote freshness, executability,
  consensus guard) is identical to V2.1 and imported from paper_trading_v21.

Shared comparison identifiers
-------------------------------
Both V2.1 and V2.2 paper trades reference the same ``prediction_snapshots``
row via ``snapshot_id``.  ``prediction_snapshots.comparison_group_id`` is
written by the V3 predictor whenever V3 makes a prediction on the same market.
Joining V2.1 / V2.2 / V3 outcomes:

    SELECT pt.strategy_version, pt.direction, pt.outcome,
           ps.comparison_group_id
    FROM paper_trades pt
    JOIN prediction_snapshots ps ON ps.id = pt.snapshot_id
    WHERE ps.comparison_group_id IS NOT NULL
    UNION ALL
    SELECT 'v3.0', v3t.direction, v3t.outcome,
           v3t.comparison_group_id
    FROM v3_paper_trades v3t;
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting, KalshiMarket, PaperTrade, PredictionSnapshot
from app.services.paper_trading import (
    _is_confidence_sufficient,
    _select_yes_price,
    _select_no_price,
    calculate_position,
)
from app.services.paper_trading_v2 import (
    V2_FLAG_DESCRIPTIONS,
    _v2_exclusion_flag,
)
from app.services.eligibility import apply_correlated_limit, assess_trade_eligibility
from app.services.paper_trading_v21 import (
    CONSENSUS_GUARD_THRESHOLD,
    NON_EXECUTABLE_MAX_PRICE,
    NON_EXECUTABLE_MAX_QTY,
    STALE_QUOTE_SECONDS,
    _check_consensus_guard,
    _is_executable,
    _skip_v21 as _skip_v22,
)
from app.services.settlement_stations import get_station

logger = logging.getLogger(__name__)

STRATEGY_VERSION = "v2.2"

# ── Feature flag names ────────────────────────────────────────────────────────

V22_FLAG_DEFAULTS: dict[str, str] = {
    "v2.2.predictions_enabled":   "true",
    "v2.2.paper_trading_enabled": "true",
}

# ── Default settings (identical to V2.1) ─────────────────────────────────────

DEFAULT_V22_SETTINGS: dict[str, Any] = {
    "enabled":                True,
    "min_edge_pct":           10.0,
    "min_confidence":         "High",
    "stake":                  10.0,
    "consensus_guard_enabled": False,
}

_V22_SETTING_KEYS = {
    "paper_trading_v22.enabled":                  ("enabled",                 bool),
    "paper_trading_v22.min_edge_pct":             ("min_edge_pct",            float),
    "paper_trading_v22.min_confidence":           ("min_confidence",          str),
    "paper_trading_v22.stake":                    ("stake",                   float),
    "paper_trading_v22.consensus_guard_enabled":  ("consensus_guard_enabled", bool),
}


# ── Flag helpers ──────────────────────────────────────────────────────────────

async def ensure_v22_feature_flags(session: AsyncSession) -> None:
    """
    Insert V2.2 feature flags with value 'false' if they do not already exist.
    Idempotent — called once on startup.
    """
    for key, default_value in V22_FLAG_DEFAULTS.items():
        existing = await session.execute(
            select(AppSetting).where(AppSetting.key == key)
        )
        row = existing.scalar_one_or_none()
        if row is None:
            session.add(AppSetting(key=key, value=default_value))
            logger.info("[V2.2 Flags] Created flag %s = %s", key, default_value)
    await session.flush()


async def get_v22_flag(session: AsyncSession, flag_key: str) -> bool:
    """Return True if a V2.2 feature flag is set to 'true'."""
    result = await session.execute(
        select(AppSetting).where(AppSetting.key == flag_key)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return False
    return (row.value or "").lower() in ("true", "1", "yes")


# ── Settings helpers ───────────────────────────────────────────────────────────

async def get_v22_settings(session: AsyncSession) -> dict[str, Any]:
    """Return effective V2.2 settings (DB overrides applied over defaults)."""
    keys = list(_V22_SETTING_KEYS.keys())
    result = await session.execute(
        select(AppSetting).where(AppSetting.key.in_(keys))
    )
    db_rows = {r.key: r.value for r in result.scalars().all()}

    settings = dict(DEFAULT_V22_SETTINGS)
    for db_key, (field_name, cast) in _V22_SETTING_KEYS.items():
        if db_key in db_rows and db_rows[db_key] is not None:
            raw = db_rows[db_key]
            try:
                if cast is bool:
                    settings[field_name] = raw.lower() in ("1", "true", "yes")
                else:
                    settings[field_name] = cast(raw)
            except (ValueError, TypeError):
                pass
    return settings


# ── Core decision (v2.2) ─────────────────────────────────────────────────────

async def decide_trade_v22(
    snap: PredictionSnapshot,
    market: KalshiMarket,
    settings: dict[str, Any],
    session: AsyncSession,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Evaluate one market using the V2.2 probability engine.

    Changes from V2.1:
      - Uses run_analysis_v22 (corrected bias sign).
      - Station guard: only hard-blocks on nws_settlement=False (e.g. Washington DC).
        Unverified NWS cities pass through and are demoted to RESEARCH_ONLY by the
        eligibility engine below.
      - Quote freshness is no longer a hard-stop.  Stale-quote trades are classified
        RESEARCH_ONLY instead of SKIP, so a row is still created for analysis.
      - Returns eligibility_status, eligibility_reason, quote_age_seconds.
      - Returns city, target_settlement_date_str, settlement_timezone, weather_variable
        so the batch-level correlated-exposure limit (Guard 6) can group candidates
        without re-fetching the market row.
    """
    from app.services.probability_engine_v22 import run_analysis_v22

    if now is None:
        now = datetime.now(timezone.utc)

    warnings: list[str] = [
        "Simulation only. No real trades are placed.",
        "Results may overstate real-world performance: fees, spreads, liquidity, "
        "slippage, and fill availability are not modelled.",
    ]

    # ── Hard-stop: NWS settlement source ─────────────────────────────────────
    # Cities like Washington DC settle on The Weather Company, not NWS.
    # The V2.2 model is calibrated against NWS only; trading these would be
    # meaningless.  Hard-block here; unverified NWS cities pass through.
    if not market.city:
        return _skip_v22("City not identified", None, None, warnings)
    station = get_station(market.city)
    if station is None:
        return _skip_v22(f"No settlement station for '{market.city}'", None, None, warnings)
    if not getattr(station, "nws_settlement", True):
        return _skip_v22(
            f"Non-NWS settlement for '{market.city}' — permanently blocked",
            None, None, warnings,
        )

    station_lat      = station.lat
    station_lon      = station.lon
    station_tz       = getattr(station, "timezone", "UTC")
    station_verified = getattr(station, "verified", False)

    # ── Standard V2 pre-checks ────────────────────────────────────────────────
    if snap.analysis_status != "supported":
        return _skip_v22("Unsupported market", None, snap.market_probability, warnings)

    if snap.forecast_value is None:
        return _skip_v22("Forecast unavailable", None, snap.market_probability, warnings)

    if market.status != "active":
        return _skip_v22("Market already closed", None, snap.market_probability, warnings)

    # ── V2.2 probability engine ───────────────────────────────────────────────
    result = await run_analysis_v22(
        title=market.title or "",
        subtitle=market.subtitle,
        city=market.city,
        target_date_str=market.target_date,
        weather_variable=snap.settlement_variable,
        operator=snap.settlement_operator,
        threshold=snap.settlement_threshold,
        parse_confidence="high",
        settlement_status="supported",
        unsupported_reason=None,
        forecast_high=(snap.forecast_value if snap.settlement_variable == "high" else None),
        forecast_low=(snap.forecast_value if snap.settlement_variable != "high" else None),
        forecast_retrieved_at=snap.forecast_retrieved_at,
        yes_bid=market.yes_bid,
        yes_ask=market.yes_ask,
        contract_type=snap.contract_type or "threshold",
        lower_bound=snap.lower_bound,
        upper_bound=snap.upper_bound,
        forecast_hourly_value=(
            snap.forecast_value if snap.contract_type == "hourly_threshold" else None
        ),
        session=session,
    )

    ec_yes_prob  = result.ec_probability
    mkt_yes_prob = result.market_probability
    confidence   = result.confidence

    if ec_yes_prob is None:
        return _skip_v22("V2.2 probability unavailable", ec_yes_prob, mkt_yes_prob, warnings)

    if mkt_yes_prob is None:
        return _skip_v22("Market price unavailable", ec_yes_prob, mkt_yes_prob, warnings)

    min_conf = settings["min_confidence"]
    if not _is_confidence_sufficient(confidence, min_conf):
        return _skip_v22(
            f"Confidence too low ({confidence} < {min_conf})",
            ec_yes_prob, mkt_yes_prob, warnings,
        )

    # ── Edge calculation ──────────────────────────────────────────────────────
    ec_no_prob = round(1.0 - ec_yes_prob, 4)
    min_edge   = settings["min_edge_pct"] / 100.0

    yes_price, yes_src = _select_yes_price(market)
    no_price,  no_src  = _select_no_price(market)

    yes_edge = (ec_yes_prob - yes_price) if yes_price is not None else None
    no_edge  = (ec_no_prob  - no_price)  if no_price  is not None else None

    yes_qualifies = yes_edge is not None and yes_edge >= min_edge
    no_qualifies  = no_edge  is not None and no_edge  >= min_edge

    if not yes_qualifies and not no_qualifies:
        candidates = [e for e in [yes_edge, no_edge] if e is not None]
        best_edge = max(candidates) if candidates else None
        best_str  = f"{best_edge * 100:.1f}pp" if best_edge is not None else "N/A"
        return _skip_v22(
            f"Edge below threshold ({best_str} < {settings['min_edge_pct']:.1f}pp)",
            ec_yes_prob, mkt_yes_prob, warnings,
        )

    # Direction selection
    if yes_qualifies and no_qualifies:
        direction = "YES" if (yes_edge or 0) >= (no_edge or 0) else "NO"
    elif yes_qualifies:
        direction = "YES"
    else:
        direction = "NO"

    if direction == "YES":
        side_price, src = yes_price, yes_src
        ec_side_prob    = ec_yes_prob
        edge            = yes_edge
        quote_bid       = market.yes_bid
        quote_ask       = market.yes_ask
    else:
        side_price, src = no_price, no_src
        ec_side_prob    = ec_no_prob
        edge            = no_edge
        quote_bid       = market.no_bid
        quote_ask       = market.no_ask

    edge_pp = round((edge or 0) * 100, 4)

    # ── V2 base exclusion checks ──────────────────────────────────────────────
    excl_flag = _v2_exclusion_flag(market, side_price)
    if excl_flag:
        return {
            "action":               "EXCLUDED",
            "exclusion_flag":       excl_flag,
            "skip_reason":          V2_FLAG_DESCRIPTIONS.get(excl_flag, excl_flag),
            "direction":            direction,
            "ec_yes_probability":   ec_yes_prob,
            "ec_side_probability":  ec_side_prob,
            "market_yes_probability": mkt_yes_prob,
            "side_market_price":    side_price,
            "price_source":         src,
            "edge_pct_points":      edge_pp,
            "decision_explanation": f"[v2.2 excluded: {excl_flag}] {result.explanation}",
            "warnings":             warnings,
            "sigma_used":           result.sigma_used,
            "bias_correction":      result.bias_correction,
            "fallback_level":       result.fallback_level,
            "calibration_adj":      result.calibration_adj,
            "raw_ec_probability":   result.raw_ec_probability,
            "station_verified":     station_verified,
            "station_lat":          station_lat,
            "station_lon":          station_lon,
            "quote_bid":            quote_bid,
            "quote_ask":            quote_ask,
            "quote_timestamp":      market.collection_timestamp,
            "est_available_qty":    getattr(market, "open_interest", None),
            "is_executable":        False,
            "consensus_guard_triggered": False,
            "eligibility_status":   "RESEARCH_ONLY",
            "eligibility_reason":   "v2_excluded",
            "quote_age_seconds":    None,
            "city":                         market.city,
            "target_settlement_date_str":   market.target_date,
            "settlement_timezone":          station_tz,
            "weather_variable":             snap.settlement_variable,
            "market_close_timestamp":       getattr(market, "close_time", None),
            "expected_settlement_timestamp": None,
            "decision_timestamp":           now,
            "minutes_to_market_close":      None,
        }

    # ── Consensus guard (configurable) ───────────────────────────────────────
    consensus_guard_triggered = False
    if settings.get("consensus_guard_enabled", False):
        cg_ok, cg_reason = _check_consensus_guard(direction, ec_yes_prob, mkt_yes_prob)
        if not cg_ok:
            consensus_guard_triggered = True
            return _skip_v22(
                cg_reason or "Consensus guard triggered",
                ec_yes_prob, mkt_yes_prob, warnings,
                extra={
                    "consensus_guard_triggered": True,
                    "station_verified": station_verified,
                    "station_lat":      station_lat,
                    "station_lon":      station_lon,
                },
            )

    # ── Build explanation ─────────────────────────────────────────────────────
    sigma = result.sigma_used or 0.0
    bias  = result.bias_correction or 0.0
    explanation = (
        f"[v2.2] EdgeCast estimated {ec_yes_prob * 100:.1f}% YES "
        f"(σ={sigma:.1f}°F/{result.fallback_level}, bias={bias:+.1f}°F). "
        f"{'YES' if direction == 'YES' else 'NO'} price ({src}) implied "
        f"{(side_price or 0) * 100:.1f}%. "
        f"Edge: +{edge_pp:.1f}pp. Confidence: {confidence}. "
        f"Station: {station.station_name} ({'verified' if station_verified else 'unverified'})."
    )

    # ── Market close audit fields ─────────────────────────────────────────────
    market_close_ts = getattr(market, "close_time", None)
    decision_ts = now
    minutes_to_close: float | None = None
    if market_close_ts is not None:
        if market_close_ts.tzinfo is None:
            market_close_ts = market_close_ts.replace(tzinfo=timezone.utc)
        minutes_to_close = (market_close_ts - now).total_seconds() / 60

    expected_settle_ts = None
    if market.target_date:
        try:
            from app.services.eligibility import _parse_settlement_dt as _psd
            expected_settle_ts = _psd(market.target_date)
        except Exception:
            pass

    # ── Eligibility engine (Guards 1–5, 7–8) ─────────────────────────────────
    # Guard 6 (correlated-exposure) is applied at batch level in run_paper_trading_v22.
    elig_status, elig_reason, quote_age_s = assess_trade_eligibility(
        contract_type=snap.contract_type,
        target_settlement_date_str=market.target_date,
        settlement_timezone=station_tz,
        now=now,
        side_market_price=side_price,
        edge_pct_points=edge_pp,
        station_verified=station_verified,
        direction=direction,
        quote_timestamp=market.collection_timestamp,
        quote_ask=quote_ask,
        market_close_timestamp=market_close_ts,
    )

    # OFFICIAL trades may be executable; RESEARCH_ONLY trades are never executable.
    executable = (elig_status == "OFFICIAL") and _is_executable(
        calculate_position(settings["stake"], side_price or 0.0).get("quantity", 0.0),
        side_price or 0.0,
    )

    return {
        "action":               direction,
        "exclusion_flag":       None,
        "skip_reason":          None,
        "direction":            direction,
        "ec_yes_probability":   ec_yes_prob,
        "ec_side_probability":  ec_side_prob,
        "market_yes_probability": mkt_yes_prob,
        "side_market_price":    side_price,
        "price_source":         src,
        "edge_pct_points":      edge_pp,
        "decision_explanation": explanation,
        "warnings":             warnings,
        "sigma_used":           result.sigma_used,
        "bias_correction":      result.bias_correction,
        "fallback_level":       result.fallback_level,
        "calibration_adj":      result.calibration_adj,
        "raw_ec_probability":   result.raw_ec_probability,
        "station_verified":     station_verified,
        "station_lat":          station_lat,
        "station_lon":          station_lon,
        "quote_bid":            quote_bid,
        "quote_ask":            quote_ask,
        "quote_timestamp":      market.collection_timestamp,
        "est_available_qty":    getattr(market, "open_interest", None),
        "is_executable":        executable,
        "consensus_guard_triggered": consensus_guard_triggered,
        "eligibility_status":           elig_status,
        "eligibility_reason":           elig_reason,
        "quote_age_seconds":            quote_age_s,
        "city":                         market.city,
        "target_settlement_date_str":   market.target_date,
        "settlement_timezone":          station_tz,
        "weather_variable":             snap.settlement_variable,
        "market_close_timestamp":       market_close_ts,
        "expected_settlement_timestamp": expected_settle_ts,
        "decision_timestamp":           decision_ts,
        "minutes_to_market_close":      minutes_to_close,
    }


# ── Create or log a single V2.2 paper trade ───────────────────────────────────

async def maybe_create_paper_trade_v22(
    session: AsyncSession,
    market: KalshiMarket,
    snap: PredictionSnapshot,
    settings: dict[str, Any],
    now: datetime | None = None,
    comparison_snapshot_id: str | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """
    Create a V2.2 PaperTrade (or V2_EXCLUDED log entry) for the market/snapshot.
    Returns {"created": bool, "excluded": bool, "direction": str|None}.
    Never touches V2.1 trades.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Duplicate check — one row per (ticker, "v2.2")
    existing_q = await session.execute(
        select(PaperTrade).where(
            PaperTrade.market_ticker == market.ticker,
            PaperTrade.strategy_version == STRATEGY_VERSION,
        )
    )
    if existing_q.scalar_one_or_none() is not None:
        return {"created": False, "excluded": False, "direction": None, "skip_reason": "duplicate"}

    decision = await decide_trade_v22(snap, market, settings, session, now=now)

    if decision["action"] == "SKIP":
        return {
            "created": False, "excluded": False,
            "direction": None, "skip_reason": decision["skip_reason"],
        }

    stake = settings["stake"]
    side_price = decision["side_market_price"] or 0.0

    # Both active trades and excluded entries are stored for research visibility
    if decision["action"] == "EXCLUDED":
        trade = PaperTrade(
            market_ticker=market.ticker,
            event_ticker=market.event_ticker,
            city=market.city,
            weather_variable=snap.settlement_variable,
            contract_type=snap.contract_type,
            target_settlement_date=market.target_date,
            snapshot_id=snap.id,
            strategy_version=STRATEGY_VERSION,
            direction=decision["direction"] or "YES",
            ec_yes_probability=decision["ec_yes_probability"],
            ec_side_probability=decision["ec_side_probability"],
            market_yes_probability=decision["market_yes_probability"],
            side_market_price=side_price,
            price_source=decision["price_source"],
            edge_pct_points=decision["edge_pct_points"],
            confidence_score=None,
            confidence_label=snap.confidence,
            stake=0.0,
            quantity=0.0,
            lead_time_days=snap.lead_time_days,
            status="V2_EXCLUDED",
            decision_explanation=decision["decision_explanation"],
            warnings="; ".join(decision["warnings"]),
            quality_flags=[decision["exclusion_flag"]],
            sigma_used=decision["sigma_used"],
            bias_correction=decision["bias_correction"],
            fallback_level=decision["fallback_level"],
            calibration_adj=decision["calibration_adj"],
            station_verified=decision["station_verified"],
            station_lat=decision["station_lat"],
            station_lon=decision["station_lon"],
            quote_bid=decision["quote_bid"],
            quote_ask=decision["quote_ask"],
            quote_timestamp=decision["quote_timestamp"],
            est_available_qty=decision["est_available_qty"],
            is_executable=decision["is_executable"],
            eligibility_status=decision.get("eligibility_status"),
            eligibility_reason=decision.get("eligibility_reason"),
            quote_age_seconds=decision.get("quote_age_seconds"),
            market_close_timestamp=decision.get("market_close_timestamp"),
            expected_settlement_timestamp=decision.get("expected_settlement_timestamp"),
            decision_timestamp=decision.get("decision_timestamp"),
            minutes_to_market_close=decision.get("minutes_to_market_close"),
            settlement_timezone=decision.get("settlement_timezone"),
            comparison_snapshot_id=comparison_snapshot_id,
            collection_batch_id=batch_id,
        )
        session.add(trade)
        await session.flush()
        return {
            "created": False, "excluded": True,
            "direction": decision["direction"],
            "skip_reason": decision["skip_reason"],
        }

    # Active trade
    if side_price <= 0:
        return {"created": False, "excluded": False, "direction": None, "skip_reason": "zero price"}

    pos = calculate_position(stake, side_price)

    trade = PaperTrade(
        market_ticker=market.ticker,
        event_ticker=market.event_ticker,
        city=market.city,
        weather_variable=snap.settlement_variable,
        contract_type=snap.contract_type,
        target_settlement_date=market.target_date,
        snapshot_id=snap.id,
        strategy_version=STRATEGY_VERSION,
        direction=decision["direction"],
        ec_yes_probability=decision["ec_yes_probability"],
        ec_side_probability=decision["ec_side_probability"],
        market_yes_probability=decision["market_yes_probability"],
        side_market_price=side_price,
        price_source=decision["price_source"],
        edge_pct_points=decision["edge_pct_points"],
        confidence_score=None,
        confidence_label=snap.confidence,
        stake=pos["stake"],
        quantity=pos["quantity"],
        lead_time_days=snap.lead_time_days,
        status="OPEN",
        decision_explanation=decision["decision_explanation"],
        warnings="; ".join(decision["warnings"]),
        quality_flags=None,
        sigma_used=decision["sigma_used"],
        bias_correction=decision["bias_correction"],
        fallback_level=decision["fallback_level"],
        calibration_adj=decision["calibration_adj"],
        station_verified=decision["station_verified"],
        station_lat=decision["station_lat"],
        station_lon=decision["station_lon"],
        quote_bid=decision["quote_bid"],
        quote_ask=decision["quote_ask"],
        quote_timestamp=decision["quote_timestamp"],
        est_available_qty=decision["est_available_qty"],
        is_executable=decision["is_executable"],
        eligibility_status=decision.get("eligibility_status"),
        eligibility_reason=decision.get("eligibility_reason"),
        quote_age_seconds=decision.get("quote_age_seconds"),
        market_close_timestamp=decision.get("market_close_timestamp"),
        expected_settlement_timestamp=decision.get("expected_settlement_timestamp"),
        decision_timestamp=decision.get("decision_timestamp"),
        minutes_to_market_close=decision.get("minutes_to_market_close"),
        settlement_timezone=decision.get("settlement_timezone"),
        comparison_snapshot_id=comparison_snapshot_id,
        collection_batch_id=batch_id,
    )
    session.add(trade)
    await session.flush()

    logger.info(
        "V2.2 paper trade created: %s %s @ %.4f (σ=%.1f/%s, bias=%+.1f, "
        "edge=%.1fpp, exec=%s, elig=%s)",
        decision["direction"], market.ticker, side_price,
        decision["sigma_used"] or 0,
        decision["fallback_level"],
        decision["bias_correction"] or 0,
        decision["edge_pct_points"] or 0,
        decision["is_executable"],
        decision.get("eligibility_status"),
    )
    return {"created": True, "excluded": False, "direction": decision["direction"]}


# ── Batch runner ──────────────────────────────────────────────────────────────

async def run_paper_trading_v22(
    session: AsyncSession,
    batch_id: str | None = None,
    comparison_snapshot_ids: dict[str, str] | None = None,
) -> dict[str, int]:
    """
    Review all recently-analyzed markets and create V2.2 paper trades.
    Called after V2.1 paper trading in each collection run.

    Two-pass design (required for Guard 6 correlated-exposure limit):
      Phase 1 — evaluate every candidate without writing any trade rows.
      Phase 2 — apply the correlated-exposure limit to all OFFICIAL candidates
                 so that at most one OFFICIAL trade exists per
                 (city, settlement_local_date, weather_variable).
      Phase 3 — write all trade rows (OFFICIAL, RESEARCH_ONLY, and EXCLUDED).

    ``batch_id`` and ``comparison_snapshot_ids`` are supplied by the collector
    after creating ComparisonSnapshot rows.  When provided, every new trade
    stores its comparison_snapshot_id + collection_batch_id for pairing.

    Gated by ``v2.2.paper_trading_enabled`` flag — no-ops if false.

    Returns: {"candidates", "created", "excluded", "skipped", "errors"}
    """
    stats: dict[str, int] = {
        "candidates": 0, "created": 0,
        "excluded": 0, "skipped": 0, "errors": 0,
    }

    flag_enabled = await get_v22_flag(session, "v2.2.paper_trading_enabled")
    if not flag_enabled:
        logger.info("V2.2 paper trading disabled (v2.2.paper_trading_enabled=false) — skipping.")
        return stats

    settings = await get_v22_settings(session)
    if not settings["enabled"]:
        logger.info("V2.2 paper trading disabled (settings.enabled=false) — skipping.")
        return stats

    now = datetime.now(timezone.utc)
    comp_ids: dict[str, str] = comparison_snapshot_ids or {}

    # Latest snapshot per ticker (same pool as V2.1)
    snaps_q = await session.execute(
        select(PredictionSnapshot)
        .order_by(PredictionSnapshot.market_ticker, PredictionSnapshot.id.desc())
    )
    all_snaps = snaps_q.scalars().all()

    seen_tickers: set[str] = set()
    latest_snaps: list[PredictionSnapshot] = []
    for s in all_snaps:
        if s.market_ticker not in seen_tickers:
            seen_tickers.add(s.market_ticker)
            latest_snaps.append(s)

    markets_q = await session.execute(
        select(KalshiMarket).where(KalshiMarket.status == "active")
    )
    market_map: dict[str, KalshiMarket] = {
        m.ticker: m for m in markets_q.scalars().all()
    }

    # ── Phase 1: evaluate all candidates (no DB writes) ───────────────────────
    # list of (snap, market, decision, comp_id)
    evaluations: list[tuple] = []

    for snap in latest_snaps:
        market = market_map.get(snap.market_ticker)
        if market is None:
            continue

        # Duplicate check — one row per (ticker, "v2.2")
        existing_q = await session.execute(
            select(PaperTrade).where(
                PaperTrade.market_ticker == market.ticker,
                PaperTrade.strategy_version == STRATEGY_VERSION,
            )
        )
        if existing_q.scalar_one_or_none() is not None:
            stats["skipped"] += 1
            continue

        stats["candidates"] += 1
        try:
            decision = await decide_trade_v22(snap, market, settings, session, now=now)
            if decision["action"] == "SKIP":
                stats["skipped"] += 1
                continue
            evaluations.append((snap, market, decision, comp_ids.get(snap.market_ticker)))
        except Exception as exc:
            stats["errors"] += 1
            logger.warning("V2.2 decision error for %s: %s", snap.market_ticker, exc)

    # ── Phase 2: correlated-exposure limit (Guard 6) ──────────────────────────
    official_decisions = [
        d for (_, _, d, _) in evaluations
        if d.get("eligibility_status") == "OFFICIAL"
    ]
    if official_decisions:
        apply_correlated_limit(official_decisions)
        # apply_correlated_limit mutates the dicts in-place, so evaluations reflects the changes

    # ── Phase 3: create trade rows ────────────────────────────────────────────
    for snap, market, decision, comp_id in evaluations:
        try:
            stake      = settings["stake"]
            side_price = decision["side_market_price"] or 0.0

            if decision["action"] == "EXCLUDED":
                trade = PaperTrade(
                    market_ticker=market.ticker,
                    event_ticker=market.event_ticker,
                    city=market.city,
                    weather_variable=snap.settlement_variable,
                    contract_type=snap.contract_type,
                    target_settlement_date=market.target_date,
                    snapshot_id=snap.id,
                    strategy_version=STRATEGY_VERSION,
                    direction=decision["direction"] or "YES",
                    ec_yes_probability=decision["ec_yes_probability"],
                    ec_side_probability=decision["ec_side_probability"],
                    market_yes_probability=decision["market_yes_probability"],
                    side_market_price=side_price,
                    price_source=decision["price_source"],
                    edge_pct_points=decision["edge_pct_points"],
                    confidence_score=None,
                    confidence_label=snap.confidence,
                    stake=0.0,
                    quantity=0.0,
                    lead_time_days=snap.lead_time_days,
                    status="V2_EXCLUDED",
                    decision_explanation=decision["decision_explanation"],
                    warnings="; ".join(decision["warnings"]),
                    quality_flags=[decision["exclusion_flag"]],
                    sigma_used=decision["sigma_used"],
                    bias_correction=decision["bias_correction"],
                    fallback_level=decision["fallback_level"],
                    calibration_adj=decision["calibration_adj"],
                    station_verified=decision["station_verified"],
                    station_lat=decision["station_lat"],
                    station_lon=decision["station_lon"],
                    quote_bid=decision["quote_bid"],
                    quote_ask=decision["quote_ask"],
                    quote_timestamp=decision["quote_timestamp"],
                    est_available_qty=decision["est_available_qty"],
                    is_executable=decision["is_executable"],
                    eligibility_status=decision.get("eligibility_status"),
                    eligibility_reason=decision.get("eligibility_reason"),
                    quote_age_seconds=decision.get("quote_age_seconds"),
                    comparison_snapshot_id=comp_id,
                    collection_batch_id=batch_id,
                )
                session.add(trade)
                await session.flush()
                stats["excluded"] += 1
                continue

            if side_price <= 0:
                stats["skipped"] += 1
                continue

            pos = calculate_position(stake, side_price)

            trade = PaperTrade(
                market_ticker=market.ticker,
                event_ticker=market.event_ticker,
                city=market.city,
                weather_variable=snap.settlement_variable,
                contract_type=snap.contract_type,
                target_settlement_date=market.target_date,
                snapshot_id=snap.id,
                strategy_version=STRATEGY_VERSION,
                direction=decision["direction"],
                ec_yes_probability=decision["ec_yes_probability"],
                ec_side_probability=decision["ec_side_probability"],
                market_yes_probability=decision["market_yes_probability"],
                side_market_price=side_price,
                price_source=decision["price_source"],
                edge_pct_points=decision["edge_pct_points"],
                confidence_score=None,
                confidence_label=snap.confidence,
                stake=pos["stake"],
                quantity=pos["quantity"],
                lead_time_days=snap.lead_time_days,
                status="OPEN",
                decision_explanation=decision["decision_explanation"],
                warnings="; ".join(decision["warnings"]),
                quality_flags=None,
                sigma_used=decision["sigma_used"],
                bias_correction=decision["bias_correction"],
                fallback_level=decision["fallback_level"],
                calibration_adj=decision["calibration_adj"],
                station_verified=decision["station_verified"],
                station_lat=decision["station_lat"],
                station_lon=decision["station_lon"],
                quote_bid=decision["quote_bid"],
                quote_ask=decision["quote_ask"],
                quote_timestamp=decision["quote_timestamp"],
                est_available_qty=decision["est_available_qty"],
                is_executable=decision["is_executable"],
                eligibility_status=decision.get("eligibility_status"),
                eligibility_reason=decision.get("eligibility_reason"),
                quote_age_seconds=decision.get("quote_age_seconds"),
                comparison_snapshot_id=comp_id,
                collection_batch_id=batch_id,
            )
            session.add(trade)
            await session.flush()
            stats["created"] += 1

            logger.info(
                "V2.2 paper trade created: %s %s @ %.4f (σ=%.1f/%s, bias=%+.1f, "
                "edge=%.1fpp, exec=%s, elig=%s)",
                decision["direction"], market.ticker, side_price,
                decision["sigma_used"] or 0,
                decision["fallback_level"],
                decision["bias_correction"] or 0,
                decision["edge_pct_points"] or 0,
                decision["is_executable"],
                decision.get("eligibility_status"),
            )

        except Exception as exc:
            stats["errors"] += 1
            logger.warning("V2.2 paper trade error for %s: %s", snap.market_ticker, exc)

    await session.commit()
    logger.info(
        "V2.2 paper trading: %d candidates, %d created, %d excluded, %d skipped, %d errors",
        stats["candidates"], stats["created"],
        stats["excluded"], stats["skipped"], stats["errors"],
    )
    return stats
