---
name: Kalshi rules API for station verification
description: How to verify settlement stations without PDF hunting — use the Kalshi market API rules_secondary field.
---

## The discovery
`GET https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}` returns `rules_primary` and `rules_secondary` on every market. `rules_secondary` names the exact NWS station in plain English, e.g. `"choosing the location \"Dallas/Fort Worth, TX\" with Daily Climate Report"`.

## How to apply
To verify a new city:
1. Find a live ticker for that city (grep paper_trades DB or try series patterns like `KXHIGHTDAL`, `KXLOWTBOS`, etc.)
2. Call the market API — no auth required for public market data
3. Read `rules_secondary` — the station name is always in the `"choosing the location \"<Name>\" with Daily Climate Report"` phrase
4. Cross-reference the station name against GHCND — for airports it's always the major NWS airport station

## Cities verified this way (2026-07-30)
Dallas (DFW), Boston (Logan), Houston (Hobby — not Bush!), Miami (MIA), Los Angeles (LAX — not USC Downtown!), Phoenix (Sky Harbor), Seattle (SEA-TAC), San Francisco (SFO), Las Vegas (LAS), Minneapolis (MSP)

## Important findings
- **LA ambiguity resolved**: Kalshi explicitly says "Los Angeles Airport, CA" — USC Downtown was never the settlement station
- **Houston**: Hobby Airport confirmed; Bush Intercontinental is NOT used
- **Washington DC KXTEMPDCH series**: settles via The Weather Company (not NWS) — GHCND doesn't match; different settlement source

## Still unverified (no live markets found 2026-07-30)
Atlanta, Philadelphia, Detroit, Portland — no live Kalshi markets found; check again when markets are active.

**Why:** Previously we assumed PDFs were needed; the API text field is authoritative and requires no downloads.
