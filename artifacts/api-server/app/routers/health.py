from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.services.kalshi import check_kalshi_health
from app.services.openmeteo import check_openmeteo_health

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def health_check():
    return {"status": "ok"}


@router.get("/health/services")
async def get_service_health(_user: dict = Depends(get_current_user)):
    kalshi = await check_kalshi_health()
    openmeteo = await check_openmeteo_health()
    return [kalshi, openmeteo]
