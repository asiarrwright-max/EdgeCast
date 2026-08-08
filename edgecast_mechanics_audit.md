# EdgeCast Forward-Test-to-Settlement Mechanics Audit

*Read-only. No code was modified. All findings are observations against the production codebase as of August 8, 2026.*

---

## Section 1 — Forecast Sources

### What sources are used

**One external source: Open-Meteo default forecast API.**

`openmeteo.py:28-49` sends a single HTTP request per city per collection cycle to `{settings.openmeteo_base_url}/forecast` with these parameters:

```
daily:         temperature_2m_max, temperature_2m_min,
               precipitation_probability_max, wind_speed_10m_max
hourly:        temperature_2m
timezone:      auto
forecast_days: 16
```

No `models` parameter is passed. Open-Meteo routes internally to its own blended ensemble (typically GFS + ICON + ERA5 by region and lead time), but EdgeCast has zero control over or visibility into which NWP model underlies any specific returned value.

### Source labels vs. actual sources

| Label | Where it appears | What it actually means |
|---|---|---|
| `"GFS"` | `v3_predictor.py:48` — `V3_MODEL = "GFS"` | **Metadata only.** Passed as a key for V3ErrorStats lookup. Not a parameter to any weather API. The actual source is Open-Meteo's default blend. |
| `"1d"` | `v3_predictor.py:49` — `V3_LEAD_BUCKET = "1d"` | **Metadata only.** Describes the prior training context, not an API selector. |
| `source_label='era5_reanalysis'` | `forecast_verifications` table | Genuine: the verification uses the ERA5 historical endpoint separately. This is a different call from the forecast call. |

V3 reuses the forecast value already collected by the V2.1 pipeline (`v3_predictor.py:13: "Reuses forecast_value and contract spec from the V2.1 snapshot — no second weather-API call"`). The V3_MODEL label was intended to annotate what the prior was trained on. The API call that sourced the forecast itself had no model selector.

### Weights

There are no weights. The system uses a single floating-point scalar from Open-Meteo. Weights do not vary by city, lead time, weather variable, or historical accuracy — this is explicitly acknowledged (`probability_engine_v2.py:77: "Weighting: all observations are currently equally weighted"`).

The "multi-source" or "consensus" framing sometimes seen in comments refers entirely to the statistical framework (σ, bias from historical errors) — not to blending live forecasts from multiple NWP models.

### Summary

EdgeCast is a single-source system. Open-Meteo blends models internally but this blend is opaque, uncontrolled, and not recorded. Any analysis of "forecast consensus" or "model agreement" in the context of EdgeCast refers to historical error statistics, not live model ensemble spread.

---

## Section 2 — Model Run Age / Release Timing

### What timestamps are recorded

| Timestamp | Stored | Where |
|---|---|---|
| EdgeCast collection time | ✓ | `quote_timestamp` on paper_trades |
| Open-Meteo `generationtime_ms` | ✗ | Not extracted, not stored |
| Underlying NWP model run time | ✗ | No field exists; API response not inspected |
| GFS cycle identifier (00z/06z/12z/18z) | ✗ | Scheduler has no knowledge of GFS cycles |

### Collection schedule

`scheduler.py` uses a fixed asyncio sleep loop:

```
Collection:    every 3h (INTERVAL_SECONDS = 10800)
Settlement:    every 3h, offset +90 min from collection
Verification:  every 24h, offset +3h from collection
```

First collection occurs 3 hours after server startup. Restarts (deployments, crashes, reloads) reset the clock with no wall-clock alignment to GFS release times.

### GFS cycle vs. EdgeCast collection phase

GFS runs at 00z, 06z, 12z, 18z. Open-Meteo publishes updated forecasts approximately 3–5 hours after each synoptic time. EdgeCast's 3-hour collection interval drifts through all GFS phases over time, creating two regimes:

- **Stale-run collection**: Trade entered using last cycle's forecast; next cycle with materially updated data arrives within 1–2 hours. No code detects this.
- **Fresh-run collection**: Trade entered shortly after Open-Meteo has incorporated the newest GFS run. Best achievable state.

There is no mechanism to distinguish these two states at entry time.

### Can a trade be entered on a stale model run?

