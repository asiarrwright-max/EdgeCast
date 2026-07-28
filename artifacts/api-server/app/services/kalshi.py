"""
Kalshi public API client.
Fetches active weather-related prediction markets — no credentials required.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known Kalshi weather series tickers (verified against live API, Jul 2026)
# Queried in Strategy 1 before falling back to keyword scan.
# ---------------------------------------------------------------------------
WEATHER_SERIES: list[str] = [
    # Temperature – Dallas
    "KXHIGHTDAL", "KXLOWTDAL",
    # Temperature – New York City
    "KXLOWTNYC", "KXNYCHOT", "HIGHNY", "HIGHNY0",
    # Temperature – Miami
    "KXHIGHMIA", "HIGHMIA",
    # Temperature – Houston
    "KXHIGHOU", "KXHOUHIGH", "KXLOWTHOU", "KXHOBBYTEMP",
    # Temperature – Denver
    "KXLOWTDEN", "KXHIGHDEN",
    # Temperature – Los Angeles
    "KXLOWTLAX", "KXTEMPLAXH",
    # Temperature – Minneapolis
    "KXHIGHTMIN",
    # Temperature – Philadelphia
    "KXPHILHIGH",
    # Temperature – San Antonio
    "KXHIGHTTSATX",
    # Temperature – Oklahoma City
    "KXLOWTOKC",
    # Temperature – Washington DC
    "KXTEMPDCH",
    # Temperature – Chicago
    "KXTEMPCHIH",
    # Temperature – US general / other
    "HIGHUS", "KXHIGHUS", "AVGTEMP", "TEMP",
    # Rain
    "KXRAINCHIM", "KXRAINHOU", "RAINHOU", "KXRAINNY", "RAINNY",
    "KXRAINNO", "RAINNO", "KXRAINSEA", "KXRAINSEAM",
    "KXRAINDALM", "KXRAIND", "KXRAINSFOM",
    # Snow
    "KXSNOWNY", "KXNYCSNOWM", "KXBOSSNOWM", "KXDETSNOWM",
    "KXDALSNOWM", "KXLAXSNOWM", "SNOWCHIM", "SNOWNYM",
    # Wind
    "KXWIND",
]

# Keywords for fallback detection when a market's series is not in WEATHER_SERIES
WEATHER_KEYWORDS: list[str] = [
    "temperature", "temp", "rain", "snow", "wind",
    "precipitation", "weather", "forecast", "high of", "low of",
    "degrees", "°", "rainfall", "snowfall", "humidity",
    "maximum temperature", "minimum temperature",
]

# ---------------------------------------------------------------------------
# Direct series-ticker → (city, lat, lon) mapping.
# Takes precedence over substring scanning. None = no specific city (US-wide).
# ---------------------------------------------------------------------------
SERIES_TO_CITY: dict[str, tuple[str, float, float] | None] = {
    "KXHIGHTDAL":   ("Dallas",          32.7767, -96.7970),
    "KXLOWTDAL":    ("Dallas",          32.7767, -96.7970),
    "KXRAINDALM":   ("Dallas",          32.7767, -96.7970),
    "KXDALSNOWM":   ("Dallas",          32.7767, -96.7970),
    "KXLOWTNYC":    ("New York City",   40.7128, -74.0060),
    "KXNYCHOT":     ("New York City",   40.7128, -74.0060),
    "HIGHNY":       ("New York City",   40.7128, -74.0060),
    "HIGHNY0":      ("New York City",   40.7128, -74.0060),
    "KXRAINNY":     ("New York City",   40.7128, -74.0060),
    "RAINNY":       ("New York City",   40.7128, -74.0060),
    "KXSNOWNY":     ("New York City",   40.7128, -74.0060),
    "KXNYCSNOWM":   ("New York City",   40.7128, -74.0060),
    "SNOWNYM":      ("New York City",   40.7128, -74.0060),
    "KXHIGHMIA":    ("Miami",           25.7617, -80.1918),
    "HIGHMIA":      ("Miami",           25.7617, -80.1918),
    "KXHIGHOU":     ("Houston",         29.7604, -95.3698),
    "KXHOUHIGH":    ("Houston",         29.7604, -95.3698),
    "KXLOWTHOU":    ("Houston",         29.7604, -95.3698),
    "KXHOBBYTEMP":  ("Houston",         29.7604, -95.3698),
    "KXRAINHOU":    ("Houston",         29.7604, -95.3698),
    "RAINHOU":      ("Houston",         29.7604, -95.3698),
    "KXLOWTDEN":    ("Denver",          39.7392, -104.9903),
    "KXHIGHDEN":    ("Denver",          39.7392, -104.9903),
    "KXLOWTLAX":    ("Los Angeles",     34.0522, -118.2437),
    "KXTEMPLAXH":   ("Los Angeles",     34.0522, -118.2437),
    "KXLAXSNOWM":   ("Los Angeles",     34.0522, -118.2437),
    "KXHIGHTMIN":   ("Minneapolis",     44.9778, -93.2650),
    "KXPHILHIGH":   ("Philadelphia",    39.9526, -75.1652),
    "KXHIGHTTSATX": ("San Antonio",     29.4241, -98.4936),
    "KXLOWTOKC":    ("Oklahoma City",   35.4676, -97.5164),
    "KXTEMPDCH":    ("Washington DC",   38.9072, -77.0369),
    "KXTEMPCHIH":   ("Chicago",         41.8781, -87.6298),
    "KXRAINCHIM":   ("Chicago",         41.8781, -87.6298),
    "SNOWCHIM":     ("Chicago",         41.8781, -87.6298),
    "KXRAINSEA":    ("Seattle",         47.6062, -122.3321),
    "KXRAINSEAM":   ("Seattle",         47.6062, -122.3321),
    "KXRAINNO":     ("New Orleans",     29.9511, -90.0715),
    "RAINNO":       ("New Orleans",     29.9511, -90.0715),
    "KXRAINSFOM":   ("San Francisco",   37.7749, -122.4194),
    "KXBOSSNOWM":   ("Boston",          42.3601, -71.0589),
    "KXDETSNOWM":   ("Detroit",         42.3314, -83.0458),
    # US-wide / no city
    "HIGHUS":  None,
    "KXHIGHUS": None,
    "AVGTEMP": None,
    "TEMP":    None,
    "KXRAIND": None,
}

# City codes checked as substrings in series_ticker for unknown series.
# Sorted longest-first to prefer specific codes over short ambiguous ones.
CITY_COORDS: dict[str, tuple[str, float, float]] = {
    "SATX": ("San Antonio",   29.4241, -98.4936),
    "NOLA": ("New Orleans",   29.9511, -90.0715),
    "PHIL": ("Philadelphia",  39.9526, -75.1652),
    "NYC":  ("New York City", 40.7128, -74.0060),
    "CHI":  ("Chicago",       41.8781, -87.6298),
    "LAX":  ("Los Angeles",   34.0522, -118.2437),
    "MIA":  ("Miami",         25.7617, -80.1918),
    "DFW":  ("Dallas",        32.7767, -96.7970),
    "DAL":  ("Dallas",        32.7767, -96.7970),
    "PHX":  ("Phoenix",       33.4484, -112.0740),
    "SEA":  ("Seattle",       47.6062, -122.3321),
    "DEN":  ("Denver",        39.7392, -104.9903),
    "ATL":  ("Atlanta",       33.7490, -84.3880),
    "BOS":  ("Boston",        42.3601, -71.0589),
    "SFO":  ("San Francisco", 37.7749, -122.4194),
    "LAS":  ("Las Vegas",     36.1699, -115.1398),
    "HOU":  ("Houston",       29.7604, -95.3698),
    "MSP":  ("Minneapolis",   44.9778, -93.2650),
    "MIN":  ("Minneapolis",   44.9778, -93.2650),
    "ORD":  ("Chicago",       41.8781, -87.6298),
    "OKC":  ("Oklahoma City", 35.4676, -97.5164),
    "MCI":  ("Kansas City",   39.0997, -94.5786),
    "STL":  ("St. Louis",     38.6270, -90.1994),
    "CLE":  ("Cleveland",     41.4993, -81.6944),
    "DET":  ("Detroit",       42.3314, -83.0458),
    "PHL":  ("Philadelphia",  39.9526, -75.1652),
    "PDX":  ("Portland",      45.5051, -122.6750),
    "DC":   ("Washington DC", 38.9072, -77.0369),
    "SF":   ("San Francisco", 37.7749, -122.4194),
    "KC":   ("Kansas City",   39.0997, -94.5786),
    "NY":   ("New York City", 40.7128, -74.0060),
    "LA":   ("Los Angeles",   34.0522, -118.2437),
    "NO":   ("New Orleans",   29.9511, -90.0715),
}

# City codes sorted longest-first for substring matching
_CITY_CODES_SORTED: list[tuple[str, tuple[str, float, float]]] = sorted(
    CITY_COORDS.items(), key=lambda x: -len(x[0])
)


@dataclass
class FetchResult:
    """Structured output of fetch_weather_markets()."""
    markets: list[dict] = field(default_factory=list)           # weather markets, city resolved
    parsing_failures: list[dict] = field(default_factory=list)  # weather markets, city unknown
    skipped_count: int = 0                                       # non-weather markets
    total_scanned: int = 0
    zero_reason: str | None = None                              # human-readable if nothing found


def _detect_market_type(raw: dict) -> str:
    """Return a broad category: temperature | rain | snow | wind | weather."""
    combined = " ".join([
        (raw.get("series_ticker") or ""),
        (raw.get("title") or ""),
        (raw.get("subtitle") or ""),
        (raw.get("ticker") or ""),
    ]).lower()

    if any(w in combined for w in ["snow", "snowfall", "blizzard"]):
        return "snow"
    if any(w in combined for w in ["rain", "precip", "rainfall", "precipitation"]):
        return "rain"
    if any(w in combined for w in ["wind", "gust"]):
        return "wind"
    if any(w in combined for w in ["temp", "temperature", "heat", "cold", "degree", "°", "high", "low"]):
        return "temperature"
    return "weather"


_DATE_SEGMENT_RE = re.compile(r"^\d{2}[A-Z]{3}\d{2}$")


def _derive_series_from_event_ticker(event_ticker: str) -> str:
    """
    Kalshi event_ticker format: SERIES-DATE  (e.g. KXLOWTNYC-26JUL27).
    Strip the trailing date segment(s) to recover the series.
    """
    parts = event_ticker.split("-")
    series_parts = [p for p in parts if not _DATE_SEGMENT_RE.match(p)]
    return "-".join(series_parts)


def extract_city(market: dict) -> tuple[str | None, float | None, float | None]:
    """
    Try to extract city name and coordinates from a Kalshi market.

    Resolution order:
    1. SERIES_TO_CITY direct lookup (most reliable)
    2. City code as substring of series_ticker (longest code wins)
    3. City code as exact part of ticker (split by "-")
    4. Full city name in title / subtitle text
    """
    series_ticker = (market.get("series_ticker") or "").upper()

    # Fallback: derive series from event_ticker when series_ticker is empty.
    # The Kalshi API sometimes omits series_ticker in response bodies.
    if not series_ticker:
        event_ticker = (market.get("event_ticker") or "").upper()
        if event_ticker:
            series_ticker = _derive_series_from_event_ticker(event_ticker)

    ticker = (market.get("ticker") or "").upper()
    title = market.get("title", "") or ""
    subtitle = market.get("subtitle", "") or ""

    # 1. Direct series lookup
    if series_ticker in SERIES_TO_CITY:
        entry = SERIES_TO_CITY[series_ticker]
        if entry is None:
            return None, None, None  # known US-wide series — no city
        return entry

    # 2. City code as substring of series_ticker (longest-first to avoid "NY" before "NYC")
    for code, (city, lat, lon) in _CITY_CODES_SORTED:
        if len(code) >= 3 and code in series_ticker:  # skip 2-char codes for series substring
            return city, lat, lon

    # 3. City code as exact part of the market ticker
    ticker_parts = set(ticker.split("-"))
    for code, (city, lat, lon) in _CITY_CODES_SORTED:
        if code in ticker_parts:
            return city, lat, lon

    # 4. Full city name in title / subtitle
    combined = (title + " " + subtitle).lower()
    for _code, (city, lat, lon) in _CITY_CODES_SORTED:
        if city.lower() in combined:
            return city, lat, lon

    return None, None, None


def _is_weather_market(raw: dict) -> bool:
    """Return True if this Kalshi market is a weather prediction market."""
    series = (raw.get("series_ticker") or "").upper()

    # Known weather series (exact match)
    if series in {s.upper() for s in WEATHER_SERIES}:
        return True

    # SERIES_TO_CITY covers additional known series
    if series in SERIES_TO_CITY:
        return True

    # Keyword fallback: check title, subtitle, ticker
    combined = " ".join([
        (raw.get("title") or ""),
        (raw.get("subtitle") or ""),
        (raw.get("ticker") or ""),
    ]).lower()
    return any(kw in combined for kw in WEATHER_KEYWORDS)


def parse_market(raw: dict) -> dict:
    """Normalise a raw Kalshi market dict into our storage format."""
    city, lat, lon = extract_city(raw)
    market_type = _detect_market_type(raw)

    def _parse_ts(val) -> datetime | None:
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return datetime.fromtimestamp(val, tz=timezone.utc)
        try:
            return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        except Exception:
            return None

    def _normalise_price(val) -> float | None:
        if val is None:
            return None
        try:
            f = float(val)
        except (TypeError, ValueError):
            return None
        return round(f / 100 if f > 1 else f, 4)

    return {
        "ticker": raw.get("ticker", ""),
        "event_ticker": raw.get("event_ticker"),
        "title": raw.get("title") or raw.get("ticker", ""),
        "subtitle": raw.get("subtitle"),
        "city": city,
        "lat": lat,
        "lon": lon,
        "weather_market_type": market_type,
        "target_date": raw.get("expected_expiration_time") or raw.get("close_time"),
        "open_time": _parse_ts(raw.get("open_time")),
        "close_time": _parse_ts(
            raw.get("close_time") or raw.get("expected_expiration_time")
        ),
        "status": raw.get("status", "active"),
        "yes_bid": _normalise_price(raw.get("yes_bid")),
        "yes_ask": _normalise_price(raw.get("yes_ask")),
        "no_bid": _normalise_price(raw.get("no_bid")),
        "no_ask": _normalise_price(raw.get("no_ask")),
        "volume": raw.get("volume"),
        "raw_data": raw,
    }


async def fetch_weather_markets() -> FetchResult:
    """
    Return all active Kalshi weather prediction markets.

    Strategy 1: Fetch by each known weather series_ticker (fast, targeted).
    Strategy 2: Scan all open markets and filter by weather keywords (fallback).

    Returns a FetchResult with structured counts so the collector can record
    exactly why each market was or wasn't collected.
    """
    settings = get_settings()
    base = settings.kalshi_base_url

    markets: list[dict] = []
    parsing_failures: list[dict] = []
    seen: set[str] = set()
    skipped_count = 0
    total_scanned = 0
    strategy1_found = 0

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:

        # ------------------------------------------------------------------ #
        # Strategy 1 – fetch by known weather series tickers                 #
        # ------------------------------------------------------------------ #
        for series in WEATHER_SERIES:
            cursor = None
            while True:
                params: dict = {"status": "open", "series_ticker": series, "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                try:
                    resp = await client.get(f"{base}/markets", params=params)
                    if resp.status_code == 404:
                        break  # series does not exist on Kalshi right now
                    resp.raise_for_status()
                    data = resp.json()
                    batch = data.get("markets", [])
                    total_scanned += len(batch)
                    for raw in batch:
                        t = raw.get("ticker", "")
                        if not t or t in seen:
                            continue
                        seen.add(t)
                        parsed = parse_market(raw)
                        if parsed["city"] is None:
                            parsing_failures.append({
                                **parsed,
                                "parsing_reason": "Unable to identify city",
                            })
                        else:
                            markets.append(parsed)
                        strategy1_found += 1
                    cursor = data.get("cursor")
                    if not cursor or not batch:
                        break
                except httpx.HTTPStatusError as exc:
                    logger.warning("Kalshi series %s: HTTP %s", series, exc.response.status_code)
                    break
                except Exception as exc:
                    logger.warning("Kalshi series %s failed: %s", series, exc)
                    break

        logger.info(
            "Strategy 1 complete: %d weather markets from %d known series",
            strategy1_found, len(WEATHER_SERIES),
        )

        # ------------------------------------------------------------------ #
        # Strategy 2 – keyword scan across all open markets (always runs     #
        # to catch series not in our curated list)                           #
        # ------------------------------------------------------------------ #
        logger.info("Strategy 2: keyword scan of all open markets…")
        cursor = None
        s2_weather = 0
        s2_skipped = 0
        while True:
            params = {"status": "open", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            try:
                resp = await client.get(f"{base}/markets", params=params)
                resp.raise_for_status()
                data = resp.json()
                batch = data.get("markets", [])
                total_scanned += len(batch)
                for raw in batch:
                    t = raw.get("ticker", "")
                    if not t or t in seen:
                        continue
                    if _is_weather_market(raw):
                        seen.add(t)
                        parsed = parse_market(raw)
                        if parsed["city"] is None:
                            parsing_failures.append({
                                **parsed,
                                "parsing_reason": "Unable to identify city",
                            })
                        else:
                            markets.append(parsed)
                        s2_weather += 1
                    else:
                        s2_skipped += 1
                cursor = data.get("cursor")
                if not cursor or not batch:
                    break
            except Exception as exc:
                logger.warning("Kalshi all-markets scan failed: %s", exc)
                break

        skipped_count = s2_skipped
        logger.info(
            "Strategy 2 complete: +%d weather, %d skipped (non-weather)",
            s2_weather, s2_skipped,
        )

    total_weather = len(markets) + len(parsing_failures)
    logger.info(
        "Kalshi fetch done: %d collected, %d parse failures, %d skipped, %d total scanned",
        len(markets), len(parsing_failures), skipped_count, total_scanned,
    )

    zero_reason: str | None = None
    if total_weather == 0:
        if total_scanned == 0:
            zero_reason = "Kalshi API returned no markets (possible connectivity or rate-limit issue)."
        else:
            zero_reason = (
                f"Kalshi returned {total_scanned:,} markets but none matched weather criteria. "
                "The API may have restructured its weather market series."
            )

    return FetchResult(
        markets=markets,
        parsing_failures=parsing_failures,
        skipped_count=skipped_count,
        total_scanned=total_scanned,
        zero_reason=zero_reason,
    )


async def check_kalshi_health() -> dict:
    """Ping Kalshi and return a ServiceStatus dict."""
    settings = get_settings()
    now = datetime.now(timezone.utc).isoformat()
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(
                f"{settings.kalshi_base_url}/markets",
                params={"limit": 1, "status": "open"},
            )
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
