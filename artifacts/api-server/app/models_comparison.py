"""
ComparisonSnapshot — frozen market + forecast inputs for one collection batch.

One row per (collection_batch_id, market_ticker).  Created before any paper
trading runs so that V2.1, V2.2, and V3 all receive identical inputs.

Rows are IMMUTABLE after creation.  Never updated.

Why a separate table (not a column on PredictionSnapshot)?
  PredictionSnapshot is V2/V2.1-specific (one per run, may be SUPERSEDED).
  This table belongs to the collection batch, not the prediction model.
  It captures the market quote + forecast as-of the cycle start so the
  source-of-truth is independent of any single strategy's snapshot schema.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ComparisonSnapshot(Base):
    """
    Frozen record of shared market + forecast inputs for one (batch, ticker).

    Linked from paper_trades.comparison_snapshot_id (V2.1, V2.2) and
    v3_paper_trades.comparison_snapshot_id (V3).  When all three strategies
    reference the same row, the trade row is "strictly paired" — meaning all
    probability calculations started from identical quote and forecast data.

    ``collection_batch_id`` is a UUID generated once per collection cycle.
    ``id`` is a UUID generated once per (batch, ticker).
    """

    __tablename__ = "comparison_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "collection_batch_id", "market_ticker",
            name="uq_comparison_snapshot_batch_ticker",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    collection_batch_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ── Market identification ──────────────────────────────────────────────

    market_ticker: Mapped[str] = mapped_column(
        String(300), nullable=False, index=True
    )
    city: Mapped[str | None] = mapped_column(String(200))
    weather_variable: Mapped[str | None] = mapped_column(String(30))
    contract_type: Mapped[str | None] = mapped_column(String(30))
    settlement_date: Mapped[str | None] = mapped_column(String(50))

    # Contract bounds (threshold, operator, or lower/upper for range contracts)
    threshold: Mapped[float | None] = mapped_column(Float)
    operator: Mapped[str | None] = mapped_column(String(10))
    lower_bound: Mapped[float | None] = mapped_column(Float)
    upper_bound: Mapped[float | None] = mapped_column(Float)

    # ── Quote — frozen from KalshiMarket.collection_timestamp ─────────────
    # These are the prices that ALL strategies evaluated; they never change.

    quote_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    yes_bid: Mapped[float | None] = mapped_column(Float)
    yes_ask: Mapped[float | None] = mapped_column(Float)
    no_bid: Mapped[float | None] = mapped_column(Float)
    no_ask: Mapped[float | None] = mapped_column(Float)
    # mid-point yes probability derived from bids at collection time
    market_yes_probability: Mapped[float | None] = mapped_column(Float)

    # ── Forecast — from the latest PredictionSnapshot at cycle start ───────

    forecast_value: Mapped[float | None] = mapped_column(Float)
    forecast_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    forecast_lead_time_days: Mapped[int | None] = mapped_column(Integer)