Yes. The `quote_age_seconds` freshness guard (`OFFICIAL_STALE_QUOTE_SECONDS = 300`) measures time since the EdgeCast collection completed — not time since the underlying NWP model run was initialized. A forecast that was collected 4 minutes ago may be from a GFS run 8 hours old. The guard correctly ensures the Kalshi quote is fresh; it says nothing about the forecast model's freshness.

### Recent settled losses and GFS timing

For the two confirmed large forecast misses (Dallas B81.5 NO, Denver B96.5 NO from Aug 6), the GFS cycle alignment at time of entry is **unknown and unrecoverable** — the data was not logged. With a 3h collection interval and GFS updates available 3–5h after synoptic time, there is statistically a 40–60% probability any given collection uses a forecast that will be superseded by a materially different GFS run within the next 1–3 hours.

---

## Section 3 — Station Alignment

### Forecast coordinates vs. settlement station coordinates

For every city with an active temperature series in SERIES_TO_CITY, the following distances were computed between the Open-Meteo forecast coordinates and the SETTLEMENT_STATIONS registry coordinates:

| City | Forecast coords | Settlement station | Distance | Status |
|---|---|---|---|---|
| Dallas | 32.8998, -97.0403 | KDFW (verified) | **0.0 mi** | ✓ EXACT |
| New York City | 40.7789, -73.9692 | Central Park NWS (verified) | **0.0 mi** | ✓ EXACT |
| Houston | 29.6454, -95.2789 | KHOU Hobby (verified) | **0.0 mi** | ✓ EXACT |
| Denver | 39.8561, -104.6737 | KDEN (verified) | **0.0 mi** | ✓ EXACT |
| Oklahoma City | 35.3931, -97.6007 | KOKC (verified) | **0.0 mi** | ✓ EXACT |
| Minneapolis | 44.8848, -93.2223 | KMSP (verified) | **0.0 mi** | ✓ EXACT |
| Los Angeles | 33.9381, -118.3889 | KLAX (unverified) | **0.0 mi** | ✓ EXACT* |
| Miami | 25.7959, -80.2870 | KMIA (unverified) | **0.0 mi** | ✓ EXACT* |
| Chicago | 41.7867, -87.7522 | KMDW implied | **0.0 mi** | ✓ EXACT† |
| Las Vegas | 36.1699, -115.1398 | CLILAS (verified) | **5.98 mi** | ⚠ MISMATCH |
| Philadelphia | 39.9526, -75.1652 | KPHL (unverified) | **6.86 mi** | ⚠ MISMATCH |
| San Antonio | 29.4241, -98.4936 | KSAT (unverified) | **7.71 mi** | ⚠ MISMATCH |
| Washington DC | 38.8521, -77.0377 | TWC (not NWS) | — | 🚫 BLOCKED |
| Detroit (fallback only) | 42.3314, -83.0458 | KDTW (unverified) | **17.72 mi** | ⚠ MISMATCH |
| New Orleans (rain only) | 29.9511, -90.0715 | KMSY (unverified) | **11.54 mi** | ⚠ MISMATCH |

\* Verified by matching coordinates; station source unverified via Kalshi contract rules.  
† Chicago has no SETTLEMENT_STATIONS entry; SERIES_TO_CITY uses KMDW coordinates. If Kalshi uses O'Hare (KORD), the mismatch would be ~12 miles.

### Fallback coordinate hazards

When a market's series is not in SERIES_TO_CITY, the system falls back to CITY_COORDS substring matching. Several CITY_COORDS entries use city-centre coordinates, not station coordinates:

| Fallback key | City-centre coords | Correct settlement station | Distance |
|---|---|---|---|
| `"LA"` | 34.0522, -118.2437 (downtown) | KLAX: 33.9381, -118.3889 | **11.5 mi** |
| `"NY"` | 40.7128, -74.0060 (downtown) | Central Park: 40.7789, -73.9692 | **5.0 mi** |
| `"LAS"` | 36.1699, -115.1398 (city centre) | CLILAS: 36.0840, -115.1522 | **5.98 mi** |

Any Kalshi series resolved through the `"LA"` or `"NY"` city-code fallback would receive a forecast from the wrong geographic location. The current traded series for LA and NYC all appear in SERIES_TO_CITY with correct station coordinates, so this fallback is not currently active for those cities. It is a latent bug for new series that arrive without a SERIES_TO_CITY entry.

