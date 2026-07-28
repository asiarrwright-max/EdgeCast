"""
Background scheduler: runs data collection every 3 hours.
Uses a plain asyncio task so there are no third-party scheduler dependencies.
A running-job check prevents overlapping executions.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 3 * 60 * 60  # 3 hours

_task: asyncio.Task | None = None


async def _loop() -> None:
    # Delay first auto-run by one full interval so startup is clean.
    logger.info("Scheduler started – first auto-collection in 3 hours.")
    await asyncio.sleep(INTERVAL_SECONDS)
    while True:
        try:
            logger.info("Scheduler: triggering automatic data collection.")
            from app.services.collector import run_collection_job

            await run_collection_job()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.exception("Scheduler collection failed: %s", exc)
        await asyncio.sleep(INTERVAL_SECONDS)


async def start_scheduler() -> None:
    global _task
    _task = asyncio.create_task(_loop())


async def shutdown_scheduler() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
