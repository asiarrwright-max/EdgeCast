"""
Settlement processing for EdgeCast paper trades — Phase 3A.

Queries the Kalshi REST API (public, no credentials required) to check
whether open paper trades have settled.  Settlement is determined solely
from authoritative Kalshi data — never inferred from weather forecasts.

Status lifecycle:
  OPEN → SETTLED (Kalshi result: "yes" or "no" confirmed)
  OPEN → VOID    (Kalshi market canceled/voided)
  OPEN → PENDING_SETTLEMENT (market closed/finalized but no result yet)
  OPEN → ERROR   (Kalshi 404 — market no longer exists; terminal)
  PENDING_SETTLEMENT → SETTLED / VOID / ERROR (re-checked every cycle)
  Transient errors (network/5xx): status unchanged, warning note appended.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AppError, PaperTrade
from app.services.paper_trading import settle_position

logger = logging.getLogger(__name__)

# Kalshi market statuses that mean the market has closed but the result
# has not yet been published.  We put the trade in PENDING_SETTLEMENT
# rather than leaving it OPEN or treating it as a loss.
_PENDING_STATUSES = frozenset({"closed", "finalized"})


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
    Return "yes", "no", "void", "pending", or None (not yet settled).

    "pending"   — market is closed/finalized but no result published yet.
                  Caller should move trade to PENDING_SETTLEMENT.

    "yes"/"no"  — confirmed final result from Kalshi.
    "void"      — market was canceled/voided.
    None        — market still open/active; leave trade OPEN.

    Kalshi stores the settlement result in different fields depending on
    API version.  We check the most common locations in order.
    """
    status = (market_data.get("status") or "").lower()

    # Canceled / voided market — check before result to avoid misclassification
    if status in ("canceled", "cancelled", "voided", "canceled_voided"):
        return "void"

    # "result" field (current API) — only trust it when it is an explicit YES/NO
    result = (market_data.get("result") or "").lower().strip()
    if result == "yes":
        return "yes"
    if result == "no":
        return "no"

    # Market has closed or been finalized but no official result yet —
    # NEVER treat the absence of a result as a loss.
    if status in _PENDING_STATUSES:
        return "pending"

    # Market is still open / active — keep the trade OPEN
    return None


async def run_settlement_job() -> dict[str, int]:
    """
    Check all open and pending-settlement paper trades against Kalshi
    and update settled ones.

    Returns stats:
      {"checked", "settled", "voided", "pending_settlement", "errors", "still_open"}

    Safe to call repeatedly — idempotent for already-settled trades.
    """
    from app.database import AsyncSessionLocal

    stats: dict[str, int] = {
        "checked": 0,
        "settled": 0,
        "voided": 0,
        "pending_settlement": 0,
        "errors": 0,
        "still_open": 0,
    }

    if AsyncSessionLocal is None:
        logger.error("Database not initialised — cannot run settlement job.")
        return stats

    async with AsyncSessionLocal() as session:
        # Check both OPEN and PENDING_SETTLEMENT trades every cycle.
        open_q = await session.execute(
            select(PaperTrade).where(
                or_(
                    PaperTrade.status == "OPEN",
                    PaperTrade.status == "PENDING_SETTLEMENT",
                )
            )
        )
        open_trades = open_q.scalars().all()

        if not open_trades:
            logger.info("Settlement job: no open or pending paper trades.")
            return stats

        logger.info(
            "Settlement job: checking %d trade(s) (OPEN + PENDING_SETTLEMENT).",
            len(open_trades),
        )

        for trade in open_trades:
            stats["checked"] += 1
            try:
                fetch = await fetch_kalshi_market(trade.market_ticker)

                if fetch.transient_error:
                    # Network / 5xx / timeout — leave status unchanged so next cycle retries.
                    note = f"Settlement check skipped (transient): {fetch.error_msg}"
                    existing = trade.warnings or ""
                    # Only keep the most recent transient-error note to avoid unbounded growth.
                    parts = [p.strip() for p in existing.split(";") if "transient" not in p.lower()]
                    parts.append(note)
                    trade.warnings = "; ".join(p for p in parts if p)
                    stats["still_open"] += 1
                    logger.info(
                        "Trade %d (%s) unchanged after transient fetch error: %s",
                        trade.id, trade.market_ticker, fetch.error_msg,
                    )
                    # Log transient failure separately for monitoring
                    try:
                        session.add(AppError(
                            error_type="settlement_transient",
                            message=f"Transient fetch error: {fetch.error_msg}"[:500],
                            context=f"trade_id={trade.id}, ticker={trade.market_ticker}",
                        ))
                    except Exception:
                        pass
                    continue

                if fetch.not_found:
                    # 404 is terminal — Kalshi no longer has this market.
                    trade.status = "ERROR"
                    existing = trade.warnings or ""
                    parts = [p.strip() for p in existing.split(";") if p.strip()]
                    parts.append("Settlement failed: market not found on Kalshi (404)")
                    trade.warnings = "; ".join(parts)
                    stats["errors"] += 1
                    logger.warning(
                        "Trade %d (%s) → ERROR: market not found on Kalshi (404).",
                        trade.id, trade.market_ticker,
                    )
                    continue

                market_data = fetch.data  # guaranteed non-None here
                kalshi_result = _extract_result(market_data)

                if kalshi_result is None:
                    # Market still active — leave OPEN
                    stats["still_open"] += 1
                    continue

                if kalshi_result == "pending":
                    # Market is closed/finalized but no result published yet.
                    # Move to PENDING_SETTLEMENT — NEVER treat as a loss.
                    if trade.status != "PENDING_SETTLEMENT":
                        trade.status = "PENDING_SETTLEMENT"
                        existing = trade.warnings or ""
                        parts = [p.strip() for p in existing.split(";") if p.strip()
                                 and "pending settlement" not in p.lower()]
                        parts.append(
                            "Market closed/finalized; awaiting official Kalshi result "
                            f"(checked {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')})"
                        )
                        trade.warnings = "; ".join(parts)
                    stats["pending_settlement"] += 1
                    logger.info(
                        "Trade %d (%s) → PENDING_SETTLEMENT (market closed, no result yet).",
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

                # Stamp settlement regime if not already set (covers pre-migration rows).
                # Kalshi is the authoritative settlement source, so Kalshi-confirmed
                # outcomes are marked verified.  ERA5 discrepancies are handled
                # separately via the integrity audit — they set outcome_verified=False.
                if trade.settlement_regime is None:
                    from app.services.settlement_regime import infer_settlement_regime
                    trade.settlement_regime = infer_settlement_regime(
                        trade.target_settlement_date
                    )
                if trade.outcome_verified is None:
                    trade.outcome_verified = True  # Kalshi is authoritative source

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
        "Settlement job done: %d checked, %d settled, %d voided, "
        "%d pending, %d errors, %d still open.",
        stats["checked"], stats["settled"], stats["voided"],
        stats["pending_settlement"], stats["errors"], stats["still_open"],
    )
    return stats
