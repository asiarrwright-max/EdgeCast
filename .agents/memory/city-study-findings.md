---
name: City Specialization Study Findings
description: Key results and architectural decisions from the 2026-08-09 city suitability study
---

# City Specialization Study — Key Results

## Recommendation
**B. SPECIALIZE_THREE_CITIES** — Denver · Houston · Oklahoma City

## City Rankings (composite score)
| Rank | City | Score | Key Signal |
|------|------|-------|-----------|
| 1 | Denver | ~71 | 57.1% v2.2 win rate; NO/range 71.4%; verified station; cold bias –2.24°F |
| 2 | Houston | ~66 | Best MAE 0.87°F; weak 22.6% win rate (model-to-market gap) |
| 3 | Oklahoma City | ~63 | Improving trend; 43.8% overall, 50% v2.2 |
| 4 | New York City | ~63 | Verified station (Central Park); MAE 1.72°F |
| 5 | Dallas | ~60 | Moderate everything |
| 6 | Minneapolis | ~52 | Dragged by –5.59°F cold bias |
| 7 | Miami | ~50 | All metrics middling |
| 8 | Los Angeles | low | Largest dataset (617 settled), 8.9% win rate; YES/hourly dominated |
| 9 | Chicago | low | 6.1% win rate; daily contract performance obscured by hourly |
| 10 | Washington DC | 0 | **NON-NWS settlement — never trade** |

## Score formula
30% forecast accuracy + 25% trading quality + 20% liquidity + 15% sample size + 10% station integrity

**Why:** Forecast accuracy matters most because EdgeCast's edge is model quality; trading quality is the primary outcome; liquidity/station/sample are guard rails.

## Critical insights
- YES-direction trades have 0% win rate in aggregate; NO/range drives all profit
- Denver NO/range win rate: 71.4% (168 settled)
- Houston MAE 0.87°F is best-in-class but doesn't yet translate to wins — investigate
- Minneapolis –5.59°F bias makes it unsuitable despite 46% overall win rate
- volume column in kalshi_markets is always NULL — cannot use for liquidity scoring
- Chicago and LA dominated by hourly/YES trades — daily contract performance is much better
- Washington DC: The Weather Company settlement — excluded permanently (nws_settlement=False)

## FTB projection for Denver (as of 2026-08-09)
- All v2.3 trades RESEARCH_ONLY (1 scan day, all stale or v2_excluded)
- Est. 1–5 OFFICIAL trades per week once fresh quotes arrive
- Time to 50 settled OFFICIAL: 10–50 weeks

**How to apply:** When evaluating city-specific strategy decisions, treat Denver as the primary signal city. Houston's forecast quality should be exploited via model improvements before reducing focus there. Never allow DC trades regardless of eligibility score.
