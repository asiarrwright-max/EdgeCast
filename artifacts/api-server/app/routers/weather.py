from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import WeatherForecast

router = APIRouter(tags=["weather"])


@router.get("/weather/forecasts")
async def get_weather_forecasts(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(WeatherForecast)
        .order_by(WeatherForecast.city.asc(), WeatherForecast.forecast_date.asc())
    )
    forecasts = result.scalars().all()
    return [
        {
            "id": f.id,
            "city": f.city,
            "forecastDate": f.forecast_date,
            "temperatureHigh": f.temperature_high,
            "temperatureLow": f.temperature_low,
            "precipitationProb": f.precipitation_prob,
            "windSpeed": f.wind_speed,
            "retrievedAt": f.retrieved_at.isoformat(),
        }
        for f in forecasts
    ]
