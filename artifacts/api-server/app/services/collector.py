"""
Data collection orchestrator.
  1. Fetch active weather markets from Kalshi.
  2. Upsert markets into kalshi_markets with parsing_status.
  3. For each city, fetch Open-Meteo forecast and upsert into weather_forecasts.
  4. Update weather_matched flag on each market.
  5. Record job run result with full stats.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.database import AsyncSessionLocal
from app.models import AppError, JobRun, KalshiMarket, WeatherForecast
from app.services.kalshi import FetchResult, fetch_weather_markets
from app.services.openmeteo import fetch_forecast

logger = logging.getLogger(__name__)

# asyncio lock so scheduled + manual runs never overlap
_collect_lock = asyncio.Lock()


async def _log_error(session, error_type: str, message: str, context: str | None = None):
    err = AppError(error_type=error_type, message=message, context=context)
    session.add(err)


async def run_collection_job(job_id: int | None = None) -> None:
    """
    Core collection routine.
    If job_id is supplied (manual trigger), updates that record.
    Otherwise creates a new scheduled-job record.
    """
    if _collect_lock.locked():
        logger.info("Collection already running – skipping.")
        return

    async with _collect_lock:
        if AsyncSessionLocal is None:
            logger.error("Database not initialised – cannot run collection.")
            return

        started_mono = time.monotonic()

        async with AsyncSessionLocal() as session:
            # Fetch or create the JobRun record
            if job_id is not None:
                job_q = await session.execute(
                    select(JobRun).where(JobRun.id == job_id)
                )
                job = job_q.scalar_one_or_none()
                if job is None:
                    job = JobRun(job_type="manual", status="running")
                    session.add(job)
                    await session.flush()
            else:
                job = JobRun(job_type="scheduled", status="running")
                session.add(job)
                await session.flush()

            await session.commit()
            await session.refresh(job)

            error_msg: str | None = None

            try:
                # ---- Step 1: Fetch Kalshi markets --------------------------
                logger.info("Fetching Kalshi weather markets…")
                result: FetchResult = await fetch_weather_markets()

                markets_collected = len(result.markets)
                parse_failures = len(result.parsing_failures)
                markets_found = markets_collected + parse_failures
                markets_skipped = result.skipped_count

                logger.info(
                    "Kalshi: %d collected, %d parse failures, %d skipped, %d total scanned",
                    markets_collected, parse_failures, markets_skipped, result.total_scanned,
                )

                # Log explicit reason when no weather markets found at all
                if markets_found == 0 and result.zero_reason:
                    await _log_error(
                        session,
                        "kalshi_no_markets",
                        result.zero_reason,
                        context=f"total_scanned={result.total_scanned}",
                    )
                    logger.warning("Zero weather markets: %s", result.zero_reason)

                # Log parse failures individually
                for mkt in result.parsing_failures:
                    logger.info(
                        "Parse failure: %s – %s",
                        mkt.get("ticker"), mkt.get("parsing_reason"),
                    )

                # ---- Step 2: Upsert markets --------------------------------
                now = datetime.now(timezone.utc)
                cities_needed: dict[str, tuple[float, float]] = {}

                def _upsert_market(mkt: dict, p_status: str, p_reason: str | None) -> None:
                    """Update or insert a single market record."""
                    nonlocal session

                # Upsert collected markets (with city)
                for mkt in result.markets:
                    ticker = mkt["ticker"]
                    existing_q = await session.execute(
                        select(KalshiMarket).where(KalshiMarket.ticker == ticker)
                    )
                    existing = existing_q.scalar_one_or_none()
                    _fields = dict(
                        title=mkt["title"],
                        subtitle=mkt["subtitle"],
                        city=mkt["city"],
                        target_date=str(mkt["target_date"]) if mkt["target_date"] else None,
                        open_time=mkt["open_time"],
                        close_time=mkt["close_time"],
                        status=mkt["status"],
                        yes_bid=mkt["yes_bid"],
                        yes_ask=mkt["yes_ask"],
                        no_bid=mkt["no_bid"],
                        no_ask=mkt["no_ask"],
                        volume=mkt["volume"],
                        parsing_status="collected",
                        parsing_reason=None,
                        weather_market_type=mkt.get("weather_market_type"),
                        collection_timestamp=now,
                        raw_data=mkt["raw_data"],
                    )
                    if existing:
                        for k, v in _fields.items():
                            setattr(existing, k, v)
                    else:
                        session.add(KalshiMarket(
                            ticker=ticker,
                            event_ticker=mkt["event_ticker"],
                            **_fields,
                        ))

                    if mkt["city"] and mkt["lat"] and mkt["lon"]:
                        cities_needed[mkt["city"]] = (mkt["lat"], mkt["lon"])

                # Upsert parsing-failure markets (weather but no city)
                for mkt in result.parsing_failures:
                    ticker = mkt["ticker"]
                    existing_q = await session.execute(
                        select(KalshiMarket).where(KalshiMarket.ticker == ticker)
                    )
                    existing = existing_q.scalar_one_or_none()
                    _fields = dict(
                        title=mkt["title"],
                        subtitle=mkt["subtitle"],
                        city=None,
                        target_date=str(mkt["target_date"]) if mkt["target_date"] else None,
                        open_time=mkt["open_time"],
                        close_time=mkt["close_time"],
                        status=mkt["status"],
                        yes_bid=mkt["yes_bid"],
                        yes_ask=mkt["yes_ask"],
                        no_bid=mkt["no_bid"],
                        no_ask=mkt["no_ask"],
                        volume=mkt["volume"],
                        parsing_status="parsing_failure",
                        parsing_reason=mkt.get("parsing_reason", "Unable to identify city"),
                        weather_market_type=mkt.get("weather_market_type"),
                        collection_timestamp=now,
                        raw_data=mkt["raw_data"],
                    )
                    if existing:
                        for k, v in _fields.items():
                            setattr(existing, k, v)
                    else:
                        session.add(KalshiMarket(
                            ticker=ticker,
                            event_ticker=mkt["event_ticker"],
                            **_fields,
                        ))

                await session.commit()

                # ---- Step 3: Fetch and store weather forecasts -------------
                logger.info("Fetching weather for %d cities…", len(cities_needed))
                forecasts_saved = 0
                weather_errors = 0
                for city, (lat, lon) in cities_needed.items():
                    rows = await fetch_forecast(city, lat, lon)
                    if not rows:
                        weather_errors += 1
                        await _log_error(
                            session,
                            "openmeteo_no_data",
                            f"No forecast data returned for {city}",
                            context=f"lat={lat}, lon={lon}",
                        )
                        logger.warning("No forecast data for %s (lat=%s, lon=%s)", city, lat, lon)
                        continue

                    # Delete stale forecasts and insert fresh rows
                    await session.execute(
                        delete(WeatherForecast).where(WeatherForecast.city == city)
                    )
                    for row in rows:
                        session.add(
                            WeatherForecast(
                                city=row["city"],
                                forecast_date=row["forecast_date"],
                                temperature_high=row["temperature_high"],
                                temperature_low=row["temperature_low"],
                                precipitation_prob=row["precipitation_prob"],
                                wind_speed=row["wind_speed"],
                                forecast_json=row["forecast_json"],
                            )
                        )
                    forecasts_saved += len(rows)

                if weather_errors:
                    await session.commit()

                await session.commit()

                # ---- Step 4: Update weather_matched flag -------------------
                mkts_q = await session.execute(select(KalshiMarket))
                for m in mkts_q.scalars().all():
                    if m.city:
                        fc_q = await session.execute(
                            select(WeatherForecast).where(
                                WeatherForecast.city == m.city
                            ).limit(1)
                        )
                        m.weather_matched = fc_q.scalar_one_or_none() is not None
                    else:
                        m.weather_matched = False

                await session.commit()
                weather_matched_count = sum(
                    1 for m in mkts_q.scalars()  # already loaded above
                    if m.weather_matched
                )

                # ---- Step 5: Finalise job ----------------------------------
                duration = round(time.monotonic() - started_mono, 2)
                job_q = await session.execute(select(JobRun).where(JobRun.id == job.id))
                job_record = job_q.scalar_one_or_none()
                if job_record:
                    job_record.status = "success"
                    job_record.completed_at = datetime.now(timezone.utc)
                    job_record.markets_found = markets_found
                    job_record.markets_skipped = markets_skipped
                    job_record.markets_rejected = 0  # not currently used
                    job_record.forecasts_retrieved = forecasts_saved
                    job_record.duration_seconds = duration
                await session.commit()

                logger.info(
                    "Collection complete in %.1fs. Collected: %d, parse failures: %d, "
                    "skipped: %d, forecast rows: %d",
                    duration, markets_collected, parse_failures, markets_skipped, forecasts_saved,
                )

            except Exception as exc:
                error_msg = str(exc)[:500]
                logger.exception("Collection failed: %s", exc)
                duration = round(time.monotonic() - started_mono, 2)
                try:
                    async with AsyncSessionLocal() as err_session:
                        await _log_error(err_session, "collection_job", error_msg)
                        jq = await err_session.execute(
                            select(JobRun).where(JobRun.id == job.id)
                        )
                        jr = jq.scalar_one_or_none()
                        if jr:
                            jr.status = "failed"
                            jr.completed_at = datetime.now(timezone.utc)
                            jr.error_message = error_msg
                            jr.duration_seconds = duration
                        await err_session.commit()
                except Exception as inner:
                    logger.error("Could not save job failure: %s", inner)
