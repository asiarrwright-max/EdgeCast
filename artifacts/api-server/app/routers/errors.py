from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import AppError

router = APIRouter(tags=["errors"])


@router.get("/errors")
async def get_errors(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(AppError).order_by(AppError.occurred_at.desc()).limit(limit)
    )
    return [
        {
            "id": e.id,
            "errorType": e.error_type,
            "message": e.message,
            "context": e.context,
            "occurredAt": e.occurred_at.isoformat(),
        }
        for e in result.scalars().all()
    ]
