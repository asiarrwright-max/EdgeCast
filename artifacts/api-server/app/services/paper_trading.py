"""
EdgeCast Paper Trading Service — Phase 3A
==========================================
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
        status="OPEN",
        decision_explanation=decision["decision_explanation"],
        warnings="; ".join(decision["warnings"]),
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

async def get_paper_trade_metrics(session: AsyncSession) -> dict:
    """Calculate summary metrics across all paper trades."""
    trades_q = await session.execute(select(PaperTrade))
    all_trades = trades_q.scalars().all()

    if not all_trades:
        return _empty_metrics()

    open_t = [t for t in all_trades if t.status == "OPEN"]
    settled_t = [t for t in all_trades if t.status == "SETTLED"]
    void_t = [t for t in all_trades if t.status == "VOID"]

    wins = [t for t in settled_t if t.outcome == "WIN"]
    losses = [t for t in settled_t if t.outcome == "LOSS"]

    win_rate = len(wins) / len(settled_t) if settled_t else None
    total_staked_settled = sum(t.stake or 0 for t in settled_t)
    net_pl = sum(t.profit_loss or 0 for t in settled_t)
    roi = (net_pl / total_staked_settled * 100) if total_staked_settled > 0 else None

    def avg(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 2) if vals else None

    all_edges = [t.edge_pct_points for t in all_trades if t.edge_pct_points is not None]
    win_edges = [t.edge_pct_points for t in wins if t.edge_pct_points is not None]
    loss_edges = [t.edge_pct_points for t in losses if t.edge_pct_points is not None]

    def perf_by(key_fn, trades_list: list) -> list[dict]:
        groups: dict[str, list] = {}
        for t in trades_list:
            k = key_fn(t) or "Unknown"
            groups.setdefault(k, []).append(t)
        result = []
        for k, group in sorted(groups.items()):
            g_settled = [t for t in group if t.status == "SETTLED"]
            g_wins = [t for t in g_settled if t.outcome == "WIN"]
            g_pl = sum(t.profit_loss or 0 for t in g_settled)
            result.append({
                "label": k,
                "total": len(group),
                "open": sum(1 for t in group if t.status == "OPEN"),
                "settled": len(g_settled),
                "wins": len(g_wins),
                "losses": len(g_settled) - len(g_wins),
                "winRate": round(len(g_wins) / len(g_settled), 4) if g_settled else None,
                "netProfitLoss": round(g_pl, 4),
            })
        return result

    return {
        "openCount": len(open_t),
        "settledCount": len(settled_t),
        "voidCount": len(void_t),
        "totalCount": len(all_trades),
        "wins": len(wins),
        "losses": len(losses),
        "winRate": round(win_rate, 4) if win_rate is not None else None,
        "totalStaked": round(sum(t.stake or 0 for t in all_trades if t.status in ("OPEN", "SETTLED")), 4),
        "netProfitLoss": round(net_pl, 4),
        "roi": round(roi, 4) if roi is not None else None,
        "avgEntryEdge": avg(all_edges),
        "avgWinEdge": avg(win_edges),
        "avgLossEdge": avg(loss_edges),
        "byDirection": perf_by(lambda t: t.direction, all_trades),
        "byConfidence": perf_by(lambda t: t.confidence_label, all_trades),
        "byCity": perf_by(lambda t: t.city, all_trades),
        "byContractType": perf_by(lambda t: t.contract_type, all_trades),
        "sampleSizeWarning": len(settled_t) < 20,
        "preliminaryNote": (
            "Results are preliminary. A minimum of several weeks of settled trades "
            "is required before drawing statistically meaningful conclusions."
        ) if len(settled_t) < 30 else None,
    }


def _empty_metrics() -> dict:
    return {
        "openCount": 0, "settledCount": 0, "voidCount": 0, "totalCount": 0,
        "wins": 0, "losses": 0, "winRate": None,
        "totalStaked": 0.0, "netProfitLoss": 0.0, "roi": None,
        "avgEntryEdge": None, "avgWinEdge": None, "avgLossEdge": None,
        "byDirection": [], "byConfidence": [], "byCity": [], "byContractType": [],
        "sampleSizeWarning": True,
        "preliminaryNote": "No settled trades yet. Results will appear after markets resolve.",
    }