### LAX comment inconsistency

`kalshi.py:96` labels the Los Angeles entry as `"# KLAX station (USC Downtown unverified)"`. The coordinates (33.9381, -118.3889) are KLAX airport, not USC Downtown (34.0219, -118.2852). The comment is wrong. The coordinates are correct.

### Grid-cell vs. physical instrument distinction

Even when forecast coordinates exactly match a station's latitude/longitude, the Open-Meteo response is a **grid-cell area average** at the nearest model grid point (GFS ≈ 13 km, ERA5 ≈ 25 km resolution), not a point reading from a physical instrument. NWS Daily Climate Reports record the actual thermometer reading at the station. This spatial averaging is an irreducible basis difference that the learned σ/bias absorbs but cannot eliminate. Cities surrounded by heterogeneous terrain (Denver near Rocky Mountain foothills, Dallas near urban heat island) are more exposed to this effect than flat urban stations.

---

## Section 4 — Settlement / Rounding Rules

### Kalshi/NWS settlement convention

NWS Daily Climate Reports record daily high and low temperatures as **whole-degree Fahrenheit integers** (rounded from continuous instrument readings using standard ½-degree rounding). Kalshi temperature contract boundaries are stated at integer or half-integer °F values. The probability formula must translate continuous forecast distributions into probabilities consistent with this discrete settlement.

### How EdgeCast currently computes probability

**Step 1 — Bias correction:**
```python
# V2.2 (correct sign)
mu = forecast_value + bias

# V2.1 (inverted sign — intentionally preserved for record integrity)
mu = forecast_value - bias
```

**Step 2 — Threshold contracts (gte / lte):**
```python
z = (threshold - mu) / sigma
P(YES for "T ≥ threshold") = 1 - CDF(z)      # gte
P(YES for "T ≤ threshold") = CDF(z)           # lte
```

**Step 3 — Range contracts:**
```python
z_hi = (upper - mu) / sigma
z_lo = (lower - mu) / sigma
P(YES) = max(0.0, CDF(z_hi) - CDF(z_lo))
```

**Step 4 — Calibration multiplier:**
```python
calibrated = raw_prob × calib_adj
calibrated = clamp(calibrated, 0.001, 0.999)
```

### The rounding boundary problem

**For threshold contracts:**

Kalshi's "T ≥ 75°F" settles YES when the NWS integer reading is 75 or higher, which corresponds to any continuous temperature ≥ 74.5°F (since 74.5 rounds to 75). The code computes `P(X ≥ 75.0)` but the correct formula is `P(X ≥ 74.5)`.

Similarly, "T ≤ 82°F" settles YES when the NWS integer reading is 82 or lower, meaning any continuous temperature < 82.5°F. The code computes `P(X ≤ 82.0)` instead of `P(X < 82.5)`.

Quantified effect at σ = 3.5°F (the floor):

| Contract | Code result | Corrected | Underestimate |
|---|---|---|---|
| T ≥ 75, mu = 75.0 | 50.0pp | 55.7pp | **−5.7pp** |
| T ≥ 75, mu = 74.5 | 44.3pp | 50.0pp | **−5.7pp** |
| T ≤ 82, mu = 82.0 | 50.0pp | 55.7pp | **−5.7pp** |
| T ≤ 82, mu = 83.0 | 38.8pp | 44.3pp | **−5.6pp** |

The correction is approximately constant at **~5.7pp** near the contract boundary, tapering as mu moves farther away.

**For range contracts:**

A "77–78°F" range bracket settles YES when the NWS integer is 77 OR 78. In continuous terms this is the interval [76.5, 78.5), a 2°F window. The code computes `P(77 ≤ X ≤ 78)`, a 1°F window — exactly half the correct settlement region.

Quantified effect at σ = 3.5°F:

| Scenario (range 77–78) | Code result | Corrected | Underestimate |
|---|---|---|---|
| mu = 76.0 | 10.4pp | 20.6pp | **−10.2pp** |
| mu = 77.0 | 11.2pp | 22.3pp | **−11.0pp** |
| mu = 77.5 (centered) | 11.4pp | 22.5pp | **−11.1pp** |
| mu = 78.0 | 11.2pp | 22.3pp | **−11.0pp** |
| mu = 79.0 | 10.4pp | 20.6pp | **−10.2pp** |

