"""
Comparison Snapshot Service
===========================
Creates one ComparisonSnapshot row per (batch_id, market_ticker) before any
paper trading runs in a collection cycle.

All three strategies (V2.1, V2.2, V3) link their paper trade rows to the
SAME ComparisonSnapshot so downstream analysis can verify identical inputs.

Called by the collector (Step 5e-pre) with the session already open.
The rows are flushed (not committed) so the strategies can see them within
the same transaction.  The strategies' own commit makes them durable.

Returns: dict[market_ticker → comparison_snapshot_id]
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KalshiMarket, PredictionSnapshot
from app.models_comparison import ComparisonSnapshot

logger = logging.getLogger(__name__)


async def create_comparison_snapshots_for_batch(
    session: AsyncSession,
    batch_id: str,
) -> dict[str, str]:
    """
    Build one ComparisonSnapshot per active (PredictionSnapshot, KalshiMarket)
    pair for this cycle.

    Uses the same snapshot-selection logic as paper_trading_v21:
      - Latest PredictionSnapshot per market_ticker (by id DESC).
      - Only markets with status == 'active'.

    Returns a dict mapping market_ticker → snapshot UUID.
    """
    # -- Latest PredictionSnapshot per ticker --------------------------------
    snaps_q = await session.execute(
        select(PredictionSnapshot).order_by(
            PredictionSnapshot.market_ticker,
            PredictionSnapshot.id.desc(),
        )
    )
    all_snaps = snaps_q.scalars().all()

    seen: set[str] = set()
    latest_snaps: dict[str, PredictionSnapshot] = {}
    for s in all_snaps:
        if s.market_ticker not in seen:
            seen.add(s.market_ticker)
            latest_snaps[s.market_ticker] = s

    # -- Active KalshiMarket map ---------------------------------------------
    markets_q = await session.execute(
        select(KalshiMarket).where(KalshiMarket.status == "active")
    )
    market_map: dict[str, KalshiMarket] = {
        m.ticker: m for m in markets_q.scalars().all()
    }

    # -- Build snapshots -----------------------------------------------------
    snap_ids: dict[str, str] = {}
    created = 0

    for ticker, ps in latest_snaps.items():
        market = market_map.get(ticker)
        if market is None:
            continue

        snap_id = str(uuid.uuid4())

        # Derive market_yes_probability from bid/ask if not on snapshot
        mkt_prob = ps.market_probability
        if mkt_prob is None and market.yes_bid is not None and market.yes_ask is not None:
            mkt_prob = round((market.yes_bid + market.yes_ask) / 2.0, 4)

        cs = ComparisonSnapshot(
            id=snap_id,
            collection_batch_id=batch_id,
            market_ticker=ticker,
            city=market.city,
            weather_variable=ps.settlement_variable,
            contract_type=ps.contract_type,
            settlement_date=market.target_date,
            threshold=ps.settlement_threshold,
            operator=ps.settlement_operator,
            lower_bound=ps.lower_bound,
            upper_bound=ps.upper_bound,
            # Quote from KalshiMarket (frozen at collection time)
            quote_timestamp=market.collection_timestamp,
            yes_bid=market.yes_bid,
            yes_ask=market.yes_ask,
            no_bid=market.no_bid,
            no_ask=market.no_ask,
            market_yes_probability=mkt_prob,
            # Forecast from the latest PredictionSnapshot
            forecast_value=ps.forecast_value,
            forecast_timestamp=ps.forecast_retrieved_at,
            forecast_lead_time_days=ps.lead_time_days,
        )
        session.add(cs)
        snap_ids[ticker] = snap_id
        created += 1

    await session.flush()
    logger.info(
        "Comparison snapshots: %d created for batch %s", created, batch_id[:8]
    )
    return snap_ids
