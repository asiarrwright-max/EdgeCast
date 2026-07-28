"""
Open-Meteo forecast client.
Free API – no key required.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_probability_max",
    "wind_speed_10m_max",
]


async def fetch_forecast(city: str, lat: float, lon: float) -> list[dict]:
    """Fetch 16-day daily forecast for a city. Returns a list of day dicts."""
    settings = get_settings()
    url = f"{settings.openmeteo_base_url}/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": ",".join(DAILY_VARS),
        "timezone": "auto",
        "forecast_days": 16,
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        daily = data.get("daily", {})
        dates = daily.get("time", [])
        highs = daily.get("temperature_2m_max", [None] * len(dates))
        lows = daily.get("temperature_2m_min", [None] * len(dates))
        precips = daily.get("precipitation_probability_max", [None] * len(dates))
        winds = daily.get("wind_speed_10m_max", [None] * len(dates))

        rows = []
        for i, date in enumerate(dates):
            rows.append({
                "city": city,
                "forecast_date": date,
                "temperature_high": highs[i] if i < len(highs) else None,
                "temperature_low": lows[i] if i < len(lows) else None,
                "precipitation_prob": precips[i] if i < len(precips) else None,
                "wind_speed": winds[i] if i < len(winds) else None,
                "forecast_json": {
                    "latitude": data.get("latitude"),
                    "longitude": data.get("longitude"),
                    "timezone": data.get("timezone"),
                },
            })
        return rows
    except Exception as exc:
        logger.warning("Open-Meteo forecast failed for %s (%s, %s): %s", city, lat, lon, exc)
        return []


async def check_openmeteo_health() -> dict:
    """Ping Open-Meteo and return a ServiceStatus dict."""
    settings = get_settings()
    now = datetime.now(timezone.utc).isoformat()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # NYC as a trivial check
            resp = await client.get(
                f"{settings.openmeteo_base_url}/forecast",
                params={
                    "latitude": 40.7128,
                    "longitude": -74.0060,
                    "daily": "temperature_2m_max",
                    "timezone": "auto",
                    "forecast_days": 1,
                },
            )
            if resp.status_code < 400:
                return {"name": "Open-Meteo", "status": "ok", "message": None, "lastChecked": now}
            return {
                "name": "Open-Meteo",
                "status": "error",
                "message": f"HTTP {resp.status_code}",
                "lastChecked": now,
            }
    except Exception as exc:
        return {
            "name": "Open-Meteo",
            "status": "error",
            "message": str(exc)[:200],
            "lastChecked": now,
        }