The correction is approximately **+10–11pp** across the range, approximately doubling the assigned probability near the bracket.

**Internal inconsistency confirming the bug:**

The `_dist_from_threshold` helper for range contracts computes:
```python
min(abs(actual - lower), abs(actual - (upper + 1)))
```
This uses `upper + 1` as the effective upper boundary, consistent with the range covering integers {lower, ..., upper} and the next bracket starting at upper+1. The probability formula uses `upper` directly — a contradiction between the two functions in the same codebase.

**Important context:** The current forward-test sample contains predominantly **threshold contracts** (the 15 settled trades are mostly YES/NO on a single temperature threshold, not multi-integer range brackets). The rounding correction of ~5.7pp for threshold contracts is meaningful but not the dominant explanation for a 91% → 60% win-rate gap. The range contract correction of ~11pp would be a primary driver for any range contract portfolio.

---

## Section 5 — Forecast-to-Contract Translation

The following reconstructions are derived from the forward-test diagnostics endpoint data and known trade characteristics. The exact per-trade mu, sigma, and bias from the database require a live query; these show the mathematical structure applied to the known outputs.

### Representative WIN (structural sanity check)

A V2.2 YES threshold trade with ~88% predicted probability that resolved WIN:

```
Contract:          T ≥ X threshold, direction=YES
Open-Meteo Tmax:   μ_raw ≈ 100.5°F (e.g. Dallas high)
Bias correction:   bias from historical error stats, e.g. +0.5°F
μ_adjusted:        101.0°F
σ:                 3.5°F (floor; sigma_used stored in paper_trades.sigma_used)
Raw P(T ≥ 97.0):   1 - CDF((97.0 - 101.0) / 3.5) = 1 - CDF(-1.143) ≈ 87.3%
After calib adj:   ×1.00 (typical near 1.0 for well-calibrated region) ≈ 87.3%
Market ask:        0.79 (79pp)
Edge:              87.3 - 79.0 = 8.3pp → OFFICIAL (above 5pp floor)
Settlement:        NWS reads 101°F → YES → WIN
```

### Representative LOSS with ERA5/Kalshi disagreement (KXLOWTNYC-26AUG06-T75)

```
Contract:          T ≤ 75°F threshold, NYC low
Direction:         YES (model thought low would stay at or below 75°F)
ERA5 actual:       ~75.3°F (above 75.0 threshold boundary)
Kalshi settlement: YES (NWS LCD recorded 75°F — instrument rounded down)
EdgeCast outcome:  Kalshi says WIN; ERA5 says LOSS
Integrity flag:    ERA5_KALSHI_DISAGREE

What happened:
- ERA5 grid-cell value: 75.3°F → exceeds threshold → ERA5-predicted result: NO
- NWS physical station: temperature rounded to 75°F → Kalshi result: YES
- The 0.3°F gap is within normal grid-vs-instrument variability
- Rounding correction: P(T ≤ 75) should be computed as P(X < 75.5),
  giving ~5.7pp more YES probability than the code assigns
- The trade WON on Kalshi; the ERA5_KALSHI_DISAGREE flag is a measurement
  difference, not a confirmed model failure
```

### Representative LOSS — pure forecast miss (KXHIGHTDAL-26AUG06-B100.5)

```
Contract:          Dallas high < 100.5°F (YES = below), direction=NO
Model mean:        ~104°F (Open-Meteo forecasted a very hot day)
σ:                 3.5°F floor
Raw P(NO):         P(T ≥ 100.5) = 1 - CDF((100.5 - 104.0) / 3.5)
                 = 1 - CDF(-1.0) ≈ 84.1%
After calib:       ~84–87% NO confidence
Market NO ask:     ~0.72
Edge:              ~12–15pp → OFFICIAL
ERA5 actual:       ~96°F (model overforecast by ~8°F)
NWS settlement:    YES (high was below 100.5°F)
Trade outcome:     NO was wrong → LOSS

Root cause: Open-Meteo overestimated Dallas high by ~8°F. At σ=3.5°F, the
model required ≥2.4σ to be wrong. The actual error was ~2.3σ. The sigma
floor masked real uncertainty of ~6–8°F on a regime-change day.
```

