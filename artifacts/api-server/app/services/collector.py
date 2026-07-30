"""
Data collection orchestrator.
  1. Fetch active weather markets from Kalshi.
  2. Upsert markets into kalshi_markets with parsing_status.
  3. For each city, fetch Open-Meteo forecast and upsert into weather_forecasts.
  4. Update weather_matched flag on each market, validating forecast date alignment.
  5. Record job run result with full stats.

Metric definitions
------------------
markets_found       Total weather markets returned by Kalshi this run
                    (= markets_collected + parse_failures for this run).
markets_collected   Weather markets that were successfully city-matched and stored.
                    Stored as parsing_status='collected' in kalshi_markets.
markets_skipped     Non-weather markets that were scanned and discarded.
markets_rejected    Markets explicitly rejected (reserved; currently 0).
parse_failures      Weather markets where city extraction failed.
                    Stored as parsing_status='parsing_failure'.
weather_matched     Markets where a forecast row exists for the correct city
                    AND the forecast covers the market's settlement date.
forecasts_retrieved Total WeatherForecast rows inserted this run.
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


def _target_date_str(target_date: str | None) -> str | None:
    """
    Extract the YYYY-MM-DD portion from a stored target_date string.
    target_date is stored as str(datetime), e.g. '2026-07-28 14:00:00+00:00'.
    Returns just '2026-07-28', or None if target_date is absent.
    """
    if not target_date:
        return None
    return str(target_date)[:10]


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
                                hourly_data=row.get("hourly_data"),
                            )
                        )
                    forecasts_saved += len(rows)

                if weather_errors:
                    await session.commit()

                await session.commit()

                # ---- Step 4: Update weather_matched flag -------------------
                # A market is weather_matched only when:
                #   a) its city is known, AND
                #   b) a forecast row exists for that city on the market's settlement date.
                # This prevents a silent match when a forecast exists for the city
                # but does not cover the specific date the market settles on.
                mkts_q = await session.execute(select(KalshiMarket))
                all_markets = mkts_q.scalars().all()

                for m in all_markets:
                    if not m.city:
                        m.weather_matched = False
                        continue

                    target_ds = _target_date_str(m.target_date)

                    if target_ds:
                        # Prefer date-specific match
                        fc_q = await session.execute(
                            select(WeatherForecast).where(
                                WeatherForecast.city == m.city,
                                WeatherForecast.forecast_date == target_ds,
                            ).limit(1)
                        )
                        matched = fc_q.scalar_one_or_none() is not None
                        if not matched:
                            logger.debug(
                                "No forecast for %s on %s (city=%s) – weather_matched=False",
                                m.ticker, target_ds, m.city,
                            )
                    else:
                        # No settlement date stored – fall back to any-forecast check
                        fc_q = await session.execute(
                            select(WeatherForecast).where(
                                WeatherForecast.city == m.city
                            ).limit(1)
                        )
                        matched = fc_q.scalar_one_or_none() is not None

                    m.weather_matched = matched

                await session.commit()
                weather_matched_count = sum(1 for m in all_markets if m.weather_matched)

                # ---- Step 5b: Run probability analysis for all markets -----
                # Imports are local to avoid circular deps at module level.
                try:
                    from app.services.analyzer import analyze_all_markets
                    analysis_summary = await analyze_all_markets(session)
                    logger.info(
                        "Analysis: %d supported, %d unsupported, %d no-forecast",
                        analysis_summary["supported"],
                        analysis_summary["unsupported"],
                        analysis_summary["no_forecast"],
                    )
                except Exception as exc:
                    logger.warning("Analysis step failed (non-fatal): %s", exc)

                # ---- Step 5c: Paper trading (v1) ----------------------------
                pt_stats: dict = {
                    "candidates": 0, "created": 0,
                    "yes_trades": 0, "no_trades": 0,
                    "skipped": 0, "errors": 0,
                }
                try:
                    from app.services.paper_trading import run_paper_trading
                    pt_stats = await run_paper_trading(session)
                    logger.info(
                        "Paper trading v1: %d candidates, %d created (%d YES / %d NO), "
                        "%d skipped, %d errors",
                        pt_stats["candidates"], pt_stats["created"],
                        pt_stats["yes_trades"], pt_stats["no_trades"],
                        pt_stats["skipped"], pt_stats["errors"],
                    )
                except Exception as exc:
                    logger.warning("Paper trading v1 step failed (non-fatal): %s", exc)

                # ---- Step 5d: Paper trading (v2 shadow) ---------------------
                pt_v2_stats: dict = {"candidates": 0, "created": 0, "excluded": 0, "skipped": 0, "errors": 0}
                try:
                    from app.services.paper_trading_v2 import run_paper_trading_v2
                    pt_v2_stats = await run_paper_trading_v2(session)
                    logger.info(
                        "Paper trading v2: %d candidates, %d created, "
                        "%d excluded, %d skipped, %d errors",
                        pt_v2_stats["candidates"], pt_v2_stats["created"],
                        pt_v2_stats["excluded"], pt_v2_stats["skipped"], pt_v2_stats["errors"],
                    )
                except Exception as exc:
                    logger.warning("Paper trading v2 step failed (non-fatal): %s", exc)

                # ---- Step 5e: Paper trading (v2.1 hardened) -----------------
                pt_v21_stats: dict = {"candidates": 0, "created": 0, "excluded": 0, "skipped": 0, "errors": 0}
                try:
                    from app.services.paper_trading_v21 import run_paper_trading_v21
                    pt_v21_stats = await run_paper_trading_v21(session)
                    logger.info(
                        "Paper trading v2.1: %d candidates, %d created, "
                        "%d excluded, %d skipped, %d errors",
                        pt_v21_stats["candidates"], pt_v21_stats["created"],
                        pt_v21_stats["excluded"], pt_v21_stats["skipped"], pt_v21_stats["errors"],
                    )
                except Exception as exc:
                    logger.warning("Paper trading v2.1 step failed (non-fatal): %s", exc)

                # ---- Step 5e_b: V2.2 paper trading (parallel challenger) ----
                pt_v22_stats: dict = {"candidates": 0, "created": 0, "excluded": 0, "skipped": 0, "errors": 0}
                try:
                    from app.services.paper_trading_v22 import run_paper_trading_v22
                    pt_v22_stats = await run_paper_trading_v22(session)
                    logger.info(
                        "Paper trading v2.2: %d candidates, %d created, "
                        "%d excluded, %d skipped, %d errors",
                        pt_v22_stats["candidates"], pt_v22_stats["created"],
                        pt_v22_stats["excluded"], pt_v22_stats["skipped"], pt_v22_stats["errors"],
                    )
                except Exception as exc:
                    logger.warning("Paper trading v2.2 step failed (non-fatal): %s", exc)

                # ---- Step 5f: V3 predictions (parallel paper trading) -------
                pt_v3_stats: dict = {
                    "status": "disabled", "created": 0, "errors": 0,
                }
                try:
                    from app.services.v3_predictor import run_v3_predictions
                    pt_v3_stats = await run_v3_predictions()
                    logger.info(
                        "V3 predictions: %d created, %d unverified, %d unsupported, %d errors",
                        pt_v3_stats.get("created", 0),
                        pt_v3_stats.get("skipped_unverified", 0),
                        pt_v3_stats.get("skipped_unsupported", 0),
                        pt_v3_stats.get("errors", 0),
                    )
                except Exception as exc:
                    logger.warning("V3 predictions step failed (non-fatal): %s", exc)

                # ---- Step 5g: V3 paper trading --------------------------------
                pt_v3_pt_stats: dict = {
                    "status": "disabled", "created": 0, "skipped": 0, "errors": 0,
                }
                try:
                    from app.services.v3_paper_trading import run_paper_trading_v3
                    pt_v3_pt_stats = await run_paper_trading_v3()
                    logger.info(
                        "V3 paper trading: %d created, %d skipped, %d errors",
                        pt_v3_pt_stats.get("created", 0),
                        pt_v3_pt_stats.get("skipped", 0),
                        pt_v3_pt_stats.get("errors", 0),
                    )
                except Exception as exc:
                    logger.warning("V3 paper trading step failed (non-fatal): %s", exc)

                # ---- Step 5h: Finalise job ---------------------------------
                duration = round(time.monotonic() - started_mono, 2)
                job_q = await session.execute(select(JobRun).where(JobRun.id == job.id))
                job_record = job_q.scalar_one_or_none()
                if job_record:
                    job_record.status = "success"
                    job_record.completed_at = datetime.now(timezone.utc)
                    job_record.markets_found = markets_found
                    job_record.markets_skipped = markets_skipped
                    job_record.markets_rejected = 0  # reserved
                    job_record.forecasts_retrieved = forecasts_saved
                    job_record.duration_seconds = duration
                    job_record.pt_candidates = pt_stats["candidates"]
                    job_record.pt_created = pt_stats["created"]
                    job_record.pt_yes_trades = pt_stats["yes_trades"]
                    job_record.pt_no_trades = pt_stats["no_trades"]
                    job_record.pt_skipped = pt_stats["skipped"]
                    job_record.pt_errors = pt_stats["errors"]
                    job_record.pt_v2_created = pt_v2_stats["created"]
                    job_record.pt_v2_skipped = pt_v2_stats["skipped"]
                await session.commit()

                logger.info(
                    "Collection complete in %.1fs. Collected: %d, parse failures: %d, "
                    "skipped: %d, weather matched: %d, forecast rows: %d",
                    duration, markets_collected, parse_failures,
                    markets_skipped, weather_matched_count, forecasts_saved,
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
