"""
V3 Provider Registry
====================
Maps provider keys to their concrete ``ForecastHistoryProvider`` implementations.

To add a new data source:
  1. Create a module under ``app/services/v3_providers/`` that subclasses
     ``ForecastHistoryProvider``.
  2. Add an entry to ``_PROVIDER_CLASSES`` below.
  3. The ingestion engine automatically picks it up — no other file needs changing.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.v3_providers.base import ForecastHistoryProvider


def _build_registry() -> dict[str, type["ForecastHistoryProvider"]]:
    # Imports are deferred to avoid circular dependencies at module load time.
    from app.services.v3_providers.open_meteo_forecast_history import (
        OpenMeteoForecastHistoryProvider,
    )

    return {
        OpenMeteoForecastHistoryProvider.PROVIDER_KEY: OpenMeteoForecastHistoryProvider,
        # Future providers:
        # "noaa-gfs-archive": NoaaGfsArchiveProvider,
        # "gefs-archive": GefsArchiveProvider,
        # "ecmwf-archive": EcmwfArchiveProvider,
    }


_registry: dict[str, type["ForecastHistoryProvider"]] | None = None


def get_provider_class(provider_key: str) -> type["ForecastHistoryProvider"]:
    """
    Return the provider class for the given key.

    Raises ``KeyError`` with a helpful message if the key is unknown.
    """
    global _registry
    if _registry is None:
        _registry = _build_registry()
    if provider_key not in _registry:
        available = ", ".join(sorted(_registry.keys()))
        raise KeyError(
            f"Unknown V3 forecast provider: '{provider_key}'. "
            f"Available: {available}"
        )
    return _registry[provider_key]


def get_all_provider_keys() -> list[str]:
    """Return all registered provider keys."""
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return sorted(_registry.keys())