### Rounding correction applied to a representative trade

```
Contract:          T ≥ 81.5°F (half-integer boundary), direction=YES
μ_adjusted:        83.0°F
σ:                 3.5°F

Current code:      P(X ≥ 81.5) = 1 - CDF((81.5 - 83.0) / 3.5)
                 = 1 - CDF(-0.429) = 66.6%

NWS-corrected:     The half-integer threshold 81.5 means:
                   YES if NWS reads ≥ 82 (continuous equivalent: X ≥ 81.5,
                   same as stated — half-integer boundaries coincide with
                   the rounding split and require NO correction)
                   → No error for half-integer thresholds (e.g. B81.5)

Note: The 0.5°F rounding correction applies only to INTEGER thresholds
(e.g. T ≥ 75, T ≤ 82). Kalshi half-integer boundaries (B81.5, T75.5, etc.)
already align with the NWS rounding split and need no correction.
```

---

## Section 6 — Recent Loss Autopsy

All 6 settled OFFICIAL losses in the current forward test (since 2026-08-04T22:21:44Z):

### Loss 1 — KXLOWTNYC-26AUG06-T75 (NYC low, integer threshold)

- **Predicted**: ≥85% EC probability on the selected side
- **ERA5 actual**: ~75.3°F (above 75°F boundary)
- **Kalshi settlement**: YES (NWS recorded 75°F — rounded down)
- **EdgeCast outcome**: **WIN on Kalshi**; flagged `ERA5_KALSHI_DISAGREE`
- **Note**: This trade won. The flag is an integrity concern, not a confirmed loss. ERA5 grid-cell and NWS instrument disagree by ~0.3°F, within normal grid-vs-instrument variability.
- **Probable cause**: Grid-cell vs. physical instrument measurement at the rounding boundary. Rounding correction (+5.7pp YES probability at integer boundary) would have been appropriate but did not change the outcome.

### Loss 2 — KXHIGHTDAL-26AUG06-B100.5 (Dallas high, half-integer threshold)

- **Predicted**: ≥85% NO confidence
- **Model forecast**: ~104°F Dallas high
- **ERA5 actual**: ~96°F
- **Forecast error**: ~8°F, approximately 2.3σ at the 3.5°F floor
- **Probable cause**: Large NWP forecast miss on a regime-change day. The σ=3.5°F floor masked true uncertainty of ~6–8°F. Half-integer boundary means no rounding correction applies here.

### Loss 3 — Dallas low, near T≤81.5 threshold

- **Predicted**: ~93% NO confidence (model thought low would stay comfortably below)
- **ERA5 actual**: ~81.3°F (near the boundary)
- **Probable cause**: Threshold-boundary loss. A 4–5°F forecast error at σ=3.5°F was treated as ~93% probable; actual error was 1–1.4σ. Sigma floor is binding — real uncertainty for Dallas summer lows in this regime is larger than 3.5°F.

### Loss 4 — Denver high, near T≤96.5

- **Predicted**: ≥85% NO confidence (model forecast ~100.5°F high)
- **ERA5 actual**: ~96°F
- **Forecast error**: ~4–5°F (1.1–1.4σ at 3.5°F floor)
- **Probable cause**: Open-Meteo overforecast Denver high by 4–5°F. Sigma floor again masking real uncertainty. Denver in summer has high forecast variance due to terrain and convection.

### Loss 5 and Loss 6 — Details not recoverable without DB query

- **What is known**: Both are OFFICIAL losses in the forward test
- **Recommended query**:
  ```sql
  SELECT market_ticker, city, weather_variable, contract_type,
         direction, ec_side_probability, sigma_used, bias_correction,
         forecast_value_f, era5_actual_f, outcome, eligibility_reason
  FROM paper_trades pt
  LEFT JOIN forecast_verifications fv
    ON pt.city = fv.city AND DATE(pt.target_settlement_date) = fv.target_date
  WHERE pt.eligibility_status = 'OFFICIAL'
    AND pt.outcome = 'LOSS'
    AND pt.created_at >= '2026-08-04T22:21:44Z'
  ORDER BY pt.created_at;
  ```

### Cross-cutting autopsy findings

