"""
Kalshi public API client.
Fetches active weather-related prediction markets — no credentials required.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# Series tickers Kalshi uses for weather markets
WEATHER_SERIES = [
    "HIGHTEMP", "LOWTEMP", "HILOW", "HITEMP", "LOTEMP",
    "PRECIP", "RAIN", "SNOW", "WIND", "WEATHER",
]

# Keywords to detect weather markets when no series_ticker match
WEATHER_KEYWORDS = [
    "temperature", "temp", "rain", "snow", "wind",
    "precipitation", "weather", "forecast", "high of", "low of",
]

# City name extraction: map Kalshi city codes to full names + coordinates
CITY_COORDS: dict[str, tuple[str, float, float]] = {
    "NYC": ("New York City", 40.7128, -74.0060),
    "NY": ("New York City", 40.7128, -74.0060),
    "CHI": ("Chicago", 41.8781, -87.6298),
    "LA": ("Los Angeles", 34.0522, -118.2437),
    "LAX": ("Los Angeles", 34.0522, -118.2437),
    "MIA": ("Miami", 25.7617, -80.1918),
    "DFW": ("Dallas", 32.7767, -96.7970),
    "PHX": ("Phoenix", 33.4484, -112.0740),
    "SEA": ("Seattle", 47.6062, -122.3321),
    "DEN": ("Denver", 39.7392, -104.9903),
    "ATL": ("Atlanta", 33.7490, -84.3880),
    "BOS": ("Boston", 42.3601, -71.0589),
    "DC": ("Washington DC", 38.9072, -77.0369),
    "SFO": ("San Francisco", 37.7749, -122.4194),
    "SF": ("San Francisco", 37.7749, -122.4194),
    "LAS": ("Las Vegas", 36.1699, -115.1398),
    "HOU": ("Houston", 29.7604, -95.3698),
    "MSP": ("Minneapolis", 44.9778, -93.2650),
    "MIN": ("Minneapolis", 44.9778, -93.2650),
    "ORD": ("Chicago", 41.8781, -87.6298),
    "MCI": ("Kansas City", 39.0997, -94.5786),
    "KC": ("Kansas City", 39.0997, -94.5786),
    "STL": ("St. Louis", 38.6270, -90.1994),
    "CLE": ("Cleveland", 41.4993, -81.6944),
    "DET": ("Detroit", 42.3314, -83.0458),
    "PHL": ("Philadelphia", 39.9526, -75.1652),
    "PDX": ("Portland", 45.5051, -122.6750),
}


def extract_city(market: dict) -> tuple[str | None, float | None, float | None]:
    """Try to extract city name and coordinates from a Kalshi market."""
    ticker = market.get("ticker", "")
    title = market.get("title", "")
    subtitle = market.get("subtitle", "") or ""

    # Check every known city code against ticker parts
    parts = ticker.upper().split("-")
    for code, (city, lat, lon) in CITY_COORDS.items():
        if code in parts:
            return city, lat, lon

    # Fallback: scan title/subtitle for city names
    combined = (title + " " + subtitle).lower()
    for code, (city, lat, lon) in CITY_COORDS.items():
        if city.lower() in combined:
            return city, lat, lon

    return None, None, None


def parse_market(raw: dict) -> dict:
    """Normalise a raw Kalshi market dict into our storage format."""
    city, lat, lon = extract_city(raw)

    # Kalshi uses unix timestamps OR ISO strings for times
    def _parse_ts(val) -> datetime | None:
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return datetime.fromtimestamp(val, tz=timezone.utc)
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except Exception:
            return None

    # Price fields: Kalshi sometimes returns cents (0–100) or fractions (0–1)
    def _normalise_price(val) -> float | None:
        if val is None:
            return None
        f = float(val)
        # If stored as cents integer (e.g. 45 means $0.45), convert to fraction
        if f > 1:
            f = f / 100
        return round(f, 4)

    return {
        "ticker": raw.get("ticker", ""),
        "event_ticker": raw.get("event_ticker"),
        "title": raw.get("title", raw.get("ticker", "")),
        "subtitle": raw.get("subtitle"),
        "city": city,
        "target_date": raw.get("expected_expiration_time") or raw.get("close_time"),
        "open_time": _parse_ts(raw.get("open_time")),
        "close_time": _parse_ts(raw.get("close_time") or raw.get("expected_expiration_time")),
        "status": raw.get("status", "active"),
        "yes_bid": _normalise_price(raw.get("yes_bid")),
        "yes_ask": _normalise_price(raw.get("yes_ask")),
        "no_bid": _normalise_price(raw.get("no_bid")),
        "no_ask": _normalise_price(raw.get("no_ask")),
        "volume": raw.get("volume"),
        "lat": lat,
        "lon": lon,
        "raw_data": raw,
    }


def _is_weather_market(raw: dict) -> bool:
    series = (raw.get("series_ticker") or "").upper()
    if any(series.startswith(s) for s in WEATHER_SERIES):
        return True
    combined = (
        (raw.get("title") or "")
        + " "
        + (raw.get("subtitle") or "")
        + " "
        + (raw.get("ticker") or "")
    ).lower()
    return any(kw in combined for kw in WEATHER_KEYWORDS)


async def fetch_weather_markets() -> list[dict]:
    """Return a list of parsed weather markets from Kalshi's public API."""
    settings = get_settings()
    base = settings.kalshi_base_url
    markets: list[dict] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        # Strategy 1: fetch by each known weather series_ticker
        for series in WEATHER_SERIES:
            cursor = None
            while True:
                params: dict = {"status": "open", "series_ticker": series, "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                try:
                    resp = await client.get(f"{base}/markets", params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    for raw in data.get("markets", []):
                        t = raw.get("ticker", "")
                        if t and t not in seen:
                            seen.add(t)
                            markets.append(parse_market(raw))
                    cursor = data.get("cursor")
                    if not cursor or not data.get("markets"):
                        break
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        break  # series doesn't exist
                    logger.warning("Kalshi series %s: HTTP %s", series, exc.response.status_code)
                    break
                except Exception as exc:
                    logger.warning("Kalshi series %s failed: %s", series, exc)
                    break

        # Strategy 2: if nothing found, scan all open markets
        if not markets:
            logger.info("No weather markets via series; scanning all open markets…")
            cursor = None
            while True:
                params = {"status": "open", "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                try:
                    resp = await client.get(f"{base}/markets", params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    batch = data.get("markets", [])
                    for raw in batch:
                        if _is_weather_market(raw):
                            t = raw.get("ticker", "")
                            if t and t not in seen:
                                seen.add(t)
                                markets.append(parse_market(raw))
                    cursor = data.get("cursor")
                    if not cursor or not batch:
                        break
                except Exception as exc:
                    logger.warning("Kalshi all-markets scan failed: %s", exc)
                    break

    logger.info("Kalshi: found %d weather markets", len(markets))
    return markets


async def check_kalshi_health() -> dict:
    """Ping Kalshi and return a ServiceStatus dict."""
    settings = get_settings()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(f"{settings.kalshi_base_url}/markets", params={"limit": 1})
            if resp.status_code < 400:
                return {"name": "Kalshi", "status": "ok", "message": None, "lastChecked": now}
            return {
                "name": "Kalshi",
                "status": "error",
                "message": f"HTTP {resp.status_code}",
                "lastChecked": now,
            }
    except Exception as exc:
        return {
            "name": "Kalshi",
            "status": "error",
            "message": str(exc)[:200],
            "lastChecked": now,
        }
