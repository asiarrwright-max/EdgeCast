"""
V3 Paper Trading — Phase 3
==========================
Creates V3PaperTrade rows from V3PredictionSnapshot data.
Called from the collection job (Step 5g), after V3 predictions run.
Gated by the ``v3.paper_trading_enabled`` feature flag.

Guards (identical to V2.1 where applicable):
  1. Station verified + NWS-settlement source
  2. Quote freshness ≤ 4 h
  3. Market still active
  4. Edge ≥ min_edge_pct (default 10pp)
  5. V3PaperTrade unique constraint: one trade per (market_ticker, "v3.0")

Isolation
---------
* Never reads/writes paper_trades (V1/V2/V2.1).
* Never modifies V3PredictionSnapshot rows (read-only here).
* All V3 trades go to v3_paper_trades.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import AppSetting, KalshiMarket
from app.models_v3 import V3PaperTrade, V3PredictionSnapshot
from app.services.paper_trading import (
    _select_yes_price,
    _select_no_price,
    calculate_position,
)
from app.services.eligibility import apply_correlated_limit, assess_trade_eligibility, REASON_STALE_QUOTE
from app.services.paper_trading_v21 import (
    STALE_QUOTE_SECONDS,
    _is_executable,
)
from app.services.settlement_stations import get_station
from app.services.v3_flags import get_v3_flag
from app.services.v3_jit_quote_audit import perform_jit_quote_shadow

logger = logging.getLogger(__name__)

STRATEGY_VERSION = "v3.0"

# ---------------------------------------------------------------------------
# Defaults — separate namespace from V2.1 so the two can be tuned independently
# ---------------------------------------------------------------------------

DEFAULT_V3_SETTINGS: dict[str, Any] = {
    "enabled":      True,
    "min_edge_pct": 10.0,   # minimum edge in percentage points
    "stake":        10.0,   # dollars per trade
}

_V3_SETTING_KEYS: dict[str, tuple[str, type]] = {
    "paper_trading_v3.enabled":      ("enabled",      bool),
    "paper_trading_v3.min_edge_pct": ("min_edge_pct", float),
    "paper_trading_v3.stake":        ("stake",         float),
}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

async def get_v3_pt_settings(session: AsyncSession) -> dict[str, Any]:
    """Return effective V3 paper-trading settings (DB overrides over defaults)."""
    keys = list(_V3_SETTING_KEYS.keys())
    result = await session.execute(
        select(AppSetting).where(AppSetting.key.in_(keys))
    )
    db_rows = {r.key: r.value for r in result.scalars().all()}
    settings = dict(DEFAULT_V3_SETTINGS)
    for db_key, (field_name, cast) in _V3_SETTING_KEYS.items():
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


# ---------------------------------------------------------------------------
# Pre-flight checks (shared with V2.1 logic)
# ---------------------------------------------------------------------------

def _check_station_nws(city: str | None) -> tuple[bool, str | None]:
    """
    Hard-block only on nws_settlement=False (e.g. Washington DC).
    Unverified NWS cities return True and are demoted to RESEARCH_ONLY
    by the eligibility engine.
    """
    if not city:
        return False, "City not identified"
    station = get_station(city)
    if station is None:
        return False, f"No settlement station for '{city}'"
    if not getattr(station, "nws_settlement", True):
        return False, f"Non-NWS settlement source for '{city}' — V3 calibration invalid"
    return True, None


# ---------------------------------------------------------------------------
# Core decision
# ---------------------------------------------------------------------------

def _decide_v3(
    snap: V3PredictionSnapshot,
    market: KalshiMarket,
    settings: dict,
    now: datetime,
) -> dict[str, Any]:
    """
    Evaluate one market and return a decision dict.

    Returns action = "YES" | "NO" | "SKIP".

    Eligibility changes:
      - Station guard: only hard-blocks on nws_settlement=False; unverified NWS
        cities pass through and are demoted to RESEARCH_ONLY by assess_trade_eligibility.
      - Quote freshness is no longer a hard-stop; stale-quote trades are
        classified RESEARCH_ONLY so a row is still stored.
      - Returns eligibility_status, eligibility_reason, quote_age_seconds,
        plus city / target_settlement_date_str / settlement_timezone / weather_variable
        for the batch-level correlated-exposure limit.
    """
    ec_yes = snap.ec_probability
    if ec_yes is None:
        return {"action": "SKIP", "skip_reason": "V3 probability unavailable",
                "direction": None}

    # Hard-stop: NWS settlement source only
    station_ok, station_reason = _check_station_nws(market.city)
    if not station_ok:
        return {"action": "SKIP", "skip_reason": station_reason, "direction": None}

    station          = get_station(market.city)  # guaranteed non-None here
    station_lat      = getattr(station, "lat", None)
    station_lon      = getattr(station, "lon", None)
    station_tz       = getattr(station, "timezone", "UTC")
    station_verified = getattr(station, "verified", False)

    if market.status != "active":
        return {"action": "SKIP", "skip_reason": "Market no longer active", "direction": None}

    # Edge calculation — reuse V2.1 price selectors for consistency
    ec_no    = round(1.0 - ec_yes, 4)
    min_edge = settings["min_edge_pct"] / 100.0

    yes_price, yes_src = _select_yes_price(market)
    no_price,  no_src  = _select_no_price(market)

    yes_edge = (ec_yes - yes_price) if yes_price is not None else None
    no_edge  = (ec_no  - no_price)  if no_price  is not None else None

    yes_qualifies = yes_edge is not None and yes_edge >= min_edge
    no_qualifies  = no_edge  is not None and no_edge  >= min_edge

    if not yes_qualifies and not no_qualifies:
        candidates = [e for e in [yes_edge, no_edge] if e is not None]
        best = max(candidates) if candidates else None
        best_str = f"{best * 100:.1f}pp" if best is not None else "N/A"
        return {
            "action": "SKIP",
            "skip_reason": (
                f"Edge below threshold ({best_str} < {settings['min_edge_pct']:.1f}pp)"
            ),
            "direction": None,
        }

    # Pick direction with the larger edge
    if yes_qualifies and no_qualifies:
        direction = "YES" if (yes_edge or 0) >= (no_edge or 0) else "NO"
    elif yes_qualifies:
        direction = "YES"
    else:
        direction = "NO"

    if direction == "YES":
        side_price, src = yes_price, yes_src
        ec_side_prob    = ec_yes
        edge            = yes_edge
        quote_bid       = market.yes_bid
        quote_ask       = market.yes_ask
    else:
        side_price, src = no_price, no_src
        ec_side_prob    = ec_no
        edge            = no_edge
        quote_bid       = getattr(market, "no_bid", None)
        quote_ask       = getattr(market, "no_ask", None)

    edge_pp      = round((edge or 0) * 100, 4)
    mkt_yes_prob = snap.market_probability

    # Market close audit fields
    market_close_ts = getattr(market, "close_time", None)
    decision_ts = now
    minutes_to_close: float | None = None
    if market_close_ts is not None:
        if market_close_ts.tzinfo is None:
            market_close_ts = market_close_ts.replace(tzinfo=timezone.utc)
        minutes_to_close = (market_close_ts - now).total_seconds() / 60

    # expected_settlement_timestamp is only set from an actual Kalshi settlement field.
    # target_date is a date-only string (for display/grouping) and must not be fabricated
    # into a timestamp here.  Leave NULL until Kalshi provides explicit settlement metadata.
    expected_settle_ts = None

    # Eligibility engine (Guards 1–5, 7–8)
    quote_ts = getattr(market, "collection_timestamp", None)
    elig_status, elig_reason, quote_age_s = assess_trade_eligibility(
        contract_type=snap.contract_type,
        target_settlement_date_str=market.target_date,
        settlement_timezone=station_tz,
        now=now,
        side_market_price=side_price,
        edge_pct_points=edge_pp,
        station_verified=station_verified,
        direction=direction,
        quote_timestamp=quote_ts,
        quote_ask=quote_ask,
        market_close_timestamp=market_close_ts,
    )

    # OFFICIAL → may be executable; RESEARCH_ONLY → never executable
    executable = (elig_status == "OFFICIAL") and _is_executable(
        calculate_position(settings["stake"], side_price or 0.0).get("quantity", 0.0),
        side_price or 0.0,
    )

    return {
        "action":              direction,
        "skip_reason":         None,
        "direction":           direction,
        "ec_yes_probability":  ec_yes,
        "ec_side_probability": ec_side_prob,
        "market_yes_probability": mkt_yes_prob,
        "side_market_price":   side_price,
        "price_source":        src,
        "edge_pct_points":     edge_pp,
        "quote_bid":           quote_bid,
        "quote_ask":           quote_ask,
        "is_executable":       executable,
        "station_lat":         station_lat,
        "station_lon":         station_lon,
        "station_verified":    station_verified,
        "decision_explanation": (
            f"[v3.0] V3 probability {ec_yes * 100:.1f}% YES "
            f"(σ={snap.final_sigma:.1f}°F/lvl{snap.fallback_level_used}, "
            f"bias_applied={snap.bias_applied}). "
            f"{'YES' if direction == 'YES' else 'NO'} price ({src}) implied "
            f"{(side_price or 0) * 100:.1f}%. "
            f"Edge: +{edge_pp:.1f}pp. "
            f"Station: {station.station_name} ({'verified' if station_verified else 'unverified'})."
        ),
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


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

async def run_paper_trading_v3(
    batch_id: str | None = None,
    comparison_snapshot_ids: dict[str, str] | None = None,
) -> dict[str, int]:
    """
    Review all recent V3PredictionSnapshot rows and create V3PaperTrade rows.
    Called after run_v3_predictions() in the collection job.

    ``batch_id`` and ``comparison_snapshot_ids`` are supplied by the collector
    to link each new V3PaperTrade to the same ComparisonSnapshot shared with
    V2.1/V2.2, enabling strictly-paired cross-strategy analysis.

    Opens its own isolated AsyncSession so the entire batch is committed
    atomically.  If an exception escapes before the final commit the session
    is closed without committing, leaving zero partial trade rows written.
    Because run_v3_predictions() commits first (its own isolated session),
    this session sees all V3 snapshot data via READ COMMITTED.

    Returns: {"status", "candidates", "created", "skipped", "errors"}
    """
    stats: dict = {"candidates": 0, "created": 0, "skipped": 0, "errors": 0}

    async with AsyncSessionLocal() as session:
        return await _run_paper_trading_v3_inner(
            session, stats,
            batch_id=batch_id,
            comparison_snapshot_ids=comparison_snapshot_ids,
        )


async def _run_paper_trading_v3_inner(
    session,
    stats: dict,
    batch_id: str | None = None,
    comparison_snapshot_ids: dict[str, str] | None = None,
) -> dict[str, int]:
    comp_ids: dict[str, str] = comparison_snapshot_ids or {}
    if not await get_v3_flag(session, "v3.paper_trading_enabled"):
        logger.info("V3 paper trading disabled — skipping.")
        return {"status": "disabled", **stats}

    settings = await get_v3_pt_settings(session)
    if not settings["enabled"]:
        logger.info("V3 paper trading disabled via settings — skipping.")
        return {"status": "disabled", **stats}

    now = datetime.now(timezone.utc)

    # Latest PENDING V3 snapshot per ticker.
    # SUPERSEDED rows are excluded so paper trading only considers the most
    # recent prediction for each ticker (the predictor marks older rows
    # SUPERSEDED before creating new PENDING ones in the same session).
    all_v3_q = await session.execute(
        select(V3PredictionSnapshot)
        .where(V3PredictionSnapshot.trade_decision == "PENDING")
        .order_by(
            V3PredictionSnapshot.market_ticker,
            V3PredictionSnapshot.id.desc(),
        )
    )
    all_v3 = all_v3_q.scalars().all()

    seen: set[str] = set()
    latest_v3: list[V3PredictionSnapshot] = []
    for s in all_v3:
        if s.market_ticker not in seen:
            seen.add(s.market_ticker)
            latest_v3.append(s)

    # Active markets map
    markets_q = await session.execute(
        select(KalshiMarket).where(KalshiMarket.status == "active")
    )
    market_map: dict[str, KalshiMarket] = {
        m.ticker: m for m in markets_q.scalars().all()
    }

    # Existing V3 paper trades (to enforce unique-per-ticker)
    existing_q = await session.execute(
        select(V3PaperTrade.market_ticker).where(
            V3PaperTrade.strategy_version == STRATEGY_VERSION
        )
    )
    existing_tickers: set[str] = {row[0] for row in existing_q}

    # ── Phase 1: evaluate all candidates (no trade rows written) ─────────────
    # list of (v3_snap, market, decision, comp_id)
    evaluations: list[tuple] = []

    for v3_snap in latest_v3:
        market = market_map.get(v3_snap.market_ticker)
        if market is None:
            continue

        if v3_snap.analysis_status != "ok" or v3_snap.ec_probability is None:
            continue

        if v3_snap.market_ticker in existing_tickers:
            stats["skipped"] += 1
            continue

        stats["candidates"] += 1

        try:
            decision = _decide_v3(v3_snap, market, settings, now)
            if decision["action"] == "SKIP":
                stats["skipped"] += 1
                continue
            evaluations.append((v3_snap, market, decision, comp_ids.get(v3_snap.market_ticker)))
        except Exception as exc:
            stats["errors"] += 1
            logger.warning("V3 decision error for %s: %s", v3_snap.market_ticker, exc, exc_info=True)

    # ── Phase 2: correlated-exposure limit (Guard 6) ──────────────────────────
    official_decisions = [
        d for (_, _, d, _) in evaluations
        if d.get("eligibility_status") == "OFFICIAL"
    ]
    if official_decisions:
        apply_correlated_limit(official_decisions)

    # ── Phase 3: create trade rows ────────────────────────────────────────────
    for v3_snap, market, decision, comp_id in evaluations:
        try:
            side_price = decision["side_market_price"] or 0.0
            if side_price <= 0:
                stats["skipped"] += 1
                continue

            stake = settings["stake"]
            pos   = calculate_position(stake, side_price)

            trade = V3PaperTrade(
                market_ticker=market.ticker,
                city=market.city,
                weather_variable=v3_snap.settlement_variable,
                contract_type=v3_snap.contract_type,
                target_settlement_date=market.target_date,
                strategy_version=STRATEGY_VERSION,
                v3_snapshot_id=v3_snap.id,
                comparison_group_id=v3_snap.comparison_group_id,
                comparison_snapshot_id=comp_id,
                collection_batch_id=batch_id,
                direction=decision["direction"],
                ec_yes_probability=decision["ec_yes_probability"],
                ec_side_probability=decision["ec_side_probability"],
                market_yes_probability=decision["market_yes_probability"],
                side_market_price=side_price,
                edge_pct_points=decision["edge_pct_points"],
                lead_time_days=v3_snap.lead_time_days,
                historical_bias_adj=v3_snap.historical_bias_adj,
                historical_sigma=v3_snap.historical_sigma,
                final_bias=v3_snap.final_bias,
                final_sigma=v3_snap.final_sigma,
                fallback_level_used=v3_snap.fallback_level_used,
                hist_sample_count=v3_snap.hist_sample_count,
                effective_hist_n=v3_snap.effective_hist_n,
                v3_forward_count=v3_snap.v3_forward_count,
                stake=pos["stake"],
                quantity=pos["quantity"],
                is_executable=decision["is_executable"],
                station_lat=decision.get("station_lat"),
                station_lon=decision.get("station_lon"),
                station_verified=decision.get("station_verified", False),
                eligibility_status=decision.get("eligibility_status"),
                eligibility_reason=decision.get("eligibility_reason"),
                quote_age_seconds=decision.get("quote_age_seconds"),
                market_close_timestamp=decision.get("market_close_timestamp"),
                expected_settlement_timestamp=decision.get("expected_settlement_timestamp"),
                decision_timestamp=decision.get("decision_timestamp"),
                minutes_to_market_close=decision.get("minutes_to_market_close"),
                settlement_timezone=decision.get("settlement_timezone"),
                status="OPEN",
                decision_explanation=decision["decision_explanation"],
            )
            session.add(trade)
            await session.flush()
            existing_tickers.add(v3_snap.market_ticker)
            stats["created"] += 1

            # ── JIT quote shadow audit (Phase 3B) ────────────────────────────
            # Fire-and-forget shadow for stale/missing-quote RESEARCH_ONLY trades.
            # The JIT result MUST NOT affect trade fields, classification, or
            # eligibility.  Uses a separate DB session (fail-closed).
            if decision.get("eligibility_reason") == REASON_STALE_QUOTE:
                import asyncio as _asyncio
                _jit_trade_id = trade.id  # captured after flush
                _asyncio.ensure_future(
                    perform_jit_quote_shadow(
                        market_ticker=market.ticker,
                        direction=decision.get("direction"),
                        collection_quote_ask=decision.get("quote_ask"),
                        collection_quote_age_seconds=decision.get("quote_age_seconds"),
                        decision_timestamp=decision.get("decision_timestamp"),
                        collection_batch_id=batch_id,
                        contract_type=v3_snap.contract_type,
                        target_settlement_date_str=market.target_date,
                        settlement_timezone=decision.get("settlement_timezone"),
                        side_market_price=decision.get("side_market_price"),
                        edge_pct_points=decision.get("edge_pct_points"),
                        station_verified=decision.get("station_verified"),
                        market_close_timestamp=decision.get("market_close_timestamp"),
                    )
                )
                logger.debug(
                    "JIT quote shadow scheduled for stale-quote RESEARCH_ONLY trade: %s",
                    market.ticker,
                )

            logger.info(
                "V3 paper trade created: %s %s @ %.4f "
                "(σ=%.1f/lvl%s, bias_applied=%s, edge=%.1fpp, exec=%s, elig=%s)",
                decision["direction"], market.ticker, side_price,
                v3_snap.final_sigma or 0, v3_snap.fallback_level_used,
                v3_snap.bias_applied,
                decision["edge_pct_points"] or 0,
                decision["is_executable"],
                decision.get("eligibility_status"),
            )

        except Exception as exc:
            stats["errors"] += 1
            logger.warning(
                "V3 paper trade error for %s: %s",
                v3_snap.market_ticker, exc, exc_info=True,
            )

    await session.commit()
    logger.info(
        "V3 paper trading: %d candidates, %d created, %d skipped, %d errors",
        stats["candidates"], stats["created"], stats["skipped"], stats["errors"],
    )
    return {"status": "ok", **stats}
