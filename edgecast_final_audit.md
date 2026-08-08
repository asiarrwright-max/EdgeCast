# EdgeCast — Final Read-Only Audit

*Calibration Adjustment Factors + Settlement / ERA5 Matching Pipeline*  
*No code modified. No production changes. Findings only.*

---

## Part 1 — Calibration Adjustment Factors

### Complete system trace

**The `calibration_adjustments` table** (`models.py:118–146`):

| Column | Type | Purpose |
|---|---|---|
| `strategy_version` | VARCHAR(50) | Partition key for lookup |
| `bucket_lo / bucket_hi` | FLOAT | Half-open interval [lo, hi) |
| `predicted_rate` | FLOAT | Avg EC probability in bucket |
| `actual_yes_rate` | FLOAT | Actual fraction resolving YES |
| `adjustment_factor` | FLOAT | `actual_yes_rate / predicted_rate` |
| `sample_size` | INTEGER | Guard: only used when ≥ 30 |
| `computed_at` | TIMESTAMPTZ | When the row was written |

**Lookup** (`probability_engine_v2.py:275–295`):
```sql
SELECT * FROM calibration_adjustments
WHERE strategy_version = "v2.0"
  AND bucket_lo <= ec_prob AND bucket_hi > ec_prob
  AND sample_size >= 30
  AND adjustment_factor IS NOT NULL
LIMIT 1
→ return row.adjustment_factor, or 1.0 if no match
```

**Application** (`probability_engine_v22.py:157–166`):
```python
ec_prob = clamp(raw_prob × calib_adj, 0.001, 0.999)
```

Both V2.1 and V2.2 query `strategy_version = "v2.0"`. V3 has **no calibration step at all** — it returns the clamped adjusted probability directly.

---

### Critical structural finding: no write mechanism exists in the codebase

A complete search of all files under `artifacts/api-server/app/` found **zero INSERT, UPDATE, seed, or populate logic** targeting `calibration_adjustments`. The model is declared. The read is implemented. There is no code that writes the rows.

The reporting code in `paper_trading.py:997–1090` computes per-bucket statistics (avg predicted probability, actual yes rate, Brier) and returns them as a dict — but **never persists** them to `calibration_adjustments`. The V2.1 analytics report (`v21_analytics.py:365–438`) does the same.

The rows currently in the database were written by an external script or manual INSERT that no longer exists in the codebase.

---

### What cannot be determined from code alone

Because no write mechanism exists, the following questions **can only be answered by querying the live database**:

```sql
-- 1. What rows exist?
SELECT strategy_version, bucket_lo, bucket_hi,
       predicted_rate, actual_yes_rate, adjustment_factor,
       sample_size, computed_at
FROM calibration_adjustments
ORDER BY strategy_version, bucket_lo;

-- 2. What paper_trades fed them? (strategy_version + date range)
SELECT strategy_version,
       MIN(created_at) AS earliest,
       MAX(created_at) AS latest,
       COUNT(*)        AS n
FROM paper_trades
WHERE status = 'SETTLED'
  AND kalshi_result IN ('yes','no')
  AND ec_yes_probability IS NOT NULL
GROUP BY strategy_version
ORDER BY MIN(created_at);
```

Until these queries are run, the following remain unknown:
- Whether any factor rows exist at all (if none, `calib_adj = 1.0` always — calibration is effectively off)
- Whether factors were derived from V2.1 inverted-bias-era data
- Whether V2.1 RESEARCH_ONLY trades were included
- Whether the data period contains enough forward-test trades to be meaningful

---

### Calibration target variable

The report code compares `ec_yes_probability` against `kalshi_result` ("yes" / "no") — the calibration target is **Kalshi authoritative settlement outcomes**. It is not ERA5 observations. This is the correct target variable.

---

### Statistical appropriateness assessment

**The multiplicative formula `calibrated = raw × factor` is mathematically fragile at extremes:**

- At raw = 0.90, factor = 0.71 → 0.639 — reasonable.
- At raw = 0.50, the same factor extrapolated outside its valid range produces unreliable results.

