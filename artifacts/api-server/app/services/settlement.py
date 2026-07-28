"""
Settlement processing for EdgeCast paper trades — Phase 3A.

Queries the Kalshi REST API (public, no credentials required) to check
whether open paper trades have settled.  Settlement is determined solely
from authoritative Kalshi data — never inferred from weather forecasts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AppError, PaperTrade
from app.services.paper_trading import settle_position

logger = logging.getLogger(__name__)


class _FetchResult:
    """Typed return value from fetch_kalshi_market to distinguish transient vs terminal failures."""
    __slots__ = ("data", "not_found", "error_msg")

    def __init__(
        self,
        data: dict | None = None,
        not_found: bool = False,
        error_msg: str | None = None,
    ):
        self.data = data
        self.not_found = not_found
        self.error_msg = error_msg

    @property
    def ok(self) -> bool:
        return self.data is not None

    @property
    def transient_error(self) -> bool:
        """True when the failure is likely temporary (network, 5xx, timeout)."""
        return not self.not_found and self.data is None


async def fetch_kalshi_market(ticker: str) -> _FetchResult:
    """
    Fetch a single market's current data from the Kalshi REST API.

    Returns a _FetchResult:
      - .ok=True, .data=dict     → success
      - .not_found=True          → 404; market does not exist (terminal)
      - .transient_error=True    → network/5xx/timeout; caller should leave trade OPEN
    """
    settings = get_settings()
    url = f"{settings.kalshi_base_url}/markets/{ticker}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                logger.info("Kalshi market %s not found (404).", ticker)
                return _FetchResult(not_found=True)
            resp.raise_for_status()
            data = resp.json()
            # Kalshi wraps the market under a "market" key
            return _FetchResult(data=data.get("market", data))
    except httpx.HTTPStatusError as exc:
        msg = f"HTTP {exc.response.status_code}"
        logger.warning("Kalshi HTTP error for %s: %s — will retry next cycle", ticker, msg)
        return _FetchResult(error_msg=msg)
    except Exception as exc:
        msg = str(exc)
        logger.warning("Kalshi fetch error for %s: %s — will retry next cycle", ticker, msg)
        return _FetchResult(error_msg=msg)


def _extract_result(market_data: dict) -> str | None:
    """
    Return "yes", "no", "void", or None (not yet settled).

    Kalshi stores the settlement result in different fields depending on
    API version.  We check the most common locations in order.
    """
    # "result" field (current API)
    result = (market_data.get("result") or "").lower()
    if result in ("yes", "no"):
        return result

    # Canceled / voided market
    status = (market_data.get("status") or "").lower()
    if status in ("canceled", "cancelled", "voided", "canceled_voided"):
        return "void"

    # Not settled yet
    return None


async def run_settlement_job() -> dict[str, int]:
    """
    Check all open paper trades against Kalshi and update settled ones.

    Returns stats: {"checked", "settled", "voided", "errors", "still_open"}
    Safe to call repeatedly — idempotent for already-settled trades.
    """
    from app.database import AsyncSessionLocal

    stats: dict[str, int] = {
        "checked": 0, "settled": 0, "voided": 0,
        "errors": 0, "still_open": 0,
    }

    if AsyncSessionLocal is None:
        logger.error("Database not initialised — cannot run settlement job.")
        return stats

    async with AsyncSessionLocal() as session:
        open_q = await session.execute(
            select(PaperTrade).where(PaperTrade.status == "OPEN")
        )
        open_trades = open_q.scalars().all()

        if not open_trades:
            logger.info("Settlement job: no open paper trades.")
            return stats

        logger.info("Settlement job: checking %d open trade(s).", len(open_trades))

        for trade in open_trades:
            stats["checked"] += 1
            try:
                fetch = await fetch_kalshi_market(trade.market_ticker)

                if fetch.transient_error:
                    # Network / 5xx / timeout — leave OPEN so next cycle retries.
                    # Append a non-permanent note to warnings but do NOT change status.
                    note = f"Settlement check skipped (transient): {fetch.error_msg}"
                    existing = trade.warnings or ""
                    # Only keep the most recent transient-error note to avoid unbounded growth.
                    parts = [p.strip() for p in existing.split(";") if "transient" not in p.lower()]
                    parts.append(note)
                    trade.warnings = "; ".join(p for p in parts if p)
                    stats["still_open"] += 1
                    logger.info(
                        "Trade %d (%s) left OPEN after transient fetch error: %s",
                        trade.id, trade.market_ticker, fetch.error_msg,
                    )
                    continue

                if fetch.not_found:
                    # 404 is terminal — Kalshi no longer has this market.
                    trade.status = "ERROR"
                    existing = trade.warnings or ""
                    parts = [p.strip() for p in existing.split(";") if p.strip()]
                    parts.append("Settlement failed: market not found on Kalshi (404)")
                    trade.warnings = "; ".join(parts)
                    stats["errors"] += 1
                    continue

                market_data = fetch.data  # guaranteed non-None here
                kalshi_result = _extract_result(market_data)

                if kalshi_result is None:
                    stats["still_open"] += 1
                    continue  # not settled yet — try again next run

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
                        "Paper trade %d (%s) → VOID (market canceled).",
                        trade.id, trade.market_ticker,
                    )
                else:
                    trade.status = "SETTLED"
                    stats["settled"] += 1
                    logger.info(
                        "Paper trade %d (%s %s) → SETTLED: %s (P/L $%.2f)",
                        trade.id, trade.direction, trade.market_ticker,
                        outcome["outcome"], outcome["profit_loss"],
                    )

            except Exception as exc:
                stats["errors"] += 1
                logger.warning(
                    "Settlement error for trade %d (%s): %s",
                    trade.id, trade.market_ticker, exc,
                )
                try:
                    session.add(AppError(
                        error_type="settlement_job",
                        message=str(exc)[:500],
                        context=f"trade_id={trade.id}, ticker={trade.market_ticker}",
                    ))
                except Exception:
                    pass

        await session.commit()

    logger.info(
        "Settlement job done: %d checked, %d settled, %d voided, %d errors, %d still open.",
        stats["checked"], stats["settled"], stats["voided"],
        stats["errors"], stats["still_open"],
    )
    return stats
