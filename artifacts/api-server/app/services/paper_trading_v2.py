"""
EdgeCast Paper Trading Service — Strategy v2 Shadow Runner
===========================================================
Runs a shadow paper-trading strategy (v2.0) alongside v1 on the same markets.
No real trades are placed.  No Kalshi trading credentials are used.

V2 differences from v1
-----------------------
- Uses probability_engine_v2 (learned σ, bias correction, calibration).
- Additional quality rules: 1-cent contracts, zero-volume, and no-liquidity
  markets create V2_EXCLUDED trades (visible in the research view) rather than
  silently being skipped.
- Stores sigma_used, bias_correction, fallback_level, calibration_adj on each
  PaperTrade for transparency.

V2 settings
-----------
Stored under paper_trading_v2.* keys in AppSetting.  Defaults mirror v1 but
are independent — changing v1 settings does not affect v2.

Isolation guarantee
-------------------
One PaperTrade per (market_ticker, "v2.0").  V2 trades never update v1 trades
and v1 trades never update v2 trades.  Both strategies' trades exist side-by-
side in the paper_trades table, distinguished by strategy_version.
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
    get_paper_trade_analytics,
    get_calibration_report,
)

logger = logging.getLogger(__name__)

STRATEGY_VERSION = "v2.0"

# V2 extra exclusion flags
FLAG_V2_BELOW_MIN_PRICE = "v2_below_min_price"
FLAG_V2_ZERO_VOLUME = "v2_zero_volume"
FLAG_V2_NO_LIQUIDITY = "v2_no_liquidity"

V2_FLAG_DESCRIPTIONS: dict[str, str] = {
    FLAG_V2_BELOW_MIN_PRICE: (
        "Entry price is at or below 1 cent — v2 excludes these contracts as "
        "near-certain losses with minimal upside."
    ),
    FLAG_V2_ZERO_VOLUME: (
        "Reported trading volume is zero — v2 excludes zero-volume markets as "
        "likely illiquid."
    ),
    FLAG_V2_NO_LIQUIDITY: (
        "No bid or ask prices are recorded — v2 requires at least one side to "
        "have a price."
    ),
}

# ── Default settings ──────────────────────────────────────────────────────────
DEFAULT_V2_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "min_edge_pct": 10.0,
    "min_confidence": "High",
    "stake": 10.0,
}

_V2_SETTING_KEYS = {
    "paper_trading_v2.enabled":        ("enabled",        bool),
    "paper_trading_v2.min_edge_pct":   ("min_edge_pct",   float),
    "paper_trading_v2.min_confidence": ("min_confidence", str),
    "paper_trading_v2.stake":          ("stake",          float),
}

_CONFIDENCE_RANK = {"Very High": 2, "High": 1, "Medium": 0, "Low": -1, "Very Low": -2}


# ── Settings helpers ──────────────────────────────────────────────────────────

async def get_v2_settings(session: AsyncSession) -> dict[str, Any]:
    """Return effective v2 settings (DB overrides applied over defaults)."""
    keys = list(_V2_SETTING_KEYS.keys())
    result = await session.execute(
        select(AppSetting).where(AppSetting.key.in_(keys))
    )
    db_rows = {r.key: r.value for r in result.scalars().all()}

    settings = dict(DEFAULT_V2_SETTINGS)
    for db_key, (field_name, cast) in _V2_SETTING_KEYS.items():
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


# ── V2 quality exclusion checks ───────────────────────────────────────────────

def _v2_exclusion_flag(market: KalshiMarket, side_price: float | None) -> str | None:
    """
    Return a V2_EXCLUDED flag name if this market fails v2-specific quality
    rules, or None if the market passes.
    """
    # No liquidity at all
    has_prices = any(
        getattr(market, f, None) is not None
        for f in ("yes_bid", "yes_ask", "no_bid", "no_ask")
    )
    if not has_prices:
        return FLAG_V2_NO_LIQUIDITY

    # Zero volume
    vol = getattr(market, "volume", None)
    if vol is not None and vol == 0:
        return FLAG_V2_ZERO_VOLUME

    # 1-cent or below entry price
    if side_price is not None and 0 < side_price <= 0.01:
        return FLAG_V2_BELOW_MIN_PRICE

    return None


# ── Core decision (v2) ───────────────────────────────────────────────────────

async def decide_trade_v2(
    snap: PredictionSnapshot,
    market: KalshiMarket,
    settings: dict[str, Any],
    session: AsyncSession,
) -> dict[str, Any]:
    """
    Evaluate one market using the v2 probability engine and return a decision.

    Keys in the returned dict (superset of v1 decide_trade):
      action: "YES" | "NO" | "SKIP" | "EXCLUDED"
      skip_reason / exclusion_flag
      direction, ec_yes_probability, ec_side_probability, market_yes_probability
      side_market_price, price_source, edge_pct_points
      decision_explanation, warnings
      sigma_used, bias_correction, fallback_level, calibration_adj
      raw_ec_probability
    """
    from app.services.probability_engine_v2 import run_analysis_v2

    warnings: list[str] = [
        "Simulation only. No real trades are placed.",
        "Results may overstate real-world performance: fees, spreads, liquidity, "
        "slippage, and fill availability are not modelled.",
    ]

    if snap.analysis_status != "supported":
        return _skip_v2("Unsupported market", None, snap.market_probability, warnings)

    if snap.forecast_value is None:
        return _skip_v2("Forecast unavailable", None, snap.market_probability, warnings)

    if market.status != "active":
        return _skip_v2("Market already closed", None, snap.market_probability, warnings)

    # ── Run v2 analysis ───────────────────────────────────────────────────────
    result = await run_analysis_v2(
        title=market.title or "",
        subtitle=market.subtitle,
        city=market.city,
        target_date_str=market.target_date,
        weather_variable=snap.settlement_variable,
        operator=snap.settlement_operator,
        threshold=snap.settlement_threshold,
        parse_confidence="high",   # already filtered to supported markets
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
        return _skip_v2("V2 probability unavailable", ec_yes_prob, mkt_yes_prob, warnings)

    if mkt_yes_prob is None:
        return _skip_v2("Market price unavailable", ec_yes_prob, mkt_yes_prob, warnings)

    min_conf = settings["min_confidence"]
    if not _is_confidence_sufficient(confidence, min_conf):
        return _skip_v2(
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
        return _skip_v2(
            f"Edge below threshold ({best_str} < {settings['min_edge_pct']:.1f}pp)",
            ec_yes_prob, mkt_yes_prob, warnings,
        )

    # Choose higher-edge direction
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
    else:
        side_price, src = no_price, no_src
        ec_side_prob = ec_no_prob
        edge = no_edge

    # ── V2 exclusion checks (after direction/price selection) ─────────────────
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
            "decision_explanation": (
                f"[v2 excluded: {excl_flag}] {result.explanation}"
            ),
            "warnings": warnings,
            "sigma_used": result.sigma_used,
            "bias_correction": result.bias_correction,
            "fallback_level": result.fallback_level,
            "calibration_adj": result.calibration_adj,
            "raw_ec_probability": result.raw_ec_probability,
        }

    explanation = (
        f"[v2] EdgeCast v2 estimated {ec_yes_prob * 100:.1f}% YES probability "
        f"(σ={result.sigma_used:.1f}°F/{result.fallback_level}, "
        f"bias={result.bias_correction:+.1f}°F). "
        f"{'YES' if direction == 'YES' else 'NO'} price ({src}) implied "
        f"{(side_price or 0) * 100:.1f}%. "
        f"Edge: +{(edge or 0) * 100:.1f}pp. "
        f"Confidence: {confidence}."
    )

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
    }


def _skip_v2(reason: str, ec_yes: float | None, mkt_yes: float | None, warnings: list) -> dict:
    return {
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
    }


# ── Create or log a single v2 paper trade ────────────────────────────────────

async def maybe_create_paper_trade_v2(
    session: AsyncSession,
    market: KalshiMarket,
    snap: PredictionSnapshot,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """
    Create a v2 PaperTrade (or V2_EXCLUDED log entry) for the market/snapshot.
    Returns {"created": bool, "excluded": bool, "direction": str|None}.
    """
    # Duplicate check
    existing_q = await session.execute(
        select(PaperTrade).where(
            PaperTrade.market_ticker == market.ticker,
            PaperTrade.strategy_version == STRATEGY_VERSION,
        )
    )
    if existing_q.scalar_one_or_none() is not None:
        return {"created": False, "excluded": False, "direction": None, "skip_reason": "duplicate"}

    decision = await decide_trade_v2(snap, market, settings, session)

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
            stake=0.0,   # no money committed for excluded trades
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
    )
    session.add(trade)
    await session.flush()

    logger.info(
        "V2 paper trade created: %s %s @ %.4f (σ=%.1f/%s, bias=%+.1f, edge=%.1fpp)",
        decision["direction"], market.ticker, side_price,
        decision["sigma_used"] or 0,
        decision["fallback_level"],
        decision["bias_correction"],
        decision["edge_pct_points"] or 0,
    )
    return {"created": True, "excluded": False, "direction": decision["direction"]}


# ── Batch runner ─────────────────────────────────────────────────────────────

async def run_paper_trading_v2(session: AsyncSession) -> dict[str, int]:
    """
    Review all recently-analyzed markets and create v2 paper trades.
    Called after every collection run (after v1's run_paper_trading).

    Returns: {"candidates", "created", "excluded", "skipped", "errors"}
    """
    stats: dict[str, int] = {
        "candidates": 0, "created": 0,
        "excluded": 0, "skipped": 0, "errors": 0,
    }

    settings = await get_v2_settings(session)
    if not settings["enabled"]:
        logger.info("V2 paper trading disabled — skipping.")
        return stats

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
            result = await maybe_create_paper_trade_v2(session, market, snap, settings)
            if result["created"]:
                stats["created"] += 1
            elif result.get("excluded"):
                stats["excluded"] += 1
            else:
                stats["skipped"] += 1
        except Exception as exc:
            stats["errors"] += 1
            logger.warning("V2 paper trade error for %s: %s", snap.market_ticker, exc)

    await session.commit()
    logger.info(
        "V2 paper trading: %d candidates, %d created, %d excluded, %d skipped, %d errors",
        stats["candidates"], stats["created"],
        stats["excluded"], stats["skipped"], stats["errors"],
    )
    return stats


# ── Strategy agreement analysis ───────────────────────────────────────────────

async def get_strategy_agreement(session: AsyncSession) -> dict:
    """
    Compare v1 and v2 open/settled trades on the same markets.

    Returns:
      both_trade          — markets where both v1 and v2 have non-excluded trades
      only_v1             — markets with v1 trade but no v2 active trade
      only_v2             — markets with v2 trade but no v1 active trade
      different_sides     — both traded but on opposite directions
      same_sides          — both traded on same direction
      prob_divergence_gt_10pp — |v1_ec_prob − v2_ec_prob| > 0.10
      samples: list of sample agreement rows (up to 20)
    """
    v1_q = await session.execute(
        select(PaperTrade).where(
            PaperTrade.strategy_version == "v1.0",
            PaperTrade.status.in_(["OPEN", "SETTLED", "PENDING_SETTLEMENT"]),
        )
    )
    v1_trades = {t.market_ticker: t for t in v1_q.scalars().all()}

    v2_q = await session.execute(
        select(PaperTrade).where(
            PaperTrade.strategy_version == STRATEGY_VERSION,
            PaperTrade.status.in_(["OPEN", "SETTLED", "PENDING_SETTLEMENT"]),
        )
    )
    v2_trades = {t.market_ticker: t for t in v2_q.scalars().all()}

    all_tickers = set(v1_trades) | set(v2_trades)

    both_trade = 0
    only_v1 = 0
    only_v2 = 0
    different_sides = 0
    same_sides = 0
    prob_divergence_gt_10pp = 0
    samples: list[dict] = []

    for ticker in sorted(all_tickers):
        t1 = v1_trades.get(ticker)
        t2 = v2_trades.get(ticker)

        has_v1 = t1 is not None
        has_v2 = t2 is not None

        if has_v1 and has_v2:
            both_trade += 1
            if t1.direction != t2.direction:
                different_sides += 1
            else:
                same_sides += 1
            p1 = t1.ec_yes_probability
            p2 = t2.ec_yes_probability
            if p1 is not None and p2 is not None and abs(p1 - p2) > 0.10:
                prob_divergence_gt_10pp += 1
            if len(samples) < 20:
                samples.append({
                    "ticker": ticker,
                    "v1Direction": t1.direction,
                    "v2Direction": t2.direction,
                    "v1EcProb": round(t1.ec_yes_probability or 0, 4),
                    "v2EcProb": round(t2.ec_yes_probability or 0, 4),
                    "probDiff": round(abs((t1.ec_yes_probability or 0) - (t2.ec_yes_probability or 0)), 4),
                    "agree": t1.direction == t2.direction,
                    "v2FallbackLevel": t2.fallback_level,
                    "v2BiasCorrection": t2.bias_correction,
                })
        elif has_v1:
            only_v1 += 1
        else:
            only_v2 += 1

    return {
        "bothTrade": both_trade,
        "onlyV1": only_v1,
        "onlyV2": only_v2,
        "differentSides": different_sides,
        "sameSides": same_sides,
        "probDivergenceGt10pp": prob_divergence_gt_10pp,
        "samples": samples,
    }
