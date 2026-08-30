"""
V3: Historical Preload — Database Models
=========================================
All tables are V3-specific and use the ``v3_`` prefix.  No existing table,
column, or index is modified by importing this module.

Strategy isolation guarantee
-----------------------------
V3 models never share mutable state with V1, V2, or V2.1.  The only shared
surface is the read-only ``AppSetting`` KV store (for feature flags) and the
``PredictionSnapshot`` table which receives a nullable ``comparison_group_id``
column (additive, no constraint on existing rows — added via _apply_migrations
in database.py).

Database safety
---------------
All tables are created via ``Base.metadata.create_all`` with ``checkfirst=True``
(called through ``create_all`` in ``database.py``).  Running on an environment
where these tables already exist is safe — SQLAlchemy's create_all only creates
missing objects; it never drops or alters existing ones.

Rollback procedure (Phase 1)
------------------------------
1. Set ``v3.ingestion_enabled = false`` in ``app_settings``.
2. No V3 code path runs.  Tables remain populated but dormant.
3. Full cleanup: ``DROP TABLE v3_ingestion_log, v3_historical_records,
   v3_raw_source_records, v3_error_stats, v3_prediction_snapshots, v3_paper_trades``
   (in that order to respect soft references).
   This has zero effect on any non-V3 table.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, Integer, JSON, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# ---------------------------------------------------------------------------
# V3 Feature flag keys (documented here for discoverability)
# ---------------------------------------------------------------------------
# v3.ingestion_enabled     — allows POST /analytics/v3/run-ingestion
# v3.validation_enabled    — Phase 2: allows compute-error-stats and walkforward
# v3.predictions_enabled   — Phase 3: enables V3 prediction hook in scheduler
# v3.paper_trading_enabled — Phase 3: enables V3 settlement hook in scheduler
#
# v3.ingestion_enabled defaults to "false" (Phase 1 data pipeline — managed
# separately via the audit UI).  All other V3 flags default to "true" so that
# predictions, paper trading, and validation are active on every deployment.
# The startup upgrade in database._enable_required_flags() also upgrades any
# existing "false" rows left by earlier deployments.

V3_FLAG_DEFAULTS: dict[str, str] = {
    "v3.ingestion_enabled":     "false",  # Phase 1 only — never auto-enabled
    "v3.validation_enabled":    "true",
    "v3.predictions_enabled":   "true",
    "v3.paper_trading_enabled": "true",
}

CURRENT_PRELOAD_VERSION = "v3.0"
TRANSFORMATION_VERSION  = "v1.0"


# ---------------------------------------------------------------------------
# V3RawSourceRecord — verbatim API response, immutable after insert
# ---------------------------------------------------------------------------

class V3RawSourceRecord(Base):
    """
    Stores the raw API response exactly as received, before any normalization,
    unit conversion, daily aggregation, or transformation.  Immutable after
    insert — never updated.  Referenced by V3HistoricalRecord.raw_source_id.

    Having the raw record allows:
    - Re-running normalization if the transformation logic changes.
    - Auditing exactly what the provider returned.
    - Detecting unit or format changes across provider API versions.
    """

    __tablename__ = "v3_raw_source_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Provider identity
    provider: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # e.g. "open-meteo-forecast-history"
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    # e.g. "GFS", "ECMWF", "GEFS"
    model_version: Mapped[str | None] = mapped_column(String(100))
    # API-reported model version string; NULL when provider does not expose it

    # Location context
    city: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    station_id: Mapped[str] = mapped_column(String(50), nullable=False)
    # Exact GHCND station ID from SettlementStation; never a city-centre proxy

    # Request provenance
    retrieval_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # UTC timestamp when we made the API call / accessed the archive
    raw_source_identifier: Mapped[str] = mapped_column(String(500), nullable=False)
    # Opaque string that uniquely identifies this request:
    # e.g. "open-meteo-forecast-history?lat=35.39&lon=-97.60&start=2024-01-15&forecast_days=3"
    source_provenance: Mapped[str] = mapped_column(Text, nullable=False)
    # Human-readable description of origin, e.g.:
    # "Open-Meteo Historical Forecast API; GFS model; 3-day lead; fetched 2026-07-30"

    # The verbatim response
    raw_response: Mapped[dict | None] = mapped_column(JSON)
    # Full JSON body exactly as returned by the provider API

    # Normalization bookkeeping
    transformation_version: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TRANSFORMATION_VERSION
    )
    # Version of the normalization code that processed this record; bump when
    # transformation logic changes so old raw records can be re-processed


# ---------------------------------------------------------------------------
# V3HistoricalRecord — normalized, validated forecast-vs-observation pair
# ---------------------------------------------------------------------------

class V3HistoricalRecord(Base):
    """
    One row per (city, station, target_date, forecast_source, model, lead_time_hours).

    A record is considered COMPLETE when both forecast_tmax_f (from the NWP
    model archive) and observed_tmax_f (from the official NOAA GHCND station)
    are populated and quality_status = 'ok'.

    Uniqueness: the same historical forecast run cannot be loaded twice.
    The unique constraint covers all dimensions that define one data point.
    """

    __tablename__ = "v3_historical_records"
    __table_args__ = (
        UniqueConstraint(
            "city", "station_id", "target_date",
            "forecast_source", "forecast_model", "lead_time_hours",
            "preload_version",
            name="uq_v3_hist_city_station_date_src_model_lead_ver",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ingestion_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Preload versioning
    preload_version: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CURRENT_PRELOAD_VERSION, index=True
    )
    # Bump this key to distinguish re-ingestions from different preload runs

    # Link to immutable raw source (soft reference — no FK constraint in SQLite)
    raw_source_id: Mapped[int | None] = mapped_column(Integer, index=True)

    # Station identity
    city: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    station_id: Mapped[str] = mapped_column(String(50), nullable=False)
    station_name: Mapped[str | None] = mapped_column(String(300))
    station_lat: Mapped[float] = mapped_column(Float, nullable=False)
    station_lon: Mapped[float] = mapped_column(Float, nullable=False)
    local_timezone: Mapped[str] = mapped_column(String(60), nullable=False)

    # Time dimensions
    target_date: Mapped[str] = mapped_column(String(20), nullable=False)
    # YYYY-MM-DD in the station's local timezone

    # Forecast provenance
    forecast_source: Mapped[str] = mapped_column(String(100), nullable=False)
    # Provider key, e.g. "open-meteo-forecast-history"
    forecast_model: Mapped[str] = mapped_column(String(100), nullable=False)
    # Model name, e.g. "GFS", "GEFS", "ECMWF"
    model_version: Mapped[str | None] = mapped_column(String(100))

    # Forecast timestamps (all UTC)
    forecast_init_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the NWP model run was initialized; CRITICAL for look-ahead validation.
    # NULL means the provider did not return init time → record is rejected.
    forecast_valid_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When this forecast is valid (corresponds to target_date in local TZ)
    forecast_retrieval_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When we fetched/accessed the archive record

    # Lead time
    lead_time_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    # Nominal hours from forecast_init_time to forecast_valid_time.
    # For derived timestamps this is the provider's declared nominal value (e.g. 24),
    # which may differ from the exact computed lead (forecast_valid_time - forecast_init_time).
    lead_time_bucket: Mapped[str] = mapped_column(String(10), nullable=False)
    # Short-range buckets (requires init_time_source = "api_provided"):
    #   "0-6h" | "6-12h" | "12-18h" | "18-24h" | "24-36h" | "36-48h"
    # Daily buckets (used when init_time is derived / not precisely known):
    #   "1d" | "2d" | "3d" | "4d" | "5d" | "6d" | "7d"
    # Phase 2 groups records by this bucket for bias/sigma estimation.
    # Broad fallback groups ("2d", "3d", …) are used when a short-range bucket
    # has insufficient sample size for stable estimates (< MIN_SAMPLE threshold).

    # Timestamp provenance
    init_time_source: Mapped[str] = mapped_column(
        String(30), nullable=False, default="derived_prior_day_00z"
    )
    # How forecast_init_time was determined:
    #   "api_provided"        — provider returned an explicit model run timestamp;
    #                           exact short-range lead-time buckets are trustworthy.
    #   "derived_prior_day_00z" — conservatively estimated as valid_date − 1 day at
    #                           00:00 UTC; short-range buckets CANNOT be computed;
    #                           lead_time_bucket is forced to the nominal daily value.
    # Future providers that expose model run metadata (NOAA GFS archives, GEFS
    # ensemble files) should set "api_provided" and populate the correct init_time.

    # Forecast values (normalized, Fahrenheit)
    forecast_tmax_f: Mapped[float | None] = mapped_column(Float)
    # Daily maximum temperature in °F as predicted by the model at issue time.
    # NULL if the provider did not return a value for this date/lead.

    # Observation values (from official NOAA GHCND station)
    observed_tmax_f: Mapped[float | None] = mapped_column(Float)
    # Official NOAA GHCND TMAX in °F for the exact settlement station.
    # NULL until observation is fetched (quality_status = 'pending_observation').

    # Derived error metrics (populated when both forecast and observation exist)
    signed_error: Mapped[float | None] = mapped_column(Float)
    # observed_tmax_f - forecast_tmax_f; positive = model ran cold
    abs_error: Mapped[float | None] = mapped_column(Float)
    squared_error: Mapped[float | None] = mapped_column(Float)

    # Calendar context
    month: Mapped[int | None] = mapped_column(Integer)    # 1–12
    season: Mapped[str | None] = mapped_column(String(10))
    # "winter" | "spring" | "summer" | "fall"

    # Quality control
    quality_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending_observation"
    )
    # "ok" | "rejected" | "pending_observation"
    missing_data_flags: Mapped[list | None] = mapped_column(JSON)
    # List of flag strings, e.g. ["MISSING_INIT_TIME", "OBSERVATION_UNAVAILABLE"]
    rejection_reason: Mapped[str | None] = mapped_column(String(200))
    # Populated when quality_status = "rejected"

    # Transformation metadata
    transformation_version: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TRANSFORMATION_VERSION
    )
    unit_conversions: Mapped[dict | None] = mapped_column(JSON)
    # Dict documenting conversions performed, e.g. {"tmax": "celsius_to_fahrenheit"}


# ---------------------------------------------------------------------------
# V3ErrorStats — V3-specific bias/sigma, computed from V3HistoricalRecord
# ---------------------------------------------------------------------------

class V3ErrorStats(Base):
    """
    V3-specific bias and sigma computed from V3HistoricalRecord rows.
    Entirely separate from ForecastErrorStats (used by V2/V2.1).

    One row per (city, model, lead_time_bucket, season, fallback_level).
    fallback_level 0 = most specific; 4 = global conservative prior.
    """

    __tablename__ = "v3_error_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    last_computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    preload_version: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CURRENT_PRELOAD_VERSION
    )

    city: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    # "__global__" for cross-city fallback rows
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    lead_time_bucket: Mapped[str] = mapped_column(String(10), nullable=False)
    # Short-range: "0-6h" | "6-12h" | "12-18h" | "18-24h" | "24-36h" | "36-48h"
    # Daily:       "1d" | "2d" | "3d" | "4d" | "5d" | "6d" | "7d" | "__all__"
    # Short-range buckets are only populated when the training data was sourced
    # from a provider with init_time_source = "api_provided".  Current data
    # from Open-Meteo date-range mode uses "1d" only.
    season: Mapped[str | None] = mapped_column(String(10))
    # "winter" | "spring" | "summer" | "fall" | NULL (all-season)
    fallback_level: Mapped[int] = mapped_column(Integer, nullable=False)
    # 0: city+model+lead+season  (most specific)
    # 1: city+model+lead
    # 2: city+model
    # 3: model+lead
    # 4: global conservative fallback

    raw_sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    effective_n: Mapped[float | None] = mapped_column(Float)
    # = raw_sample_size * discount_factor; reflects autocorrelation in daily weather

    bias: Mapped[float | None] = mapped_column(Float)
    # mean(observed - forecast) in °F; positive = model ran cold on average.
    # This is the SHRUNK bias (after partial pooling); always stored even when
    # the bias gate blocks it from being applied to a prediction.
    sigma_raw: Mapped[float | None] = mapped_column(Float)
    # std_dev of (observed - forecast) before shrinkage
    sigma_shrunk: Mapped[float | None] = mapped_column(Float)
    # After partial pooling toward parent level; always >= SIGMA_FLOOR (3.5°F).
    # sigma_shrunk is ALWAYS applied to predictions regardless of the bias gate.
    # It is the primary calibration signal from the historical preload.
    mae: Mapped[float | None] = mapped_column(Float)
    rmse: Mapped[float | None] = mapped_column(Float)

    # ── Bias gate fields (Phase 3 two-component architecture) ──────────────
    bias_t_stat: Mapped[float | None] = mapped_column(Float)
    # |bias| / (sigma_raw / sqrt(n_eff)); measures if bias is statistically
    # distinguishable from zero.  NULL when n_eff < 2 or sigma_raw unavailable.
    bias_gate_passed: Mapped[bool | None] = mapped_column(Boolean)
    # True only when ALL of the following hold:
    #   1. n_eff >= config.bias_min_effective_n  (default 50)
    #   2. |bias_t_stat| >= config.bias_min_t_stat  (default 2.0, ≈ 95% CI)
    #   3. |bias| >= config.bias_min_magnitude  (default 0.3°F)
    # When False, the bias field is stored for reference but NOT applied to
    # predictions; only sigma_shrunk influences the probability distribution.
    bias_suppressed_reason: Mapped[str | None] = mapped_column(String(200))
    # Human-readable reason why bias_gate_passed is False, e.g.:
    # "n_eff=28.2 < 50.0" | "|t|=1.34 < 2.0" | "|bias|=0.21°F < 0.3°F"


# ---------------------------------------------------------------------------
# V3PredictionSnapshot — V3 prediction output (Phase 3)
# ---------------------------------------------------------------------------

class V3PredictionSnapshot(Base):
    """
    V3-specific prediction record.  Created independently of PredictionSnapshot.
    Shared markets are linked via comparison_group_id.

    NOTE: comparison_group_id is also added as a nullable column on the existing
    prediction_snapshots table (additive migration in database.py) so the two
    sides of a paired comparison can be joined.
    """

    __tablename__ = "v3_prediction_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    market_ticker: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    comparison_group_id: Mapped[str | None] = mapped_column(String(36), index=True)
    # UUID linking this V3 snapshot to the V2.1 PredictionSnapshot for the same market

    # Forecast inputs
    forecast_date: Mapped[str | None] = mapped_column(String(20))
    forecast_value: Mapped[float | None] = mapped_column(Float)
    forecast_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    forecast_model: Mapped[str | None] = mapped_column(String(100))
    forecast_source: Mapped[str | None] = mapped_column(String(100))

    # Settlement contract fields
    settlement_variable: Mapped[str | None] = mapped_column(String(30))
    settlement_operator: Mapped[str | None] = mapped_column(String(10))
    settlement_threshold: Mapped[float | None] = mapped_column(Float)
    contract_type: Mapped[str | None] = mapped_column(String(30))

    # V3 bias/sigma decomposition (the key distinguishing feature from V2.1)
    historical_bias_adj: Mapped[float | None] = mapped_column(Float)
    # Bias adjustment from V3 historical preload
    historical_sigma: Mapped[float | None] = mapped_column(Float)
    # Sigma from V3 historical preload
    forward_learning_adj: Mapped[float | None] = mapped_column(Float)
    # Incremental adjustment from V3 forward observations (0.0 initially)
    final_bias: Mapped[float | None] = mapped_column(Float)
    final_sigma: Mapped[float | None] = mapped_column(Float)

    # Sample counts
    hist_sample_count: Mapped[int | None] = mapped_column(Integer)
    effective_hist_n: Mapped[float | None] = mapped_column(Float)
    v3_forward_count: Mapped[int | None] = mapped_column(Integer)

    # Fallback metadata
    fallback_level_used: Mapped[int | None] = mapped_column(Integer)
    # 0–4 matching V3ErrorStats.fallback_level
    config_version: Mapped[str | None] = mapped_column(String(50))

    # Probability outputs
    ec_probability: Mapped[float | None] = mapped_column(Float)
    market_probability: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[str | None] = mapped_column(String(20))
    claimed_edge: Mapped[float | None] = mapped_column(Float)

    # Two-component architecture (Phase 3)
    bias_applied: Mapped[bool | None] = mapped_column(Boolean)
    # True = bias correction was applied to mu; False = sigma-only prediction
    bias_suppressed_reason: Mapped[str | None] = mapped_column(String(200))
    # Non-empty when bias_applied is False (why the gate rejected the bias)

    # Decision
    trade_decision: Mapped[str | None] = mapped_column(String(20))
    # "YES" | "NO" | "SKIP" | "PENDING" | "SUPERSEDED"
    # "SUPERSEDED" — a newer prediction has been created for this ticker in a
    # subsequent collection cycle.  Trade-linked rows (those referenced by a
    # V3PaperTrade.v3_snapshot_id) are never superseded so trade provenance is
    # always intact.
    decision_reason: Mapped[str | None] = mapped_column(Text)
    analysis_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="unsupported"
    )


# ---------------------------------------------------------------------------
# V3PaperTrade — V3 paper trades (Phase 3)
# ---------------------------------------------------------------------------

class V3PaperTrade(Base):
    """
    V3-specific paper trade.  Never touches paper_trades (V1/V2/V2.1).
    One row per (market_ticker, "v3.0") — V3 strategy version is always "v3.0".
    """

    __tablename__ = "v3_paper_trades"
    __table_args__ = (
        UniqueConstraint(
            "market_ticker", "strategy_version",
            name="uq_v3_paper_trade_ticker_strategy",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    market_ticker: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    city: Mapped[str | None] = mapped_column(String(200))
    weather_variable: Mapped[str | None] = mapped_column(String(30))
    contract_type: Mapped[str | None] = mapped_column(String(30))
    target_settlement_date: Mapped[str | None] = mapped_column(String(50))
    strategy_version: Mapped[str] = mapped_column(
        String(50), nullable=False, default="v3.0"
    )
    v3_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    # Reference to V3PredictionSnapshot.id
    comparison_group_id: Mapped[str | None] = mapped_column(String(36), index=True)
    # Prospective comparison linkage — shared with V2.1 + V2.2 for the same
    # (cycle, ticker).  NULL on pre-comparison-snapshot trades.
    comparison_snapshot_id: Mapped[str | None] = mapped_column(String(36), index=True)
    collection_batch_id: Mapped[str | None] = mapped_column(String(36), index=True)

    # Trade decision (immutable)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    ec_yes_probability: Mapped[float | None] = mapped_column(Float)
    ec_side_probability: Mapped[float | None] = mapped_column(Float)
    market_yes_probability: Mapped[float | None] = mapped_column(Float)
    side_market_price: Mapped[float | None] = mapped_column(Float)
    edge_pct_points: Mapped[float | None] = mapped_column(Float)
    lead_time_days: Mapped[int | None] = mapped_column(Integer)

    # V3-specific fields (preserved for attribution)
    historical_bias_adj: Mapped[float | None] = mapped_column(Float)
    historical_sigma: Mapped[float | None] = mapped_column(Float)
    final_bias: Mapped[float | None] = mapped_column(Float)
    final_sigma: Mapped[float | None] = mapped_column(Float)
    fallback_level_used: Mapped[int | None] = mapped_column(Integer)
    hist_sample_count: Mapped[int | None] = mapped_column(Integer)
    effective_hist_n: Mapped[float | None] = mapped_column(Float)
    v3_forward_count: Mapped[int | None] = mapped_column(Integer)

    # Position
    stake: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[float | None] = mapped_column(Float)
    is_executable: Mapped[bool | None] = mapped_column(Boolean)
    station_verified: Mapped[bool | None] = mapped_column(Boolean)
    station_lat: Mapped[float | None] = mapped_column(Float)
    station_lon: Mapped[float | None] = mapped_column(Float)

    # Lifecycle
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    kalshi_result: Mapped[str | None] = mapped_column(String(10))
    outcome: Mapped[str | None] = mapped_column(String(10))
    settlement_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gross_payout: Mapped[float | None] = mapped_column(Float)
    profit_loss: Mapped[float | None] = mapped_column(Float)
    return_pct: Mapped[float | None] = mapped_column(Float)

    decision_explanation: Mapped[str | None] = mapped_column(Text)
    quality_flags: Mapped[list | None] = mapped_column(JSON)

    # Official Trade Eligibility — hardening pass
    eligibility_status: Mapped[str | None] = mapped_column(String(20), index=True)
    eligibility_reason: Mapped[str | None] = mapped_column(String(60))
    quote_age_seconds: Mapped[float | None] = mapped_column(Float)

    # Safety hardening pass 2 — market close time and decision audit trail
    market_close_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_settlement_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    minutes_to_market_close: Mapped[float | None] = mapped_column(Float)
    settlement_timezone: Mapped[str | None] = mapped_column(String(100))


# ---------------------------------------------------------------------------
# V3JitQuoteAudit — shadow JIT quote confirmation records (Phase 3B)
# ---------------------------------------------------------------------------

class V3JitQuoteAudit(Base):
    """
    Shadow record written when a V3 candidate is stopped by a stale/missing
    executable quote (eligibility_reason = "missing_or_stale_executable_quote").

    A just-in-time read-only Kalshi quote confirmation is attempted
    immediately before final eligibility completion.  The result is stored
    here for diagnostics only.

    Safety invariants:
      - This record NEVER changes the trade's eligibility_status,
        eligibility_reason, entry price, direction, edge, model probability,
        classification, or any historical record.
      - Fail-closed: any error during the JIT fetch is stored as
        jit_outcome="error" and does not affect trade creation.
      - Written in a separate DB session from the trade row to ensure zero
        coupling between shadow state and trade state.
    """

    __tablename__ = "v3_jit_quote_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Identity linking
    market_ticker: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    v3_paper_trade_id: Mapped[int | None] = mapped_column(Integer, index=True)
    # Set after trade row is created; NULL if trade creation failed.

    collection_batch_id: Mapped[str | None] = mapped_column(String(36), index=True)

    # Context at time of JIT fetch
    direction: Mapped[str | None] = mapped_column(String(10))
    collection_quote_ask: Mapped[float | None] = mapped_column(Float)
    # The stale/missing ask that caused RESEARCH_ONLY classification.
    collection_quote_age_seconds: Mapped[float | None] = mapped_column(Float)
    # Age of the collection-time quote (may be None if quote was absent).
    decision_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # JIT fetch result
    jit_fetch_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    jit_latency_ms: Mapped[float | None] = mapped_column(Float)

    # Outcome of the JIT fetch.
    # Values: "unchanged" | "changed" | "no_ask" | "inactive_market"
    #         | "timeout" | "http_error" | "rate_limit" | "parse_error" | "error"
    jit_outcome: Mapped[str | None] = mapped_column(String(30), index=True)

    jit_yes_ask: Mapped[float | None] = mapped_column(Float)
    jit_no_ask: Mapped[float | None] = mapped_column(Float)
    jit_market_status: Mapped[str | None] = mapped_column(String(20))

    # Whether all OTHER eligibility guards (excluding the quote guard) passed
    # based on stored contemporaneous fields.
    other_guards_pass: Mapped[bool | None] = mapped_column(Boolean)
    other_guards_fail_reason: Mapped[str | None] = mapped_column(String(60))

    error_detail: Mapped[str | None] = mapped_column(Text)
    # Stores exception message on failure outcomes.


# ---------------------------------------------------------------------------
# V3IngestionLog — per-run audit trail
# ---------------------------------------------------------------------------

class V3IngestionLog(Base):
    """
    One row per city+provider+date-range run.  Persists regardless of success
    or failure so the audit view can show exactly what happened.
    """

    __tablename__ = "v3_ingestion_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # UUID shared by all log rows from the same orchestrator invocation

    city: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[str] = mapped_column(String(20), nullable=False)
    # YYYY-MM-DD
    end_date: Mapped[str] = mapped_column(String(20), nullable=False)
    lead_times_json: Mapped[list | None] = mapped_column(JSON)
    # List of lead_time_hours values requested, e.g. [24, 48, 72, 96, 120, 144, 168]

    records_attempted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejection_breakdown: Mapped[dict | None] = mapped_column(JSON)
    # {reason_string: count}, e.g. {"LOOKAHEAD_VIOLATION": 3, "MISSING_INIT_TIME": 1}
    missing_observation_dates: Mapped[list | None] = mapped_column(JSON)
    # List of YYYY-MM-DD strings where NOAA observation was unavailable
    api_errors: Mapped[list | None] = mapped_column(JSON)
    # List of error message strings from provider API calls

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    # "running" | "success" | "partial" | "failed"
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