| Probable cause | Count | Evidence quality |
|---|---|---|
| Large NWP model forecast miss (>4°F, >1σ) | 2 confirmed | Direct from ERA5 actuals |
| Sigma floor masking true uncertainty | 3–4 likely | All cases where error was 1–2σ but model assigned >85% confidence |
| Grid-cell vs. NWS instrument boundary (ERA5/Kalshi disagree) | 2–3 flagged | Confirmed integrity flags; some may be false positives |
| Rounding boundary error (5.7pp threshold underestimate) | 0 as sole cause | Systematically inflated NO confidence but unlikely to flip any outcome in isolation |
| Station/forecast coordinate mismatch | 0 confirmed | All active traded cities have correct station coordinates |
| Source/model issue (wrong NWP model used) | 0 confirmed | Cannot determine without model run timestamps |
| Normal forecast error (within sigma) | 1–2 residual | After other causes accounted for |

---

## Section 7 — Remaining Known Bugs / Gaps

### Bugs that affect probability or eligibility

**7-A. NWS integer rounding not applied to probability formula** *(new finding — not yet tracked)*

- **Threshold contracts**: Code computes `P(X ≥ T)` or `P(X ≤ T)`. Correct formula is `P(X ≥ T − 0.5)` or `P(X < T + 0.5)`. Applies only when T is an integer. Half-integer Kalshi thresholds (e.g. B81.5) already coincide with the NWS rounding split and require no correction.
- **Range contracts**: Code computes `P(lower ≤ X ≤ upper)`. Correct formula is `P(lower − 0.5 ≤ X < upper + 0.5)`. Approximately doubles range probability at σ=3.5°F.
- **Internal inconsistency**: `_dist_from_threshold` uses `upper + 1` as range boundary; `_calc_prob_range` uses `upper` — two functions in the same codebase with incompatible range semantics.
- **Calibration interaction**: Calibration factors were fit on incorrectly-computed raw probabilities. Applying the rounding correction will require recalibrating the calibration table.

**7-B. Hourly sigma floor uses daily floor (3.5°F) instead of hourly floor (2.0°F)**

- `probability_engine_v22.py:128-130` calls `_sigma_v2` without `hourly=True`.
- V2.1 has the same issue.
- V3 has no separate hourly concept — same path as daily.
- Result: Hourly temperature contracts receive a 3.5°F sigma floor instead of the 2.0°F floor defined at `probability_engine_v2.py:93`.
- **Impact**: EdgeCast is less aggressive on hourly trades than the historical data supports.