The more fundamental problem: the formula assumes `actual_yes_rate` is proportional to `predicted_rate`. The raw probabilities are systematically 5–6pp too low for threshold contracts and ~11pp for range contracts (the rounding bug). A calibration factor fit on these undercorrected raw probabilities will produce:

```
factor ≈ actual_rate / (true_rate − 5.7pp)
```

After the rounding fix shifts raw probabilities up by ~5.7pp, applying existing factors will over-correct.

---

### Contribution to the 91% → 60% win-rate gap

The 91% average predicted probability on the 15 forward-test trades is the POST-calibration value stored in `ec_side_probability`. This means either:

- **(a) Calibration is inactive** (`calib_adj = 1.0` everywhere — no rows or sample_size < 30 for all matching buckets), and the raw Gaussian outputs ~91%.
- **(b) Calibration is active** and even after adjustment the model still outputs ~91%, implying raw Gaussians > 100% before clamping — implausible at σ = 3.5°F.

Scenario (a) is far more likely. If calibration is off, the 91% → 60% gap is explained by:
1. Sigma floor too tight relative to real forecast variance (3.5°F floor masks true uncertainty)
2. Rounding underestimate (~5.7pp) causes the system to enter NO trades at inflated confidence
3. Large NWP forecast misses that any reasonable sigma would not prevent

---

### Calibration decisions before Forward Test B

1. Run the DB queries above before making any decision.
2. If rows exist and were derived from V2.1 inverted-bias data: **discard them**.
3. If rows exist and were derived from clean V2.2 data: **discard them anyway** — the rounding fix shifts raw probabilities ~5.7pp, making all existing factors miscalibrated for the corrected engine.
4. After rounding fix: introduce `strategy_version = "v2.3"`. No `v2.3` rows exist → `calib_adj = 1.0` automatically → calibration bypassed by design until clean data accumulates.
5. Build a proper write mechanism — the refit belongs in the codebase as an endpoint or admin script, not an external one-off. This gap must be closed before any future calibration row is seeded.

---

## Part 2 — Settlement / ERA5 Matching Pipeline

### How Kalshi settlement is obtained and stored

**Pipeline** (`settlement.py`):

1. Scheduler triggers `run_settlement_job()` every 3h, offset 90 min from collection.
2. Fetches all OPEN and PENDING_SETTLEMENT `PaperTrade` rows.
3. For each: `GET {kalshi_base_url}/markets/{trade.market_ticker}` — exact ticker match, no transformation.
4. `_extract_result(market_data)` reads `market_data["result"]` → `"yes"` / `"no"` / `"void"` / `"pending"` / `None`.
5. On confirmed yes/no: writes `trade.kalshi_result`, `outcome`, payout, P/L, `settlement_timestamp = now(UTC)`, status = "SETTLED".

**Ticker matching is exact and reliable.** No regex, no normalization. V3 trades use `v3_settlement.py` with identical logic. No date ambiguity in settlement — the result field is categorical.

---

### How ERA5 verification is obtained and stored

**Pipeline** (`forecast_verifier.py`, called every 24h):

1. Fetches all `PaperTrade` rows with `kalshi_result IN ('yes', 'no')` — V2 trades only.
2. Groups by unique `(city, weather_variable, date_str)` where **`date_str = trade.target_settlement_date[:10]`**.
3. Looks up coordinates from `WeatherLocation` table (primary) → `SETTLEMENT_STATIONS` (fallback) → `CITY_COORDS` (final fallback).
4. Calls `_fetch_observation(city, lat, lon, date_str, noaa_token)`:
   - With NOAA token: fetches GHCND CDO for `station.ghcnd_station_id` on `date_str`.
   - Without / GHCND failure: ERA5 archive API with those coordinates, `timezone=auto`, Fahrenheit.
5. Stores actual value in `ForecastVerification`; computes `forecast_error = actual − forecast_value`.

---

### Identified pipeline issues

#### Issue A — Date extraction: potential UTC-to-local one-day mismatch *(CRITICAL — suspected)*

