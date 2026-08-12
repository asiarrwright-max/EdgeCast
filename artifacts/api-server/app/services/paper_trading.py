"""
EdgeCast Paper Trading Service — Phase 3A / 3B
===============================================
Simulates paper trades based on EdgeCast probability analysis vs Kalshi market
prices.  No real trades are placed.  No trading API credentials are used.

Assumptions and limitations
----------------------------
- Contracts pay $1.00 per winning contract (Kalshi-style binary contracts).
- Simulated quantity = stake / purchase_price (fractional; real fills may differ).
- No fees, slippage, partial fills, or liquidity constraints are modelled.
- Paper results may overstate real-world performance because of these omissions.
- Several weeks of settled trades are required before drawing conclusions.

Trade direction rules
---------------------
YES trade:  EdgeCast YES probability − YES purchase price ≥ min_edge
NO trade:   (1 − EdgeCast YES probability) − NO purchase price ≥ min_edge

Price preference: YES ask for YES; NO ask for NO.
Falls back to bid when ask is unavailable.

Duplicate prevention
--------------------
One paper trade per (market_ticker, strategy_version).
Later collection runs do not create a second trade for the same (ticker, version).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting, KalshiMarket, PaperTrade, PredictionSnapshot

# ── Phase 3B: quality-flag constants ─────────────────────────────────────────

FLAG_DESCRIPTIONS: dict[str, str] = {
    "missing_settlement_station": (
        "Settlement weather station is not identified — outcome attribution may be unclear."
    ),
    "missing_expiration_time": (
        "Market has no recorded expiration time — the trade window is unknown."
    ),
    "unsupported_settlement_rule": (
        "Market settlement rule could not be parsed — edge calculation may be unreliable."
    ),
    "zero_volume": (
        "Reported trading volume is zero — the market may be illiquid."
    ),
    "missing_liquidity": (
        "No bid or ask prices are recorded — entry price reliability is unknown."
    ),
    "large_bid_ask_spread": (
        "The YES bid/ask spread exceeds 20 percentage points — entry cost may be unfavorable."
    ),
    "low_entry_price": (
        "Entry price is below 5 cents — the implied probability is very low."
    ),
    "stale_market_quote": (
        "Market quote may be stale (collected >24 hours before trade creation) — "
        "prices may not reflect current conditions."
    ),
    "forecast_after_trade": (
        "The forecast data timestamp is later than the trade creation time — "
        "this forecast may not have been available at the time of entry."
    ),
    "correlated_trades": (
        "Multiple open trades exist for the same city, date, and weather event — "
        "results across these trades are correlated."
    ),
    "low_fillability": (
        "Entry price is below 3 cents — realistically very difficult to fill at this price."
    ),
}

logger = logging.getLogger(__name__)

# ── Default settings ──────────────────────────────────────────────────────────
DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "min_edge_pct": 10.0,        # percentage points
    "min_confidence": "High",    # "High" | "Very High"
    "stake": 10.0,               # dollars per trade
    "strategy_version": "v1.0",
}

_SETTING_KEYS = {
    "paper_trading.enabled":          ("enabled",          bool),
    "paper_trading.min_edge_pct":     ("min_edge_pct",     float),
    "paper_trading.min_confidence":   ("min_confidence",   str),
    "paper_trading.stake":            ("stake",            float),
    "paper_trading.strategy_version": ("strategy_version", str),
}

_CONFIDENCE_RANK = {"Very High": 2, "High": 1, "Medium": 0, "Low": -1, "Very Low": -2}


# ── Settings helpers ──────────────────────────────────────────────────────────

async def get_paper_trade_settings(session: AsyncSession) -> dict[str, Any]:
    """Return effective settings — DB overrides applied over hard-coded defaults."""
    keys = list(_SETTING_KEYS.keys())
    result = await session.execute(
        select(AppSetting).where(AppSetting.key.in_(keys))
    )
    db_rows = {r.key: r.value for r in result.scalars().all()}

    settings = dict(DEFAULT_SETTINGS)
    for db_key, (field_name, cast) in _SETTING_KEYS.items():
        if db_key in db_rows and db_rows[db_key] is not None:
            raw = db_rows[db_key]
            try:
                if cast is bool:
                    settings[field_name] = raw.lower() in ("1", "true", "yes")
                else:
                    settings[field_name] = cast(raw)
            except (ValueError, TypeError):
                pass  # keep default on bad data

    return settings


async def save_paper_trade_settings(
    session: AsyncSession, updates: dict[str, Any]
) -> dict[str, Any]:
    """Persist settings changes to AppSetting key-value table."""
    field_to_db = {v[0]: k for k, v in _SETTING_KEYS.items()}
    for field_name, value in updates.items():
        db_key = field_to_db.get(field_name)
        if db_key is None:
            continue
        row_q = await session.execute(
            select(AppSetting).where(AppSetting.key == db_key)
        )
        row = row_q.scalar_one_or_none()
        str_val = str(value).lower() if isinstance(value, bool) else str(value)
        if row:
            row.value = str_val
        else:
            session.add(AppSetting(key=db_key, value=str_val))
    await session.flush()
    return await get_paper_trade_settings(session)


# ── Eligibility helpers ───────────────────────────────────────────────────────

def _is_confidence_sufficient(label: str | None, min_confidence: str) -> bool:
    if label is None:
        return False
    return _CONFIDENCE_RANK.get(label, -99) >= _CONFIDENCE_RANK.get(min_confidence, 0)


def _select_yes_price(market: KalshiMarket) -> tuple[float | None, str | None]:
    """Return (price, source_label) for buying YES.  Prefer ask."""
    if market.yes_ask is not None:
        return market.yes_ask, "YES_ASK"
    if market.yes_bid is not None:
        return market.yes_bid, "YES_BID"
    return None, None


def _select_no_price(market: KalshiMarket) -> tuple[float | None, str | None]:
    """Return (price, source_label) for buying NO.  Prefer ask."""
    if market.no_ask is not None:
        return market.no_ask, "NO_ASK"
    if market.no_bid is not None:
        return market.no_bid, "NO_BID"
    return None, None


# ── Core decision logic ───────────────────────────────────────────────────────

def decide_trade(
    snap: PredictionSnapshot,
    market: KalshiMarket,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """
    Evaluate one market snapshot and return a decision dict.

    Keys in the returned dict:
      action: "YES" | "NO" | "SKIP"
      skip_reason: str | None
      direction: "YES" | "NO" | None
      ec_yes_probability, ec_side_probability, market_yes_probability: float | None
      side_market_price: float | None
      price_source: str | None
      edge_pct_points: float | None  (percentage points, e.g. 15.3)
      decision_explanation: str
      warnings: list[str]
    """
    ec_yes_prob = snap.ec_probability
    mkt_yes_prob = snap.market_probability
    confidence = snap.confidence
    min_edge = settings["min_edge_pct"] / 100.0
    min_conf = settings["min_confidence"]

    warnings: list[str] = [
        "Simulation only. No real trades are placed.",
        "Results may overstate real-world performance: fees, spreads, liquidity, "
        "slippage, and fill availability are not modelled.",
    ]

    # ── Eligibility checks ────────────────────────────────────────────────────
    if snap.analysis_status != "supported":
        return _skip("Unsupported market", ec_yes_prob, mkt_yes_prob, warnings)

    if ec_yes_prob is None:
        return _skip("Forecast unavailable", ec_yes_prob, mkt_yes_prob, warnings)

    if mkt_yes_prob is None:
        return _skip("Market price unavailable", ec_yes_prob, mkt_yes_prob, warnings)

    if not _is_confidence_sufficient(confidence, min_conf):
        return _skip(
            f"Confidence too low ({confidence} < {min_conf})",
            ec_yes_prob, mkt_yes_prob, warnings,
        )

    if market.status != "active":
        return _skip("Market already closed", ec_yes_prob, mkt_yes_prob, warnings)

    # ── Direction candidates ──────────────────────────────────────────────────
    ec_no_prob = round(1.0 - ec_yes_prob, 4)

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
        return _skip(
            f"Edge below threshold ({best_str} < {settings['min_edge_pct']:.1f}pp)",
            ec_yes_prob, mkt_yes_prob, warnings,
        )

    # Choose the higher-edge qualifying direction
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
        explanation = (
            f"EdgeCast estimated a {ec_yes_prob * 100:.1f}% YES probability. "
            f"The YES purchase price ({src}) implied {(yes_price or 0) * 100:.1f}%. "
            f"The difference was +{(edge or 0) * 100:.1f} percentage points, "
            f"exceeding the {settings['min_edge_pct']:.1f}pp threshold. "
            f"Confidence was {confidence}, so a simulated "
            f"${settings['stake']:.2f} YES trade was created."
        )
    else:
        side_price, src = no_price, no_src
        ec_side_prob = ec_no_prob
        edge = no_edge
        explanation = (
            f"EdgeCast estimated a {ec_yes_prob * 100:.1f}% YES probability "
            f"(i.e. {ec_no_prob * 100:.1f}% NO probability). "
            f"The NO purchase price ({src}) implied {(no_price or 0) * 100:.1f}%. "
            f"The NO-side difference was +{(edge or 0) * 100:.1f} percentage points, "
            f"exceeding the {settings['min_edge_pct']:.1f}pp threshold. "
            f"Confidence was {confidence}, so a simulated "
            f"${settings['stake']:.2f} NO trade was created."
        )

    return {
        "action": direction,
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
    }


def _skip(
    reason: str,
    ec_yes_prob: float | None,
    mkt_yes_prob: float | None,
    warnings: list[str],
) -> dict:
    return {
        "action": "SKIP",
        "skip_reason": reason,
        "direction": None,
        "ec_yes_probability": ec_yes_prob,
        "ec_side_probability": None,
        "market_yes_probability": mkt_yes_prob,
        "side_market_price": None,
        "price_source": None,
        "edge_pct_points": None,
        "decision_explanation": f"Skipped: {reason}",
        "warnings": warnings,
    }


# ── Phase 3B: quality-flag computation ───────────────────────────────────────

def compute_quality_flags(
    market: KalshiMarket,
    snap: PredictionSnapshot,
    side_market_price: float | None,
    created_at: datetime | None = None,
    correlated_count: int = 0,
) -> list[str]:
    """
    Compute data-quality warning flags for a paper trade at creation time.
    Returns a (possibly empty) list of flag-name strings from FLAG_DESCRIPTIONS.

    Flags are informational only.  Flagged trades are NOT auto-excluded from
    metrics; callers decide the inclusion policy.
    """
    flags: list[str] = []
    now = created_at or datetime.now(timezone.utc)

    # 1. Missing settlement station (no parsed weather variable)
    if getattr(snap, "settlement_variable", None) is None:
        flags.append("missing_settlement_station")

    # 2. Missing expiration time
    if getattr(market, "close_time", None) is None:
        flags.append("missing_expiration_time")

    # 3. Unsupported settlement rule (defensive — decide_trade already skips these)
    if getattr(snap, "analysis_status", None) != "supported":
        flags.append("unsupported_settlement_rule")

    # 4. Zero reported volume
    vol = getattr(market, "volume", None)
    if vol is not None and vol == 0:
        flags.append("zero_volume")

    # 5. Missing liquidity (no bid/ask prices at all)
    has_prices = any(
        getattr(market, f, None) is not None
        for f in ("yes_bid", "yes_ask", "no_bid", "no_ask")
    )
    if not has_prices:
        flags.append("missing_liquidity")

    # 6. Large YES bid/ask spread (> 20pp)
    yes_bid = getattr(market, "yes_bid", None)
    yes_ask = getattr(market, "yes_ask", None)
    if yes_bid is not None and yes_ask is not None and (yes_ask - yes_bid) > 0.20:
        flags.append("large_bid_ask_spread")

    # 7. Low entry price (< 5 cents)
    if side_market_price is not None and 0 < side_market_price < 0.05:
        flags.append("low_entry_price")

    # 8. Stale market quote (collection timestamp > 24h before trade creation)
    coll_ts = getattr(market, "collection_timestamp", None)
    if coll_ts is not None and isinstance(coll_ts, datetime):
        if coll_ts.tzinfo is None:
            coll_ts = coll_ts.replace(tzinfo=timezone.utc)
        if (now - coll_ts).total_seconds() > 86_400:
            flags.append("stale_market_quote")

    # 9. Forecast retrieved after trade creation (data anomaly)
    fc_ts = getattr(snap, "forecast_retrieved_at", None)
    if fc_ts is not None and isinstance(fc_ts, datetime):
        if fc_ts.tzinfo is None:
            fc_ts = fc_ts.replace(tzinfo=timezone.utc)
        if fc_ts > now:
            flags.append("forecast_after_trade")

    # 10. Correlated trades (same city + date + weather_event already open)
    if correlated_count > 0:
        flags.append("correlated_trades")

    # 11. Low fillability (< 3 cents — very hard to fill in practice)
    if side_market_price is not None and 0 < side_market_price < 0.03:
        flags.append("low_fillability")

    return flags


# ── Phase 3B: bucket helpers ──────────────────────────────────────────────────

def edge_bucket(edge_pct: float | None) -> str:
    if edge_pct is None:
        return "unknown"
    if edge_pct < 10:
        return "<10pp"
    if edge_pct < 20:
        return "10-20pp"
    if edge_pct < 30:
        return "20-30pp"
    if edge_pct < 40:
        return "30-40pp"
    return "≥40pp"

EDGE_BUCKET_ORDER = ["<10pp", "10-20pp", "20-30pp", "30-40pp", "≥40pp", "unknown"]


def price_bucket(price: float | None) -> str:
    """price in [0, 1] decimal; buckets in cents."""
    if price is None:
        return "unknown"
    cents = price * 100
    if cents <= 5:
        return "1-5¢"
    if cents <= 15:
        return "6-15¢"
    if cents <= 30:
        return "16-30¢"
    if cents <= 50:
        return "31-50¢"
    return ">50¢"

PRICE_BUCKET_ORDER = ["1-5¢", "6-15¢", "16-30¢", "31-50¢", ">50¢", "unknown"]


def lead_bucket(days: int | None) -> str:
    if days is None:
        return "unknown"
    if days <= 1:
        return "0-1d"
    if days <= 3:
        return "2-3d"
    if days <= 7:
        return "4-7d"
    return ">7d"

LEAD_BUCKET_ORDER = ["0-1d", "2-3d", "4-7d", ">7d", "unknown"]


def _breakdown_row(
    label: str,
    group: list[PaperTrade],
    total_cost_rate: float = 0.0,
) -> dict:
    """Single breakdown row for a group of trades."""
    settled = [t for t in group if t.status == "SETTLED"]
    wins = [t for t in settled if t.outcome == "WIN"]
    total_stake = sum(t.stake or 0 for t in settled)
    raw_pl = sum(t.profit_loss or 0 for t in settled)
    deductions = sum((t.stake or 0) * total_cost_rate / 100 for t in settled) if total_cost_rate > 0 else 0.0
    adj_pl = raw_pl - deductions
    win_rate = len(wins) / len(settled) if settled else None
    roi = (raw_pl / total_stake * 100) if total_stake > 0 else None
    adj_roi = (adj_pl / total_stake * 100) if (total_stake > 0 and total_cost_rate > 0) else None
    return {
        "label": label,
        "settledCount": len(settled),
        "wins": len(wins),
        "losses": len(settled) - len(wins),
        "winRate": round(win_rate, 4) if win_rate is not None else None,
        "totalStake": round(total_stake, 4),
        "profitLoss": round(raw_pl, 4),
        "roi": round(roi, 4) if roi is not None else None,
        "adjProfitLoss": round(adj_pl, 4) if total_cost_rate > 0 else None,
        "adjRoi": round(adj_roi, 4) if adj_roi is not None else None,
    }


def _build_breakdown(
    trades: list[PaperTrade],
    key_fn: Any,
    order: list[str] | None = None,
    total_cost_rate: float = 0.0,
) -> list[dict]:
    groups: dict[str, list] = {}
    for t in trades:
        k = key_fn(t) or "Unknown"
        groups.setdefault(k, []).append(t)
    rows: list[dict] = []
    seen: set[str] = set()
    for k in (order or []):
        if k in groups:
            rows.append(_breakdown_row(k, groups[k], total_cost_rate))
            seen.add(k)
    for k in sorted(groups.keys()):
        if k not in seen:
            rows.append(_breakdown_row(k, groups[k], total_cost_rate))
    return rows


# ── Position math ─────────────────────────────────────────────────────────────

def calculate_position(stake: float, purchase_price: float) -> dict[str, float]:
    """
    Calculate simulated position size.

    Kalshi contracts pay $1.00 per winning contract.
    quantity = stake / purchase_price  (full precision)

    Returns: {"quantity": float, "stake": float}
    """
    quantity = stake / purchase_price if purchase_price > 0 else 0.0
    return {"quantity": quantity, "stake": stake}


def settle_position(
    direction: str,
    quantity: float,
    stake: float,
    kalshi_result: str,
) -> dict[str, Any]:
    """
    Calculate settlement outcome for a paper trade.

    direction:      "YES" | "NO"
    quantity:       simulated contract count
    stake:          dollars staked
    kalshi_result:  "yes" | "no" | "void"

    Returns: {"outcome": str, "gross_payout": float, "profit_loss": float, "return_pct": float}
    """
    if kalshi_result == "void":
        return {
            "outcome": "VOID",
            "gross_payout": stake,
            "profit_loss": 0.0,
            "return_pct": 0.0,
        }

    won = (
        (direction == "YES" and kalshi_result == "yes") or
        (direction == "NO" and kalshi_result == "no")
    )

    if won:
        gross_payout = quantity * 1.0  # $1 per contract
        profit_loss = gross_payout - stake
        return_pct = (profit_loss / stake * 100) if stake > 0 else 0.0
        return {
            "outcome": "WIN",
            "gross_payout": round(gross_payout, 6),
            "profit_loss": round(profit_loss, 6),
            "return_pct": round(return_pct, 4),
        }
    else:
        return {
            "outcome": "LOSS",
            "gross_payout": 0.0,
            "profit_loss": round(-stake, 6),
            "return_pct": -100.0,
        }


# ── Trade creation ────────────────────────────────────────────────────────────

async def maybe_create_paper_trade(
    session: AsyncSession,
    market: KalshiMarket,
    snap: PredictionSnapshot,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """
    Evaluate one market and create a PaperTrade record if eligible.
    Returns {"created": bool, "direction": str|None, "skip_reason": str|None}.
    """
    strategy_version = settings["strategy_version"]

    # Duplicate check
    existing_q = await session.execute(
        select(PaperTrade).where(
            PaperTrade.market_ticker == market.ticker,
            PaperTrade.strategy_version == strategy_version,
        ).limit(1)
    )
    if existing_q.scalar_one_or_none() is not None:
        return {"created": False, "direction": None, "skip_reason": "Existing paper trade"}

    decision = decide_trade(snap, market, settings)

    if decision["action"] == "SKIP":
        return {"created": False, "direction": None, "skip_reason": decision["skip_reason"]}

    stake = settings["stake"]
    side_price = decision["side_market_price"]
    pos = calculate_position(stake, side_price)

    # Check for correlated trades (same city + target date + weather event)
    correlated_count = 0
    if market.city and market.target_date and snap.settlement_variable:
        corr_q = await session.execute(
            select(PaperTrade).where(
                PaperTrade.city == market.city,
                PaperTrade.target_settlement_date == market.target_date,
                PaperTrade.weather_variable == snap.settlement_variable,
                PaperTrade.status == "OPEN",
                PaperTrade.strategy_version == strategy_version,
            ).limit(5)
        )
        correlated_count = len(corr_q.scalars().all())

    now = datetime.now(timezone.utc)
    flags = compute_quality_flags(market, snap, side_price, created_at=now, correlated_count=correlated_count)

    from app.services.settlement_regime import infer_settlement_regime  # local import avoids circular

    trade = PaperTrade(
        market_ticker=market.ticker,
        event_ticker=market.event_ticker,
        city=market.city,
        weather_variable=snap.settlement_variable,
        contract_type=snap.contract_type,
        target_settlement_date=market.target_date,
        snapshot_id=snap.id,
        strategy_version=strategy_version,
        direction=decision["direction"],
        ec_yes_probability=decision["ec_yes_probability"],
        ec_side_probability=decision["ec_side_probability"],
        market_yes_probability=decision["market_yes_probability"],
        side_market_price=side_price,
        price_source=decision["price_source"],
        edge_pct_points=decision["edge_pct_points"],
        confidence_score=None,
        confidence_label=snap.confidence,
        stake=stake,
        quantity=pos["quantity"],
        lead_time_days=snap.lead_time_days,
        status="OPEN",
        decision_explanation=decision["decision_explanation"],
        warnings="; ".join(decision["warnings"]),
        quality_flags=flags if flags else None,
        settlement_regime=infer_settlement_regime(market.target_date),
        outcome_verified=None,  # set to True when Kalshi confirms settlement
    )
    session.add(trade)
    await session.flush()

    logger.info(
        "Paper trade created: %s %s @ %.4f (edge %.1fpp, %s)",
        decision["direction"],
        market.ticker,
        side_price,
        decision["edge_pct_points"] or 0,
        snap.confidence,
    )
    return {"created": True, "direction": decision["direction"], "skip_reason": None}


# ── Batch runner ──────────────────────────────────────────────────────────────

async def run_paper_trading(session: AsyncSession) -> dict[str, int]:
    """
    Review all recently-analyzed markets and create paper trades for eligible ones.
    Called after every collection + analysis run.

    Returns: {"candidates", "created", "yes_trades", "no_trades", "skipped", "errors"}
    """
    stats: dict[str, int] = {
        "candidates": 0, "created": 0,
        "yes_trades": 0, "no_trades": 0,
        "skipped": 0, "errors": 0,
    }

    settings = await get_paper_trade_settings(session)

    if not settings["enabled"]:
        logger.info("Paper trading disabled — skipping.")
        return stats

    # Latest snapshot per ticker (highest id wins)
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

    # Active markets lookup
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
            result = await maybe_create_paper_trade(session, market, snap, settings)
            if result["created"]:
                stats["created"] += 1
                if result["direction"] == "YES":
                    stats["yes_trades"] += 1
                else:
                    stats["no_trades"] += 1
            else:
                stats["skipped"] += 1
        except Exception as exc:
            stats["errors"] += 1
            logger.warning("Paper trade error for %s: %s", snap.market_ticker, exc)

    await session.commit()
    logger.info(
        "Paper trading: %d candidates, %d created (%d YES / %d NO), %d skipped, %d errors",
        stats["candidates"], stats["created"],
        stats["yes_trades"], stats["no_trades"],
        stats["skipped"], stats["errors"],
    )
    return stats


# ── Metrics ───────────────────────────────────────────────────────────────────

def compute_metrics_from_trades(trades: list) -> dict:
    """
    Compute summary metrics from any list of trade-like objects.

    Compatible with PaperTrade, V3PaperTrade, or any object exposing:
    status, outcome, stake, profit_loss, edge_pct_points, side_market_price,
    direction, city, contract_type.

    confidence_label is read via getattr — present on PaperTrade (V2.x),
    absent on V3PaperTrade; missing values are grouped as "Unknown".

    This is the canonical aggregation kernel: it makes no DB calls and is
    safe to call on a combined list of V2 + V3 trade objects.
    """
    if not trades:
        return _empty_metrics()

    open_t    = [t for t in trades if t.status == "OPEN"]
    settled_t = [t for t in trades if t.status == "SETTLED"]
    void_t    = [t for t in trades if t.status == "VOID"]

    wins   = [t for t in settled_t if t.outcome == "WIN"]
    losses = [t for t in settled_t if t.outcome == "LOSS"]

    win_rate             = len(wins) / len(settled_t) if settled_t else None
    total_staked_settled = sum(t.stake or 0 for t in settled_t)
    net_pl               = sum(t.profit_loss or 0 for t in settled_t)
    roi                  = (net_pl / total_staked_settled * 100) if total_staked_settled > 0 else None

    def avg(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 2) if vals else None

    all_edges  = [t.edge_pct_points   for t in trades if t.edge_pct_points  is not None]
    win_edges  = [t.edge_pct_points   for t in wins   if t.edge_pct_points  is not None]
    loss_edges = [t.edge_pct_points   for t in losses if t.edge_pct_points  is not None]
    all_prices = [t.side_market_price for t in trades if t.side_market_price is not None]

    def perf_by(key_fn, trades_list: list) -> list[dict]:
        groups: dict[str, list] = {}
        for t in trades_list:
            k = key_fn(t) or "Unknown"
            groups.setdefault(k, []).append(t)
        result = []
        for k, group in sorted(groups.items()):
            g_settled = [t for t in group if t.status == "SETTLED"]
            g_wins    = [t for t in g_settled if t.outcome == "WIN"]
            g_pl      = sum(t.profit_loss or 0 for t in g_settled)
            result.append({
                "label":         k,
                "total":         len(group),
                "open":          sum(1 for t in group if t.status == "OPEN"),
                "settled":       len(g_settled),
                "wins":          len(g_wins),
                "losses":        len(g_settled) - len(g_wins),
                "winRate":       round(len(g_wins) / len(g_settled), 4) if g_settled else None,
                "netProfitLoss": round(g_pl, 4),
            })
        return result

    settled_stake = round(total_staked_settled, 4)
    open_capital  = round(sum(t.stake or 0 for t in open_t), 4)

    return {
        "openCount":      len(open_t),
        "settledCount":   len(settled_t),
        "voidCount":      len(void_t),
        "totalCount":     len(trades),
        "wins":           len(wins),
        "losses":         len(losses),
        "winRate":        round(win_rate, 4) if win_rate is not None else None,
        "settledStake":   settled_stake,
        "openCapital":    open_capital,
        "totalStaked":    round(settled_stake + open_capital, 4),
        "netProfitLoss":  round(net_pl, 4),
        "roi":            round(roi, 4) if roi is not None else None,
        "avgEntryEdge":   avg(all_edges),
        "avgEntryPrice":  avg(all_prices),
        "avgWinEdge":     avg(win_edges),
        "avgLossEdge":    avg(loss_edges),
        "byDirection":    perf_by(lambda t: t.direction, trades),
        "byConfidence":   perf_by(lambda t: getattr(t, "confidence_label", None), trades),
        "byCity":         perf_by(lambda t: t.city, trades),
        "byContractType": perf_by(lambda t: t.contract_type, trades),
        "sampleSizeWarning": len(settled_t) < 20,
        "preliminaryNote": (
            "Results are preliminary. A minimum of several weeks of settled trades "
            "is required before drawing statistically meaningful conclusions."
        ) if len(settled_t) < 30 else None,
    }


async def get_paper_trade_metrics(
    session: AsyncSession,
    strategy_version: str | None = None,
    strategy_versions: list[str] | None = None,
    is_executable: bool | None = None,
    paired_only: bool = False,
) -> dict:
    """
    Calculate summary metrics.

    strategy_version  — exact match on a single version string
    strategy_versions — restrict to a list of versions (e.g. ['v2.1','v2.2'])
    is_executable     — filter on the is_executable flag; None = no filter
                        (legacy V1/V2 rows have NULL, which only matches when None)
    paired_only       — when True, restrict to trades with a non-NULL
                        comparison_snapshot_id (strictly paired head-to-head)
    """
    q = select(PaperTrade).where(PaperTrade.status != "V2_EXCLUDED")
    if strategy_version:
        q = q.where(PaperTrade.strategy_version == strategy_version)
    if strategy_versions:
        q = q.where(PaperTrade.strategy_version.in_(strategy_versions))
    if is_executable is not None:
        q = q.where(PaperTrade.is_executable == is_executable)
    if paired_only:
        q = q.where(PaperTrade.comparison_snapshot_id.isnot(None))
    trades_q = await session.execute(q)
    all_trades = trades_q.scalars().all()

    return compute_metrics_from_trades(list(all_trades))


def _empty_metrics() -> dict:
    return {
        "openCount": 0, "settledCount": 0, "voidCount": 0, "totalCount": 0,
        "wins": 0, "losses": 0, "winRate": None,
        "settledStake": 0.0, "openCapital": 0.0, "totalStaked": 0.0,
        "netProfitLoss": 0.0, "roi": None,
        "avgEntryEdge": None, "avgEntryPrice": None,
        "avgWinEdge": None, "avgLossEdge": None,
        "byDirection": [], "byConfidence": [], "byCity": [], "byContractType": [],
        "sampleSizeWarning": True,
        "preliminaryNote": "No settled trades yet. Results will appear after markets resolve.",
    }


# ── Phase 3B: analytics ───────────────────────────────────────────────────────

async def get_paper_trade_analytics(
    session: AsyncSession,
    strategy_version: str | None = None,
    strategy_versions: list[str] | None = None,
    is_executable: bool | None = None,
    include_flagged: bool = True,
    fee_pct: float = 0.0,
    slippage_pct: float = 0.0,
    spread_adj: float = 0.0,
) -> dict:
    """
    Performance breakdowns for settled paper trades.

    strategy_version   — legacy single-version filter (exact match)
    strategy_versions  — restrict to a list of versions (takes precedence if set)
    is_executable      — filter on is_executable flag; None = no filter
    Realistic-result adjustments:
      adj_pl = raw_pl - stake * (fee_pct + slippage_pct + spread_adj) / 100
    These are simplified model approximations; clearly labelled in the UI.
    """
    # Always exclude V2_EXCLUDED and TEST_EXCLUDED from analytics
    q = select(PaperTrade).where(
        PaperTrade.status.notin_(["V2_EXCLUDED", "TEST_EXCLUDED"])
    )
    if strategy_versions is not None:
        # Empty list ⇒ caller wants no results (e.g. v3_challenger — V3 lives in v3_paper_trades)
        if not strategy_versions:
            q = q.where(PaperTrade.strategy_version.in_([]))
        else:
            q = q.where(PaperTrade.strategy_version.in_(strategy_versions))
    elif strategy_version:
        q = q.where(PaperTrade.strategy_version == strategy_version)
    if is_executable is not None:
        q = q.where(PaperTrade.is_executable == is_executable)
    result = await session.execute(q)
    all_trades = result.scalars().all()

    # Optional: exclude flagged trades
    if not include_flagged:
        all_trades = [t for t in all_trades if not (t.quality_flags)]

    settled_t = [t for t in all_trades if t.status == "SETTLED"]
    total_cost_rate = fee_pct + slippage_pct + spread_adj

    # Cumulative P/L time series (settled, sorted by settlement timestamp)
    settled_by_time = sorted(
        settled_t,
        key=lambda t: t.settlement_timestamp or datetime.min.replace(tzinfo=timezone.utc),
    )
    cumulative = 0.0
    adj_cumulative = 0.0
    cumulative_pl: list[dict] = []
    for t in settled_by_time:
        pl = t.profit_loss or 0.0
        deduction = (t.stake or 0.0) * total_cost_rate / 100
        cumulative += pl
        adj_cumulative += pl - deduction
        cumulative_pl.append({
            "date": t.settlement_timestamp.isoformat() if t.settlement_timestamp else None,
            "cumulativePl": round(cumulative, 4),
            "adjCumulativePl": round(adj_cumulative, 4) if total_cost_rate > 0 else None,
            "tradeId": t.id,
        })

    # Daily P/L
    daily: dict[str, dict] = {}
    for t in settled_by_time:
        if not t.settlement_timestamp:
            continue
        day = t.settlement_timestamp.strftime("%Y-%m-%d")
        if day not in daily:
            daily[day] = {"date": day, "pl": 0.0, "adjPl": 0.0, "count": 0}
        pl = t.profit_loss or 0.0
        deduction = (t.stake or 0.0) * total_cost_rate / 100
        daily[day]["pl"] += pl
        daily[day]["adjPl"] += pl - deduction
        daily[day]["count"] += 1
    daily_pl = [
        {
            "date": v["date"],
            "pl": round(v["pl"], 4),
            "adjPl": round(v["adjPl"], 4) if total_cost_rate > 0 else None,
            "count": v["count"],
        }
        for v in sorted(daily.values(), key=lambda x: x["date"])
    ]

    return {
        "strategyVersion": strategy_version,
        "includeFlagged": include_flagged,
        "realisticAdjustments": {
            "feePct": fee_pct,
            "slippagePct": slippage_pct,
            "spreadAdj": spread_adj,
            "totalCostPct": total_cost_rate,
        },
        "cumulativePl": cumulative_pl,
        "dailyPl": daily_pl,
        "byDirection": _build_breakdown(
            settled_t, lambda t: t.direction, total_cost_rate=total_cost_rate,
        ),
        "byCity": _build_breakdown(
            settled_t, lambda t: t.city, total_cost_rate=total_cost_rate,
        ),
        "byContractType": _build_breakdown(
            settled_t, lambda t: t.contract_type, total_cost_rate=total_cost_rate,
        ),
        "byEdgeBucket": _build_breakdown(
            settled_t,
            lambda t: edge_bucket(t.edge_pct_points),
            order=EDGE_BUCKET_ORDER,
            total_cost_rate=total_cost_rate,
        ),
        "byPriceBucket": _build_breakdown(
            settled_t,
            lambda t: price_bucket(t.side_market_price),
            order=PRICE_BUCKET_ORDER,
            total_cost_rate=total_cost_rate,
        ),
        "byLeadTime": _build_breakdown(
            settled_t,
            lambda t: lead_bucket(t.lead_time_days),
            order=LEAD_BUCKET_ORDER,
            total_cost_rate=total_cost_rate,
        ),
    }


# ── Phase 3B: calibration report ─────────────────────────────────────────────

_CALIBRATION_BUCKETS = [
    ("0-10%",   0.00, 0.10),
    ("11-20%",  0.10, 0.20),
    ("21-30%",  0.20, 0.30),
    ("31-40%",  0.30, 0.40),
    ("41-50%",  0.40, 0.50),
    ("51-60%",  0.50, 0.60),
    ("61-70%",  0.60, 0.70),
    ("71-80%",  0.70, 0.80),
    ("81-90%",  0.80, 0.90),
    ("91-100%", 0.90, 1.001),  # inclusive of 1.0
]


async def get_calibration_report(
    session: AsyncSession,
    strategy_version: str | None = None,
    strategy_versions: list[str] | None = None,
    is_executable: bool | None = None,
) -> dict:
    """
    Compare EdgeCast YES-probability estimates against actual Kalshi settlement.
    Uses only SETTLED non-void trades with a known ec_yes_probability.

    strategy_versions  — restrict to a list of versions (takes precedence if set)
    is_executable      — filter on is_executable flag; None = no filter
    Brier score = (1/n) * Σ (ec_prob − actual_yes)²
    where actual_yes = 1 if market settled YES, 0 if NO.
    """
    q = (
        select(PaperTrade)
        .where(
            PaperTrade.status == "SETTLED",
            PaperTrade.kalshi_result.in_(["yes", "no"]),
            PaperTrade.ec_yes_probability.isnot(None),
        )
    )
    if strategy_versions is not None:
        if not strategy_versions:
            q = q.where(PaperTrade.strategy_version.in_([]))
        else:
            q = q.where(PaperTrade.strategy_version.in_(strategy_versions))
    elif strategy_version:
        q = q.where(PaperTrade.strategy_version == strategy_version)
    if is_executable is not None:
        q = q.where(PaperTrade.is_executable == is_executable)
    result = await session.execute(q)
    trades = result.scalars().all()

    if not trades:
        return {
            "buckets": [
                {"bucket": b, "count": 0, "avgEcProb": None, "actualYesRate": None, "calibrationDiff": None}
                for b, _, _ in _CALIBRATION_BUCKETS
            ],
            "brierScore": None,
            "totalSettled": 0,
            "strategyVersion": strategy_version,
        }

    bucket_items: dict[str, list[tuple[float, int]]] = {b: [] for b, _, _ in _CALIBRATION_BUCKETS}
    brier_sum = 0.0

    for t in trades:
        ec_prob: float = t.ec_yes_probability  # type: ignore[assignment]
        actual_yes = 1 if t.kalshi_result == "yes" else 0
        brier_sum += (ec_prob - actual_yes) ** 2
        for label, lo, hi in _CALIBRATION_BUCKETS:
            if lo <= ec_prob < hi:
                bucket_items[label].append((ec_prob, actual_yes))
                break

    buckets = []
    for label, _, _ in _CALIBRATION_BUCKETS:
        items = bucket_items[label]
        if not items:
            buckets.append({"bucket": label, "count": 0, "avgEcProb": None, "actualYesRate": None, "calibrationDiff": None})
        else:
            avg_prob = sum(p for p, _ in items) / len(items)
            yes_rate = sum(o for _, o in items) / len(items)
            buckets.append({
                "bucket": label,
                "count": len(items),
                "avgEcProb": round(avg_prob, 4),
                "actualYesRate": round(yes_rate, 4),
                "calibrationDiff": round(avg_prob - yes_rate, 4),
            })

    return {
        "buckets": buckets,
        "brierScore": round(brier_sum / len(trades), 6),
        "totalSettled": len(trades),
        "strategyVersion": strategy_version,
    }
