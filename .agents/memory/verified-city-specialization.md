---
name: Verified City Specialization
description: Results of the verified-only city specialization study (2026-08-09)
---

# Verified City Specialization — Key Results

## Verification Findings (all 5 target cities)
All were already verified in settlement_stations.py from 2026-07-30 Kalshi API queries.
No station flags changed in this task.

| City | Verdict | Station | Evidence Field |
|------|---------|---------|---------------|
| Houston | VERIFIED | KHOU (Hobby Airport) | rules_secondary: "Houston-Hobby, TX" |
| Oklahoma City | VERIFIED | KOKC (Will Rogers) | Settlement audit: KXLOWTOKC-26JUL28-B71.5 |
| Dallas | VERIFIED | KDFW (DFW Airport) | rules_secondary: "Dallas/Fort Worth, TX" |
| Minneapolis | VERIFIED | KMSP (MSP Airport) | rules_secondary: "Minneapolis/St Paul, MN" |
| Miami | VERIFIED | KMIA (Miami Intl) | rules_primary: "highest temperature at Miami International Airport" |

## Final 3-City Specialization Set (verified+NWS only)
**Denver + New York City + Oklahoma City**

### Why this set:
- **Denver** (#1 composite 71/100): best trading history (50.2% WR), improving across all versions, verified KDEN station
- **New York City** (#2 forecast among verified, MAE 1.72°F): Central Park station verified, solid trading (39.1%), adequate sample (156 settled)
- **Oklahoma City** (#3 improving trend): 43.8% overall (50% v2.2), KOKC verified via settlement audit, balanced profile

### Bench cities:
- **Houston**: Best MAE (0.87°F) but only 22.6% WR — model doesn't yet convert forecast advantage into wins. Add when WR improves.
- **Minneapolis**: Verified KMSP station but –5.59°F systematic cold bias tanks forecast score. Bias must be corrected first.

## Bet Watch Guidance (not yet implemented)
- Primary cities (Best Bet eligible): Denver, NYC, OKC
- Informational only (WATCHING): all other verified cities
- "Best Bet Right Now" should be restricted to the 3-city specialization set

**How to apply:** When implementing Bet Watch direction filtering or best-bet restrictions, use verified_nws=True AND city IN (Denver, NYC, OKC) as the primary filter. Houston can be promoted when win rate exceeds ~35% on 50+ OFFICIAL settled trades.

## TypeScript naming note
`CityVerificationResult` (not `VerificationResult`) — renamed to avoid collision with `VerificationResult` already exported from `paper-trading-v2.ts`.
