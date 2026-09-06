"""
V3 JIT Quote Shadow Audit — Phase 3B
======================================
For V3 candidates stopped by a stale/missing executable quote
(eligibility_reason = "missing_or_stale_executable_quote"), this module
performs one read-only just-in-time Kalshi quote confirmation **immediately
before final eligibility completion** and stores the result for diagnostics.

Safety invariants (enforced here and verified by tests):
  - The JIT result NEVER changes eligibility_status, eligibility_reason,
    entry price, direction, edge, model probability, or any trade field.
  - Written in a separate session from the V3PaperTrade row.
  - Fail-closed: any exception during the JIT fetch is caught, stored as
    jit_outcome="error", and does not propagate to the caller.
  - Only called when eligibility_reason is exactly REASON_STALE_QUOTE.
  - Does not read or modify any V3PaperTrade row.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models_v3 import V3JitQuoteAudit
from app.services.eligibility import (
    REASON_CUTOFF,
    REASON_EXTREME_EDGE,
    REASON_HOURLY,
    REASON_PRICE_FLOOR,
    REASON_SAME_DAY,
    REASON_STATION,
    REASON_STALE_QUOTE,
    assess_trade_eligibility,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Outcome code constants — stable strings stored in jit_outcome column
# ---------------------------------------------------------------------------
JIT_OUTCOME_UNCHANGED     = "unchanged"
JIT_OUTCOME_CHANGED       = "changed"
JIT_OUTCOME_NO_ASK        = "no_ask"
JIT_OUTCOME_INACTIVE      = "inactive_market"
JIT_OUTCOME_TIMEOUT       = "timeout"
JIT_OUTCOME_HTTP_ERROR    = "http_error"
JIT_OUTCOME_RATE_LIMIT    = "rate_limit"
JIT_OUTCOME_PARSE_ERROR   = "parse_error"
JIT_OUTCOME_ERROR         = "error"

# Maximum time to wait for the JIT quote fetch.
_JIT_TIMEOUT_SECONDS: float = 5.0


# ---------------------------------------------------------------------------
# Internal: fetch a single market from Kalshi
# ---------------------------------------------------------------------------

async def _fetch_single_market(ticker: str) -> dict[str, Any]:
    """
    Fetch one Kalshi market by ticker.  Returns the raw JSON dict.

    Raises:
      httpx.TimeoutException   — on timeout
      httpx.HTTPStatusError    — on HTTP 4xx/5xx
      ValueError               — on parse failure
    """
    settings = get_settings()
    base = settings.kalshi_base_url
    url = f"{base}/markets/{ticker}"
    async with httpx.AsyncClient(timeout=_JIT_TIMEOUT_SECONDS, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        # Kalshi wraps the market in a "market" key for the single-market endpoint
        if "market" in data:
            return data["market"]
        # Fallback: some endpoints return the market directly
        if "ticker" in data:
            return data
        raise ValueError(f"Unexpected Kalshi market response shape for {ticker!r}")


def _normalise_price(raw: Any) -> float | None:
    """Convert cents-or-fraction price to fraction in [0, 1], or None."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    # Kalshi returns prices in cents (0–100); normalise to fraction
    if v > 1.0:
        v = v / 100.0
    return round(v, 4) if 0.0 <= v <= 1.0 else None


# ---------------------------------------------------------------------------
# Internal: evaluate other-guards pass/fail against stored trade fields
# ---------------------------------------------------------------------------

