from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class KalshiEvent(Base):
    __tablename__ = "kalshi_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_ticker: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(100))
    series_ticker: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KalshiMarket(Base):
    __tablename__ = "kalshi_markets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    event_ticker: Mapped[str | None] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(500))
    city: Mapped[str | None] = mapped_column(String(200))
    target_date: Mapped[str | None] = mapped_column(String(50))
    open_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    yes_bid: Mapped[float | None] = mapped_column(Float)
    yes_ask: Mapped[float | None] = mapped_column(Float)
    no_bid: Mapped[float | None] = mapped_column(Float)
    no_ask: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    weather_matched: Mapped[bool] = mapped_column(Boolean, default=False)
    # Per-market parsing metadata
    parsing_status: Mapped[str | None] = mapped_column(String(50))   # collected | parsing_failure
    parsing_reason: Mapped[str | None] = mapped_column(Text)
    weather_market_type: Mapped[str | None] = mapped_column(String(50))  # temperature | rain | snow | wind | weather
    collection_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_data: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WeatherLocation(Base):
    __tablename__ = "weather_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), default="auto")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WeatherForecast(Base):
    __tablename__ = "weather_forecasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city: Mapped[str] = mapped_column(String(200), nullable=False)
    forecast_date: Mapped[str] = mapped_column(String(20), nullable=False)
    temperature_high: Mapped[float | None] = mapped_column(Float)
    temperature_low: Mapped[float | None] = mapped_column(Float)
    precipitation_prob: Mapped[float | None] = mapped_column(Float)
    wind_speed: Mapped[float | None] = mapped_column(Float)
    forecast_json: Mapped[dict | None] = mapped_column(JSON)
    # Phase 2B: list of {hour: int, temperature: float} for each hour of the day
    hourly_data: Mapped[list | None] = mapped_column(JSON)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="running"
    )
    markets_found: Mapped[int | None] = mapped_column(Integer)
    markets_skipped: Mapped[int | None] = mapped_column(Integer)
    markets_rejected: Mapped[int | None] = mapped_column(Integer)
    forecasts_retrieved: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    error_message: Mapped[str | None] = mapped_column(Text)
    # Phase 3A paper-trading counts
    pt_candidates: Mapped[int | None] = mapped_column(Integer)
    pt_created: Mapped[int | None] = mapped_column(Integer)
    pt_yes_trades: Mapped[int | None] = mapped_column(Integer)
    pt_no_trades: Mapped[int | None] = mapped_column(Integer)
    pt_skipped: Mapped[int | None] = mapped_column(Integer)
    pt_errors: Mapped[int | None] = mapped_column(Integer)


class AppError(Base):
    __tablename__ = "app_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    error_type: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PredictionSnapshot(Base):
    """
    Immutable record of EdgeCast's probability estimate for a market at a
    specific point in time.  A new snapshot is written on every collection
    run; old snapshots are never overwritten or deleted.  This allows future
    accuracy tracking and time-series analysis.
    """

    __tablename__ = "prediction_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_ticker: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Forecast fields
    forecast_date: Mapped[str | None] = mapped_column(String(20))
    forecast_value: Mapped[float | None] = mapped_column(Float)
    forecast_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lead_time_days: Mapped[int | None] = mapped_column(Integer)

    # Settlement contract fields (extracted from market title/subtitle)
    settlement_variable: Mapped[str | None] = mapped_column(String(30))   # 'high' | 'low' | 'hourly_temperature'
    settlement_operator: Mapped[str | None] = mapped_column(String(10))   # 'gte' | 'lte' | None (range)
    settlement_threshold: Mapped[float | None] = mapped_column(Float)

    # Phase 2B contract type and range/hourly fields
    contract_type: Mapped[str | None] = mapped_column(String(30))         # 'threshold' | 'range' | 'hourly_threshold'
    target_hour: Mapped[int | None] = mapped_column(Integer)              # 0-23 local time for hourly contracts
    target_timezone_str: Mapped[str | None] = mapped_column(String(20))   # e.g. 'EDT' for hourly contracts
    lower_bound: Mapped[float | None] = mapped_column(Float)              # lower temp bound for range contracts
    upper_bound: Mapped[float | None] = mapped_column(Float)              # upper temp bound for range contracts

    # Probability outputs
    ec_probability: Mapped[float | None] = mapped_column(Float)
    market_probability: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[str | None] = mapped_column(String(20))
    explanation: Mapped[str | None] = mapped_column(Text)

    # Analysis meta
    analysis_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="unsupported"
    )
    analysis_reason: Mapped[str | None] = mapped_column(Text)


