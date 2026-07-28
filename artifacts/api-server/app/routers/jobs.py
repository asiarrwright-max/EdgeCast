from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import JobRun

logger = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])

# Simple in-process lock to prevent overlapping manual triggers
_collection_lock = asyncio.Lock()


def _to_dict(j: JobRun) -> dict:
    return {
        "id": j.id,
        "jobType": j.job_type,
        "startedAt": j.started_at.isoformat(),
        "completedAt": j.completed_at.isoformat() if j.completed_at else None,
        "status": j.status,
        "marketsFound": j.markets_found,
        "marketsSkipped": j.markets_skipped,
        "marketsRejected": j.markets_rejected,
        "forecastsRetrieved": j.forecasts_retrieved,
        "durationSeconds": j.duration_seconds,
        "errorMessage": j.error_message,
    }


@router.get("/jobs")
async def get_jobs(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(JobRun).order_by(JobRun.started_at.desc()).limit(50)
    )
    return [_to_dict(j) for j in result.scalars().all()]


@router.post("/jobs/collect")
async def trigger_collection(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Trigger a manual data collection run. Returns immediately with a pending job."""
    # Check for already-running job
    running_q = await db.execute(
        select(JobRun).where(JobRun.status == "running").limit(1)
    )
    running = running_q.scalar_one_or_none()
    if running:
        return _to_dict(running)

    # Create a job record immediately so the UI can track progress
    job = JobRun(job_type="manual", status="running")
    db.add(job)
    await db.flush()
    await db.refresh(job)
    job_id = job.id
    result = _to_dict(job)
    await db.commit()

    # Run collection in background
    background_tasks.add_task(_bg_collect, job_id)
    return result


async def _bg_collect(job_id: int) -> None:
    """Background task that runs data collection and updates the job record."""
    from app.database import AsyncSessionLocal
    from app.services.collector import run_collection_job

    if AsyncSessionLocal is None:
        return
    async with _collection_lock:
        await run_collection_job(job_id=job_id)