`date_str = trade.target_settlement_date[:10]`

`target_settlement_date` is sourced from `parse_market()` which converts Kalshi's `expected_expiration_time` or `close_time` (both UTC timestamps) to a UTC datetime. The stored string may be `"2026-08-07T05:00:00+00:00"` for a Dallas market that expires at midnight CDT August 6 (= 05:00 UTC August 7).

`[:10]` extracts `"2026-08-07"` — **one calendar day late for CDT/MDT/PDT markets** whose local midnight is 5–7 hours behind UTC.

ERA5 with `timezone=auto` at Dallas coordinates then returns the high/low for August 7 in local time — a full day after the actual settlement period. GHCND fetches August 7 station data for the same reason.

**This corrupts both the ERA5 diagnostic value and the σ/bias error statistics fed back into the probability engine** for affected trades.

**Required investigation (DB query):**
```sql
SELECT market_ticker, city, target_settlement_date,
       LEFT(target_settlement_date, 10) AS extracted_date,
       settlement_timezone
FROM paper_trades
WHERE eligibility_status = 'OFFICIAL'
  AND created_at >= '2026-08-04T22:21:44Z'
ORDER BY created_at;
```

If any `extracted_date` differs from the expected local settlement date, this must be fixed before Forward Test B.

---

#### Issue B — ERA5 coordinates may differ from forecast coordinates *(suspected)*

The forecast uses `SERIES_TO_CITY` coordinates (station-aligned for known series). ERA5 verification uses the `WeatherLocation` table as primary source — populated during market collection, not from `SERIES_TO_CITY`. If `WeatherLocation` was seeded with city-centre coordinates before station-coordinate alignment, ERA5 fetches from a different grid point than the one used for the original forecast.

**Required verification (DB query):**
```sql
SELECT city, latitude, longitude
FROM weather_locations
WHERE city IN ('Dallas','New York City','Houston','Denver',
               'Los Angeles','Oklahoma City','Minneapolis')
ORDER BY city;
```
Compare against `SERIES_TO_CITY` values. Any mismatch means ERA5 and forecast use different coordinates.

---

#### Issue C — ERA5 continuous values compared to integer thresholds without rounding *(confirmed)*

`_fetch_era5_temps` returns raw floats (e.g., 75.3°F). The diagnostic endpoint compares this directly to the integer contract threshold without rounding. A trade with:

- Contract: T ≤ 75°F
- ERA5 actual: 75.3°F → ERA5 predicted result: **NO**
- NWS instrument: 75°F (rounds down) → Kalshi result: **YES**
- → **ERA5_KALSHI_DISAGREE** is flagged

This is an inherent grid-cell vs. instrument difference, but the diagnostic amplifies it by not applying the same rounding that NWS applies before publishing. The diagnostic endpoint should round ERA5 actuals to the nearest integer before computing `era5PredictedResult` for integer-boundary threshold contracts. Currently, ERA5_KALSHI_DISAGREE fires for any case where ERA5 falls within ±0.5°F of an integer threshold — which is measurement noise, not a genuine disagreement.

---

#### Issue D — Error statistics mix GHCND, ERA5, and legacy observations *(confirmed)*

`recompute_error_stats()` (`forecast_verifier.py:391–486`) pools ALL `ForecastVerification` rows regardless of `source_label` — `ghcnd_observation`, `ghcnd_observation_unverified`, `era5_reanalysis`, and `open_meteo_historical` are all combined into σ and bias estimates used by the probability engine.

ERA5 grid-cell errors are typically larger (1–4°F) than GHCND station errors. If most historical observations used ERA5 (NOAA_CDO_TOKEN not always configured), σ estimates are inflated relative to what station-error data alone would show. This is partially offsetting to the overconfidence problem but is not principled. The correct approach separates σ/bias computation by source label.

---

### Investigation of ERA5_KALSHI_DISAGREE records

The flag is generated at query time in the diagnostics endpoint (not stored permanently), at `paper_trades.py:1443–1447`.

