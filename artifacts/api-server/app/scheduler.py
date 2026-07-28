"""
Background scheduler: runs data collection every 3 hours, and a settlement
check job every 3 hours (offset by 1.5 hours so they don't overlap).

Uses plain asyncio tasks — no third-party scheduler dependencies.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 3 * 60 * 60       # 3 hours
SETTLEMENT_OFFSET = 90 * 60           # 1.5 hours offset for settlement loop
VERIFICATION_INTERVAL = 24 * 60 * 60  # 24 hours
VERIFICATION_OFFSET = 3 * 60 * 60     # 3 hours offset for verification loop

_collection_task: asyncio.Task | None = None
_settlement_task: asyncio.Task | None = None
_verification_task: asyncio.Task | None = None


async def _collection_loop() -> None:
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


async def _settlement_loop() -> None:
    # Offset by 1.5 h so settlement checks don't overlap with collection.
    await asyncio.sleep(SETTLEMENT_OFFSET)
    while True:
        try:
            logger.info("Scheduler: running settlement check.")
            from app.services.settlement import run_settlement_job
            await run_settlement_job()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.exception("Scheduler settlement failed: %s", exc)
        await asyncio.sleep(INTERVAL_SECONDS)


async def _verification_loop() -> None:
    # Offset by 3 h so it doesn't overlap with collection or settlement.
    await asyncio.sleep(VERIFICATION_OFFSET)
    while True:
        try:
            logger.info("Scheduler: running forecast verification + error stats recompute.")
            from app.database import AsyncSessionLocal
            if AsyncSessionLocal is not None:
                async with AsyncSessionLocal() as session:
                    from app.services.forecast_verifier import (
                        fetch_and_store_verifications,
                        recompute_error_stats,
                    )
                    vstats = await fetch_and_store_verifications(session)
                    estats = await recompute_error_stats(session)
                    logger.info(
                        "Verification: created=%d updated=%d skipped=%d errors=%d; "
                        "error stats groups=%d",
                        vstats.get("created", 0), vstats.get("updated", 0),
                        vstats.get("skipped", 0), vstats.get("errors", 0),
                        estats.get("groups_computed", 0),
                    )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.exception("Scheduler verification failed: %s", exc)
        await asyncio.sleep(VERIFICATION_INTERVAL)


async def start_scheduler() -> None:
    global _collection_task, _settlement_task, _verification_task
    _collection_task = asyncio.create_task(_collection_loop())
    _settlement_task = asyncio.create_task(_settlement_loop())
    _verification_task = asyncio.create_task(_verification_loop())


async def shutdown_scheduler() -> None:
    global _collection_task, _settlement_task, _verification_task
    for task in [_collection_task, _settlement_task, _verification_task]:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    _collection_task = None
    _settlement_task = None
    _verification_task = None


def get_scheduler_status() -> dict:
    """Return a dict describing the current scheduler state."""
    col_running = bool(_collection_task and not _collection_task.done())
    set_running = bool(_settlement_task and not _settlement_task.done())
    ver_running = bool(_verification_task and not _verification_task.done())
    if col_running or set_running or ver_running:
        return {
            "running": True,
            "message": (
                f"Running – auto-collection every {INTERVAL_SECONDS // 3600}h, "
                f"settlement check offset {SETTLEMENT_OFFSET // 60}min, "
                f"verification every {VERIFICATION_INTERVAL // 3600}h"
            ),
        }
    return {"running": False, "message": "Scheduler stopped"}
