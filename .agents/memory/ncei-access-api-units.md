---
name: NCEI access API units — full degrees, not tenths
description: The NCEI access services API (v1) returns full degrees, not tenths-of-degrees. TMAX_SCALE_FACTOR must be 1.0.
---

## Rule
The NCEI access services API at `https://www.ncei.noaa.gov/access/services/data/v1` returns TMAX in **full degrees**:
- `units=metric` → full degrees Celsius (e.g. `31.7` = 31.7°C)
- `units=standard` → full degrees Fahrenheit (e.g. `89.0` = 89°F)

Do **not** divide by 10.

**Why:** The old CDO v1 API (`ncdc.noaa.gov/cdo-web/api/v2/data`) and raw GHCND `.dly` files store values in tenths-of-degrees Celsius. This is NOT the same as the newer NCEI access API. Mixing up the two APIs causes a 10× scale error followed by a °C→°F conversion, producing corrupted observations (e.g. reporting a Denver July high of 48°F instead of 89°F).

**How to apply:** When using `https://www.ncei.noaa.gov/access/services/data/v1`, set `TMAX_SCALE_FACTOR = 1.0` and use `units=metric` to get Celsius → convert directly to Fahrenheit. The V3 NOAA GHCND observations client (`app/services/v3_providers/noaa_ghcnd_observations.py`) was fixed to TMAX_SCALE_FACTOR=1.0.
