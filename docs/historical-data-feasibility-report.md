# EdgeCast Historical Data Feasibility Report

**Date:** July 28, 2026  
**Scope:** Research only — no implementation. No v1/v2 logic or trade data modified.  
**Purpose:** Determine whether reliable historical observations and true archived forecasts are available before building any new historical-weather model.

---

## Table of Contents

1. [Kalshi Settlement Station Mapping](#1-kalshi-settlement-station-mapping)
2. [Historical Observation Sources](#2-historical-observation-sources)
3. [Archived Forecast Sources](#3-archived-forecast-sources)
4. [Source Comparison Table](#4-source-comparison-table)
5. [Approach Ranking](#5-approach-ranking)
6. [Minimum Viable Historical-Data Plan](#6-minimum-viable-historical-data-plan)
7. [Data Quality Requirements & Proposed Schema](#7-data-quality-requirements--proposed-schema)
8. [Risks: Look-ahead Bias and Bad Joins](#8-risks-look-ahead-bias-and-bad-joins)
9. [Implementation Phases](#9-implementation-phases)
10. [Conclusion](#10-conclusion)

---

## 1. Kalshi Settlement Station Mapping

Kalshi weather contracts settle on the **National Weather Service (NWS) Daily Climate Report** for a specific official climate station — not the general city location and not an airport METAR feed directly. The NWS Daily Climate Report is produced by local NWS forecast offices and is the authoritative record.

### Verified station mappings (from CFTC-filed contract specifications)

| City (code) | Contract series | Settlement station | Station type | GHCND ID | Timezone | Status |
|---|---|---|---|---|---|---|
| New York City (NYC / NY) | NHIGH / NLOW | **Central Park, NY** | NWS climate station | USW00094728 | EST/EDT | ✅ Verified |
| Chicago (CHI / ORD) | CHIHIGH / CHILOW | **Chicago Midway Airport, IL** | ASOS / NWS climate | USW00014819 | CST/CDT | ✅ Verified |
| Denver (DEN) | DHIGH / DLOW | **Denver Intl Airport (KDEN)** | ASOS / NWS climate | USW00003017 | MST/MDT | ✅ Verified (wethr.net) |
| Washington DC (DC) | DCHIGH / DCLOW | **Reagan National Airport (KDCA)** | ASOS / NWS climate | USW00013743 | EST/EDT | ✅ Verified (wethr.net) |
| Dallas — DFW (DFW) | DFWHIGH / DFWLOW | **Dallas/Fort Worth Intl (KDFW)** | ASOS / NWS climate | USW00003927 | CST/CDT | ✅ Verified (wethr.net) |
| Dallas — Love (DAL) | DALHIGH / DALHE | **Dallas Love Field (KDAL)** | ASOS | USW00013960 | CST/CDT | ✅ Verified (wethr.net) |
| Boston (BOS) | BOSHIGH / BOSLOW | **Boston Logan Airport (KBOS)** | ASOS / NWS climate | USW00014739 | EST/EDT | ✅ Verified (wethr.net) |
| Atlanta (ATL) | ATLHIGH / ATLLOW | **Hartsfield-Jackson Atlanta (KATL)** | ASOS / NWS climate | USW00013874 | EST/EDT | ✅ Verified (wethr.net) |
| Houston (HOU) | HOUHIGH / HOULOW | **Houston Hobby Airport (KHOU)** | ASOS / NWS climate | USW00012918 | CST/CDT | ⚠️ Probable — HOU not IAH |
| Miami (MIA) | MIAHIGH / MIALOW | **Miami International Airport (KMIA)** | ASOS / NWS climate | USW00012839 | EST/EDT | ⚠️ Probable |
| Los Angeles (LAX / LA) | LAHIGH / LALOW | **USC Downtown or LAX** | NWS climate | USC00045114 or USW00023174 | PST/PDT | ⚠️ **Uncertain** — LAX ≠ downtown |
| Phoenix (PHX) | PHXHIGH / PHXLOW | **Phoenix Sky Harbor (KPHX)** | ASOS / NWS climate | USW00023183 | MST (no DST) | ⚠️ Probable |
| Seattle (SEA) | SEAHIGH / SEALOW | **Seattle-Tacoma Airport (KSEA)** | ASOS / NWS climate | USW00024233 | PST/PDT | ⚠️ Probable |
| Minneapolis (MSP / MIN) | MSPHIGH / MSPLOW | **Minneapolis–St. Paul Airport (KMSP)** | ASOS / NWS climate | USW00014922 | CST/CDT | ⚠️ Probable |
| Philadelphia (PHL / PHIL) | PHLHIGH / PHLLOW | **Philadelphia Intl Airport (KPHL)** | ASOS / NWS climate | USW00013739 | EST/EDT | ⚠️ Probable |
| Detroit (DET) | DETHIGH / DETLOW | **Detroit Metro Airport (KDTW)** | ASOS / NWS climate | USW00014733 | EST/EDT | ⚠️ Probable |
| San Francisco (SFO / SF) | SFOHIGH / SFOLOW | **SFO International (KSFO)** | ASOS / NWS climate | USW00023234 | PST/PDT | ⚠️ Probable |
| Las Vegas (LAS) | LASHIGH / LASLOW | **Las Vegas (KLAS / McCarran)** | ASOS / NWS climate | USW00023169 | PST/PDT | ⚠️ Probable |
| Portland (PDX) | PDXHIGH / PDXLOW | **Portland Intl Airport (KPDX)** | ASOS / NWS climate | USW00024229 | PST/PDT | ⚠️ Probable |
| Kansas City (MCI / KC) | KCIHIGH / KCILOW | **Kansas City Intl Airport (KMCI)** | ASOS / NWS climate | USW00003947 | CST/CDT | ⚠️ Probable |
| St. Louis (STL) | STLHIGH / STLLOW | **St. Louis Lambert Airport (KSTL)** | ASOS / NWS climate | USW00013994 | CST/CDT | ⚠️ Probable |
| Cleveland (CLE) | CLEHIGH / CLELOW | **Cleveland Hopkins Airport (KCLE)** | ASOS / NWS climate | USW00014820 | EST/EDT | ⚠️ Probable |
| Oklahoma City (OKC) | OKCHIGH / OKCLOW | **Will Rogers Airport (KOKC)** | ASOS / NWS climate | USW00013967 | CST/CDT | ⚠️ Probable |
| San Antonio (SATX) | SATXHIGH / SATXLOW | **San Antonio Intl (KSAT)** | ASOS / NWS climate | USW00012921 | CST/CDT | ⚠️ Probable |
| New Orleans (NOLA / NO) | NOLAHIGH / NOLALOW | **New Orleans Intl (KMSY)** | ASOS / NWS climate | USW00012916 | CST/CDT | ⚠️ Probable |

**Legend:**  
✅ Verified — station confirmed from CFTC-filed contract terms or official Kalshi documentation  
⚠️ Probable — inferred from airport code / NWS climate station convention; must be verified against Kalshi contract PDFs before ingestion  

**Critical note on Los Angeles:** The current EdgeCast coordinates for LAX point to downtown LA (34.0522, -118.2437), not LAX airport. Kalshi's LA contract may settle on USC Downtown or on LAX. This must be verified — the two stations differ by up to 5–8°F on hot days.

**Current EdgeCast mismatch:** EdgeCast uses city-centre coordinates from Open-Meteo, not the NWS climate station coordinates. This introduces a systematic location offset for every city. The magnitude varies: negligible for most airports (within 5–10 miles of city centre), potentially significant for cities where the settlement station is a suburban airport far from the urban grid (e.g., Dallas/Fort Worth vs Love Field, Houston Hobby vs IAH).

---

## 2. Historical Observation Sources

### Source A: NOAA GHCND via Climate Data Online (CDO) API

**What it is:** The Global Historical Climatology Network — Daily database. The world's largest collection of daily climate summaries from land surface stations. Managed by NOAA NCEI.

| Attribute | Detail |
|---|---|
| Data | Daily TMAX, TMIN, PRCP, SNOW, SNWD, plus ~40 other elements |
| Geographic coverage | 100,000+ stations globally; all 24 Kalshi cities have GHCND stations |
| Historical depth | Many stations go back 50–100+ years; all US airports since ~1940s |
| Update delay | 1–2 days |
| API | REST API at `https://www.ncdc.noaa.gov/cdo-web/api/v2/` |
| Authentication | Free token via email registration at ncdc.noaa.gov/cdo-web/token |
| Rate limits | 1,000 requests/day; 1,000 records/request on free tier |
| Cost | Free |
| Format | JSON or CSV |
| Reliability | Very high — official government archive |
| Settlement match | **High** — NWS Daily Climate Reports are derived from these same ASOS observations |
| Licensing | Free for non-commercial research; some international data has WMO restrictions |

**Verdict:** Best source for historical observations. Directly maps to Kalshi settlement stations once the correct GHCND station ID is confirmed for each city.

**Limitation:** The 1,000 requests/day limit means initial bulk loading of 10+ years of data for 24 cities must be rate-managed. At 24 cities × 365 days/year × 10 years = 87,600 station-days, and 1,000 records per request, that is ~88 requests to load the full US dataset — comfortably within limits.

---

### Source B: NOAA ISD (Integrated Surface Database)

**What it is:** Hourly and sub-hourly surface observations from 35,000+ stations worldwide, including all major ASOS airport stations.

| Attribute | Detail |
|---|---|
| Data | Hourly temperature, dew point, wind, pressure, precipitation |
| Historical depth | 1901–present; ASOS stations from ~1970s |
| Update delay | ~2 hours |
| Access | Bulk download from NCEI S3 bucket or API |
| Authentication | None for bulk download |
| Cost | Free |
| Format | Fixed-width text (ISD-Lite) or full ISD format |
| Reliability | Very high |
| Settlement match | Good — same ASOS stations Kalshi uses, but hourly not daily |
| Rate limits | None for S3 bulk downloads |

**Verdict:** Good complement to GHCND for hourly contract verification. More complex to parse than GHCND (fixed-width format). Not necessary for daily high/low contracts; valuable for hourly contracts.

---

### Source C: Synoptic Data (MesoWest) API

**What it is:** Commercial API over ASOS and other surface observation networks. Provides clean, JSON-formatted access to the same underlying ASOS data.

| Attribute | Detail |
|---|---|
| Data | Hourly ASOS obs including temperature, wind, precip |
| Historical depth | ~2007–present |
| Free tier | 1,000 requests/day, 1 station per request |
| Cost | Free tier sufficient for validation; paid plans for bulk |
| Format | JSON |
| Reliability | High |

**Verdict:** Useful for real-time settlement verification; GHCND is better for historical bulk loading.

---

### Source D: Open-Meteo Historical API (`/archive`)

**What it is:** Provides ERA5 reanalysis data — a climate model re-run of the atmosphere over historical periods using all available observations. This is **NOT** actual station observations and **NOT** true archived forecasts.

| Attribute | Detail |
|---|---|
| Data | Grid-point values interpolated from ERA5 reanalysis |
| Resolution | 0.25° grid (~25 km); ERA5-Land at 0.1° (~9 km) |
| This is NOT | Actual ASOS station readings or true operational forecasts |
| Look-ahead risk | **PRESENT** — ERA5 uses future observations to constrain the model. It is a retrospective analysis, not what any model predicted at the time. |
| Free | Yes |

**Verdict:** **Do not use as observations or as archived forecasts.** ERA5 values at a city's grid point will often differ from the NWS Daily Climate Report for that city's official station. The ERA5 daily max at NYC grid point ≠ Central Park TMAX. The discrepancy can be 2–5°F on individual days. Using ERA5 to train or verify a model that will predict on official Kalshi settlement values introduces a systematic measurement mismatch.

**Current EdgeCast usage of Open-Meteo:** The current `forecast_verifier.py` fetches `/archive` data and labels it "Open-Meteo ERA5 reanalysis." This is the correct honest labelling — but it means the verification data currently stored in `ForecastVerification` is ERA5 reanalysis, not the official NWS station readings that determine Kalshi settlement. The error statistics built from this data will be measuring "how well Open-Meteo forecasts match ERA5 reanalysis" — not "how well Open-Meteo forecasts match what Kalshi actually settles on."

---

## 3. Archived Forecast Sources

This is the hardest part. A "true archived forecast" means the value the model issued before the event — not the observation, not the reanalysis.

### Source F: NCAR RDA ds084.1 — NCEP GFS 0.25° Historical Archive

**What it is:** The NCEP Global Forecast System operational analysis and forecast grids, archived in GRIB2 format. Contains the actual model output at each forecast cycle going back to 2015.

| Attribute | Detail |
|---|---|
| Data | GFS 0.25° global grids; all forecast hours (0–384h) |
| Available from | ~January 2015 |
| Point extraction | Must bilinearly interpolate from the nearest 4 grid points |
| Format | GRIB2 — requires wgrib2, cfgrib, or Herbie Python library |
| Size | ~400 GB/month for full global archive; 6-hourly cycles |
| Cost | Free (NCAR RDA account required) |
| Rate limits | Download throttling; bulk requests via NCAR Globus or HTTPS |
| Complexity | **High** — GRIB2 processing, grid-to-point extraction, handling initialization cycles |
| True point-in-time forecasts | **Yes** — this is the actual model run issued at a specific time |
| Lead time coverage | 0h–384h at 6-hourly initialization, 1-hour increments |

**Verdict:** Scientifically valid but operationally complex. Extracting temperature forecasts for 24 specific locations across multiple lead times from GRIB2 global grids requires non-trivial engineering. Storage for 10 years of 24-city, 0–14-day temperature forecasts would be manageable after extraction (~50–100 MB), but the extraction pipeline itself is substantial.

---

### Source G: NOMADS Real-time GFS (NOAA)

NOMADS retains only the last ~10 days of GFS output. Not useful for historical backtesting. Useful for the ongoing forward-collection system that EdgeCast already runs.

---

### Source H: EdgeCast's own PredictionSnapshot table (forward collection)

**What it is:** Every time EdgeCast analyzes a market, it stores the Open-Meteo forecast in `PredictionSnapshot`. This includes `forecast_high`, `forecast_low`, the collection timestamp, and the target date. This is a true point-in-time forecast (captured before settlement).

| Attribute | Detail |
|---|---|
| Data | Open-Meteo forecast at collection time |
| Coverage | All EdgeCast-analyzed markets, from the first collection run |
| Forecast model | Open-Meteo (ECMWF IFS, GFS ensemble) |
| True point-in-time | **Yes** — captured before settlement |
| Lead time | Known from (collection_timestamp, target_date) |
| Observation to pair with | Needs the NWS Daily Climate Report value (GHCND) |
| Years available | Only from EdgeCast launch forward |
| Station match | Partial — Open-Meteo uses city-centre coordinates, not the settlement station |

**Verdict:** The only immediately available source of true archived forecasts for EdgeCast markets. However: (a) history is short (weeks/months), and (b) the forecast location is city-centre, not the settlement station. The location offset introduces a systematic bias that varies by city.

---

### Source I: Open-Meteo Archive as a "forecast proxy" (already in use)

As noted above: ERA5 reanalysis labelled as a forecast is **scientifically inappropriate** for backtesting. It can be used for climatology (long-run average bias characterisation) but not as a substitute for what the model actually predicted.

---

## 4. Source Comparison Table

| Source | True observations | True archived forecasts | Cost | Complexity | Station match | Look-ahead risk | Use case |
|---|---|---|---|---|---|---|---|
| NOAA GHCND / CDO | ✅ Yes | ❌ No | Free | Low | ✅ High (GHCND = settlement) | None | Observations ✅ |
| NOAA ISD | ✅ Yes (hourly) | ❌ No | Free | Medium | ✅ High | None | Hourly contracts ✅ |
| Synoptic API | ✅ Yes (hourly) | ❌ No | Free tier | Low | ✅ High | None | Real-time settlement check ✅ |
| Open-Meteo /archive (ERA5) | ❌ Reanalysis | ❌ No | Free | Low | ⚠️ Grid ≠ station | **Present** | Climatology only ⚠️ |
| NCAR RDA ds084.1 (GFS) | ❌ No | ✅ Yes | Free | **Very High** | ⚠️ Grid interpolation | None | True backtesting ✅ (hard) |
| EdgeCast PredictionSnapshot | ❌ No | ✅ Yes (partial) | Free (already) | None (exists) | ⚠️ City-centre offset | None | Forward pilot ✅ |
| Climatology (NORMALS) | ✅ Averages | ❌ No | Free | Low | ✅ High | None | Climatology research ✅ |

---

## 5. Approach Ranking

### Approach A — True archived forecasts (NCAR RDA) + official observations (GHCND)

**Scientific validity:** ★★★★★  
The only rigorous approach for true backtesting. Uses what the model actually said before the event (GFS 0.25° from NCAR RDA) paired with official NWS station readings (GHCND).

**Appropriate for:** True backtesting, strategy validation, model calibration over historical periods.  
**Not appropriate for:** Quick results. Complexity is high; ingestion pipeline is 2–4 weeks of engineering.  
**Risk:** Grid interpolation from 0.25° GFS to the exact settlement station introduces a small but non-zero location error. GFS covers 24–48h lead times well; beyond 7 days the model is climatology anyway.

---

### Approach B — EdgeCast PredictionSnapshot (forward) + GHCND observations

**Scientific validity:** ★★★★☆  
The correct approach for forward validation. Already partially implemented: PredictionSnapshot has true point-in-time forecasts. Pairing with GHCND gives the official settlement observation.

**Appropriate for:** Forward paper testing, v2 calibration from market launch forward.  
**Not appropriate for:** Backtesting (no historical depth), Kalshi settlement verification (location offset).  
**Current gap:** `forecast_verifier.py` currently pairs PredictionSnapshot forecasts with ERA5 reanalysis, not GHCND station readings. This should be corrected for v2 to learn the right thing.

---

### Approach C — Historical observations (GHCND) + climatology only

**Scientific validity:** ★★★☆☆  
Useful for identifying long-run bias and seasonal patterns. No forecast-error measurement possible without a forecast source.

**Appropriate for:** Climatology research, understanding historical temperature distributions by city/month/season.  
**Not appropriate for:** Measuring forecast model accuracy.

---

### Approach D — Open-Meteo ERA5 reanalysis as observation proxy

**Scientific validity:** ★★☆☆☆  
Acceptable as a rough stand-in when GHCND data is unavailable, but **must be clearly labelled as reanalysis**. Systematic 1–4°F discrepancies from official NWS readings are common. Fine for directional research, not for calibration that will affect live trading decisions.

**Appropriate for:** Approximate research, directional signal, rough bias estimates.  
**Not appropriate for:** Calibration of a model that predicts official Kalshi settlement values.

---

## 6. Minimum Viable Historical-Data Plan

### Recommended approach: Approach B corrected + limited Approach C

**Phase 1 (forward, low complexity):** Correct the existing verification pipeline to use GHCND instead of ERA5 for observations, starting with 3 pilot cities. This immediately produces scientifically valid data for v2 calibration.

**Phase 2 (historical observation, medium complexity):** Backfill GHCND daily TMAX/TMIN for the pilot cities going back 5 years. This enables climatology research and long-run bias analysis.

**Phase 3 (optional, high complexity):** If true backtesting is required, extract historical GFS forecasts from NCAR RDA for the pilot cities. This unlocks Approach A.

---

### Pilot city selection (3 cities to start)

Prioritise cities where: (1) the settlement station is definitively verified, (2) GHCND data is clean and dense, (3) EdgeCast already has the most settled trades.

| Priority | City | Settlement station | GHCND ID | Reason |
|---|---|---|---|---|
| 1 | **New York City** | Central Park (USW00094728) | USW00094728 | Verified from contract terms; most liquid Kalshi market; longest NWS record |
| 2 | **Chicago** | Chicago Midway (USW00014819) | USW00014819 | Verified from contract terms; clear airport station |
| 3 | **Denver** | Denver Intl Airport (USW00003017) | USW00003017 | Verified from wethr.net; clean KDEN record |

Cities explicitly **deferred** until station verified: Los Angeles (station ambiguity), Houston (Hobby vs IAH ambiguity).

---

### Data volume estimate (Phase 1 + 2)

| Item | Detail |
|---|---|
| Scope | 3 cities × 5 years of GHCND daily TMAX/TMIN |
| Record count | 3 × 365 × 5 = 5,475 station-days |
| NOAA CDO requests | ~6 requests (1,000 records/request) |
| Storage | < 1 MB |
| Ongoing | 3 requests/day to stay current |
| API cost | Free |
| Engineering time | 1–2 days |

### Observation ingestion frequency

Daily, run after NWS Daily Climate Reports are typically published (usually by 9 AM local time the following morning). Safe to run 36 hours after the target date.

---

### Fallback behaviour

If GHCND is unavailable for a city, fall back to ERA5 reanalysis from Open-Meteo with a `record_type = 'reanalysis'` flag. The engine should decline to update σ/bias statistics from reanalysis records until the station mapping is confirmed.

---

## 7. Data Quality Requirements & Proposed Schema

### Required fields for every historical record

```sql
CREATE TABLE HistoricalWeatherRecord (
    id                    BIGSERIAL PRIMARY KEY,
    -- Identity
    source_name           VARCHAR(50) NOT NULL,       -- 'GHCND', 'ISD', 'ERA5', 'GFS_NCAR', 'EDGECAST_SNAPSHOT'
    source_record_id      VARCHAR(200),               -- NOAA station+date key, GRIB message ID, etc.
    record_type           VARCHAR(30) NOT NULL,       -- 'observation', 'archived_forecast', 'reanalysis', 'climatology'
    -- Location
    station_id            VARCHAR(50),                -- GHCND ID (e.g. USW00094728) or grid reference
    station_name          VARCHAR(200),
    city                  VARCHAR(100) NOT NULL,
    latitude              FLOAT,
    longitude             FLOAT,
    -- Timing
    valid_date            DATE NOT NULL,              -- The date the temperature is for
    valid_hour            SMALLINT,                   -- NULL for daily; 0–23 for hourly
    timezone_name         VARCHAR(50) NOT NULL,       -- 'America/New_York', etc. — IANA tz
    observation_timestamp TIMESTAMPTZ,                -- When the obs was recorded (for observations)
    ingestion_timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- For forecasts only
    issue_timestamp       TIMESTAMPTZ,                -- When the forecast was issued (NULL for observations)
    lead_time_hours       FLOAT,                      -- Hours from issue to valid_date midnight
    forecast_model        VARCHAR(50),                -- 'Open-Meteo', 'GFS', 'ECMWF', etc.
    -- Temperature
    temperature_f         FLOAT,                      -- °F (Kalshi uses °F)
    temperature_type      VARCHAR(10) NOT NULL,       -- 'high', 'low', 'hourly'
    unit                  VARCHAR(5) NOT NULL DEFAULT 'F',
    -- Quality
    quality_flags         JSONB DEFAULT '[]',         -- Source QC flags
    is_estimated          BOOLEAN DEFAULT FALSE,
    -- Deduplication
    UNIQUE (source_name, source_record_id, temperature_type)
);

CREATE INDEX idx_hwr_city_date ON HistoricalWeatherRecord (city, valid_date);
CREATE INDEX idx_hwr_station_date ON HistoricalWeatherRecord (station_id, valid_date);
CREATE INDEX idx_hwr_record_type ON HistoricalWeatherRecord (record_type);
```

### Validation checks required at ingestion

| Check | Rule |
|---|---|
| Impossible temperature | Reject if temperature_f < −70°F or > 135°F for any US city |
| Missing timestamp | Reject if valid_date is NULL or ingestion_timestamp is NULL |
| Timezone | Reject if timezone_name is not a valid IANA tz identifier |
| Unit mismatch | Reject if unit ≠ 'F' (all Kalshi markets are in °F) |
| Duplicate records | Upsert on (source_name, source_record_id, temperature_type) |
| Forecast before event | Reject archived_forecast if issue_timestamp ≥ valid_date (would be look-ahead) |
| Station mismatch | Warn if station_id does not match the city's approved settlement station |
| Missing settlement mapping | Do not compute σ/bias statistics for a city where settlement station is unverified (⚠️ status) |
| Lead time negative | Reject if lead_time_hours < 0 for forecast records |

---

## 8. Risks: Look-ahead Bias and Bad Joins

### Risk 1: ERA5 as a forecast substitute — HIGH RISK

ERA5 reanalysis incorporates observations made after the target date during its data assimilation window. Using ERA5 as if it were a forecast is a form of hindsight bias. It will make the model appear better calibrated than it actually is because ERA5 "knew" more about the atmosphere than any real forecast model did at issue time.

**Mitigation:** Tag all ERA5 records as `record_type = 'reanalysis'`. Never use reanalysis records to update σ or bias statistics in the v2 engine. Use them only for rough climatology.

### Risk 2: City-centre coordinates vs settlement station — MEDIUM RISK

EdgeCast currently forecasts for city-centre coordinates (e.g., 40.7128, -74.0060 for NYC). Central Park is at approximately 40.7830, -73.9654 — 5 miles north. The Open-Meteo grid point for city-centre differs from Central Park. On most days this difference is < 1°F, but in extreme heat events it can exceed 2°F and affect whether a contract settles above or below a threshold.

**Mitigation:** Phase 1 of the data plan should update forecast coordinates to match settlement stations for pilot cities. This should be done for observations first (before changing any live forecast coordinates).

### Risk 3: Station changes over time — LOW RISK (but must be checked)

GHCND station networks change: stations relocate, are decommissioned, and replaced. A city's GHCND series for 2010–2026 should be reviewed for any station-change events. NCEI documents these as "station breaks." A station move of even 1 mile can introduce a step change in the temperature record.

**Mitigation:** When loading GHCND data, check the station's `period_of_record` and flag any gaps or station replacements.

### Risk 4: Bad joins between forecasts and observations — MEDIUM RISK

If a forecast was issued at 12:00 UTC on Day D for Day D+3, and the observation covers the calendar day in local time, the lead time calculation must account for the timezone offset. A 1-day error in lead time assignment will put a forecast in the wrong σ bucket.

**Mitigation:** All timestamps stored in UTC with a separate `timezone_name` column. Lead time computed as `(valid_date midnight in local tz) − issue_timestamp` in hours.

### Risk 5: NWS Daily Climate Report revisions — LOW RISK

NWS occasionally revises its Daily Climate Reports (e.g., to correct a sensor error). If Kalshi settles based on the first published report and the revision comes later, the revision should not be used to retroactively re-evaluate trades.

**Mitigation:** The schema stores `observation_timestamp` (when the observation was recorded) separately from `ingestion_timestamp` (when EdgeCast ingested it). Use the first-published value for trade evaluation; flag revisions.

---

## 9. Implementation Phases

### Phase 1 — Correct existing forward collection (1–2 days, low risk)

- Replace ERA5 calls in `forecast_verifier.py` with NOAA GHCND CDO API calls for the 3 pilot cities
- Store `record_type = 'observation'` and `station_id` (GHCND ID) alongside the temperature
- Add the GHCND station ID for NYC, Chicago, and Denver to the city configuration
- Existing ERA5 records are retained but flagged `record_type = 'reanalysis'`
- The v2 σ/bias engine only uses `record_type = 'observation'` records going forward
- **No changes to v1, v2 logic, or existing trades**

### Phase 2 — Historical GHCND backfill for pilot cities (2–3 days, low risk)

- One-time ingestion of 5 years of GHCND daily TMAX/TMIN for NYC, Chicago, Denver
- Populate `HistoricalWeatherRecord` table
- These historical observations cannot be paired with EdgeCast forecasts (no archived forecast exists), but they enable long-run climatology analysis:
  - Average TMAX by month/season per city
  - Historical temperature distribution (useful for sanity-checking σ values)
  - Station-level data to verify that the current σ fixed table is reasonable

### Phase 3 — Verify remaining city station mappings (1 week, research only)

- Download and read Kalshi contract PDFs for all 24 cities
- Confirm or correct the ⚠️ entries in the station mapping table
- Update the city configuration with verified GHCND station IDs

### Phase 4 — Expand to remaining 21 cities (after Phase 3, 1 day)

- Once station mappings are verified, the Phase 1/2 pipeline generalises immediately

### Phase 5 — GFS historical archive (optional, 3–6 weeks, high complexity)

- Extract historical GFS temperature forecasts from NCAR RDA ds084.1 for pilot cities
- Pair with GHCND observations
- Build a proper multi-year σ and bias table
- This is the only path to true backtesting

---

## 10. Conclusion

**For true backtesting (measuring historical forecast accuracy with zero look-ahead bias):**  
→ **Historical backtesting is not currently reliable with available data**  
NCAR RDA ds084.1 contains the necessary GFS archives but requires significant engineering to extract, and the current EdgeCast forecast source (Open-Meteo) is not in that archive. There is no easy way to obtain what Open-Meteo predicted for a given city on a given day in 2023.

**For forward paper testing starting now:**  
→ **Ready to build a limited pilot**  
EdgeCast already captures true point-in-time forecasts in `PredictionSnapshot`. Pairing these with GHCND observations for the 3 verified pilot cities (NYC, Chicago, Denver) produces scientifically valid forward verification data. The existing `forecast_verifier.py` needs one correction: replace ERA5 lookups with GHCND CDO API calls.

**Recommended next step (pending approval):**  
Phase 1 only — correct the observation source in `forecast_verifier.py` for 3 cities. This is a one-file change of low risk and low complexity that immediately makes the v2 verification data scientifically valid. All other phases follow from there.

**What this does NOT unlock yet:**  
- Historical backtesting
- A multi-year σ table built from true forecast errors
- Calibration data for cities where the settlement station is unverified

**Total estimated cost:** $0 (NOAA GHCND API is free with a free token).

---

*Report prepared: July 28, 2026. No code was modified in the preparation of this report.*
