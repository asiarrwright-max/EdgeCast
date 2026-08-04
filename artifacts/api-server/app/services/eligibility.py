"""
app/services/eligibility.py
Official Trade Eligibility Engine — V2.2 / V3 Hardening Pass

Applies eight independent per-trade guards to decide whether a qualifying
trade is an OFFICIAL paper trade (forward-test record) or a RESEARCH_ONLY
signal (stored but excluded from all official metrics).

Guard numbering matches the specification:
  1. Daily weather only    — no hourly_threshold contracts
  2. Lead time ≥ 1 day    — settlement-location timezone, not UTC
  3. Hard cutoff guard     — configurable buffer before settlement close
  4. Entry-price floor     — configurable minimum (default $0.20)
  5. Extreme-edge guard    — configurable ceiling (default 50 pp)
  6. Correlated-exposure   — batch-level only; not applied here
  7. Verified NWS station  — city must have verified=True station
  8. Fresh executable quote— quote_timestamp non-null, within freshness window;
                              YES uses yes_ask, NO uses no_ask

A ninth "nws_settlement" hard-block (e.g. Washington DC) is handled
UPSTREAM in the pipeline and prevents the trade decision from being
reached at all — it is NOT an eligibility classification, it is a SKIP.

Returns
-------
(eligibility_status, eligibility_reason, quote_age_seconds)
  eligibility_status : "OFFICIAL" | "RESEARCH_ONLY"
  eligibility_reason : reason-code string or None when OFFICIAL
  quote_age_seconds  : float seconds since quote was taken, or None
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable defaults (can be overridden at call-site from AppSettings)
# ---------------------------------------------------------------------------

OFFICIAL_MIN_ENTRY_PRICE: float = 0.20           # Guard 4
OFFICIAL_MAX_EDGE_PP: float = 50.0               # Guard 5
OFFICIAL_CUTOFF_BUFFER_MINUTES: int = 120        # Guard 3
OFFICIAL_STALE_QUOTE_SECONDS: int = 4 * 3600     # Guard 8 — 4 hours


# ---------------------------------------------------------------------------
# Reason codes (stable strings; used in DB rows and API responses)
# ---------------------------------------------------------------------------

REASON_HOURLY           = "hourly_temperature_not_approved"
REASON_STATION          = "settlement_station_unverified"
REASON_STALE_QUOTE      = "missing_or_stale_executable_quote"
REASON_CUTOFF           = "cutoff_unverified_or_too_close"
REASON_SAME_DAY         = "same_day_not_approved"
REASON_PRICE_FLOOR      = "entry_price_below_official_floor"
REASON_EXTREME_EDGE     = "extreme_edge_requires_validation"
REASON_CORRELATED       = "correlated_outcome_limit"


# ---------------------------------------------------------------------------
# Settlement-date parser
# ---------------------------------------------------------------------------

def _parse_settlement_dt(date_str: str) -> datetime:
    """
    Parse a settlement date string to a timezone-aware UTC datetime.

    Accepts:
      "2026-08-04T14:00:00Z"  — ISO 8601 with Z suffix
      "2026-08-04T14:00:00+00:00" — ISO 8601 with offset
      "2026-08-04"             — date only; treated as 23:59 UTC that day
    """
    date_str = date_str.strip()
    if "T" in date_str:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    # Date-only — assume end of day UTC as conservative bound
    from datetime import date as _date
    d = _date.fromisoformat(date_str[:10])
    return datetime(d.year, d.month, d.day, 23, 59, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Core eligibility assessment
# ---------------------------------------------------------------------------

def assess_trade_eligibility(
    *,
    contract_type: str | None,
    target_settlement_date_str: str | None,
    settlement_timezone: str,
    now: datetime,
    side_market_price: float | None,
    edge_pct_points: float | None,
    station_verified: bool,
    direction: str,
    quote_timestamp: datetime | None,
    quote_ask: float | None,
    min_entry_price: float = OFFICIAL_MIN_ENTRY_PRICE,
    max_edge_pp: float = OFFICIAL_MAX_EDGE_PP,
    cutoff_buffer_minutes: int = OFFICIAL_CUTOFF_BUFFER_MINUTES,
    stale_quote_seconds: int = OFFICIAL_STALE_QUOTE_SECONDS,
) -> tuple[str, str | None, float | None]:
    """
    Return ``(eligibility_status, eligibility_reason, quote_age_seconds)``.

    Guards are evaluated in this order; the first failure is returned.
    Passing all guards returns ``("OFFICIAL", None, quote_age_seconds)``.

    Parameters
    ----------
    contract_type
        "threshold" | "range" | "hourly_threshold" — from snap.contract_type.
    target_settlement_date_str
        Market settlement date string (ISO 8601 or YYYY-MM-DD).
    settlement_timezone
        IANA timezone string for the settlement station (e.g. "America/Denver").
    now
        Current UTC time (timezone-aware).
    side_market_price
        Entry price in [0, 1].
    edge_pct_points
        Claimed edge in percentage points (e.g. 15.3).
    station_verified
        Whether the settlement station has been confirmed from Kalshi/CFTC docs.
    direction
        "YES" or "NO".
    quote_timestamp
        When the market quote was fetched (timezone-aware).
    quote_ask
        The ask price on our side: yes_ask for YES direction, no_ask for NO.
    """
    # Normalise quote age first — used in every return path
    quote_age_seconds: float | None = None
    if quote_timestamp is not None:
        qt = quote_timestamp
        if qt.tzinfo is None:
            qt = qt.replace(tzinfo=timezone.utc)
        now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        age = (now_aware - qt).total_seconds()
        quote_age_seconds = max(0.0, age)

    # Guard 1: Daily weather only
    if contract_type == "hourly_threshold":
        return "RESEARCH_ONLY", REASON_HOURLY, quote_age_seconds

    # Guard 7: Verified NWS settlement station
    if not station_verified:
        return "RESEARCH_ONLY", REASON_STATION, quote_age_seconds

    # Guard 8: Fresh executable quote
    if quote_timestamp is None:
        return "RESEARCH_ONLY", REASON_STALE_QUOTE, quote_age_seconds
    if quote_ask is None:
        return "RESEARCH_ONLY", REASON_STALE_QUOTE, quote_age_seconds
    if quote_age_seconds is not None and quote_age_seconds > stale_quote_seconds:
        return "RESEARCH_ONLY", REASON_STALE_QUOTE, quote_age_seconds

    # Guard 3 + Guard 2: Settlement cutoff + same-day check
    if not target_settlement_date_str:
        return "RESEARCH_ONLY", REASON_CUTOFF, quote_age_seconds
    try:
        from zoneinfo import ZoneInfo
        settlement_dt = _parse_settlement_dt(target_settlement_date_str)
        now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        seconds_to_settlement = (settlement_dt - now_aware).total_seconds()

        # Guard 3: Cutoff buffer
        if seconds_to_settlement < cutoff_buffer_minutes * 60:
            return "RESEARCH_ONLY", REASON_CUTOFF, quote_age_seconds

        # Guard 2: Same-day in settlement-station local timezone
        local_tz = ZoneInfo(settlement_timezone)
        settlement_local_date = settlement_dt.astimezone(local_tz).date()
        now_local_date = now_aware.astimezone(local_tz).date()
        lead_local = (settlement_local_date - now_local_date).days
        if lead_local < 1:
            return "RESEARCH_ONLY", REASON_SAME_DAY, quote_age_seconds

    except Exception as exc:
        logger.warning("Eligibility cutoff parse error for %r: %s", target_settlement_date_str, exc)
        return "RESEARCH_ONLY", REASON_CUTOFF, quote_age_seconds

    # Guard 4: Entry-price floor
    if side_market_price is not None and side_market_price < min_entry_price:
        return "RESEARCH_ONLY", REASON_PRICE_FLOOR, quote_age_seconds

    # Guard 5: Extreme-edge cap
    if edge_pct_points is not None and edge_pct_points >= max_edge_pp:
        return "RESEARCH_ONLY", REASON_EXTREME_EDGE, quote_age_seconds

    return "OFFICIAL", None, quote_age_seconds


# ---------------------------------------------------------------------------
# Batch-level correlated-exposure limit (Guard 6)
# ---------------------------------------------------------------------------

def apply_correlated_limit(
    candidates: list[dict[str, Any]],
) -> None:
    """
    Enforce at most one OFFICIAL open trade per
    (city, settlement_local_date, weather_variable) per strategy.

    Mutates the ``eligibility_status`` / ``eligibility_reason`` /
    ``is_executable`` fields of every candidate dict in-place.

    Candidates are dicts that must contain:
      city, target_settlement_date_str, settlement_timezone,
      weather_variable, eligibility_status, edge_pct_points,
      side_market_price, quote_timestamp

    Ranking (best first):
      1. Highest EV = edge_pct_points / side_market_price  (bang-per-buck)
      2. Lower claimed edge  (more conservative estimate)
      3. Latest quote timestamp
    """
    from collections import defaultdict
    from zoneinfo import ZoneInfo

    groups: dict[tuple, list[dict]] = defaultdict(list)

    for cand in candidates:
        if cand.get("eligibility_status") != "OFFICIAL":
            continue

        city = cand.get("city") or ""
        weather_var = cand.get("weather_variable") or ""
        tz_str = cand.get("settlement_timezone") or "UTC"
        date_str = cand.get("target_settlement_date_str") or ""

        try:
            local_tz = ZoneInfo(tz_str)
            settlement_dt = _parse_settlement_dt(date_str)
            local_date = str(settlement_dt.astimezone(local_tz).date())
        except Exception:
            local_date = date_str[:10]

        key = (city, local_date, weather_var)
        groups[key].append(cand)

    for key, group in groups.items():
        if len(group) <= 1:
            continue

        def _rank(c: dict) -> tuple:
            price = c.get("side_market_price") or 0.001
            edge = c.get("edge_pct_points") or 0.0
            ev = edge / max(price, 0.001)
            qt = c.get("quote_timestamp")
            qt_epoch = qt.timestamp() if (qt and hasattr(qt, "timestamp")) else 0.0
            return (-ev, edge, -qt_epoch)   # lower = better

        group.sort(key=_rank)
        # Best candidate stays OFFICIAL; the rest become RESEARCH_ONLY
        for cand in group[1:]:
            cand["eligibility_status"] = "RESEARCH_ONLY"
            cand["eligibility_reason"] = REASON_CORRELATED
            cand["is_executable"] = False
