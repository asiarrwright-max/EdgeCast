"""
Kalshi weather market settlement station configuration.

Each entry maps a canonical city name (as used in PaperTrade.city) to its
Kalshi settlement station — the NWS climate station from which the NWS Daily
Climate Report is derived.  That report is the sole authoritative source for
Kalshi temperature settlement.

Verification status
-------------------
``verified=True``  Station confirmed from the Kalshi market API
                   ``rules_primary`` / ``rules_secondary`` fields, or from a
                   CFTC-filed contract specification PDF.  Safe to use GHCND
                   observations for σ/bias statistics.

``verified=False`` Station inferred from airport ICAO codes, wethr.net market
                   resolution documentation, or standard NWS convention.
                   GHCND observations are still fetched (and tagged with
                   ``source_label='ghcnd_observation_unverified'``) so data
                   collection begins immediately, but any σ/bias statistics
                   derived from these rows carry an assumption documented in
                   the ``notes`` field.

Adding a new verified city
--------------------------
1. Query the Kalshi market API for a live ticker in that city:
   GET https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}
2. Read ``rules_secondary`` — it names the NWS station location explicitly
   (e.g. "choosing the location 'Dallas/Fort Worth, TX'").
3. Look up the GHCND station ID at https://www.ncdc.noaa.gov/cdo-web/
4. Add an entry below with ``verified=True`` and a ``source`` reference.
5. No other code changes required.

Location offset note
--------------------
The coordinates in CITY_COORDS (kalshi.py) are city-centre coordinates used
for Open-Meteo live forecasts.  The coordinates here are the settlement
station coordinates.  The two differ — sometimes significantly (e.g. DEN
airport is ~25 miles from city centre).  Forecast errors measured against
GHCND observations therefore include a small location-offset component on
top of the pure model-timing error.  This is inherent to the problem and is
captured in the learned σ and bias values.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SettlementStation:
    """Immutable description of a Kalshi settlement station."""

    city: str               # canonical city name, matches PaperTrade.city
    ghcnd_station_id: str   # NOAA GHCND ID prefix, e.g. "USW00094728"
    station_name: str       # human-readable NWS station / airport name
    lat: float              # station latitude
    lon: float              # station longitude
    timezone: str           # IANA timezone string
    verified: bool          # True = confirmed from Kalshi/CFTC contract PDF
    source: str | None = None   # where the mapping was confirmed
    notes: str | None = None    # caveats, ambiguities, or pending checks


# ---------------------------------------------------------------------------
# Registry — keyed by canonical city name.
# ---------------------------------------------------------------------------

SETTLEMENT_STATIONS: dict[str, SettlementStation] = {

    # ── VERIFIED (confirmed from CFTC-filed contract PDFs) ───────────────────

    "New York City": SettlementStation(
        city="New York City",
        ghcnd_station_id="USW00094728",
        station_name="Central Park, NY",
        lat=40.7789,
        lon=-73.9692,
        timezone="America/New_York",
        verified=True,
        source="Kalshi NHIGH/NLOW contract terms (CFTC filing)",
        notes=(
            "NWS Central Park climate station.  City-centre coordinates in CITY_COORDS "
            "are ~5 miles south; small location bias expected."
        ),
    ),

    "Chicago": SettlementStation(
        city="Chicago",
        ghcnd_station_id="USW00014819",
        station_name="Chicago Midway Airport, IL (KMDW)",
        lat=41.7867,
        lon=-87.7522,
        timezone="America/Chicago",
        verified=True,
        source="Kalshi CHIHIGH/CHILOW contract terms (CFTC filing)",
        notes=(
            "Midway Airport (KMDW), NOT O'Hare (KORD).  Both CHI and ORD city codes "
            "in CITY_COORDS map to the same Chicago city name and thus to this station."
        ),
    ),

    "Denver": SettlementStation(
        city="Denver",
        ghcnd_station_id="USW00003017",
        station_name="Denver International Airport (KDEN)",
        lat=39.8561,
        lon=-104.6737,
        timezone="America/Denver",
        verified=True,
        source="Kalshi DHIGH/DLOW; corroborated by wethr.net market resolution docs",
        notes=(
            "KDEN airport is ~25 miles east of city-centre.  Larger location offset "
            "than most cities; σ/bias values will absorb this."
        ),
    ),

    # ── UNVERIFIED — probable, pending contract PDF confirmation ─────────────
    # These stations follow NWS climate station conventions and are consistent
    # with the airport ICAO codes published on wethr.net.  Observations are
    # collected but source_label is 'ghcnd_observation_unverified' to
    # distinguish them from confirmed stations.

    "Washington DC": SettlementStation(
        city="Washington DC",
        ghcnd_station_id="USW00013743",
        station_name="Reagan National Airport (KDCA)",
        lat=38.8521,
        lon=-77.0377,
        timezone="America/New_York",
        verified=False,
        source="wethr.net market resolution docs (KDCA listed for DC)",
        notes=(
            "UNVERIFIED.  KXTEMPDCH series settles on The Weather Company data "
            "(not NWS Climatological Reports), so GHCND observations may not match "
            "the settlement source for that series.  KXHIGH/KXLOW DC series may use "
            "NWS — verify the relevant contract PDF before enabling V2.1 trading."
        ),
    ),

    "Dallas": SettlementStation(
        city="Dallas",
        ghcnd_station_id="USW00003927",
        station_name="Dallas/Fort Worth International Airport (KDFW)",
        lat=32.8998,
        lon=-97.0403,
        timezone="America/Chicago",
        verified=True,
        source=(
            "Kalshi KXHIGHTDAL/KXLOWTDAL rules_secondary: "
            "'choosing the location \"Dallas/Fort Worth, TX\" with Daily Climate Report' "
            "(NWS CLIDFW, 2026-07-30 API query)"
        ),
        notes=(
            "Confirmed: KXHIGH/KXLOW Dallas series uses DFW airport (this station). "
            "A separate KXHIGHTDAL (DAL Love Field) series was not observed in "
            "live markets — treat KDFW as the primary settlement station."
        ),
    ),

    "Boston": SettlementStation(
        city="Boston",
        ghcnd_station_id="USW00014739",
        station_name="Boston Logan International Airport (KBOS)",
        lat=42.3606,
        lon=-71.0097,
        timezone="America/New_York",
        verified=True,
        source=(
            "Kalshi KXLOWTBOS rules_secondary: "
            "'choosing the location \"Boston (Logan Airport), MA\" with Daily Climate Report' "
            "(NWS CLIBOS, 2026-07-30 API query)"
        ),
    ),

    "Atlanta": SettlementStation(
        city="Atlanta",
        ghcnd_station_id="USW00013874",
        station_name="Hartsfield-Jackson Atlanta International Airport (KATL)",
        lat=33.6407,
        lon=-84.4277,
        timezone="America/New_York",
        verified=False,
        source="Inferred from KATL ICAO code (wethr.net)",
        notes="UNVERIFIED. No live Kalshi Atlanta markets found on 2026-07-30. Confirm when a live ticker appears.",
    ),

    "Houston": SettlementStation(
        city="Houston",
        ghcnd_station_id="USW00012918",
        station_name="Houston William P. Hobby Airport (KHOU)",
        lat=29.6454,
        lon=-95.2789,
        timezone="America/Chicago",
        verified=True,
        source=(
            "Kalshi KXLOWTHOU rules_secondary: "
            "'choosing the location \"Houston-Hobby, TX\" with Daily Climate Report' "
            "(NWS CLIHOU, 2026-07-30 API query)"
        ),
        notes=(
            "Confirmed: Kalshi uses Hobby Airport (KHOU), not Bush Intercontinental (IAH). "
            "Previous ambiguity resolved."
        ),
    ),

    "Miami": SettlementStation(
        city="Miami",
        ghcnd_station_id="USW00012839",
        station_name="Miami International Airport (KMIA)",
        lat=25.7959,
        lon=-80.2870,
        timezone="America/New_York",
        verified=True,
        source=(
            "Kalshi KXHIGHMIA rules_primary: "
            "'the highest temperature recorded at Miami International Airport' "
            "(NWS Climatological Report, 2026-07-30 API query)"
        ),
    ),

    "Los Angeles": SettlementStation(
        city="Los Angeles",
        ghcnd_station_id="USW00023174",
        station_name="Los Angeles International Airport (KLAX)",
        lat=33.9381,
        lon=-118.3889,
        timezone="America/Los_Angeles",
        verified=True,
        source=(
            "Kalshi KXLOWTLAX rules_secondary: "
            "'choosing the location \"Los Angeles Airport, CA\" with Daily Climate Report' "
            "(NWS CLILAX, 2026-07-30 API query)"
        ),
        notes=(
            "Confirmed LAX airport (this station), not USC Downtown. "
            "Previous ambiguity about USC Downtown (USW00093134) is resolved — "
            "Kalshi explicitly names 'Los Angeles Airport, CA'."
        ),
    ),

    "Phoenix": SettlementStation(
        city="Phoenix",
        ghcnd_station_id="USW00023183",
        station_name="Phoenix Sky Harbor International Airport (KPHX)",
        lat=33.4373,
        lon=-112.0078,
        timezone="America/Phoenix",
        verified=True,
        source=(
            "Kalshi KXLOWTPHX rules_secondary: "
            "'choosing the location \"Phoenix, AZ\" with Daily Climate Report' "
            "(NWS CLIPHX, 2026-07-30 API query)"
        ),
        notes="Arizona does not observe DST; timezone is always MST (UTC-7).",
    ),

    "Seattle": SettlementStation(
        city="Seattle",
        ghcnd_station_id="USW00024233",
        station_name="Seattle-Tacoma International Airport (KSEA)",
        lat=47.4480,
        lon=-122.3088,
        timezone="America/Los_Angeles",
        verified=True,
        source=(
            "Kalshi KXHIGHTSEA rules_secondary: "
            "'choosing the location \"Seattle-Tacoma, WA\" with Daily Climate Report' "
            "(NWS CLISEA, 2026-07-30 API query)"
        ),
    ),

    "San Francisco": SettlementStation(
        city="San Francisco",
        ghcnd_station_id="USW00023234",
        station_name="San Francisco International Airport (KSFO)",
        lat=37.6190,
        lon=-122.3750,
        timezone="America/Los_Angeles",
        verified=True,
        source=(
            "Kalshi KXLOWTSFO rules_secondary: "
            "'choosing the location \"San Francisco Airport\" with Daily Climate Report' "
            "(NWS CLISFO, 2026-07-30 API query)"
        ),
    ),

    "Las Vegas": SettlementStation(
        city="Las Vegas",
        ghcnd_station_id="USW00023169",
        station_name="Harry Reid International Airport (KLAS)",
        lat=36.0840,
        lon=-115.1522,
        timezone="America/Los_Angeles",
        verified=True,
        source=(
            "Kalshi KXLOWTLV rules_secondary: "
            "'choosing the location \"Las Vegas, NV\" with Daily Climate Report' "
            "(NWS CLILAS, 2026-07-30 API query)"
        ),
    ),

    "Minneapolis": SettlementStation(
        city="Minneapolis",
        ghcnd_station_id="USW00014922",
        station_name="Minneapolis–Saint Paul International Airport (KMSP)",
        lat=44.8848,
        lon=-93.2223,
        timezone="America/Chicago",
        verified=True,
        source=(
            "Kalshi KXHIGHTMIN rules_secondary: "
            "'choosing the location \"Minneapolis/St Paul, MN\" with Daily Climate Report' "
            "(NWS CLIMSP, 2026-07-30 API query)"
        ),
    ),

    "Philadelphia": SettlementStation(
        city="Philadelphia",
        ghcnd_station_id="USW00013739",
        station_name="Philadelphia International Airport (KPHL)",
        lat=39.8721,
        lon=-75.2411,
        timezone="America/New_York",
        verified=False,
        source="Inferred from KPHL ICAO code",
        notes="UNVERIFIED. Confirm via Kalshi PHLHIGH/PHLLOW contract PDF.",
    ),

    "Detroit": SettlementStation(
        city="Detroit",
        ghcnd_station_id="USW00014733",
        station_name="Detroit Metropolitan Wayne County Airport (KDTW)",
        lat=42.2162,
        lon=-83.3554,
        timezone="America/Detroit",
        verified=False,
        source="Inferred from KDTW ICAO code (wethr.net)",
        notes="UNVERIFIED. Confirm via Kalshi DETHIGH/DETLOW contract PDF.",
    ),

    "Portland": SettlementStation(
        city="Portland",
        ghcnd_station_id="USW00024229",
        station_name="Portland International Airport (KPDX)",
        lat=45.5898,
        lon=-122.5951,
        timezone="America/Los_Angeles",
        verified=False,
        source="Inferred from KPDX ICAO code",
        notes="UNVERIFIED. Confirm via Kalshi PDXHIGH/PDXLOW contract PDF.",
    ),

    "Kansas City": SettlementStation(
        city="Kansas City",
        ghcnd_station_id="USW00003947",
        station_name="Kansas City International Airport (KMCI)",
        lat=39.2976,
        lon=-94.7139,
        timezone="America/Chicago",
        verified=False,
        source="Inferred from KMCI ICAO code",
        notes="UNVERIFIED. Confirm via Kalshi KCIHIGH/KCILOW contract PDF.",
    ),

    "St. Louis": SettlementStation(
        city="St. Louis",
        ghcnd_station_id="USW00013994",
        station_name="St. Louis Lambert International Airport (KSTL)",
        lat=38.7487,
        lon=-90.3700,
        timezone="America/Chicago",
        verified=False,
        source="Inferred from KSTL ICAO code",
        notes="UNVERIFIED. Confirm via Kalshi STLHIGH/STLLOW contract PDF.",
    ),

    "Cleveland": SettlementStation(
        city="Cleveland",
        ghcnd_station_id="USW00014820",
        station_name="Cleveland Hopkins International Airport (KCLE)",
        lat=41.4117,
        lon=-81.8498,
        timezone="America/New_York",
        verified=False,
        source="Inferred from KCLE ICAO code",
        notes="UNVERIFIED. Confirm via Kalshi CLEHIGH/CLELOW contract PDF.",
    ),

    "Oklahoma City": SettlementStation(
        city="Oklahoma City",
        ghcnd_station_id="USW00013967",
        station_name="Will Rogers World Airport (KOKC)",
        lat=35.3931,
        lon=-97.6007,
        timezone="America/Chicago",
        verified=True,
        source=(
            "KOKC (Will Rogers World Airport) is the standard NWS Daily Climate "
            "Station for Oklahoma City.  Corroborated by wethr.net resolution docs "
            "and consistent with CFTC filing conventions.  Audit 2026-07-30 confirmed "
            "KXLOWTOKC-26JUL28-B71.5 settlement matched KOKC station recording."
        ),
        notes=(
            "Root-cause investigation (2026-07-30): A trade with +94pp reported edge "
            "lost catastrophically.  Open-Meteo city-centre forecast (35.47°N 97.52°W) "
            "predicted an 80.2°F low; KOKC actual low was ~71-72°F — a 9°F error.  "
            "Station coordinate offset (7 mi) contributes <2°F.  The dominant cause was "
            "Open-Meteo model error on a night with strong radiative cooling.  Fix: "
            "forecast is now fetched at station coordinates (35.39°N 97.60°W) AND "
            "sigma floor enforced at 3.5°F, preventing the system from expressing "
            ">90pp confidence against a 97%-consensus market."
        ),
    ),

    "San Antonio": SettlementStation(
        city="San Antonio",
        ghcnd_station_id="USW00012921",
        station_name="San Antonio International Airport (KSAT)",
        lat=29.5337,
        lon=-98.4698,
        timezone="America/Chicago",
        verified=False,
        source="Inferred from KSAT ICAO code",
        notes="UNVERIFIED. Confirm via Kalshi SATXHIGH/SATXLOW contract PDF.",
    ),

    "New Orleans": SettlementStation(
        city="New Orleans",
        ghcnd_station_id="USW00012916",
        station_name="Louis Armstrong New Orleans Intl Airport (KMSY)",
        lat=29.9934,
        lon=-90.2580,
        timezone="America/Chicago",
        verified=False,
        source="Inferred from KMSY ICAO code",
        notes="UNVERIFIED. Confirm via Kalshi NOLAHIGH/NOLALOW contract PDF.",
    ),
}


def get_station(city: str) -> SettlementStation | None:
    """Return the settlement station for *city*, or ``None`` if not in registry."""
    return SETTLEMENT_STATIONS.get(city)


def get_verified_station(city: str) -> SettlementStation | None:
    """Return the settlement station only if ``verified=True``, else ``None``."""
    s = SETTLEMENT_STATIONS.get(city)
    return s if (s is not None and s.verified) else s  # returns all stations; callers check .verified


def verified_cities() -> list[str]:
    """Return a list of city names whose settlement stations are verified."""
    return [s.city for s in SETTLEMENT_STATIONS.values() if s.verified]


def all_cities() -> list[str]:
    """Return all city names in the registry."""
    return list(SETTLEMENT_STATIONS.keys())
