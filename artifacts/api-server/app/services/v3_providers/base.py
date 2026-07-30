"""
V3 Forecast History Provider Interface
=======================================
Defines the contract every historical-forecast provider must satisfy.

The abstraction exists so the V3 ingestion engine, learning model, and analytics
are all provider-agnostic.  Adding a new source (GEFS, ECMWF, NOAA archived GFS)
requires only:
  1. A new class that subclasses ``ForecastHistoryProvider``.
  2. Registration in ``registry.py``.

Nothing in the V3 learning engine or ingestion orchestrator needs to change.

Data integrity requirements (enforced by providers)
-----------------------------------------------------
Every ``RawForecastRecord`` returned by a provider MUST have:
- ``forecast_init_time`` populated (UTC, timezone-aware).
  If the underlying API cannot provide the model initialization time,
  the provider MUST raise ``ProviderDataError`` rather than return a record
  with a guessed or absent init time.
- ``forecast_valid_time`` populated (UTC, timezone-aware).
- A clear distinction between "model output" and "reanalysis".
  Providers that serve reanalysis (e.g. ERA5) MUST set
  ``is_reanalysis=True`` so the look-ahead validator can reject them.

A provider MUST NOT blend records from different underlying models into one
unlabeled aggregate.  Separate model runs must be returned as separate records.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class ProviderDataError(Exception):
    """Raised when a provider cannot satisfy data-integrity requirements."""


@dataclass
class RawForecastRecord:
    """
    One archived model forecast for a single city / date / lead-time, exactly
    as the provider returned it (values in the provider's native units).

    Fields that start with ``raw_`` are verbatim from the API response and
    must not be modified after construction.
    """

    # ── Provider identity ──────────────────────────────────────────────────
    provider: str
    # Provider key matching registry.py, e.g. "open-meteo-forecast-history"
    model: str
    # Underlying NWP model, e.g. "GFS", "GEFS", "ECMWF"
    model_version: str | None
    # API-reported version string; None when provider does not expose it

    # ── Location ───────────────────────────────────────────────────────────
    city: str
    station_id: str
    # GHCND station ID from SettlementStation; never a city-centre proxy
    station_lat: float
    station_lon: float
    local_timezone: str

    # ── Time dimensions ────────────────────────────────────────────────────
    forecast_init_time: datetime
    # UTC, timezone-aware.  When the NWP model run was initialized.
    # CRITICAL: look-ahead validator uses this to ensure the forecast was
    # available before the simulated decision time.
    forecast_valid_time: datetime
    # UTC, timezone-aware.  When this forecast applies.
    retrieval_timestamp: datetime
    # UTC, timezone-aware.  When we fetched / accessed the archive.
    target_date_local: str
    # YYYY-MM-DD in the station's local timezone.
    lead_time_hours: int
    # Nominal lead: round((forecast_valid_time - forecast_init_time).total_seconds() / 3600)

    # ── Forecast value (native provider units) ─────────────────────────────
    forecast_tmax_raw: float
    # Daily max temperature in the provider's native unit (usually Celsius).
    raw_unit: str
    # "celsius" | "fahrenheit" | "kelvin"

    # ── Provenance ─────────────────────────────────────────────────────────
    raw_source_identifier: str
    # Opaque string that reproduces this exact fetch, e.g. a URL or cache key.
    source_provenance: str
    # Human-readable description for the audit log.

    # ── Full raw response ──────────────────────────────────────────────────
    raw_response: Any
    # The verbatim API response (dict / str) for this record.
    # Stored in V3RawSourceRecord.raw_response before any transformation.

    # ── Data-quality flags ─────────────────────────────────────────────────
    is_reanalysis: bool = False
    # True when the provider served reanalysis data (e.g. ERA5) rather than
    # a genuine archived model forecast.  Records with is_reanalysis=True are
    # rejected by the look-ahead validator with reason REANALYSIS_NOT_ALLOWED.
    data_flags: list[str] = field(default_factory=list)
    # Any provider-side quality flags, e.g. ["INTERPOLATED", "COAST_CORRECTION"]


class ForecastHistoryProvider(ABC):
    """
    Abstract base class for V3 historical forecast providers.

    Subclass this for every external data source: Open-Meteo Forecast History,
    NOAA archived GFS, GEFS, ECMWF, etc.
    """

    PROVIDER_KEY: str
    """Unique lowercase-hyphen key registered in registry.py."""

    MODEL: str
    """Primary underlying NWP model this provider serves, e.g. "GFS"."""

    @abstractmethod
    async def fetch_history(
        self,
        city: str,
        station_id: str,
        lat: float,
        lon: float,
        local_timezone: str,
        start_date: str,   # YYYY-MM-DD (local)
        end_date: str,     # YYYY-MM-DD (local)
        lead_time_hours_list: list[int],
        # e.g. [24, 48, 72, 96, 120, 144, 168] for 1-day through 7-day lead times
    ) -> list[RawForecastRecord]:
        """
        Fetch archived model forecasts for a city/station over a date range.

        The provider is responsible for:
        - Returning one ``RawForecastRecord`` per (date, lead_time_hours) pair
          that has data.
        - Populating ``forecast_init_time`` and ``forecast_valid_time`` for every
          record; raising ``ProviderDataError`` if the API cannot supply them.
        - Setting ``is_reanalysis=True`` if the data source is reanalysis.
        - Returning raw values in the provider's native units.

        The ingestion orchestrator handles normalization, unit conversion,
        look-ahead validation, and DB persistence.
        """
        ...
