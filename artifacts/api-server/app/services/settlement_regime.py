"""
app/services/settlement_regime.py
----------------------------------
Authoritative module for settlement-source regime determination.

Settlement regime tracks WHICH data authority governs a Kalshi daily
temperature contract's final settlement value.

REGIME CONSTANTS
----------------
LEGACY_NWS
    Applies to all contracts whose settlement date precedes 2026-08-14.
    Settlement was determined from the National Weather Service (NWS)
    official climate observation (NOAA GHCND / CLI reports).

WEATHER_COMPANY
    Applies to all contracts whose settlement date is 2026-08-14 or later.
    Kalshi's notice (published ~2026-08-12):
        "Effective Friday, August 14th, daily temperature markets will
        transition their settlement source from the National Weather Service
        (NWS) to The Weather Company. The Weather Company utilizes NWS as
        its primary underlying source, and official settlement data will be
        accessible at weather.com/kalshi."

INVESTIGATION STATUS (as of 2026-08-12)
-----------------------------------------
VERIFIED FACTS:
  - Kalshi confirmed the transition in market notices.
  - Effective date: Friday, August 14, 2026.
  - weather.com/kalshi is the new official settlement URL.
  - The Weather Company uses NWS as its primary underlying data source
    (per Kalshi's own announcement).

UNRESOLVED QUESTIONS (cannot be verified pre-transition):
  - Exact rounding rules for The Weather Company temperature values.
  - Whether station/location mappings change for any city.
  - Time-zone and observation-day boundary rules under the new source.
  - Machine-readability of weather.com/kalshi (API vs page-scrape).
  - Reliability and latency of the new settlement data publication.
  - Whether any systematic differences exist between NWS CLI reports
    and The Weather Company's reported high/low temperatures.

SAFETY RULE:
  Never mix LEGACY_NWS and WEATHER_COMPANY performance figures in a single
  calibration computation until the methodology equivalence is confirmed.
  Analytics must always support regime-filtered views.

HISTORICAL INTEGRITY:
  Never rewrite historical LEGACY_NWS settlements retroactively.
  Pre-transition trades remain valid under the rules that applied when
  those contracts existed.
"""
from __future__ import annotations

from datetime import date

# ── Regime constants (never change these — historical records depend on them) ──

REGIME_LEGACY_NWS: str = "LEGACY_NWS"
REGIME_WEATHER_COMPANY: str = "WEATHER_COMPANY"

# The calendar date on which The Weather Company settlement source takes effect.
WEATHER_COMPANY_TRANSITION_DATE: date = date(2026, 8, 14)


def infer_settlement_regime(target_settlement_date_str: str | None) -> str:
    """
    Determine the settlement regime from a contract's target settlement date.

    Parameters
    ----------
    target_settlement_date_str:
        The ``target_settlement_date`` string stored on a ``PaperTrade`` row,
        e.g. ``"2026-08-14T19:00:00Z"`` or ``"2026-08-14"``.
        ``None`` is treated conservatively as LEGACY_NWS.

    Returns
    -------
    str
        ``REGIME_LEGACY_NWS`` or ``REGIME_WEATHER_COMPANY``.

    Notes
    -----
    Regime is determined by the *settlement date*, not by when the trade was
    *created*.  A trade created on Aug 12 for a contract settling Aug 14 is
    a WEATHER_COMPANY trade even though it was evaluated under NWS rules.
    The transition is about which authority publishes the final result.

    Do NOT determine regime from EdgeCast's collection timestamp — the
    contract's own settlement rules govern which source applies.
    """
    if not target_settlement_date_str:
        return REGIME_LEGACY_NWS
    try:
        # Accept both "2026-08-14T19:00:00Z" and "2026-08-14" formats
        date_part = target_settlement_date_str.split("T")[0].strip()
        settlement_date = date.fromisoformat(date_part)
        if settlement_date >= WEATHER_COMPANY_TRANSITION_DATE:
            return REGIME_WEATHER_COMPANY
    except (ValueError, TypeError, AttributeError):
        pass
    return REGIME_LEGACY_NWS


def describe_regime(regime: str | None) -> str:
    """Return a short human-readable description of a settlement regime code."""
    if regime == REGIME_WEATHER_COMPANY:
        return "The Weather Company (weather.com/kalshi)"
    if regime == REGIME_LEGACY_NWS:
        return "National Weather Service (NWS GHCND / CLI)"
    return "Unknown (assumed NWS — pre-migration row)"