| Trade ticker | ERA5 actual | Threshold | ERA5 pred | Kalshi result | Most likely explanation |
|---|---|---|---|---|---|
| KXLOWTNYC-26AUG06-T75 | ~75.3°F | T ≤ 75°F | NO | YES | **Issue C** — ERA5 continuous 75.3 > 75.0; NWS instrument rounded to 75°F. Measurement boundary difference within ±0.5°F. |
| KXHIGHTDAL-26AUG06-B100.5 | ~96°F | T ≤ 100.5°F | YES | NO | ERA5 predicts YES (below 100.5); Kalshi says NO (above 100.5). Severe disagreement — **Issue A** (UTC date mismatch fetching wrong day) is the primary suspect. Cannot confirm without DB date query. |
| Remaining flags (up to 3 total) | Unknown | — | — | — | Most likely **Issue C** (rounding boundary); possibly **Issue A** for CDT/MDT markets. Cannot determine without raw ERA5 actuals. |

---

## Final Table — Issues Materially Affecting Forward Test B Validity

| Issue | Confirmed / Suspected | Severity | Must fix before Forward Test B? | Exact recommended action |
|---|---|---|---|---|
| NWS integer rounding missing from threshold probability formula (~5.7pp underestimate of YES) | **Confirmed** | CRITICAL | **YES** | Apply ±0.5°F correction to integer threshold boundaries (`z = (T ± 0.5 − μ) / σ`). No change for half-integer thresholds. Fix in V2.1, V2.2, V3 probability engines. |
| NWS rounding missing from range probability formula (~11pp underestimate — roughly 2× error) | **Confirmed** | CRITICAL | **YES** | Use `lower − 0.5` / `upper + 0.5` as integration bounds in `_calc_prob_range` and V3 equivalent. |
| ERA5 date extraction: UTC close time `[:10]` may give wrong local date for CDT/MDT/PDT markets | **Suspected** | CRITICAL | **YES** (investigate first) | Run DB query on `target_settlement_date` for forward-test OFFICIAL trades. If any UTC date ≠ local settlement date, fix extraction to convert UTC timestamp to station's local date before slicing. |
| Hourly contracts using daily sigma floor (3.5°F instead of 2.0°F) | **Confirmed** | HIGH | **YES** | Pass `hourly=True` to `_sigma_v2` when `contract_type == "hourly_threshold"` in V2.1 and V2.2. |
| V2.2 and V3 paper trading do not check `.verified` on station (unverified cities pass eligibility) | **Confirmed** | HIGH | **YES** | Fix `get_verified_station` body (returns `s` in both branches); add `.verified` check in `paper_trading_v22.py:193` and `v3_paper_trading.py:103`. |
| Philadelphia and San Antonio forecast coordinates differ from settlement station (6.86 mi and 7.71 mi) | **Confirmed** | HIGH | **YES** | Update `SERIES_TO_CITY["KXPHILHIGH"]` to (39.8721, −75.2411); `["KXHIGHTTSATX"]` to (29.5337, −98.4698). Both remain `verified=False`; station guard blocks them until verified. |
| Calibration factors derived from incorrectly-rounded raw probabilities and possibly V2.1 inverted-bias data | **Confirmed structural** / source unknown | HIGH | **YES** | Run DB audit queries. Discard all existing `calibration_adjustments` rows. Bump strategy version to `"v2.3"` — no v2.3 rows → `calib_adj = 1.0` by design. |
| No calibration write mechanism exists in codebase | **Confirmed** | HIGH | **YES** (before refit needed) | Build a proper calibration refit endpoint or admin script before any future rows are inserted. |
| ERA5 coordinates (`WeatherLocation` table) may differ from forecast coordinates (`SERIES_TO_CITY`) | **Suspected** | MODERATE | **YES** (verify first) | Query `WeatherLocation` lat/lon for active cities; compare against `SERIES_TO_CITY`. If mismatched, update rows or change coordinate resolution order in `forecast_verifier.py`. |
| ERA5 continuous values compared to integer thresholds without rounding (inflates ERA5_KALSHI_DISAGREE count) | **Confirmed** | MODERATE | **NO** (diagnostic integrity, not trading correctness) | Round ERA5 actual to nearest integer before computing `era5PredictedResult` in diagnostics endpoint for integer-boundary contracts. |
| Error statistics mix GHCND, ERA5, and legacy observations without source filtering | **Confirmed** | MODERATE | **NO** (affects σ quality over time) | Add source-label column to `ForecastErrorStats`; compute separate σ/bias from GHCND-only observations once sample size allows. |
| LAX `SERIES_TO_CITY` comment incorrect ("USC Downtown unverified") — coordinates are correct (KLAX) | **Confirmed** | LOW | **NO** (cosmetic) | Fix comment in `kalshi.py:96`. |