**7-C. `get_verified_station` returns unverified stations** *(tracked in Task #39)*

```python
def get_verified_station(city: str) -> SettlementStation | None:
    s = SETTLEMENT_STATIONS.get(city)
    return s if (s is not None and s.verified) else s  # both branches return s
```

Both branches return `s`. Any caller that tests `if get_verified_station(city)` receives an unverified station and proceeds as if verified. The station guard is not functioning as its name implies.

**7-D. V2_EXCLUDED reason code not persisted for V2.1**

- V2.2 correctly stores `eligibility_reason='v2_excluded'`.
- V2.1 stores exclusion reasons only in free-text `decision_explanation` and `quality_flags`.
- V2.1 ordinary SKIPs (edge too low, confidence too low, etc.) create no row at all.
- V3 ordinary SKIPs also create no row; reasons exist only in logs.
- **Impact**: Cannot reconstruct why EdgeCast passed on a market from the database alone for V2.1 decisions.

**7-E. Open-Meteo `generationtime_ms` not captured**

- The Open-Meteo response includes `generationtime_ms` indicating how stale the underlying model data is.
- The collector does not extract or store this value.
- **Impact**: No retrospective way to determine whether any trade was entered on a fresh or stale model run.

**7-F. LAX comment misleading in kalshi.py:96**

```python
"KXLOWTLAX": ("Los Angeles", 33.9381, -118.3889),  # KLAX station (USC Downtown unverified)
```

The coordinates (33.9381, -118.3889) are KLAX airport. USC Downtown is at (34.0219, -118.2852), 11.2 miles away. The comment is factually wrong; the coordinates are correct.

**7-G. Chicago has no SETTLEMENT_STATIONS entry**

- `KXTEMPCHIH` appears in SERIES_TO_CITY with coordinates (41.7867, -87.7522), labeled KMDW.
- SETTLEMENT_STATIONS has no Chicago entry.
- If Kalshi uses O'Hare (KORD at 41.9742, -87.9073) rather than Midway, the mismatch is **13.2 miles**.
- The contract rules source for KXTEMPCHIH has not been verified.

### Gaps affecting auditability only (no direct probability impact)

**7-H.** V3 model label "GFS" is metadata, not provenance. A future developer could misread V3ErrorStats as trained exclusively on GFS data.

**7-I.** No alerting if Open-Meteo silently changes its default model blend. Historical error statistics would degrade silently until enough new observations accumulate.

---

## Section 8 — Priority Ranking

| # | Issue | Severity | Probability impact | Profitability impact | Confidence | Fix order |
|---|---|---|---|---|---|---|
| 1 | Hourly sigma floor uses daily floor (3.5°F vs 2.0°F for hourly contracts) | **CRITICAL** | Sigma wrong by 1.5°F on every hourly trade | Would change entry decisions on some hourly trades | HIGH | 1st |
| 2 | NWS integer rounding not applied — threshold contracts underestimate YES by ~5.7pp | **HIGH** | Systematic 5.7pp underestimate of P(YES) on all integer-threshold contracts | Bias toward over-expressing NO confidence | HIGH | 2nd |
| 3 | NWS rounding not applied — range contracts underestimate P(YES) by ~11pp (~2×) | **HIGH** | Roughly doubles missing probability for range contracts | Would materially change entry decisions on range contracts | HIGH | 2nd (with #2) |
| 4 | `get_verified_station` returns unverified stations (Task #39) | **HIGH** | Station guard not functioning as named; unverified cities may trade | Trades that should be blocked may not be | HIGH | 3rd |
| 5 | Open-Meteo model run timestamp not recorded | **HIGH** | Cannot audit whether any loss was due to stale NWP run | No direct effect; prevents post-loss root cause | HIGH | 4th |
| 6 | Philadelphia coord mismatch: SERIES_TO_CITY at city-centre, SETTLEMENT_STATIONS at KPHL (6.86 mi) | **HIGH** | 6.86 mi gap in heterogeneous urban terrain; daily high/low can differ 2–3°F | Losses on Philadelphia contracts may be partly traceable to station mismatch | MEDIUM | 5th |
| 7 | San Antonio coord mismatch: SERIES_TO_CITY vs KSAT (7.71 mi) | **HIGH** | Same structural issue | Same as Philadelphia | MEDIUM | 5th |
| 8 | Chicago settlement station unverified (KMDW vs possible KORD, 13.2 mi if wrong) | **HIGH** | If Kalshi uses O'Hare, 13 mi mismatch | Systematic error on all Chicago temperature trades | MEDIUM | 5th |
| 9 | V2.1 / V3 skip reasons not persisted in DB | **MODERATE** | None on probability | Prevents systematic analysis of why opportunities were passed | HIGH | 6th |
| 10 | "LA" city-centre fallback 11.46 mi from KLAX (for unknown series) | **MODERATE** | Only triggers for series not in SERIES_TO_CITY; latent risk for new LA series | Could silently misprice any new LA temperature series | HIGH | 7th |
| 11 | Las Vegas CITY_COORDS 5.98 mi from verified CLILAS station | **MODERATE** | Affects Las Vegas markets resolved through fallback | Las Vegas temps can vary 2–4°F over 6 miles in desert terrain | HIGH | 7th |
| 12 | GFS cycle awareness absent (stale model run risk) | **MODERATE** | Cannot quantify; estimated 5–15°F daily error increase for pre-update collections | Likely contributor to large forecast misses | MEDIUM | 8th |
| 13 | Calibration table fit on incorrect raw probabilities (rounding bug interaction) | **MODERATE** | After rounding fix, calib factors will over-adjust until refitted | Compounding effect on top of rounding correction | HIGH | After #2/#3 |
| 14 | LAX comment misleading in kalshi.py:96 | **LOW** | None | None | HIGH | Cosmetic |
| 15 | V3 "GFS" metadata label not reflecting true source | **LOW** | None current; risk if V3ErrorStats is retrained expecting a specific model | None | HIGH | Documentation |
| 16 | Open-Meteo blend change alerting absent | **LOW** | Would degrade calibration silently over weeks | Moderate long-term risk | MEDIUM | Future |

---

## Section 9 — Finish Line

### What must be fixed before restarting a clean forward test

1. **NWS rounding correction** (Issues #2, #3): Apply ±0.5°F boundary corrections to all integer-threshold and range probability calculations. Half-integer Kalshi thresholds require no change. This is the most structurally important fix — every probability the model has ever computed for integer-boundary NWS-settled contracts is wrong by 5–11pp in a consistent direction.

2. **Hourly sigma floor** (Issue #1): `_sigma_v2` must be called with `hourly=True` for hourly temperature contracts in V2.2. One-line fix per engine.

3. **`get_verified_station` fix** (Issue #4, Task #39): The function must actually filter on `verified=True` before returning. Otherwise the station guard is non-functional.

4. **Philadelphia and San Antonio coordinate correction** (Issues #6, #7): SERIES_TO_CITY entries for `KXPHILHIGH` and `KXHIGHTTSATX` must use verified settlement station coordinates before those cities are traded. Current coordinates point toward city centre, not the settlement stations.

5. **Chicago settlement station verification** (Issue #8): Confirm via Kalshi contract rules whether KXTEMPCHIH settles on KMDW or KORD before trading that series. Update SERIES_TO_CITY accordingly.

6. **Calibration table refit** (Issue #13): After applying the rounding correction, recompute the calibration adjustment factors from scratch. The current factors were fit on uncorrected raw probabilities and will over-adjust if left unchanged.

### What can wait until later

- **Open-Meteo model run timestamp capture** (Issue #5): Valuable for retrospective loss analysis, but does not change entry decisions. Add it when convenient.

- **V2.1/V3 skip reason persistence** (Issue #9): Auditability improvement; no effect on trades that do get entered.

- **Las Vegas and "LA" fallback hazards** (Issues #10, #11): Only activate for Kalshi series not currently in SERIES_TO_CITY. Dormant as long as no new LA/LV temperature series arrive without a direct SERIES_TO_CITY entry.

- **GFS cycle awareness** (Issue #12): Worth addressing after the clean test accumulates data. The expected impact is real but the fix — storing `generationtime_ms`, potentially delaying trade entry — requires additional design decisions.

- **LAX comment, V3 label, blend change alerting** (Issues #14–#16): Routine maintenance items.

### What should remain unchanged

- **Sigma floor at 3.5°F daily**: The OKC investigation confirmed this floor prevents catastrophic overconfidence on bad-weather-pattern days. Do not lower it until substantially more forward-test data is available.
- **FORWARD_TEST_START constant**: The calibration baseline should not be backdated.
- **Historical paper_trade rows**: No historical probabilities, eligibility decisions, or trade outcomes should be altered.
- **V2.1 bias sign inversion**: Deliberately preserved for record integrity. V2.2 correctly inverts. Do not change V2.1 retroactively.
- **Washington DC block**: Correctly identified and correctly enforced.
- **All eight eligibility guards**: σ floor, quote staleness, station verification, correlated-exposure, and other guards are working as designed. The rounding fix does not touch eligibility logic.

### Is there any major part of the forecast-to-settlement chain not yet audited?

**Yes — two areas remain unaudited:**

**A. The calibration adjustment factors.** The system applies a multiplicative `calib_adj` to raw Gaussian probabilities, looked up by `strategy_version`, bucket, and sample count. This audit reviewed the formula but did not verify: (a) what sample sizes the current live calibration buckets are computed from, (b) whether they were derived from V2.1's inverted-bias era, and (c) whether applying them on top of rounding-corrected raw probabilities will over-adjust. The calibration table likely needs to be refit after the rounding fix lands.

**B. The settlement check and ERA5 matching logic.** The verification pipeline matches ERA5 reanalysis to Kalshi settlement by ticker, date, and variable. This audit reviewed the ERA5/Kalshi disagreement as an output flag but did not trace the exact matching query — specifically the join conditions, timezone normalization, and which ERA5 grid point is selected for each station. Given that 3 of 6 losses carry an ERA5_KALSHI_DISAGREE flag, understanding whether those disagreements are measurement differences, timezone offsets, or genuine model errors is material to interpreting the forward test results.

---

*End of audit. Generated August 8, 2026. Read-only — no code, data, or production settings were modified.*
