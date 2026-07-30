"""
City availability service.

Computes the availability status of every city in the settlement station
registry by combining two data sources:

  1. settlement_stations.py — static properties: ``verified``,
     ``nws_settlement``
  2. kalshi_markets DB table — dynamic: does this city have at least one
     active Kalshi market collected in the last MARKET_STALENESS_HOURS?

Three status values:

  active   — verified + nws_settlement + recent Kalshi markets found.
              V2.1 will trade, data collection proceeds normally.

  inactive — not permanently blocked, but not currently tradeable.
             Either the station is unverified, or Kalshi has no live
             markets for this city right now.  Will auto-reactivate the
             next time the collection job picks up markets for this city.

  blocked  — nws_settlement=False.  Kalshi settles this city's contracts
             on a non-NWS data source (e.g. The Weather Company).  V2.1
             will never trade this city regardless of verified status.

Auto-reactivation
-----------------
No separate discovery loop is needed.  The collection job already queries
Kalshi for ALL active weather markets every 3 hours.  When Kalshi launches
markets for a previously inactive city, the next collection run will upsert
them into kalshi_markets, and the next call to ``compute_city_statuses``
will reflect the new status automatically.  Call
``get_recently_reactivated(session)`` after each collection run to log
city transitions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KalshiMarket
from app.services.settlement_stations import SETTLEMENT_STATIONS

# A city is considered "has active markets" if at least one of its markets
# was collected within this window.  3× the collection interval (3 h).
MARKET_STALENESS_HOURS = 9


def _classify(city: str, station, last_seen: datetime | None) -> tuple[str, str | None]:
    """Return (status, reason) for a single city."""
    if not getattr(station, "nws_settlement", True):
        return "blocked", (
            "Kalshi settles this city's contracts via The Weather Company, not NWS. "
            "EdgeCast's sigma/bias are built on NWS/GHCND data — edge estimates would "
            "be systematically wrong."
        )

    cutoff = datetime.now(timezone.utc) - timedelta(hours=MARKET_STALENESS_HOURS)
    has_markets = last_seen is not None and last_seen >= cutoff

    if has_markets and station.verified:
        return "active", None

    if not station.verified and not has_markets:
        return "inactive", (
            "Station not yet verified and no recent Kalshi markets found. "
            "City will reactivate automatically once Kalshi launches markets "
            "and the station is confirmed."
        )
    if not station.verified:
        return "inactive", (
            "Station not yet verified — settlement station mapping unconfirmed. "
            "Verify via Kalshi API rules_secondary field, then set verified=True."
        )
    # verified but no markets
    return "inactive", (
        f"No active Kalshi markets found in the last {MARKET_STALENESS_HOURS} hours. "
        "City will reactivate automatically on the next collection run once Kalshi "
        "launches eligible markets."
    )


async def compute_city_statuses(session: AsyncSession) -> list[dict]:
    """
    Return a list of dicts — one per city — with status, reason, and metadata.

    This is the primary API surface; call it from any endpoint that needs
    city availability information.
    """
    # Most recent active market per city in the DB
    q = await session.execute(
        select(
            KalshiMarket.city,
            func.max(KalshiMarket.collection_timestamp).label("last_seen"),
        )
        .where(KalshiMarket.city.is_not(None))
        .group_by(KalshiMarket.city)
    )
    last_seen_map: dict[str, datetime] = {
        row[0]: row[1] for row in q.all() if row[0]
    }

    results: list[dict] = []
    for city, station in SETTLEMENT_STATIONS.items():
        last_seen = last_seen_map.get(city)
        status, reason = _classify(city, station, last_seen)
        results.append({
            "city": city,
            "status": status,          # active | inactive | blocked
            "reason": reason,          # human-readable explanation when not active
            "verified": station.verified,
            "nwsSettlement": getattr(station, "nws_settlement", True),
            "lastMarketSeenAt": last_seen.isoformat() if last_seen else None,
        })

    return results


async def get_active_cities(session: AsyncSession) -> list[str]:
    """Return city names whose status is 'active'."""
    statuses = await compute_city_statuses(session)
    return [s["city"] for s in statuses if s["status"] == "active"]


async def get_recently_reactivated(
    session: AsyncSession,
    *,
    within_hours: int = 4,
) -> list[str]:
    """
    Return cities that appear to have transitioned from inactive → active
    recently (i.e. their first Kalshi market was collected in the last
    ``within_hours``).  Useful for logging after a collection run.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)

    # Cities whose earliest-ever market was collected inside the window
    q = await session.execute(
        select(
            KalshiMarket.city,
            func.min(KalshiMarket.collection_timestamp).label("first_seen"),
        )
        .where(KalshiMarket.city.is_not(None))
        .group_by(KalshiMarket.city)
        .having(func.min(KalshiMarket.collection_timestamp) >= cutoff)
    )
    return [row[0] for row in q.all() if row[0]]


def summarise(statuses: list[dict]) -> dict:
    """Aggregate status counts and city lists from compute_city_statuses output."""
    active   = [s for s in statuses if s["status"] == "active"]
    inactive = [s for s in statuses if s["status"] == "inactive"]
    blocked  = [s for s in statuses if s["status"] == "blocked"]
    return {
        "activeCount":   len(active),
        "inactiveCount": len(inactive),
        "blockedCount":  len(blocked),
        "totalCount":    len(statuses),
        "activeCities":  [s["city"] for s in active],
        "inactiveCities": [s["city"] for s in inactive],
        "blockedCities": [s["city"] for s in blocked],
    }