---

## Final Questions

### 1. Have we audited the full forecast → probability → trade → settlement chain?

**Yes, with two remaining data-layer blind spots.**

| Chain segment | Audited | Source |
|---|---|---|
| Forecast source and model labeling | ✓ | Prior mechanics audit §1 |
| Model run age / GFS cycle timing | ✓ | Prior mechanics audit §2 |
| Station alignment — all cities, distances computed | ✓ | Prior mechanics audit §3 |
| NWS rounding / probability formula | ✓ | Prior mechanics audit §4 |
| Forecast-to-contract translation | ✓ | Prior mechanics audit §5 |
| Loss autopsy — all 6 forward-test losses | ✓ | Prior mechanics audit §6 |
| Known bugs and eligibility gaps | ✓ | Prior mechanics audit §7 |
| Calibration system architecture | ✓ | This audit §1 |
| Kalshi settlement fetch and storage | ✓ | This audit §2 |
| ERA5 verification fetch and storage | ✓ | This audit §2 |
| Settlement parser (title/subtitle → contract type) | ✓ | `settlement_parser.py` reviewed |

**Remaining blind spots (data, not code):**
- Actual content of `calibration_adjustments` table → requires DB query
- `WeatherLocation` table coordinates → requires DB query
- Exact `target_settlement_date` format confirming UTC/local date alignment → requires DB query

### 2. Is there any known major correctness issue still unexamined?

One: the **ERA5 date extraction UTC/local mismatch** (Issue A) is suspected but unconfirmed. It is the highest-priority investigation item. If confirmed, it means some ERA5 verification values and the error statistics computed from them reflect the wrong day's temperatures — corrupting σ/bias estimates for affected city/date combinations.

### 3. Smallest possible set of changes required before starting Forward Test B

| Order | Change | Rationale |
|---|---|---|
| 0 | **Run three DB audit queries** (calibration rows, WeatherLocation coords, target_settlement_date formats) | Must know before fixing calibration and date extraction |
| 1 | **NWS rounding correction in all three probability engines** — ±0.5°F for integer thresholds; −0.5/+0.5 for range bounds | Largest single source of systematic probability error; required for all subsequent calibration to be valid |
| 2 | **Hourly sigma floor fix** — `hourly=True` in V2.1 and V2.2 | Independent one-line fix |
| 3 | **Fix `get_verified_station` + add `.verified` checks in V2.2 and V3 paper trading** | Closes station eligibility gap |
| 4 | **Fix Philadelphia and San Antonio coordinates** in `SERIES_TO_CITY` | Coordinate accuracy before those cities trade |
| 5 | **Discard `calibration_adjustments` rows; bump strategy version to `"v2.3"`** | Prevents stale factors from contaminating Forward Test B |
| 6 | **Fix ERA5 date extraction** if DB query confirms UTC/local mismatch | Critical if any trade's verification data is from the wrong day |
| 7 | **Update `FORWARD_TEST_START`** after deployment | Clean boundary in all diagnostics |

**Not required for Forward Test B:** ERA5 rounding in diagnostics endpoint, error-stat source separation, calibration write mechanism, `generationtime_ms` capture, GFS cycle awareness, LAX comment fix.

---

*End of final read-only audit. Generated August 8, 2026.*
*No code, data, model logic, settings, calibration, or production was modified.*
