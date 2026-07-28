"""
Data collection orchestrator.
  1. Fetch active weather markets from Kalshi.
  2. Upsert markets into kalshi_markets.
  3. For each city, fetch Open-Meteo forecast and upsert into weather_forecasts.
  4. Update weather_matched flag on each market.
  5. Record job run result.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import AsyncSessionLocal
from app.models import AppError, JobRun, KalshiMarket, WeatherForecast
from app.services.kalshi import fetch_weather_markets
from app.services.openmeteo import fetch_forecast

logger = logging.getLogger(__name__)

# Simple asyncio lock so scheduled + manual runs never overlap
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

            markets_found = 0
            forecasts_saved = 0
            error_msg = None

            try:
                # ---- Step 1: Fetch Kalshi markets --------------------------
                logger.info("Fetching Kalshi weather markets…")
                raw_markets = await fetch_weather_markets()
                markets_found = len(raw_markets)
                logger.info("Retrieved %d weather markets from Kalshi.", markets_found)

                # ---- Step 2: Upsert markets --------------------------------
                cities_needed: dict[str, tuple[float, float]] = {}
                for mkt in raw_markets:
                    ticker = mkt["ticker"]
                    # Check existing
                    existing_q = await session.execute(
                        select(KalshiMarket).where(KalshiMarket.ticker == ticker)
                    )
                    existing = existing_q.scalar_one_or_none()
                    if existing:
                        existing.title = mkt["title"]
                        existing.subtitle = mkt["subtitle"]
                        existing.city = mkt["city"]
                        existing.target_date = str(mkt["target_date"]) if mkt["target_date"] else None
                        existing.open_time = mkt["open_time"]
                        existing.close_time = mkt["close_time"]
                        existing.status = mkt["status"]
                        existing.yes_bid = mkt["yes_bid"]
                        existing.yes_ask = mkt["yes_ask"]
                        existing.no_bid = mkt["no_bid"]
                        existing.no_ask = mkt["no_ask"]
                        existing.volume = mkt["volume"]
                        existing.raw_data = mkt["raw_data"]
                    else:
                        new_mkt = KalshiMarket(
                            ticker=ticker,
                            event_ticker=mkt["event_ticker"],
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
                            raw_data=mkt["raw_data"],
                        )
                        session.add(new_mkt)

                    if mkt["city"] and mkt["lat"] and mkt["lon"]:
                        cities_needed[mkt["city"]] = (mkt["lat"], mkt["lon"])

                await session.commit()

                # ---- Step 3: Fetch and store weather forecasts -------------
                logger.info("Fetching weather for %d cities…", len(cities_needed))
                for city, (lat, lon) in cities_needed.items():
                    rows = await fetch_forecast(city, lat, lon)
                    if not rows:
                        await _log_error(
                            session,
                            "openmeteo_fetch",
                            f"No forecast data returned for {city}",
                            context=f"lat={lat}, lon={lon}",
                        )
                        continue

                    # Delete stale forecasts for this city before reinserting
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

                await session.commit()

                # ---- Step 4: Update weather_matched flag -------------------
                mkts_q = await session.execute(
                    select(KalshiMarket).where(KalshiMarket.status == "active")
                )
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

                # ---- Step 5: Finalise job ----------------------------------
                job_q = await session.execute(select(JobRun).where(JobRun.id == job.id))
                job_record = job_q.scalar_one_or_none()
                if job_record:
                    job_record.status = "success"
                    job_record.completed_at = datetime.now(timezone.utc)
                    job_record.markets_found = markets_found
                    job_record.forecasts_retrieved = forecasts_saved
                await session.commit()
                logger.info(
                    "Collection complete. Markets: %d, Forecast rows: %d",
                    markets_found,
                    forecasts_saved,
                )

            except Exception as exc:
                error_msg = str(exc)[:500]
                logger.exception("Collection failed: %s", exc)
                try:
                    async with AsyncSessionLocal() as err_session:
                        await _log_error(
                            err_session, "collection_job", error_msg
                        )
                        jq = await err_session.execute(
                            select(JobRun).where(JobRun.id == job.id)
                        )
                        jr = jq.scalar_one_or_none()
                        if jr:
                            jr.status = "failed"
                            jr.completed_at = datetime.now(timezone.utc)
                            jr.error_message = error_msg
                        await err_session.commit()
                except Exception as inner:
                    logger.error("Could not save job failure: %s", inner)
