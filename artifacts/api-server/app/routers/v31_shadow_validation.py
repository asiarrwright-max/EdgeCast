"""Read-only API for prospective frozen V3.1 shadow evidence."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.services.v31_shadow_validation import load_shadow_report

router = APIRouter(tags=["V3.1 shadow validation"])


@router.get("/analytics/v3/v31-shadow-validation")
async def get_v31_shadow_validation(
    _user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Return cumulative forward metrics without changing application state."""
    report = await load_shadow_report(session)
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "collection_state": (
            "COLLECTING" if report["total_observations"] else "AWAITING_FIRST_OBSERVATION"
        ),
        "cohort_boundary": (
            "Only V3 paper opportunities created after this instrumentation is deployed; "
            "no historical backfill."
        ),
        "report": report,
    }
