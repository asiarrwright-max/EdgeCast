---
name: GHCND settlement station integration
description: How EdgeCast fetches official Kalshi settlement observations via NOAA GHCND instead of ERA5 reanalysis.
---

# GHCND Settlement Station Integration

## The rule
`forecast_verifier.py` now uses NOAA GHCND CDO API for official NWS station readings.
ERA5 reanalysis is the fallback only when no token is set or GHCND returns nothing.

**Why:** Open-Meteo /archive returns ERA5 reanalysis, which differs from the official NWS Daily
Climate Report (what Kalshi actually settles on) by 1–4°F on individual days. Training σ/bias
on ERA5 errors ≠ training on Kalshi settlement errors.

## Key files
- `app/services/settlement_stations.py` — config-driven registry; add cities here, no other code changes
- `app/services/ghcnd_client.py` — async CDO API client (TMAX/TMIN, units=standard → °F)
- `app/services/forecast_verifier.py` — routes per city; exposes SRC_* label constants
- `app/config.py` — `noaa_cdo_token` optional setting (empty = ERA5 fallback for all)

## Verified cities (3)
- New York City → USW00094728 (Central Park)
- Chicago → USW00014819 (Midway Airport KMDW, NOT O'Hare)
- Denver → USW00003017 (KDEN)

## source_label values
| Label | Meaning |
|-------|---------|
| `ghcnd_observation` | Confirmed settlement station, GHCND reading |
| `ghcnd_observation_unverified` | Probable station (not yet confirmed from contract PDF) |
| `era5_reanalysis` | ERA5 fallback (new rows when token absent or GHCND fails) |
| `open_meteo_historical` | Legacy label for rows written before this integration |

## How to apply
- Adding a new verified city: set `verified=True` in `settlement_stations.py` — no verifier changes needed
- Fallback is automatic: if GHCND returns `{high: None, low: None}`, ERA5 is tried next
- NOAA CDO token: free at https://www.ncdc.noaa.gov/cdo-web/token; set as NOAA_CDO_TOKEN secret

## Chicago disambiguation
CHI and ORD city codes in CITY_COORDS both resolve to the city name "Chicago" and therefore
to the same settlement station (Midway, USW00014819). This is correct — Kalshi CHIHIGH
settles on Midway, not O'Hare.

## Los Angeles warning
LA is `verified=False` and flagged HIGH AMBIGUITY. Kalshi may settle on USC Downtown
(not LAX). Do not promote to verified until the contract PDF is read.