class PaperTrade(Base):
    """
    Simulated paper trade record.  Created automatically after each collection
    run for every market that meets the EdgeCast eligibility criteria.

    IMPORTANT: original entry values (prices, probabilities, stake) are NEVER
    updated after creation.  Only status/settlement fields are written later.
    No real trades are placed.  No Kalshi trading credentials are used.
    """

    __tablename__ = "paper_trades"
    __table_args__ = (
        UniqueConstraint(
            "market_ticker", "strategy_version",
            name="uq_paper_trade_ticker_strategy",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Market / contract identification
    market_ticker: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    event_ticker: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(200))
    weather_variable: Mapped[str | None] = mapped_column(String(30))    # 'high' | 'low' | 'hourly_temperature'
    contract_type: Mapped[str | None] = mapped_column(String(30))       # 'threshold' | 'range' | 'hourly_threshold'
    target_settlement_date: Mapped[str | None] = mapped_column(String(50))

    # Snapshot link (immutable — never updated after creation)
    snapshot_id: Mapped[int | None] = mapped_column(Integer)

    # Strategy
    strategy_version: Mapped[str] = mapped_column(String(50), nullable=False, default="v1.0")

    # Trade decision (immutable)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)   # 'YES' | 'NO'
    ec_yes_probability: Mapped[float | None] = mapped_column(Float)
    ec_side_probability: Mapped[float | None] = mapped_column(Float)
    market_yes_probability: Mapped[float | None] = mapped_column(Float)
    side_market_price: Mapped[float | None] = mapped_column(Float)       # purchase price in [0,1]
    price_source: Mapped[str | None] = mapped_column(String(20))         # 'YES_ASK' | 'YES_BID' | 'NO_ASK' | 'NO_BID'
    edge_pct_points: Mapped[float | None] = mapped_column(Float)         # percentage points, e.g. 15.3

    # Confidence (immutable)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    confidence_label: Mapped[str | None] = mapped_column(String(20))

    # Position (immutable)
    stake: Mapped[float] = mapped_column(Float, nullable=False)          # dollars
    quantity: Mapped[float | None] = mapped_column(Float)                # contracts = stake / price

    # Lifecycle status
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    # OPEN → SETTLED | VOID | ERROR

    # Settlement (written after Kalshi finalizes)
    kalshi_result: Mapped[str | None] = mapped_column(String(10))        # 'yes' | 'no' | 'void'
    outcome: Mapped[str | None] = mapped_column(String(10))              # 'WIN' | 'LOSS' | 'VOID'
    settlement_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gross_payout: Mapped[float | None] = mapped_column(Float)            # dollars
    profit_loss: Mapped[float | None] = mapped_column(Float)             # dollars (negative = loss)
    return_pct: Mapped[float | None] = mapped_column(Float)              # percentage, e.g. -100.0

    # Forecast lead time at trade creation (immutable, copied from snapshot)
    lead_time_days: Mapped[int | None] = mapped_column(Integer)

    # Explanation and warnings
    decision_explanation: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[str | None] = mapped_column(Text)                  # semicolon-separated list

    # Phase 3B: structured data-quality flags (JSON list of flag name strings)
    quality_flags: Mapped[list | None] = mapped_column(JSON)
