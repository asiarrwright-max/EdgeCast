"""
V3 Settlement — Phase 3
=======================
Settles open V3PaperTrade rows by checking the Kalshi REST API.
Called from the settlement scheduler loop after the V1/V2/V2.1 settlement job.

Status lifecycle (identical to V1/V2/V2.1):
  OPEN → SETTLED              (confirmed Kalshi result)
  OPEN → VOID                 (market canceled)
  OPEN → PENDING_SETTLEMENT   (market closed but no result yet)
  OPEN → ERROR                (Kalshi 404 — market no longer exists)
  PENDING_SETTLEMENT → SETTLED / VOID / ERROR (rechecked each cycle)

Isolation
---------
* Never touches paper_trades (V1/V2/V2.1).
* Reuses fetch_kalshi_market + _extract_result from settlement.py (pure helpers).
* Reuses settle_position from paper_trading.py (pure math).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models_v3 import V3PaperTrade
from app.services.paper_trading import settle_position
from app.services.settlement import _extract_result, fetch_kalshi_market

logger = logging.getLogger(__name__)


async def run_v3_settlement_job() -> dict[str, int]:
    """
    Check all open and pending V3PaperTrade rows against Kalshi and settle.

    Returns:
        {"checked", "settled", "voided", "pending_settlement", "errors", "still_open"}
    """
    stats: dict[str, int] = {
        "checked": 0,
        "settled": 0,
        "voided": 0,
        "pending_settlement": 0,
        "errors": 0,
        "still_open": 0,
    }

    if AsyncSessionLocal is None:
        logger.error("Database not initialised — cannot run V3 settlement job.")
        return stats

    async with AsyncSessionLocal() as session:
        open_q = await session.execute(
            select(V3PaperTrade).where(
                or_(
                    V3PaperTrade.status == "OPEN",
                    V3PaperTrade.status == "PENDING_SETTLEMENT",
                )
            )
        )
        open_trades = open_q.scalars().all()

        if not open_trades:
            logger.info("V3 settlement: no open or pending trades.")
            return stats

        logger.info(
            "V3 settlement: checking %d trade(s) (OPEN + PENDING_SETTLEMENT).",
            len(open_trades),
        )

        for trade in open_trades:
            stats["checked"] += 1
            try:
                fetch = await fetch_kalshi_market(trade.market_ticker)

                if fetch.transient_error:
                    # Network/5xx/timeout — leave OPEN, retry next cycle
                    note = f"V3 settlement skipped (transient): {fetch.error_msg}"
                    existing = trade.decision_explanation or ""
                    # Keep only the last transient note to avoid unbounded growth
                    lines = [l for l in existing.split("\n") if "transient" not in l.lower()]
                    lines.append(note)
                    trade.decision_explanation = "\n".join(l for l in lines if l)
                    stats["still_open"] += 1
                    logger.info(
                        "V3 trade %d (%s) unchanged after transient error: %s",
                        trade.id, trade.market_ticker, fetch.error_msg,
                    )
                    continue

                if fetch.not_found:
                    trade.status = "ERROR"
                    note = "Settlement failed: market not found on Kalshi (404)"
                    existing = trade.decision_explanation or ""
                    trade.decision_explanation = (existing + "\n" + note).strip()
                    stats["errors"] += 1
                    logger.warning(
                        "V3 trade %d (%s) → ERROR: 404 not found.",
                        trade.id, trade.market_ticker,
                    )
                    continue

                kalshi_result = _extract_result(fetch.data)

                if kalshi_result is None:
                    stats["still_open"] += 1
                    continue

                if kalshi_result == "pending":
                    if trade.status != "PENDING_SETTLEMENT":
                        trade.status = "PENDING_SETTLEMENT"
                        note = (
                            f"Market closed; awaiting Kalshi result "
                            f"(checked {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')})"
                        )
                        existing = trade.decision_explanation or ""
                        trade.decision_explanation = (existing + "\n" + note).strip()
                    stats["pending_settlement"] += 1
                    logger.info(
                        "V3 trade %d (%s) → PENDING_SETTLEMENT.",
                        trade.id, trade.market_ticker,
                    )
                    continue

                outcome = settle_position(
                    direction=trade.direction,
                    quantity=trade.quantity or 0.0,
                    stake=trade.stake,
                    kalshi_result=kalshi_result,
                )

                trade.kalshi_result = kalshi_result
                trade.outcome = outcome["outcome"]
                trade.gross_payout = outcome["gross_payout"]
                trade.profit_loss = outcome["profit_loss"]
                trade.return_pct = outcome["return_pct"]
                trade.settlement_timestamp = datetime.now(timezone.utc)

                if kalshi_result == "void":
                    trade.status = "VOID"
                    stats["voided"] += 1
                    logger.info(
                        "V3 trade %d (%s) → VOID.",
                        trade.id, trade.market_ticker,
                    )
                else:
                    trade.status = "SETTLED"
                    stats["settled"] += 1
                    logger.info(
                        "V3 paper trade %d (%s %s) → SETTLED: %s (P/L $%.2f)",
                        trade.id, trade.direction, trade.market_ticker,
                        outcome["outcome"], outcome["profit_loss"],
                    )

            except Exception as exc:
                stats["errors"] += 1
                logger.warning(
                    "V3 settlement error for trade %d (%s): %s",
                    trade.id, trade.market_ticker, exc,
                )

        await session.commit()

    logger.info(
        "V3 settlement done: %d checked, %d settled, %d voided, "
        "%d pending, %d errors, %d still open.",
        stats["checked"], stats["settled"], stats["voided"],
        stats["pending_settlement"], stats["errors"], stats["still_open"],
    )
    return stats
