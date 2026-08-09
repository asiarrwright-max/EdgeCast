"""
app/routers/bet_watch.py
Bet Watch — Read-only decision-support layer.

Surfaces the most attractive Kalshi weather opportunities EdgeCast currently
sees, including candidates that have NOT yet passed every Forward Test B
eligibility guard.

SAFETY GUARANTEES (enforced, not assumed):
  • This endpoint NEVER modifies paper_trades, eligibility_status, models,
    calibration parameters, Guard 8, or the 300-second freshness threshold.
  • It reads existing paper_trades rows — it does not create new ones.
  • A PRELIMINARY or WATCHING recommendation cannot make a trade OFFICIAL.
  • The response always carries "trading_state_modified": false.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import PaperTrade

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bet-watch"])

# ---------------------------------------------------------------------------
# City specialization — read-only display filter.
# Does NOT affect FTB eligibility, paper_trades rows, or model logic.
# Non-focus cities still appear in the ranked list as WATCHING so users
# can monitor them, but they are never surfaced as "Best Bet Right Now".
# ---------------------------------------------------------------------------

SPECIALIZATION_CITIES: frozenset[str] = frozenset({
    "Denver",
    "New York City",
    "Oklahoma City",
})

_SPECIALIZATION_NOTE = (
    "Best Bet Right Now is restricted to the three verified specialization cities "
    "(Denver · New York City · Oklahoma City). Other verified cities appear as "
    "WATCHING so you can monitor signal quality, but they are excluded from the "
    "Best Bet recommendation until they meet the win-rate threshold."
)

# ---------------------------------------------------------------------------
# Module constants — read-only.  None of these change FTB behaviour.
# ---------------------------------------------------------------------------

_FTB_STRATEGY = "v2.3"
_SCAN_WINDOW_HOURS = 48       # look-back window for recent paper_trades rows
_TOP_N = 5                    # candidates to surface in the ranked list
_MIN_INTERESTING_EDGE_PP = 3.0   # pp — below this we note the thin edge
_AVOID_AGE_HOURS = 2.0           # hours — quote older than this → AVOID/STALE
_NEAR_OFFICIAL_STALE_SECS = 900  # 15 min — freshly-stale quote stays NEAR OFFICIAL


# ---------------------------------------------------------------------------
# Reason-code → human descriptions  (never changes eligibility behaviour)
# ---------------------------------------------------------------------------

_REASON_PLAIN: dict[str, str] = {
    "hourly_temperature_not_approved":  "Hourly contracts are excluded from Forward Test B.",
    "settlement_station_unverified":    "The settlement station for this city has not been verified.",
    "missing_or_stale_executable_quote": (
        "The Kalshi quote is older than the 300-second Forward Test B freshness limit."
    ),
    "cutoff_unverified_or_too_close": (
        "The market closes in fewer than 120 minutes, or the close time could not be confirmed."
    ),
    "same_day_not_approved":           "Same-day contracts are excluded from Forward Test B.",
    "entry_price_below_official_floor": "The entry price is below the $0.20 Forward Test B minimum.",
    "extreme_edge_requires_validation": (
        "The estimated edge exceeds 50 pp, which triggers a validation hold."
    ),
    "correlated_outcome_limit": (
        "A correlated trade in the same city/date/variable was already selected as the primary candidate."
    ),
    "v2_excluded": "The Kalshi price is at or below $0.01 — effectively no real market at this price.",
}

# Short category label used in the "failed_ftb_guards" list
_REASON_CATEGORY: dict[str, str] = {
    "missing_or_stale_executable_quote":  "stale quote",
    "entry_price_below_official_floor":   "insufficient liquidity",
    "v2_excluded":                         "no liquidity (penny price)",
    "settlement_station_unverified":       "station verification",
    "cutoff_unverified_or_too_close":     "settlement integrity",
    "extreme_edge_requires_validation":   "extreme edge — validation hold",
    "correlated_outcome_limit":           "correlated exposure limit",
    "hourly_temperature_not_approved":    "hourly contract — FTB rule",
    "same_day_not_approved":              "same-day contract — FTB rule",
}


# ---------------------------------------------------------------------------
# Watch status  (pure function — no side effects)
# ---------------------------------------------------------------------------

def _current_quote_age(row: PaperTrade) -> float | None:
    """Seconds between row.quote_timestamp and now, or None if no timestamp."""
    if row.quote_timestamp is None:
        return None
    qt = row.quote_timestamp
    if qt.tzinfo is None:
        qt = qt.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - qt).total_seconds()
    return max(0.0, age)


def _watch_status(row: PaperTrade, age_now: float | None) -> str:
    """
    Assign a human-readable watch status to a paper_trade row.

    This is purely observational — it NEVER modifies eligibility_status or
    any FTB decision.

    Note: even an OFFICIAL row becomes AVOID/STALE for Bet Watch purposes
    if the underlying quote is now too old to support a decision.  The DB
    row remains OFFICIAL (immutable); this is a display-only classification.
    """
    reason = row.eligibility_reason or ""

    # Truly dead market — no real liquidity (check before anything else)
    if reason == "v2_excluded":
        return "AVOID / STALE"
    if row.side_market_price is not None and row.side_market_price <= 0.01:
        return "AVOID / STALE"

    # Quote too old to be useful for decision support — including OFFICIAL rows
    if age_now is not None and age_now > _AVOID_AGE_HOURS * 3600:
        return "AVOID / STALE"
    # No timestamp at all on a stale-quote row → truly gone
    if age_now is None and reason == "missing_or_stale_executable_quote":
        return "AVOID / STALE"

    # OFFICIAL — passed all eight FTB guards with a still-fresh quote
    if row.eligibility_status == "OFFICIAL":
        return "OFFICIAL-ELIGIBLE"

    # Correlated exposure winner already selected — second best in same group
    if reason == "correlated_outcome_limit":
        return "NEAR OFFICIAL"

    if reason == "missing_or_stale_executable_quote":
        # Was fresh enough when evaluated; may have just drifted past 300 s
        if age_now is not None and age_now < _NEAR_OFFICIAL_STALE_SECS:
            return "NEAR OFFICIAL"
        return "PRELIMINARY"

    if reason in (
        "settlement_station_unverified",
        "cutoff_unverified_or_too_close",
        "extreme_edge_requires_validation",
    ):
        return "WATCHING"

    if reason in (
        "entry_price_below_official_floor",
        "hourly_temperature_not_approved",
        "same_day_not_approved",
    ):
        return "PRELIMINARY"

    return "WATCHING"


_STATUS_ORDER = {
    "OFFICIAL-ELIGIBLE": 0,
    "NEAR OFFICIAL":     1,
    "WATCHING":          2,
    "PRELIMINARY":       3,
    "AVOID / STALE":     4,
}


def _score(row: PaperTrade, age_now: float | None, status: str) -> float:
    """
    Composite attractiveness score — higher is better.
    Used purely for ranking within a status tier.
    """
    edge = row.edge_pct_points or 0.0

    # Freshness: 1.0 (brand-new) → 0.0 (1 hour old)
    if age_now is not None:
        freshness = max(0.0, 1.0 - age_now / 3600.0)
    else:
        freshness = 0.05  # unknown age is heavily penalised

    # Liquidity factor
    price = row.side_market_price or 0.0
    if row.is_executable:
        liq = 1.0
    elif price > 0.01:
        liq = 0.5
    else:
        liq = 0.02

    status_bonus = {
        "OFFICIAL-ELIGIBLE": 40.0,
        "NEAR OFFICIAL":     20.0,
        "WATCHING":           8.0,
        "PRELIMINARY":        3.0,
        "AVOID / STALE":      0.0,
    }.get(status, 0.0)

    station_bonus = 5.0 if row.station_verified else 0.0
    return (edge * freshness * liq) + status_bonus + station_bonus


# ---------------------------------------------------------------------------
# Ticker parser
# ---------------------------------------------------------------------------

_TICKER_BOUNDARY_RE = re.compile(
    r"-[A-Z\d]+-(?P<kind>[TB])(?P<value>[\d.]+)$",
    re.IGNORECASE,
)


def _parse_ticker(ticker: str) -> dict[str, Any]:
    m = _TICKER_BOUNDARY_RE.search(ticker or "")
    if not m:
        return {"contract_boundary": None, "boundary_value": None}
    kind = m.group("kind").upper()
    try:
        value = float(m.group("value"))
    except ValueError:
        return {"contract_boundary": None, "boundary_value": None}
    label = f"≥ {value:.1f}°F" if kind == "T" else f"{value:.1f}°F range bound"
    return {"contract_boundary": label, "boundary_value": value}


# ---------------------------------------------------------------------------
# Forecast extraction from decision_explanation
# ---------------------------------------------------------------------------

_FORECAST_RE = re.compile(
    r"Open-Meteo (?:high|low|hourly):\s*([\d.]+)°F",
    re.IGNORECASE,
)
_SIGMA_RE = re.compile(r"σ\s*=\s*([\d.]+)°F", re.IGNORECASE)


def _extract_forecast(explanation: str | None) -> float | None:
    if not explanation:
        return None
    m = _FORECAST_RE.search(explanation)
    return float(m.group(1)) if m else None


def _extract_sigma(explanation: str | None) -> float | None:
    if not explanation:
        return None
    m = _SIGMA_RE.search(explanation)
    return float(m.group(1)) if m else None


def _extract_model_version(explanation: str | None, fallback: str) -> str:
    if not explanation:
        return fallback
    if "[v2.3]" in explanation:
        return "v2.3"
    if "[v2.2]" in explanation:
        return "v2.2"
    return fallback


# ---------------------------------------------------------------------------
# FTB status plain English
# ---------------------------------------------------------------------------

def _ftb_status_text(row: PaperTrade, age_now: float | None) -> str:
    if row.eligibility_status == "OFFICIAL":
        return "OFFICIAL eligible — passes all eight Forward Test B guards."

    reason = row.eligibility_reason or "unknown"
    desc = _REASON_PLAIN.get(reason, f"Research-only: {reason}.")

    # Personalise stale-quote message with actual age
    if reason == "missing_or_stale_executable_quote" and age_now is not None:
        mins = age_now / 60.0
        desc = (
            f"Would qualify except the quote is {mins:.0f} minute(s) old "
            "(limit: 5 minutes / 300 seconds)."
        )

    return f"Research-only: {desc}"


# ---------------------------------------------------------------------------
# Plain-English generators
# ---------------------------------------------------------------------------

def _why_this_bet(
    row: PaperTrade,
    ticker_info: dict,
    forecast: float | None,
    age_now: float | None,
) -> str:
    city = row.city or "this city"
    direction = row.direction or "YES"
    side_prob = row.ec_side_probability or row.ec_yes_probability or 0.0
    mkt_prob = row.market_yes_probability or 0.0
    edge = row.edge_pct_points or 0.0
    price = row.side_market_price
    boundary = ticker_info.get("contract_boundary") or "the contract boundary"
    wv = row.weather_variable or "temperature"
    wv_label = (
        "high temperature" if wv == "high"
        else "low temperature" if wv == "low"
        else wv
    )

    parts: list[str] = []

    if forecast is not None:
        parts.append(
            f"The latest forecast puts {city}'s {wv_label} near {forecast:.1f}°F."
        )

    price_str = f"{price:.0%}" if price is not None else "an unknown price"
    parts.append(
        f"EdgeCast estimates the probability of {direction} at {side_prob * 100:.0f}%, "
        f"while Kalshi implies {mkt_prob * 100:.0f}% ({price_str} ask on the selected side). "
        f"That's a {edge:.1f} percentage-point disagreement in EdgeCast's favour."
    )

    if row.confidence_label:
        parts.append(f"Model confidence: {row.confidence_label}.")

    if row.station_verified:
        parts.append("The settlement station is verified.")
    else:
        parts.append(
            f"Note: the settlement station for {city} has not been verified — "
            "the contract may settle at a different location than EdgeCast's model assumes."
        )

    if row.is_executable:
        parts.append("The market has an active, executable quote.")
    elif row.eligibility_reason == "missing_or_stale_executable_quote":
        parts.append(
            "The quote is currently stale. This edge is real, "
            "but cannot be acted on until a fresh quote arrives."
        )
    elif row.eligibility_reason == "v2_excluded":
        parts.append(
            "Market liquidity is effectively zero at this price — treat this as an observation only."
        )

    return " ".join(parts)


def _what_to_watch(
    row: PaperTrade,
    ticker_info: dict,
    forecast: float | None,
    age_now: float | None,
) -> str:
    parts: list[str] = []
    bv = ticker_info.get("boundary_value")
    edge = row.edge_pct_points or 0.0
    sigma = _extract_sigma(row.decision_explanation)

    # Proximity to boundary
    if forecast is not None and bv is not None and sigma is not None:
        distance = abs(forecast - bv)
        if distance < sigma:
            parts.append(
                f"The forecast ({forecast:.1f}°F) is within σ={sigma:.1f}°F "
                f"of the contract boundary ({bv:.1f}°F). "
                "A 1–2°F forecast shift could materially change this recommendation."
            )

    # Quote staleness
    if age_now is not None and age_now > 300:
        mins = age_now / 60.0
        parts.append(
            f"Good signal, bad market right now. "
            f"The Kalshi quote is {mins:.0f} minute(s) old — "
            "wait for a fresh quote before acting."
        )

    # Market close
    if row.market_close_timestamp:
        mct = row.market_close_timestamp
        if mct.tzinfo is None:
            mct = mct.replace(tzinfo=timezone.utc)
        mins_left = (mct - datetime.now(timezone.utc)).total_seconds() / 60.0
        if mins_left < 0:
            parts.append("This market has already closed.")
        elif mins_left < 60:
            parts.append(f"The market closes in about {mins_left:.0f} minutes — time is limited.")
        elif mins_left < 180:
            parts.append(f"The market closes in about {mins_left:.0f} minutes.")

    # Station
    if not row.station_verified:
        parts.append(
            "EdgeCast likes this opportunity, but the settlement station is unverified. "
            "Verify the NWS station from the Kalshi contract rules before acting."
        )

    # Thin edge
    if edge < _MIN_INTERESTING_EDGE_PP:
        parts.append(
            "The edge is thin — a small shift in either the forecast or Kalshi price could erase it."
        )

    if not parts:
        parts.append(
            "No specific concerns at this time. "
            "Monitor quote freshness and any forecast updates before the market closes."
        )

    return " ".join(parts)


def _changed_since_creation(row: PaperTrade, age_now: float | None) -> list[str]:
    """Surface meaningful state changes between evaluation time and now."""
    changes: list[str] = []
    now = datetime.now(timezone.utc)

    created = row.created_at
    if created and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if created:
        hours_old = (now - created).total_seconds() / 3600.0
        if hours_old < 1.0:
            changes.append("New opportunity — detected in the latest scan.")

    # Quote freshness transition
    stored_age = row.quote_age_seconds or 0.0
    if age_now is not None:
        if stored_age <= 300.0 and age_now > 300.0:
            changes.append(
                f"Quote was fresh when evaluated ({stored_age:.0f}s) "
                f"but has now aged to {age_now / 60:.0f} minutes — no longer OFFICIAL-eligible."
            )

    # Market close approaching
    if row.market_close_timestamp:
        mct = row.market_close_timestamp
        if mct.tzinfo is None:
            mct = mct.replace(tzinfo=timezone.utc)
        mins_left = (mct - now).total_seconds() / 60.0
        if mins_left < 0:
            changes.append("Market has now closed.")
        elif mins_left < 60:
            changes.append(f"Market closes in {mins_left:.0f} minutes.")

    return changes


# ---------------------------------------------------------------------------
# Row → serialisable candidate dict
# ---------------------------------------------------------------------------

def _row_to_candidate(row: PaperTrade, age_now: float | None) -> dict[str, Any]:
    status = _watch_status(row, age_now)

    # Specialization cap — display-only; never touches FTB or eligibility_status.
    # Non-focus cities remain visible as WATCHING so signal can be monitored,
    # but they cannot surface as "Best Bet Right Now".
    city_name = row.city or ""
    in_specialization = city_name in SPECIALIZATION_CITIES
    if status != "AVOID / STALE" and not in_specialization:
        status = "WATCHING"

    composite_score = _score(row, age_now, status)
    ticker_info = _parse_ticker(row.market_ticker or "")
    forecast = _extract_forecast(row.decision_explanation)
    model_ver = _extract_model_version(row.decision_explanation, row.strategy_version or "v2.3")

    # Market close
    mct = row.market_close_timestamp
    if mct and mct.tzinfo is None:
        mct = mct.replace(tzinfo=timezone.utc)
    minutes_to_close: float | None = None
    if mct:
        minutes_to_close = (mct - datetime.now(timezone.utc)).total_seconds() / 60.0

    # Liquidity status
    if row.is_executable:
        liquidity_status = "executable"
    elif row.side_market_price is not None and row.side_market_price > 0.01:
        liquidity_status = "limited — ask exists but not confirmed executable"
    else:
        liquidity_status = "none — price at or below $0.01"

    # Data freshness label
    if age_now is None:
        data_freshness = "UNKNOWN"
    elif age_now < 60:
        data_freshness = "very fresh (< 1 min)"
    elif age_now < 300:
        data_freshness = "fresh (< 5 min)"
    elif age_now < 900:
        data_freshness = "aging (5–15 min)"
    elif age_now < 3600:
        data_freshness = "stale (15 min – 1 hr)"
    else:
        data_freshness = "very stale (> 1 hr)"

    # Failed FTB guards
    failed_guards: list[str] = []
    if row.eligibility_status == "RESEARCH_ONLY" and row.eligibility_reason:
        cat = _REASON_CATEGORY.get(row.eligibility_reason, row.eligibility_reason)
        failed_guards.append(cat)

    # Contract question
    direction = row.direction or "YES"
    wv = row.weather_variable or "temperature"
    city = row.city or "Unknown"
    boundary = ticker_info.get("contract_boundary") or "the threshold"
    wv_label = (
        "high temperature" if wv == "high"
        else "low temperature" if wv == "low"
        else wv
    )
    if direction == "YES":
        question = f"Will the {wv_label} in {city} reach {boundary}?"
    else:
        question = f"Will the {wv_label} in {city} NOT reach {boundary}?"

    price = row.side_market_price

    return {
        # Ranking
        "rank": 0,              # filled by caller
        # Specialization
        "specialization_city": in_specialization,
        "_score": composite_score,
        # Identity
        "city": city,
        "ticker": row.market_ticker,
        "side": direction,
        "contract_question": question,
        "contract_type": row.contract_type,
        "weather_variable": wv,
        "settlement_date": row.target_settlement_date,
        # Prices — NEVER fabricated; None when not available
        "kalshi_price": round(price, 4) if price is not None else None,
        "kalshi_bid": round(row.quote_bid, 4) if row.quote_bid is not None else None,
        "kalshi_ask": round(row.quote_ask, 4) if row.quote_ask is not None else None,
        # Model
        "model_probability": round(
            (row.ec_side_probability or row.ec_yes_probability or 0.0), 4
        ),
        "ec_yes_probability": round(row.ec_yes_probability or 0.0, 4),
        "edge": round(row.edge_pct_points or 0.0, 2),
        "model_version": model_ver,
        "model_agreement": None,   # reserved for future multi-version agreement signal
        "forecast_value": forecast,
        "contract_boundary": ticker_info.get("contract_boundary"),
        "confidence": row.confidence_label or "UNKNOWN",
        # Quote / market metadata
        "quote_timestamp": row.quote_timestamp.isoformat() if row.quote_timestamp else None,
        "quote_age_seconds": round(age_now, 1) if age_now is not None else None,
        "market_close": mct.isoformat() if mct else None,
        "minutes_to_close": round(minutes_to_close, 1) if minutes_to_close is not None else None,
        "volume": None,           # not stored in paper_trades
        "open_interest": round(row.est_available_qty, 0) if row.est_available_qty is not None else None,
        "market_status": "closed" if (minutes_to_close is not None and minutes_to_close < 0) else "open",
        # Quality
        "liquidity_status": liquidity_status,
        "data_freshness": data_freshness,
        "station_verified": row.station_verified,
        # FTB status
        "watch_status": status,
        "ftb_status": _ftb_status_text(row, age_now),
        "ftb_eligible": row.eligibility_status == "OFFICIAL",
        "failed_ftb_guards": failed_guards,
        # Narratives
        "why_this_bet": _why_this_bet(row, ticker_info, forecast, age_now),
        "what_to_watch": _what_to_watch(row, ticker_info, forecast, age_now),
        "changed_since_previous_scan": _changed_since_creation(row, age_now),
        # Created
        "evaluated_at": row.created_at.isoformat() if row.created_at else None,
    }


# ---------------------------------------------------------------------------
# Recommendation helpers
# ---------------------------------------------------------------------------

def _recommendation_text(best: dict | None, all_candidates: list[dict]) -> str:
    if best is None:
        return "EdgeCast does not see a bet worth taking right now."

    city = best.get("city", "Unknown")
    direction = best.get("side", "YES")
    boundary = best.get("contract_boundary") or "the contract boundary"
    price = best.get("kalshi_price")
    model_prob = best.get("model_probability", 0.0)
    edge = best.get("edge", 0.0)
    status = best.get("watch_status", "")

    price_str = f"{price:.0%}" if price is not None else "an unknown price"
    prob_str = f"{model_prob * 100:.0f}%"
    action = "YES" if direction == "YES" else "NO"

    text = (
        f"EdgeCast's best current opportunity is {action} on {city} — {boundary}. "
        f"Kalshi is pricing {action} at {price_str} while EdgeCast estimates the probability "
        f"at {prob_str}, giving an estimated {edge:.0f} percentage-point edge."
    )

    if status == "OFFICIAL-ELIGIBLE":
        text += " This opportunity currently passes all Forward Test B eligibility guards."
    elif status == "NEAR OFFICIAL":
        text += " This is a near-OFFICIAL opportunity — close to qualifying for Forward Test B."
    elif status in ("WATCHING", "PRELIMINARY"):
        guards = best.get("failed_ftb_guards") or []
        reason = guards[0] if guards else "an eligibility check"
        text += f" Preliminary signal — fails Forward Test B due to: {reason}."

    return text


def _wait_message(all_candidates: list[dict]) -> str | None:
    """Return a WAIT message when the top candidate has execution concerns."""
    if not all_candidates:
        return "Nothing compelling right now."

    top = all_candidates[0]
    status = top.get("watch_status", "")
    guards = top.get("failed_ftb_guards") or []
    reason = guards[0] if guards else "unknown"

    if status == "AVOID / STALE":
        return (
            f"Good signal, bad market right now. "
            f"The best candidate fails due to: {reason}. "
            "Wait for the market to become active."
        )
    if status == "PRELIMINARY" and "stale quote" in reason:
        return "Wait for a fresh Kalshi quote — the edge looks good but the market data is stale."
    if status == "WATCHING" and "station" in reason:
        return "Promising opportunity, but the settlement station has not been verified for this city."
    return None


def _build_summary(all_candidates: list[dict]) -> dict[str, Any]:
    actionable = sum(
        1 for c in all_candidates if c["watch_status"] in ("OFFICIAL-ELIGIBLE", "NEAR OFFICIAL")
    )
    near_official = sum(1 for c in all_candidates if c["watch_status"] == "NEAR OFFICIAL")
    watching = sum(1 for c in all_candidates if c["watch_status"] == "WATCHING")
    preliminary = sum(1 for c in all_candidates if c["watch_status"] == "PRELIMINARY")
    avoid_stale = sum(1 for c in all_candidates if c["watch_status"] == "AVOID / STALE")

    best_ticker = all_candidates[0]["ticker"] if all_candidates else None

    parts: list[str] = []
    if actionable:
        label = "opportunity" if actionable == 1 else "opportunities"
        parts.append(f"{actionable} actionable {label}")
    if near_official:
        label = "near-OFFICIAL candidate" if near_official == 1 else "near-OFFICIAL candidates"
        parts.append(f"{near_official} {label}")
    if watching:
        label = "market worth watching" if watching == 1 else "markets worth watching"
        parts.append(f"{watching} {label}")

    if parts:
        text = "Right now EdgeCast sees " + ", ".join(parts) + "."
        if all_candidates:
            top = all_candidates[0]
            text += f" The strongest current signal is {top['side']} on {top['ticker']}."
    else:
        text = "No compelling opportunities detected at this time."

    return {
        "total_evaluated": len(all_candidates),
        "actionable": actionable,
        "near_official": near_official,
        "watching": watching,
        "preliminary": preliminary,
        "avoid_stale": avoid_stale,
        "best_ticker": best_ticker,
        "text": text,
    }


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/bet-watch")
async def get_bet_watch(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Bet Watch — read-only decision-support snapshot.

    Returns the best Kalshi weather opportunities EdgeCast currently sees,
    including candidates that have not yet passed every Forward Test B
    eligibility guard.

    SAFETY: This endpoint never writes to paper_trades or changes eligibility.
    The response field "trading_state_modified" is always false.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=_SCAN_WINDOW_HOURS)

    # Read-only query — no INSERT / UPDATE / DELETE anywhere in this function
    result = await db.execute(
        select(PaperTrade)
        .where(
            PaperTrade.strategy_version == _FTB_STRATEGY,
            PaperTrade.created_at >= cutoff,
        )
        .order_by(desc(PaperTrade.created_at))
    )
    rows: list[PaperTrade] = list(result.scalars().all())

    # Build candidates — pure functions, no DB writes
    candidates_raw: list[dict] = []
    for row in rows:
        age_now = _current_quote_age(row)
        cand = _row_to_candidate(row, age_now)
        candidates_raw.append(cand)

    # Sort: status tier first, then composite score descending
    candidates_raw.sort(
        key=lambda c: (_STATUS_ORDER.get(c["watch_status"], 9), -c["_score"])
    )

    # Assign ranks, strip internal score
    for i, c in enumerate(candidates_raw, start=1):
        c["rank"] = i
    for c in candidates_raw:
        c.pop("_score", None)

    # Best opportunity = highest-ranked non-AVOID/STALE specialization-city candidate
    # with real edge and a known price.  Non-focus cities are intentionally excluded
    # from this selection — they remain visible as WATCHING in the ranked list.
    best: dict | None = None
    for c in candidates_raw:
        if (
            c["watch_status"] != "AVOID / STALE"
            and c["edge"] >= _MIN_INTERESTING_EDGE_PP
            and c["kalshi_price"] is not None   # never fabricate from a missing price
            and c.get("specialization_city", False)  # only focus cities as best bet
        ):
            best = c
            break

    top_n = candidates_raw[:_TOP_N]
    recommendation = _recommendation_text(best, candidates_raw)
    wait_msg = _wait_message(candidates_raw) if best is None else None
    summary = _build_summary(candidates_raw)

    return {
        "generated_at": now.isoformat(),
        # Safety attestations — always False / True respectively
        "trading_state_modified": False,
        "ftb_untouched": True,
        # Summary
        "summary": summary,
        "recommendation": recommendation,
        "wait_message": wait_msg,
        # Best opportunity (specialization cities only)
        "best_opportunity": best,
        # Ranked list
        "candidates": top_n,
        "all_candidate_count": len(candidates_raw),
        # Specialization
        "specialization_cities": sorted(SPECIALIZATION_CITIES),
        "specialization_note": _SPECIALIZATION_NOTE,
    }
