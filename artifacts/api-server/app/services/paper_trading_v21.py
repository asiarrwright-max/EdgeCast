"""
EdgeCast Paper Trading Service — Strategy v2.1
===============================================
Incremental hardening of v2.0 addressing four root causes found in the
July 2026 pipeline audit:

  1. Station coordinates — forecasts fetched at settlement-station lat/lon
     (e.g. OKC Will Rogers Airport) rather than city-centre.
  2. Sigma floor — σ cannot fall below SIGMA_FLOOR (3.5°F daily) regardless
     of what the DB has learned; prevents illusory 90+pp edges.
  3. Execution realism — quote staleness threshold tightened to 4 h; trades
     with >50 contracts at < $0.10/contract flagged non-executable.
  4. Unverified-station guard — markets for cities whose settlement station
     is unverified are skipped (not logged as EXCLUDED — they are invisible to
     V2.1 until the station mapping is confirmed).

Consensus guard (disabled by default)
--------------------------------------
When ``paper_trading_v21.consensus_guard_enabled`` AppSetting = "true", V2.1
additionally skips any market where the Kalshi consensus probability against
our direction is ≥ CONSENSUS_GUARD_THRESHOLD (default 85%).

The guard can be back-tested offline via ``consensus_guard_backtest()``.

Isolation guarantee
--------------------
One PaperTrade per (market_ticker, "v2.1").  V2.1 trades never touch v1 or
v2.0 trades.  All three strategies coexist in the paper_trades table.
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
    settle_position,          # pure math — safe to share
    edge_bucket,
    price_bucket,
    lead_bucket,
    get_paper_trade_metrics,  # already strategy-version-aware
)
from app.services.paper_trading_v2 import (
    FLAG_V2_BELOW_MIN_PRICE,
    FLAG_V2_ZERO_VOLUME,
    FLAG_V2_NO_LIQUIDITY,
    V2_FLAG_DESCRIPTIONS,
    _v2_exclusion_flag,
)
from app.services.settlement_stations import get_station

logger = logging.getLogger(__name__)

STRATEGY_VERSION = "v2.1"

# ── Constants ─────────────────────────────────────────────────────────────────

# Tightened from 24 h → 4 h: prices can move significantly overnight; a
# stale quote may have crossed the break-even threshold by the time we decide.
STALE_QUOTE_SECONDS = 4 * 3600   # 4 hours

# Flag a trade as non-executable when >50 contracts are required at a price
# below $0.10/contract.  At 50 contracts × $0.10 = $5 nominal, the book is
# typically too thin to fill at the ask without significant slippage.
NON_EXECUTABLE_MAX_QTY   = 50
NON_EXECUTABLE_MAX_PRICE = 0.10  # decimal (10 cents)

# Consensus guard threshold.  When enabled, skip trades where the market's
# implied probability AGAINST our side is ≥ this level.
CONSENSUS_GUARD_THRESHOLD = 0.85  # 85%

# ── Default settings ──────────────────────────────────────────────────────────
DEFAULT_V21_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "min_edge_pct": 10.0,
    "min_confidence": "High",
    "stake": 10.0,
    "consensus_guard_enabled": False,   # OFF by default until backtested
}

_V21_SETTING_KEYS = {
    "paper_trading_v21.enabled":                  ("enabled",                 bool),
    "paper_trading_v21.min_edge_pct":             ("min_edge_pct",            float),
    "paper_trading_v21.min_confidence":           ("min_confidence",          str),
    "paper_trading_v21.stake":                    ("stake",                   float),
    "paper_trading_v21.consensus_guard_enabled":  ("consensus_guard_enabled", bool),
}

_CONFIDENCE_RANK = {"Very High": 2, "High": 1, "Medium": 0, "Low": -1, "Very Low": -2}


# ── Settings helpers ───────────────────────────────────────────────────────────

async def get_v21_settings(session: AsyncSession) -> dict[str, Any]:
    """Return effective v2.1 settings (DB overrides applied over defaults)."""
    keys = list(_V21_SETTING_KEYS.keys())
    result = await session.execute(
        select(AppSetting).where(AppSetting.key.in_(keys))
    )
    db_rows = {r.key: r.value for r in result.scalars().all()}

    settings = dict(DEFAULT_V21_SETTINGS)
    for db_key, (field_name, cast) in _V21_SETTING_KEYS.items():
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


# ── V2.1 pre-flight checks ────────────────────────────────────────────────────

def _check_station_verified(city: str | None) -> tuple[bool, str | None]:
    """
    Return (ok, skip_reason).

    ok = True means the settlement station is verified and coordinates are
    known.  ok = False means we must skip the market for V2.1.
    """
    if not city:
        return False, "City not identified"
    station = get_station(city)
    if station is None:
        return False, f"No settlement station registered for '{city}'"
    if not station.verified:
        return False, (
            f"Settlement station for '{city}' is UNVERIFIED "
            f"({station.station_name}). "
            "V2.1 does not trade unverified stations to prevent systematic "
            "errors from wrong location offsets."
        )
    return True, None


def _check_quote_freshness(market: KalshiMarket, now: datetime) -> tuple[bool, str | None]:
    """Return (ok, skip_reason). ok=False if the market quote is stale (>4 h)."""
    coll_ts = getattr(market, "collection_timestamp", None)
    if coll_ts is None:
        # No timestamp means we can't assess freshness; be conservative and skip
        return False, "No collection_timestamp; cannot assess quote freshness"
    if coll_ts.tzinfo is None:
        coll_ts = coll_ts.replace(tzinfo=timezone.utc)
    age_s = (now - coll_ts).total_seconds()
    if age_s > STALE_QUOTE_SECONDS:
        hours = age_s / 3600
        return False, f"Market quote is stale ({hours:.1f}h old; limit 4h)"
    return True, None


def _check_consensus_guard(
    direction: str,
    ec_yes_probability: float,
    market_yes_probability: float | None,
) -> tuple[bool, str | None]:
    """
    Return (ok, skip_reason).

    Skip when Kalshi consensus against our trade ≥ 85%.
    Example: trading YES, market says only 8% YES → 92% consensus NO → skip.
    """
    if market_yes_probability is None:
        return True, None  # can't assess; allow through
    if direction == "YES":
        consensus_against = 1.0 - market_yes_probability
    else:
        consensus_against = market_yes_probability
    if consensus_against >= CONSENSUS_GUARD_THRESHOLD:
        return False, (
            f"Consensus guard: Kalshi market implies "
            f"{consensus_against * 100:.0f}% probability against our {direction} trade "
            f"(threshold {CONSENSUS_GUARD_THRESHOLD * 100:.0f}%)."
        )
    return True, None


def _is_executable(quantity: float, price: float) -> bool:
    """
    Return False when the trade is unlikely to be fillable in practice.

    Heuristic: >50 contracts at <$0.10 each.  At this price level the
    book is typically too thin; the true fill price would be materially
    worse than the mid/ask.
    """
    if quantity > NON_EXECUTABLE_MAX_QTY and price < NON_EXECUTABLE_MAX_PRICE:
        return False
    return True


# ── Core decision (v2.1) ─────────────────────────────────────────────────────

async def decide_trade_v21(
    snap: PredictionSnapshot,
    market: KalshiMarket,
    settings: dict[str, Any],
    session: AsyncSession,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Evaluate one market using the v2 probability engine with V2.1 guards.

    Extra V2.1 keys vs. decide_trade_v2:
      station_verified, station_lat, station_lon
      quote_bid, quote_ask, quote_timestamp, est_available_qty
      is_executable
      consensus_guard_triggered
    """
    from app.services.probability_engine_v2 import run_analysis_v2

    if now is None:
        now = datetime.now(timezone.utc)

    warnings: list[str] = [
        "Simulation only. No real trades are placed.",
        "Results may overstate real-world performance: fees, spreads, liquidity, "
        "slippage, and fill availability are not modelled.",
    ]

    # ── Guard 1: station verification ────────────────────────────────────────
    station_ok, station_skip_reason = _check_station_verified(market.city)
    if not station_ok:
        return _skip_v21(station_skip_reason, None, None, warnings)

    station = get_station(market.city)  # guaranteed non-None and verified here
    station_lat = station.lat
    station_lon = station.lon

    # ── Guard 2: quote freshness (4 h) ───────────────────────────────────────
    fresh_ok, fresh_skip_reason = _check_quote_freshness(market, now)
    if not fresh_ok:
        return _skip_v21(fresh_skip_reason, None, None, warnings)

    # ── Standard V2 pre-checks ────────────────────────────────────────────────
    if snap.analysis_status != "supported":
        return _skip_v21("Unsupported market", None, snap.market_probability, warnings)

    if snap.forecast_value is None:
        return _skip_v21("Forecast unavailable", None, snap.market_probability, warnings)

    if market.status != "active":
        return _skip_v21("Market already closed", None, snap.market_probability, warnings)

    # ── V2 probability engine ─────────────────────────────────────────────────
    result = await run_analysis_v2(
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

    ec_yes_prob = result.ec_probability
    mkt_yes_prob = result.market_probability
    confidence = result.confidence

    if ec_yes_prob is None:
        return _skip_v21("V2.1 probability unavailable", ec_yes_prob, mkt_yes_prob, warnings)

    if mkt_yes_prob is None:
        return _skip_v21("Market price unavailable", ec_yes_prob, mkt_yes_prob, warnings)

    min_conf = settings["min_confidence"]
    if not _is_confidence_sufficient(confidence, min_conf):
        return _skip_v21(
            f"Confidence too low ({confidence} < {min_conf})",
            ec_yes_prob, mkt_yes_prob, warnings,
        )

    # ── Edge calculation ──────────────────────────────────────────────────────
    ec_no_prob = round(1.0 - ec_yes_prob, 4)
    min_edge = settings["min_edge_pct"] / 100.0

    yes_price, yes_src = _select_yes_price(market)
    no_price, no_src = _select_no_price(market)

    yes_edge = (ec_yes_prob - yes_price) if yes_price is not None else None
    no_edge = (ec_no_prob - no_price) if no_price is not None else None

    yes_qualifies = yes_edge is not None and yes_edge >= min_edge
    no_qualifies = no_edge is not None and no_edge >= min_edge

    if not yes_qualifies and not no_qualifies:
        candidates = [e for e in [yes_edge, no_edge] if e is not None]
        best_edge = max(candidates) if candidates else None
        best_str = f"{best_edge * 100:.1f}pp" if best_edge is not None else "N/A"
        return _skip_v21(
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
        ec_side_prob = ec_yes_prob
        edge = yes_edge
        quote_bid = market.yes_bid
        quote_ask = market.yes_ask
    else:
        side_price, src = no_price, no_src
        ec_side_prob = ec_no_prob
        edge = no_edge
        quote_bid = market.no_bid
        quote_ask = market.no_ask

    # ── V2 base exclusion checks ──────────────────────────────────────────────
    excl_flag = _v2_exclusion_flag(market, side_price)
    if excl_flag:
        return {
            "action": "EXCLUDED",
            "exclusion_flag": excl_flag,
            "skip_reason": V2_FLAG_DESCRIPTIONS.get(excl_flag, excl_flag),
            "direction": direction,
            "ec_yes_probability": ec_yes_prob,
            "ec_side_probability": ec_side_prob,
            "market_yes_probability": mkt_yes_prob,
            "side_market_price": side_price,
            "price_source": src,
            "edge_pct_points": round((edge or 0) * 100, 4),
            "decision_explanation": f"[v2.1 excluded: {excl_flag}] {result.explanation}",
            "warnings": warnings,
            "sigma_used": result.sigma_used,
            "bias_correction": result.bias_correction,
            "fallback_level": result.fallback_level,
            "calibration_adj": result.calibration_adj,
            "raw_ec_probability": result.raw_ec_probability,
            "station_verified": True,
            "station_lat": station_lat,
            "station_lon": station_lon,
            "quote_bid": quote_bid,
            "quote_ask": quote_ask,
            "quote_timestamp": market.collection_timestamp,
            "est_available_qty": getattr(market, "open_interest", None),
            "is_executable": None,
            "consensus_guard_triggered": False,
        }

    # ── Guard 3: consensus guard (configurable) ───────────────────────────────
    consensus_guard_triggered = False
    if settings.get("consensus_guard_enabled", False):
        cg_ok, cg_reason = _check_consensus_guard(direction, ec_yes_prob, mkt_yes_prob)
        if not cg_ok:
            consensus_guard_triggered = True
            return _skip_v21(
                cg_reason or "Consensus guard triggered",
                ec_yes_prob, mkt_yes_prob, warnings,
                extra={
                    "consensus_guard_triggered": True,
                    "station_verified": True,
                    "station_lat": station_lat,
                    "station_lon": station_lon,
                },
            )

    # ── Build explanation ─────────────────────────────────────────────────────
    sigma = result.sigma_used or 0.0
    bias = result.bias_correction or 0.0
    explanation = (
        f"[v2.1] EdgeCast estimated {ec_yes_prob * 100:.1f}% YES "
        f"(σ={sigma:.1f}°F/{result.fallback_level}, bias={bias:+.1f}°F). "
        f"{'YES' if direction == 'YES' else 'NO'} price ({src}) implied "
        f"{(side_price or 0) * 100:.1f}%. "
        f"Edge: +{(edge or 0) * 100:.1f}pp. Confidence: {confidence}. "
        f"Station: {station.station_name} (verified)."
    )

    # ── Executability ─────────────────────────────────────────────────────────
    pos_check = calculate_position(settings["stake"], side_price or 0.0)
    executable = _is_executable(pos_check.get("quantity", 0.0), side_price or 0.0)

    return {
        "action": direction,
        "exclusion_flag": None,
        "skip_reason": None,
        "direction": direction,
        "ec_yes_probability": ec_yes_prob,
        "ec_side_probability": ec_side_prob,
        "market_yes_probability": mkt_yes_prob,
        "side_market_price": side_price,
        "price_source": src,
        "edge_pct_points": round((edge or 0) * 100, 4),
        "decision_explanation": explanation,
        "warnings": warnings,
        "sigma_used": result.sigma_used,
        "bias_correction": result.bias_correction,
        "fallback_level": result.fallback_level,
        "calibration_adj": result.calibration_adj,
        "raw_ec_probability": result.raw_ec_probability,
        # V2.1-specific
        "station_verified": True,
        "station_lat": station_lat,
        "station_lon": station_lon,
        "quote_bid": quote_bid,
        "quote_ask": quote_ask,
        "quote_timestamp": market.collection_timestamp,
        "est_available_qty": getattr(market, "open_interest", None),
        "is_executable": executable,
        "consensus_guard_triggered": consensus_guard_triggered,
    }


def _skip_v21(
    reason: str | None,
    ec_yes: float | None,
    mkt_yes: float | None,
    warnings: list,
    extra: dict | None = None,
) -> dict:
    base = {
        "action": "SKIP",
        "exclusion_flag": None,
        "skip_reason": reason,
        "direction": None,
        "ec_yes_probability": ec_yes,
        "ec_side_probability": None,
        "market_yes_probability": mkt_yes,
        "side_market_price": None,
        "price_source": None,
        "edge_pct_points": None,
        "decision_explanation": f"Skipped: {reason}",
        "warnings": warnings,
        "sigma_used": None,
        "bias_correction": 0.0,
        "fallback_level": "fixed_table",
        "calibration_adj": 1.0,
        "raw_ec_probability": None,
        "station_verified": None,
        "station_lat": None,
        "station_lon": None,
        "quote_bid": None,
        "quote_ask": None,
        "quote_timestamp": None,
        "est_available_qty": None,
        "is_executable": None,
        "consensus_guard_triggered": False,
    }
    if extra:
        base.update(extra)
    return base


# ── Create or log a single v2.1 paper trade ───────────────────────────────────

async def maybe_create_paper_trade_v21(
    session: AsyncSession,
    market: KalshiMarket,
    snap: PredictionSnapshot,
    settings: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Create a v2.1 PaperTrade (or V2_EXCLUDED log entry) for the market/snapshot.
    Returns {"created": bool, "excluded": bool, "direction": str|None}.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Duplicate check
    existing_q = await session.execute(
        select(PaperTrade).where(
            PaperTrade.market_ticker == market.ticker,
            PaperTrade.strategy_version == STRATEGY_VERSION,
        )
    )
    if existing_q.scalar_one_or_none() is not None:
        return {"created": False, "excluded": False, "direction": None, "skip_reason": "duplicate"}

    decision = await decide_trade_v21(snap, market, settings, session, now=now)

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
            # V2.1 fields
            station_verified=decision["station_verified"],
            station_lat=decision["station_lat"],
            station_lon=decision["station_lon"],
            quote_bid=decision["quote_bid"],
            quote_ask=decision["quote_ask"],
            quote_timestamp=decision["quote_timestamp"],
            est_available_qty=decision["est_available_qty"],
            is_executable=decision["is_executable"],
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
        # V2.1 fields
        station_verified=decision["station_verified"],
        station_lat=decision["station_lat"],
        station_lon=decision["station_lon"],
        quote_bid=decision["quote_bid"],
        quote_ask=decision["quote_ask"],
        quote_timestamp=decision["quote_timestamp"],
        est_available_qty=decision["est_available_qty"],
        is_executable=decision["is_executable"],
    )
    session.add(trade)
    await session.flush()

    logger.info(
        "V2.1 paper trade created: %s %s @ %.4f (σ=%.1f/%s, bias=%+.1f, edge=%.1fpp, executable=%s)",
        decision["direction"], market.ticker, side_price,
        decision["sigma_used"] or 0,
        decision["fallback_level"],
        decision["bias_correction"] or 0,
        decision["edge_pct_points"] or 0,
        decision["is_executable"],
    )
    return {"created": True, "excluded": False, "direction": decision["direction"]}


# ── Batch runner ──────────────────────────────────────────────────────────────

async def run_paper_trading_v21(session: AsyncSession) -> dict[str, int]:
    """
    Review all recently-analyzed markets and create v2.1 paper trades.
    Called after every collection run (after v2.0's run_paper_trading_v2).

    Returns: {"candidates", "created", "excluded", "skipped", "errors"}
    """
    stats: dict[str, int] = {
        "candidates": 0, "created": 0,
        "excluded": 0, "skipped": 0, "errors": 0,
    }

    settings = await get_v21_settings(session)
    if not settings["enabled"]:
        logger.info("V2.1 paper trading disabled — skipping.")
        return stats

    now = datetime.now(timezone.utc)

    # Latest snapshot per ticker
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

    for snap in latest_snaps:
        market = market_map.get(snap.market_ticker)
        if market is None:
            continue

        stats["candidates"] += 1
        try:
            result = await maybe_create_paper_trade_v21(session, market, snap, settings, now=now)
            if result["created"]:
                stats["created"] += 1
            elif result.get("excluded"):
                stats["excluded"] += 1
            else:
                stats["skipped"] += 1
        except Exception as exc:
            stats["errors"] += 1
            logger.warning("V2.1 paper trade error for %s: %s", snap.market_ticker, exc)

    await session.commit()
    logger.info(
        "V2.1 paper trading: %d candidates, %d created, %d excluded, %d skipped, %d errors",
        stats["candidates"], stats["created"],
        stats["excluded"], stats["skipped"], stats["errors"],
    )
    return stats


# ── Consensus guard backtest ──────────────────────────────────────────────────

async def consensus_guard_backtest(
    session: AsyncSession,
    threshold: float = CONSENSUS_GUARD_THRESHOLD,
) -> dict:
    """
    Compute the impact of applying the consensus guard retroactively to all
    settled V2.0 trades.

    Returns a report dict with:
      threshold, total_settled, guard_would_block, guard_would_allow,
      wins_avoided_by_guard, losses_avoided_by_guard,
      pnl_with_guard, pnl_without_guard, roi_delta_pp,
      note
    """
    v2_settled_q = await session.execute(
        select(PaperTrade).where(
            PaperTrade.strategy_version == "v2.0",
            PaperTrade.status == "SETTLED",
        )
    )
    trades = v2_settled_q.scalars().all()

    total = len(trades)
    guard_block = 0
    guard_allow = 0
    wins_avoided = 0
    losses_avoided = 0
    pnl_with = 0.0
    pnl_without = 0.0

    for t in trades:
        mkt_yes = t.market_yes_probability
        direction = t.direction
        pnl = t.profit_loss or 0.0
        won = (t.outcome == "WIN") if t.outcome else (pnl > 0)

        # Determine whether the guard would block this trade
        if mkt_yes is not None:
            if direction == "YES":
                consensus_against = 1.0 - mkt_yes
            else:
                consensus_against = mkt_yes
            blocked = consensus_against >= threshold
        else:
            blocked = False

        if blocked:
            guard_block += 1
            if won:
                wins_avoided += 1
            else:
                losses_avoided += 1
        else:
            guard_allow += 1
            pnl_with += pnl

        pnl_without += pnl

    # ROI delta: how much better/worse is guarded ROI vs unguarded?
    # Expressed in pp of total capital deployed
    total_stake_without = sum(t.stake or 0 for t in trades)
    total_stake_with = sum(
        t.stake or 0 for t in trades
        if not (
            t.market_yes_probability is not None and (
                (1.0 - t.market_yes_probability if t.direction == "YES" else t.market_yes_probability)
                >= threshold
            )
        )
    )
    roi_without = (pnl_without / total_stake_without * 100) if total_stake_without > 0 else 0.0
    roi_with = (pnl_with / total_stake_with * 100) if total_stake_with > 0 else 0.0

    return {
        "threshold": threshold,
        "total_settled": total,
        "guard_would_block": guard_block,
        "guard_would_allow": guard_allow,
        "wins_avoided_by_guard": wins_avoided,
        "losses_avoided_by_guard": losses_avoided,
        "pnl_with_guard": round(pnl_with, 4),
        "pnl_without_guard": round(pnl_without, 4),
        "roi_with_guard_pct": round(roi_with, 2),
        "roi_without_guard_pct": round(roi_without, 2),
        "roi_delta_pp": round(roi_with - roi_without, 2),
        "note": (
            "Backtest only. Guard is currently DISABLED by default. "
            "Enable via AppSetting paper_trading_v21.consensus_guard_enabled=true."
        ),
    }