def _evaluate_other_guards(
    *,
    contract_type: str | None,
    target_settlement_date_str: str | None,
    settlement_timezone: str | None,
    decision_timestamp: datetime | None,
    side_market_price: float | None,
    edge_pct_points: float | None,
    station_verified: bool | None,
    direction: str | None,
    market_close_timestamp: datetime | None,
) -> tuple[bool, str | None]:
    """
    Re-evaluate every eligibility guard EXCEPT the quote guard (Guard 8)
    using stored contemporaneous fields.

    Returns (all_pass: bool, fail_reason: str | None).
    """
    now = decision_timestamp or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    tz = settlement_timezone or "UTC"

    # We inject a synthetic valid quote to bypass Guard 8 so assess_trade_eligibility
    # evaluates all other guards normally.  This is safe because:
    #   - We use a fixed synthetic value (age=0, ask=0.5) only for guard-bypass.
    #   - The returned eligibility result is used only for other_guards_pass in
    #     the audit row; it never affects any trade classification.
    synthetic_quote_ts = now
    synthetic_quote_ask: float = 0.50  # any valid price

    status, reason, _ = assess_trade_eligibility(
        contract_type=contract_type,
        target_settlement_date_str=target_settlement_date_str,
        settlement_timezone=tz,
        now=now,
        side_market_price=side_market_price,
        edge_pct_points=edge_pct_points,
        station_verified=bool(station_verified),
        direction=direction or "YES",
        quote_timestamp=synthetic_quote_ts,
        quote_ask=synthetic_quote_ask,
        market_close_timestamp=market_close_timestamp,
    )
    if status == "OFFICIAL":
        return True, None
    # Exclude the stale-quote reason itself — that's what we bypassed
    if reason == REASON_STALE_QUOTE:
        return True, None
    return False, reason


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def perform_jit_quote_shadow(
    *,
    market_ticker: str,
    v3_paper_trade_id: int | None = None,
    direction: str | None,
    collection_quote_ask: float | None,
    collection_quote_age_seconds: float | None,
    decision_timestamp: datetime | None,
    collection_batch_id: str | None,
    # Fields for other-guard evaluation
    contract_type: str | None,
    target_settlement_date_str: str | None,
    settlement_timezone: str | None,
    side_market_price: float | None,
    edge_pct_points: float | None,
    station_verified: bool | None,
    market_close_timestamp: datetime | None,
) -> None:
    """
    Perform a shadow JIT quote check and persist the result in a separate
    database session.

    SHADOW/AUDIT ONLY — return value is None. The caller MUST NOT use the
    return value of this function to change any trade field, eligibility
    classification, or decision input.

    Fail-closed: any exception is caught and logged; the audit row still
    records the error.  Failures never propagate to the caller.
    """
    jit_yes_ask:      float | None = None
    jit_no_ask:       float | None = None
    jit_market_status: str | None  = None
    jit_outcome:      str          = JIT_OUTCOME_ERROR
    jit_fetch_ts:     datetime | None = None
    jit_latency_ms:   float | None = None
    error_detail:     str | None   = None
    other_guards_pass: bool | None = None
    other_guards_fail_reason: str | None = None

    # Evaluate other guards before the JIT fetch (uses only stored fields)
    try:
        other_guards_pass, other_guards_fail_reason = _evaluate_other_guards(
            contract_type=contract_type,
            target_settlement_date_str=target_settlement_date_str,
            settlement_timezone=settlement_timezone,
            decision_timestamp=decision_timestamp,
            side_market_price=side_market_price,
            edge_pct_points=edge_pct_points,
            station_verified=station_verified,
            direction=direction,
            market_close_timestamp=market_close_timestamp,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("JIT audit other-guard evaluation error for %s: %s", market_ticker, exc)
        # Leave other_guards_pass=None if we couldn't evaluate

    # Perform the JIT fetch
    start_ns = time.monotonic_ns()
    try:
        jit_fetch_ts = datetime.now(timezone.utc)
        raw = await _fetch_single_market(market_ticker)
        jit_latency_ms = (time.monotonic_ns() - start_ns) / 1_000_000

        jit_market_status = raw.get("status")
        if jit_market_status != "active":
            jit_outcome = JIT_OUTCOME_INACTIVE
        else:
            jit_yes_ask = _normalise_price(
                raw.get("yes_ask_dollars") or raw.get("yes_ask")
            )
            jit_no_ask = _normalise_price(
                raw.get("no_ask_dollars") or raw.get("no_ask")
            )
            side_jit_ask = jit_yes_ask if direction == "YES" else jit_no_ask
            if side_jit_ask is None:
                jit_outcome = JIT_OUTCOME_NO_ASK
            elif collection_quote_ask is None:
                # Was missing at collection time; now present
                jit_outcome = JIT_OUTCOME_CHANGED
            elif abs(side_jit_ask - collection_quote_ask) < 1e-6:
                jit_outcome = JIT_OUTCOME_UNCHANGED
            else:
                jit_outcome = JIT_OUTCOME_CHANGED

    except asyncio.TimeoutError:
        jit_latency_ms = (time.monotonic_ns() - start_ns) / 1_000_000
        jit_outcome = JIT_OUTCOME_TIMEOUT
        error_detail = "asyncio.TimeoutError"
        logger.debug("JIT quote timeout for %s", market_ticker)
    except httpx.TimeoutException as exc:
        jit_latency_ms = (time.monotonic_ns() - start_ns) / 1_000_000
        jit_outcome = JIT_OUTCOME_TIMEOUT
        error_detail = str(exc)[:500]
        logger.debug("JIT quote HTTP timeout for %s: %s", market_ticker, exc)
    except httpx.HTTPStatusError as exc:
        jit_latency_ms = (time.monotonic_ns() - start_ns) / 1_000_000
        status_code = exc.response.status_code
        jit_outcome = JIT_OUTCOME_RATE_LIMIT if status_code == 429 else JIT_OUTCOME_HTTP_ERROR
        error_detail = f"HTTP {status_code}"
        logger.debug("JIT quote HTTP error for %s: %s", market_ticker, exc)
    except ValueError as exc:
        jit_latency_ms = (time.monotonic_ns() - start_ns) / 1_000_000
        jit_outcome = JIT_OUTCOME_PARSE_ERROR
        error_detail = str(exc)[:500]
        logger.debug("JIT quote parse error for %s: %s", market_ticker, exc)
    except Exception as exc:  # noqa: BLE001
        jit_latency_ms = (time.monotonic_ns() - start_ns) / 1_000_000
        jit_outcome = JIT_OUTCOME_ERROR
        error_detail = repr(exc)[:500]
        logger.warning("JIT quote unexpected error for %s: %s", market_ticker, exc, exc_info=True)

    # Persist the audit row in its own isolated session
    try:
        async with AsyncSessionLocal() as session:
            audit = V3JitQuoteAudit(
                market_ticker=market_ticker,
                v3_paper_trade_id=v3_paper_trade_id,
                collection_batch_id=collection_batch_id,
                direction=direction,
                collection_quote_ask=collection_quote_ask,
                collection_quote_age_seconds=collection_quote_age_seconds,
                decision_timestamp=decision_timestamp,
                jit_fetch_timestamp=jit_fetch_ts,
                jit_latency_ms=jit_latency_ms,
                jit_outcome=jit_outcome,
                jit_yes_ask=jit_yes_ask,
                jit_no_ask=jit_no_ask,
                jit_market_status=jit_market_status,
                other_guards_pass=other_guards_pass,
                other_guards_fail_reason=other_guards_fail_reason,
                error_detail=error_detail,
            )
            session.add(audit)
            await session.commit()
            logger.debug(
                "JIT quote audit stored: ticker=%s outcome=%s latency=%.0fms other_guards=%s",
                market_ticker, jit_outcome, jit_latency_ms or 0, other_guards_pass,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "JIT quote audit DB write failed for %s: %s", market_ticker, exc, exc_info=True
        )
