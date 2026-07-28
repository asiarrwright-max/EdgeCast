"""
Analyzer — orchestrates settlement parsing, probability calculation,
and PredictionSnapshot persistence for all active markets.

Called from the collector after every successful collection run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KalshiMarket, PredictionSnapshot, WeatherForecast
from app.services.settlement_parser import parse_settlement
from app.services.probability_engine import run_analysis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timezone helpers for hourly contracts
# ---------------------------------------------------------------------------

# Common US timezone abbreviations → UTC offset in hours
# Positive offset = west of UTC (subtract to get UTC)
# Note: abbreviations like ET/CT are ambiguous; we assume DST (summer) values.
_TZ_UTC_OFFSET: dict[str, int] = {
    "EDT": -4, "EST": -5,
    "CDT": -5, "CST": -6,
    "MDT": -6, "MST": -7,
    "PDT": -7, "PST": -8,
    "ADT": -3, "AST": -4,
    # Ambiguous abbreviations — assume summer/DST
    "ET": -4, "CT": -5, "MT": -6, "PT": -7,
    # UTC / GMT
    "UTC": 0, "GMT": 0,
}


def _contract_to_city_hour(
    target_date_str: str,   # "YYYY-MM-DD" (the market's settlement date)
    target_hour: int,       # 0–23 in tz_abbrev timezone
    tz_abbrev: str,         # e.g. "EDT"
    city_tz_name: str,      # IANA name, e.g. "America/Chicago"
) -> tuple[str, int] | None:
    """
    Convert a (date, hour) in the given timezone abbreviation to the
    corresponding (local_date, local_hour) in the city's IANA timezone.

    Returns (local_date_str, local_hour) or None if conversion fails.

    Example:
        "12am EDT" on "2026-07-28" for Chicago (America/Chicago / CDT = UTC-5):
        0h EDT = 4h UTC = 23h CDT (2026-07-27)
        → returns ("2026-07-27", 23)
    """
    from datetime import date as date_cls

    utc_offset_hours = _TZ_UTC_OFFSET.get(tz_abbrev.upper())
    if utc_offset_hours is None:
        logger.warning("Unknown timezone abbreviation: %s", tz_abbrev)
        return None

    # Convert target_hour in tz_abbrev to UTC
    # e.g. 0h EDT (UTC-4) → 0 - (-4) = 4h UTC
    utc_hour = target_hour - utc_offset_hours

    try:
        base_date = date_cls.fromisoformat(target_date_str)
    except ValueError:
        return None

    # Build UTC datetime (handle day rollover)
    day_offset = utc_hour // 24
    utc_hour_mod = utc_hour % 24
    utc_date = base_date + timedelta(days=day_offset)
    utc_dt = datetime(
        utc_date.year, utc_date.month, utc_date.day,
        utc_hour_mod, 0, 0, tzinfo=timezone.utc
    )

    # Convert UTC to city local time
    try:
        city_tz = ZoneInfo(city_tz_name)
    except (ZoneInfoNotFoundError, Exception) as exc:
        logger.warning("Cannot load timezone %s: %s", city_tz_name, exc)
        return None

    local_dt = utc_dt.astimezone(city_tz)
    return local_dt.strftime("%Y-%m-%d"), local_dt.hour


async def _resolve_hourly_temp(
    session: AsyncSession,
    city: str | None,
    target_date_str: str | None,
    target_hour: int | None,
    tz_abbrev: str | None,
    fallback_forecast: WeatherForecast | None,
) -> float | None:
    """
    Resolve the hourly temperature forecast for an hourly contract.

    Converts the settlement time from its stated timezone (tz_abbrev) to the
    city's local time, looks up the correct WeatherForecast row, and returns
    the temperature for that hour.

    Returns None if the forecast is unavailable.
    """
    if not city or not target_date_str or target_hour is None or not tz_abbrev:
        return None

    # Get city timezone from the stored Open-Meteo metadata
    city_tz_name: str | None = None
    if fallback_forecast and fallback_forecast.forecast_json:
        city_tz_name = fallback_forecast.forecast_json.get("timezone")

    if not city_tz_name:
        # Fallback: if we have no timezone info, we cannot safely convert
        logger.warning("No city timezone found for %s — cannot resolve hourly forecast", city)
        return None

    result = _contract_to_city_hour(target_date_str, target_hour, tz_abbrev, city_tz_name)
    if result is None:
        return None

    local_date, local_hour = result

    # Fetch the WeatherForecast row for the computed local date
    if local_date == target_date_str and fallback_forecast is not None:
        forecast_row = fallback_forecast
    else:
        fc_q = await session.execute(
            select(WeatherForecast).where(
                WeatherForecast.city == city,
                WeatherForecast.forecast_date == local_date,
            ).limit(1)
        )
        forecast_row = fc_q.scalar_one_or_none()

    if forecast_row is None:
        logger.debug("No forecast row for %s on %s (hourly lookup)", city, local_date)
        return None

    hourly_entries = forecast_row.hourly_data
    if not hourly_entries:
        logger.debug("No hourly_data on forecast row for %s on %s", city, local_date)
        return None

    # Find the matching hour
    match = next((e for e in hourly_entries if e.get("hour") == local_hour), None)
    if match is None:
        logger.debug("Hour %d not found in hourly_data for %s on %s", local_hour, city, local_date)
        return None

    temp = match.get("temperature")
    logger.debug(
        "Resolved hourly forecast: %s %s local hour %d = %.1f°F (from %s %s)",
        city, local_date, local_hour, temp or 0, target_date_str, tz_abbrev,
    )
    return temp


# ---------------------------------------------------------------------------
# Per-market analysis
# ---------------------------------------------------------------------------

async def analyze_market(
    session: AsyncSession,
    market: KalshiMarket,
) -> PredictionSnapshot | None:
    """
    Run the full analysis pipeline for one market and return a new
    PredictionSnapshot.  Returns None if the market cannot be processed at all.
    """
    # Only analyze temperature markets
    if market.weather_market_type not in ("temperature", None):
        return None

    # Parse the settlement contract
    contract = parse_settlement(market.title or "", market.subtitle)

    # Look up the matching daily forecast row (always needed for daily markets;
    # also used to obtain the city timezone for hourly contracts)
    forecast: WeatherForecast | None = None
    target_ds: str | None = None
    if market.city and market.target_date:
        target_ds = str(market.target_date)[:10]
        fc_q = await session.execute(
            select(WeatherForecast).where(
                WeatherForecast.city == market.city,
                WeatherForecast.forecast_date == target_ds,
            ).limit(1)
        )
        forecast = fc_q.scalar_one_or_none()

    # For hourly contracts: resolve the hourly temperature forecast
    forecast_hourly_value: float | None = None
    if contract.contract_type == "hourly_threshold" and contract.status == "supported":
        forecast_hourly_value = await _resolve_hourly_temp(
            session=session,
            city=market.city,
            target_date_str=target_ds,
            target_hour=contract.target_hour,
            tz_abbrev=contract.target_timezone_str,
            fallback_forecast=forecast,
        )

    # Run the probability engine
    result = run_analysis(
        title=market.title or "",
        subtitle=market.subtitle,
        city=market.city,
        target_date_str=target_ds,
        weather_variable=contract.variable,
        operator=contract.operator,
        threshold=contract.threshold,
        parse_confidence=contract.parse_confidence,
        settlement_status=contract.status,
        unsupported_reason=contract.unsupported_reason,
        forecast_high=forecast.temperature_high if forecast else None,
        forecast_low=forecast.temperature_low if forecast else None,
        forecast_retrieved_at=forecast.retrieved_at if forecast else None,
        yes_bid=market.yes_bid,
        yes_ask=market.yes_ask,
        # Phase 2B
        contract_type=contract.contract_type,
        lower_bound=contract.lower_bound,
        upper_bound=contract.upper_bound,
        forecast_hourly_value=forecast_hourly_value,
    )

    snapshot = PredictionSnapshot(
        market_ticker=market.ticker,
        created_at=datetime.now(timezone.utc),
        forecast_date=target_ds,
        forecast_value=result.forecast_value,
        forecast_retrieved_at=forecast.retrieved_at if forecast else None,
        lead_time_days=result.lead_time_days,
        settlement_variable=contract.variable,
        settlement_operator=contract.operator,
        settlement_threshold=contract.threshold,
        ec_probability=result.ec_probability,
        market_probability=result.market_probability,
        confidence=result.confidence,
        explanation=result.explanation,
        analysis_status=result.analysis_status,
        analysis_reason=result.analysis_reason,
        # Phase 2B
        contract_type=contract.contract_type,
        target_hour=contract.target_hour,
        target_timezone_str=contract.target_timezone_str,
        lower_bound=contract.lower_bound,
        upper_bound=contract.upper_bound,
    )
    session.add(snapshot)
    return snapshot


async def analyze_all_markets(session: AsyncSession) -> dict:
    """
    Analyze every active market and store one PredictionSnapshot per market.
    Returns a summary dict with counts.
    """
    q = await session.execute(
        select(KalshiMarket).where(KalshiMarket.status == "active")
    )
    markets = q.scalars().all()

    supported = 0
    unsupported = 0
    no_forecast = 0
    errors = 0

    for market in markets:
        try:
            snap = await analyze_market(session, market)
            if snap is None:
                continue
            if snap.analysis_status == "supported":
                supported += 1
            elif snap.analysis_status == "no_forecast":
                no_forecast += 1
            else:
                unsupported += 1
        except Exception as exc:
            errors += 1
            logger.warning("Analysis failed for %s: %s", market.ticker, exc)

    await session.commit()

    logger.info(
        "Analysis complete: %d supported, %d unsupported, %d no-forecast, %d errors",
        supported, unsupported, no_forecast, errors,
    )
    return {
        "supported": supported,
        "unsupported": unsupported,
        "no_forecast": no_forecast,
        "errors": errors,
        "total": supported + unsupported + no_forecast,
    }


async def get_latest_snapshot(
    session: AsyncSession, ticker: str
) -> PredictionSnapshot | None:
    """Return the most recent PredictionSnapshot for a market ticker."""
    q = await session.execute(
        select(PredictionSnapshot)
        .where(PredictionSnapshot.market_ticker == ticker)
        .order_by(PredictionSnapshot.created_at.desc())
        .limit(1)
    )
    return q.scalar_one_or_none()
