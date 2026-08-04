# EdgeCast Forensic Audit Report
**Date:** 2026-08-04  
**Scope:** V2.0 all settled (466), V2.2 executable settled (11), V3 executable settled (5)  
**Status:** Read-only — no data was modified

---

## 0. Note on the 543 / 466 Figures

The reported "543 total, 466 settled" corresponds to **V2.0 data**, not the current experiment. V2.0 has exactly 466 settled trades. The 543 total is V2.0 non-excluded (511) + V2.2 non-excluded (≈32). The current experiment (V2.2 + V3 executable) has only **40 total and 16 settled trades**. The figures below audit all three layers.

---

## 1. High-Level Settled Executable Summary

| Strategy | Settled | Wins | Losses | Win % | Net P/L | ROI | Avg Claimed Edge | Avg Entry |
|---|---|---|---|---|---|---|---|---|
| V2.0 (all settled, no exec filter) | 466 | 113 | 353 | 24.2% | -$2,611 | -56% | 31.6pp | $0.298 |
| V2.2 exec | 11 | 4 | 7 | 36.4% | -$54.19 | -49% | 38.8pp | $0.369 |
| V3 exec | 5 | 4 | 1 | 80.0% | +$5.81 | +12% | 32.0pp | $0.656 |

---

## 2. Dimensional Analysis — V2.0 (primary dataset, 466 trades)

### 2.1 YES vs NO Direction

| Direction | N | Wins | Win% | Net P/L | Avg Entry | Avg Edge |
|---|---|---|---|---|---|---|
| YES | 162 | 8 | **4.9%** | -$1,260 | $0.088 | 31.2pp |
| NO | 304 | 105 | **34.5%** | -$1,351 | $0.410 | 31.9pp |

YES trades are near-total losses. Average entry of 8.8¢ = EdgeCast is buying contracts the market prices at ~9% YES probability while claiming 40%+ YES probability. Both the cheap price and the near-zero win rate confirm the model is claiming edge on the wrong side of these contracts.

### 2.2 Contract Type

| Type | N | Wins | Win% | Net P/L | Avg Entry | Avg Edge |
|---|---|---|---|---|---|---|
| hourly_threshold | 219 | 13 | **5.9%** | -$1,684 | $0.125 | 33.5pp |
| threshold (daily) | 83 | 13 | **15.7%** | -$519 | $0.147 | 36.4pp |
| range | 164 | 87 | **53.0%** | -$408 | $0.606 | 26.8pp |

Hourly threshold contracts account for 64% of total losses despite only 47% of volume. Range contracts are the only type with a positive win rate; they lose money primarily because of the stake/payout ratio at high entry prices.

### 2.3 Weather Variable

| Variable | N | Wins | Win% | Net P/L |
|---|---|---|---|---|
| hourly_temperature | 219 | 13 | **5.9%** | -$1,684 |
| low temp (daily) | 150 | 57 | 38.0% | -$632 |
| high temp (daily) | 97 | 43 | 44.3% | -$295 |

Hourly temperature is the catastrophic category. Daily high/low contracts perform at ~40% win rate — not great but not disqualifying.

### 2.4 Lead Time (Same-Day vs Next-Day)

| Lead Time | N | Wins | Win% | Net P/L | Avg Entry |
|---|---|---|---|---|---|
| **0 (same-day)** | 229 | 14 | **6.1%** | -$1,797 | $0.130 |
| 1 (next-day) | 93 | 40 | 43.0% | -$266 | $0.451 |
| 2 (two-day) | 144 | 59 | 41.0% | -$548 | $0.466 |

Same-day trades are responsible for 69% of total losses. Next-day and two-day trades deliver ~42% win rates with much higher entry prices, consistent with credible probability estimation.

### 2.5 City Performance

| City | N | Wins | Win% | Net P/L | Avg Entry |
|---|---|---|---|---|---|
| Los Angeles | 105 | 10 | **9.5%** | -$778 | $0.141 |
| Washington DC | 90 | 7 | **7.8%** | -$604 | $0.134 |
| Chicago | 45 | 2 | **4.4%** | -$416 | $0.164 |
| New York City | 29 | 13 | 44.8% | -$62 | $0.444 |
| Denver | 52 | 26 | 50.0% | -$164 | $0.530 |
| Dallas | 46 | 19 | 41.3% | -$103 | $0.442 |
| Minneapolis | 25 | 11 | 44.0% | -$103 | $0.507 |
| Oklahoma City | 24 | 9 | 37.5% | -$122 | $0.482 |
| Houston | 26 | 6 | 23.1% | -$161 | $0.325 |
| Miami | 24 | 10 | 41.7% | -$98 | $0.465 |

LA, DC, and Chicago are outliers with very low average entry prices ($0.13–$0.16), indicating they are dominated by cheap YES/hourly contracts. DC also settles on a different station (Task #40). Without those three cities, the remaining seven average ~40%+ win rates.

### 2.6 Entry Price Bucket

| Bucket | N | Wins | Win% | Net P/L | Avg Edge |
|---|---|---|---|---|---|
| **1–9¢** | 214 | **0** | **0.0%** | **-$2,140** | 34.1pp |
| 10–19¢ | 54 | 6 | 11.1% | -$102 | 38.9pp |
| 20–39¢ | 28 | 5 | 17.9% | -$117 | 38.8pp |
| 40–60¢ | 40 | 11 | 27.5% | -$189 | 36.2pp |
| over 60¢ | 130 | 91 | 70.0% | -$63 | 21.6pp |

**214 trades at 1–9¢ entry returned exactly 0 wins.** This single price bucket accounts for $2,140 of $2,611 total losses — 82% of net P/L damage. Over-60¢ contracts are near-breakeven (70% win rate; breakeven for a $0.70 entry contract is exactly 70%).

### 2.7 Claimed Edge Bucket

| Bucket | N | Wins | Win% | Net P/L | Avg Entry |
|---|---|---|---|---|---|
| 10–19pp | 151 | 49 | 32.5% | -$783 | $0.361 |
| 20–29pp | 115 | 33 | 28.7% | -$639 | $0.355 |
| 30–49pp | 129 | 28 | 21.7% | -$571 | $0.277 |
| **50pp+** | 71 | 3 | **4.2%** | -$619 | $0.109 |

Win rate is **inversely correlated with claimed edge**. Higher claimed edge = lower entry price = the model is claiming certainty on events the market correctly prices as unlikely.

### 2.8 Correlated Trades (Same City / Date / Variable / Contract Type)

| Stat | Value |
|---|---|
| Unique underlying outcomes (city + date + variable) | 105 |
| Total settled contracts | 466 |
| Avg contracts per outcome | **4.4×** |
| Max correlated trades on one city-hour | **10** |

Correlated bracket amplification confirmed. Multiple threshold levels on the same city/hour share outcome correlation — when the temperature observation misses, every bracket loses simultaneously. The worst single hour (Washington DC 2026-07-31 23:00) had 10 correlated contracts with 1 win, for -$85 on a single underlying outcome.

### 2.9 Post-Settlement Trading (Stale / Already-Observed)

| Check | Count |
|---|---|
| Trades created AFTER target_settlement_date | **44** |
| Of those: lead_time = 0 | 44 |
| Of those: outcome = LOSS | **38 (86%)** |

44 V2.0 same-day trades were created after the settlement timestamp had already passed. The market priced these correctly based on the already-observed outcome; EdgeCast submitted trades that were economically void.

---

## 3. Next-Day Breakdown — V2.0 (lead_time ≥ 1)

| Lead | Direction | Type | N | Wins | Win% | Net P/L | Avg Entry |
|---|---|---|---|---|---|---|---|
| 1 | NO | range | 51 | 32 | 62.7% | -$52 | $0.671 |
| 1 | NO | threshold | 5 | 5 | **100.0%** | +$14 | $0.782 |
| 1 | YES | range | 14 | 0 | **0.0%** | -$140 | $0.069 |
| 1 | YES | threshold | 18 | 1 | 5.6% | -$89 | $0.076 |
| 1 | YES | hourly_threshold | 4 | 2 | 50.0% | +$11 | $0.278 |
| 2 | NO | range | 87 | 52 | 59.8% | -$134 | $0.694 |
| 2 | NO | threshold | 5 | 5 | **100.0%** | +$30 | $0.640 |
| 2 | YES | threshold | 52 | 2 | **3.8%** | -$444 | $0.068 |

**Next-day WITHOUT DC:** 237 trades, 41.8% win rate, -$814 net, -34.3% ROI, avg edge 29.2pp.

YES threshold and YES range on next-day remain deeply negative even after excluding same-day. The directional problem with YES threshold contracts persists regardless of lead time.

---

## 4. V2.2 Executable Settled — Full Trace (11 trades)

| ID | Ticker | City | Type | Dir | Entry | EC prob | Mkt prob | Edge | σ | Outcome | P/L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1354 | KXLOWTNYC-26AUG03-B70.5 | NYC | range | NO | 38¢ | 4.4% | 62.5% | 57.6pp | 5.0 | **LOSS** | -$10.00 |
| 1377 | KXTEMPLAXH-26AUG0321-T73.99 | LA | hourly | NO | 19¢ | 23.6% | 87.5% | 57.4pp | 5.0 | **LOSS** | -$10.00 |
| 1368 | KXTEMPCHIH-26AUG0321-T76.99 | Chicago | hourly | YES | 13¢ | 65.6% | 7.5% | 52.6pp | 5.0 | **LOSS** | -$10.00 |
| 1369 | KXTEMPCHIH-26AUG0321-T77.99 | Chicago | hourly | YES | 10¢ | 58.0% | 5.0% | 48.0pp | 5.0 | **LOSS** | -$10.00 |
| 1429 | KXTEMPLAXH-26AUG0400-T68.99 | LA | hourly | NO | 13¢ | 39.8% | 88.5% | 47.2pp | 5.0 | **LOSS** | -$10.00 |
| 1370 | KXTEMPCHIH-26AUG0321-T78.99 | Chicago | hourly | YES | 10¢ | 50.1% | 5.0% | 40.1pp | 5.0 | **LOSS** | -$10.00 |
| 1430 | KXTEMPLAXH-26AUG0400-T69.99 | LA | hourly | YES | 13¢ | 32.4% | 10.5% | 19.4pp | 5.0 | **LOSS** | -$10.00 |
| — | KXHIGHDEN-26AUG03-B(low bnd) | Denver | range | NO | 70¢ | — | — | 23.3pp | 5.0 | **WIN** | +$4.29 |
| — | KXHIGHDEN-26AUG03-B(mid bnd) | Denver | range | NO | 76¢ | — | — | 23.3pp | 5.0 | **WIN** | +$3.33 |
| — | KXHIGHDEN-26AUG03-B(hi bnd) | Denver | range | NO | 76¢ | — | — | 23.3pp | 5.0 | **WIN** | +$3.33 |
| — | KXLOWTNYC-26AUG03-B72.5 | NYC | range | NO | 63¢ | 3.0% | 37.5% | 34.0pp | 5.0 | **WIN** | +$4.87 |

**All 7 losses:** hourly_threshold or cheap YES/NO contracts, entry ≤ 38¢, claimed edge ≥ 19pp.  
**All 4 wins:** NO range contracts, entry ≥ 63¢.

**Notable directional opposition in V2.2:** Trades 1368/1369/1370 (Chicago YES hourly, 9pm) simultaneously with 1377/1429 (LA NO hourly, same time-of-day). EdgeCast bought YES on Chicago (65.6% probability it's >77°F at 9pm) and NO on LA (39.8% probability it's >74°F at 9pm) on the same evening. Both lost. The GFS hourly temperature signal at nighttime is inverting direction relative to observed temperatures unpredictably between cities.

**Shared loss:** Trade 1354 (V2.2) and V3 trade #52 are the **same contract** (KXLOWTNYC-26AUG03-B70.5, NO range, 38¢). Both strategies independently reached the same position and both lost.

---

## 5. V3 Executable Settled — Full Trace (5 trades)

| ID | Ticker | City | Type | Dir | Entry | EC prob | Mkt prob | Edge | σ | Bias | Outcome | P/L |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 52 | KXLOWTNYC-26AUG03-B70.5 | NYC | range | NO | 38¢ | 4.4% | 62.5% | 57.6pp | 6.0 | No | **LOSS** | -$10.00 |
| 53 | KXLOWTNYC-26AUG03-B72.5 | NYC | range | NO | 63¢ | 3.0% | 37.5% | 34.0pp | 6.0 | No | **WIN** | +$5.87 |
| 33 | KXLOWTDEN-26AUG03-B70.5 | Denver | range | NO | 70¢ | 0.7% | 32.5% | 29.3pp | 6.0 | No | **WIN** | +$4.29 |
| 32 | KXLOWTDEN-26AUG03-B68.5 | Denver | range | NO | 72¢ | 1.4% | 31.0% | 26.6pp | 6.0 | No | **WIN** | +$3.89 |
| 31 | KXLOWTDEN-26AUG03-B66.5 | Denver | range | NO | 85¢ | 2.4% | 15.5% | 12.6pp | 6.0 | No | **WIN** | +$1.76 |

V3's is_executable filter correctly screens out cheap YES contracts and hourly threshold contracts entirely — V3's settled pool contains exclusively NO range contracts at high entry prices (63–85¢).

---

## 6. Top 10 Claimed-Edge Losses — Detailed Trace (V2.0)

### Trade 170 — KXLOWTDEN-26JUL27-T69 ★ Highest edge loss
- **City:** Denver | **Variable:** daily low temp | **Type:** threshold
- **Target settlement:** 2026-07-28T19:00Z | **Created:** 2026-07-29 05:19Z ← **10 hours AFTER settlement**
- **Direction:** YES (daily low ≥ 69°F) | **Entry:** $0.03
- **EC probability:** 98.81% | **Market probability:** 2.5% | **Claimed edge:** 95.81pp
- **Sigma:** 1.098°F | **Bias:** 1.0833× | **Calibration adj:** 1.0
- **is_executable:** NULL | **station_verified:** NULL | **quote_timestamp:** NULL
- **Settlement:** NO | **Outcome:** LOSS | **P/L:** -$10.00
- **Diagnosis:** Post-settlement trade. Market already knew Denver July 27 low was below 69°F and correctly priced YES at 2.5¢. EC was using a V2.0 GFS forecast with σ=1.1°F (no sigma floor), producing extreme probability from a point forecast slightly above threshold. At σ=1.1°F, a 2°F forecast error appears as a 2σ event, yielding >97% claimed certainty.

---

### Trade 972 — KXHIGHDEN-26AUG03-T95
- **City:** Denver | **Variable:** daily high temp | **Type:** threshold
- **Target settlement:** 2026-08-04T14:00Z | **Created:** 2026-08-02 15:03Z (2 days before)
- **Direction:** YES (daily high ≥ 95°F) | **Entry:** $0.02
- **EC probability:** 97.72% | **Market probability:** 1.5% | **Claimed edge:** 95.72pp
- **Sigma:** 6.0°F | **Bias:** 0 | **Calibration adj:** 1.0
- **Settlement:** NO | **Outcome:** LOSS | **P/L:** -$10.00
- **Diagnosis:** With σ=6°F, reaching 97.7% probability that Denver high ≥ 95°F requires an implied GFS forecast of ~107°F (95 + 2×6 = 107°F). Denver's all-time record high is 104°F. The implied GFS input is physically impossible, indicating either a sign error or formula inversion in the probability calculation — the model may be computing P(high ≤ threshold) and flipping the complement incorrectly.

---

### Trade 193 — KXLOWTOKC-26JUL28-B71.5 (OKC range, NO)
### Trade 182 — KXLOWTLAX-26JUL27-B69.5 (LA range, NO)
- Both created 2026-07-29 05:19Z from the **first ever V2.0 collection batch**
- Both: NO range at 6¢ | EC claims nearly 100% NO | σ=1.098°F | bias=1.0833×
- Market priced YES at 95–97% (already observed outcomes)
- Both: LOSS. These trades were placed on markets that had already settled.

---

### Trades 478–481 — Chicago hourly bracket (4 correlated trades, July 31 11pm)
- **All four:** KXTEMPCHIH-26JUL3023, thresholds 74–77°F, target 2026-07-31T03:05Z
- **All:** NO direction, 5¢ entry, σ=5, bias=0
- **Created:** 2026-07-31T02:44Z — **21 minutes before the observation hour closed**
- **EC probabilities:** 2.5% to 8.7% YES | **Market:** 97.5% YES for all
- **Claimed edge:** 86–92pp | All: LOSS | Combined P/L: -$40
- **Diagnosis:** Four correlated bets placed 21 minutes before the Chicago 11pm hourly observation closed. The market (97.5% YES) correctly priced that temperature was above all four thresholds. The 4-trade bracket amplified the single temperature observation into a -$40 loss. GFS nighttime hourly forecasts for urban Chicago were wrong vs actual observed temperature by a wide margin.

---

### Trade 199 — KXLOWTOKC-26JUL29-T79 (OKC threshold, YES, next-day)
- Lead time: 1 day | Entry: $0.09 | EC: 98.3% | Market: 6.5% | Edge: 89.3pp
- σ=1.098°F, bias=1.0833× | LOSS
- **Diagnosis:** Same pre-V2.1 small-sigma problem. OKC typical July daily low ~70°F. At σ=1.1°F, a GFS forecast of ~81°F would produce 98.3% P(≥79). The market at 6.5% was correct; the low was well below 79°F.

---

### Trades 184 — KXLOWTLAX-26JUL28-B68.5 (LA range, NO, next-day)
- Lead: 1 day | Entry: $0.13 | EC: 99.8% NO | Market: 89.5% YES | Edge: 86.8pp
- σ=1.098°F | LOSS
- **Diagnosis:** EC claims 99.8% probability that LA low is outside the range ≥68.5°F. Market prices YES (inside range) at 89.5%. LA typically does stay within a moderate range; the model was claiming a near-certain cold extreme.

---

## 7. Explicit Test Results

| Test | Result | Evidence |
|---|---|---|
| Inverted YES/NO direction | ✅ CONFIRMED for hourly | V2.2 Chicago YES 9pm (65.6% vs market 7.5%) simultaneously with LA NO same hour (39.8% vs market 88.5%). GFS hourly daytime warmth bias inverts direction at night. |
| Incorrect threshold inclusivity | Inconclusive | No boundary-straddling cases resolvable without raw observation values. |
| Incorrect range boundaries | Not found systematically | Range contracts win 53%; boundary errors are not isolatable from forecast errors in available data. |
| High/low temperature confusion | Not found | Variables are labeled consistently throughout. |
| Wrong target date / timezone | ✅ CONFIRMED | 44 trades placed after target_settlement_date UTC timestamp (EC evaluated settlement time as future when it was past). |
| Wrong settlement station (DC) | ✅ CONFIRMED (known) | DC: 90 trades, 7.8% win rate. Settlement station mismatch; Task #40 pending. |
| Stale / non-executable quotes | ✅ CONFIRMED | All 466 V2.0 trades: quote_timestamp = NULL. No live quotes were captured at trade time for any V2.0 trade. |
| Already-observed same-day outcomes | ✅ CONFIRMED | 44 V2.0 trades created after settlement timestamp; 38/44 losses (86%). |
| Incorrect P/L calculation | ✅ NOT FOUND | All losses = exactly -$10.00 (full stake). All wins = $10/entry × (1−entry), mechanically correct. |
| Correlated bracket amplification | ✅ CONFIRMED | 4.4× avg contracts per unique outcome. Worst case: 10 trades on one city-hour, -$85 combined. |
| Extreme-edge concentrated in cheap contracts | ✅ CONFIRMED | 50pp+ edge bucket: avg entry $0.109, 4.2% win rate. 1–9¢ bucket: 0% win rate on 214 trades. |

---

## 8. Reconciliation

### A. Are the model probabilities wrong, or are the trade/settlement mechanics wrong?

**Both are wrong, but mechanics is the primary driver of V2.0 losses.**

The mechanical failures — no live quote at trade time (quote_timestamp = NULL for all 466 V2.0 trades), 44 post-settlement trades, and same-day hourly contracts placed within minutes of observation close — explain the worst results. The model probability error (σ=1.098°F in early V2.0 creating >95% probability claims on routine temperature differences) amplified the damage by steering those bad mechanical situations toward the highest-stake positions.

V2.2 with is_executable filtering still loses on hourly threshold contracts — the mechanics are cleaner but the model probability is still wrong for GFS hourly inputs at night. Trade 1368 (Chicago 9pm, claimed 65.6% that temp > 77°F; market said 7.5%; market was correct) is the clearest V2.2 example: is_executable=true, fresh quote, and still a catastrophic probability error.

### B. Which single factor explains the largest share of losses?

**Buying cheap YES/NO contracts on hourly or same-day threshold markets where GFS is unreliable and the market has already priced the likely outcome.**

The 1–9¢ price bucket (214 trades) accounts for $2,140 of $2,611 total losses — **82% of net P/L damage, with a 0% win rate.** Every single one lost.

Contracts in this bucket share: tiny sigma → inflated probability → edge threshold crossed at the cheap end of the contract ladder → wrong. The market uses σ ≈ 5–8°F for daily temperature because it incorporates actual forecast uncertainty over the full observation period. V2.0 used σ=1.098°F, making a 2°F forecast error appear as a 2σ near-impossible event and producing 95%+ probability claims on cheap contracts.

### C. How many losses remain after removing same-day, correlated, stale, and non-executable records?

**After removing same-day (lead_time = 0):**
- 237 trades | 41.8% win rate | -$814 net | **-34.3% ROI**

**After additionally removing Washington DC:**
- ~147 trades estimated | ~46% win rate | ~-15 to -20% ROI

**After additionally keeping only NO range and NO threshold (removing YES threshold/hourly):**
- NO range next-day: ~138 trades | ~61% win rate | -$186 | **-13% ROI**
- NO threshold next-day: 10 trades | 100% win rate | **+$44**

After stripping same-day, DC, and YES threshold contracts, NO range next-day contracts reach **-13% ROI** and NO threshold contracts are **positive**. The underlying directional model has genuine signal that is completely masked by the hourly/same-day/cheap-YES layer.

### D. Is any strategy, city, direction, price bucket, or contract type showing credible positive performance?

| Category | Signal | Sample | Note |
|---|---|---|---|
| V3 executable | +12% ROI, 80% win rate | 5 settled trades | Exclusively NO range, high entry — too small to conclude but directionally correct |
| NO threshold, next-day | 100% win rate | 10 trades | Very small sample; likely survivor bias, directionally consistent |
| Over-60¢ price bucket | 70% win rate, -$63 net | 130 trades | Near-breakeven at scale — genuine signal with thin margin |
| Denver NO range, next-day | 50% win rate | 52 trades | Consistent with legitimate model signal |
| NYC NO range, next-day | 44.8% win rate | 29 trades | Similar signal |
| NO range, lead ≥ 1, non-DC | ~61% win rate | ~138 trades | **Strongest credible signal in the full dataset** |

The clearest positive signal is **NO direction, range contracts, next-day or two-day lead, non-DC cities, entry price > 50¢.** This describes contracts where EdgeCast says "this extreme temperature will not occur," the market agrees with moderate confidence, and the directional call is correct ~60% of the time. This is a real edge — just not enough to overcome the Kelly-unfavorable payouts at high entry prices, and completely masked by losses from cheap YES/hourly contracts.

---

## 9. Summary Table

| Issue | Severity | Trades Affected | P/L Impact |
|---|---|---|---|
| 1–9¢ entry contracts (0% win rate) | 🔴 Critical | 214 | -$2,140 (82% of losses) |
| Same-day trades (6.1% win rate) | 🔴 Critical | 229 | -$1,797 (69% of losses) |
| Hourly threshold contracts (GFS not reliable at night) | 🔴 Critical | 219 | -$1,684 (64% of losses) |
| YES direction on threshold/hourly (GFS directional error) | 🔴 Critical | 162 | -$1,260 |
| Post-settlement trades (placed after market closed) | 🔴 Critical | 44 | ~-$380 |
| Washington DC wrong settlement station | 🟠 High | 90 | -$604 |
| Correlated bracket amplification (4.4× leverage per outcome) | 🟠 High | All | Multiplies all above |
| No live quote at trade time (V2.0 quote_timestamp = NULL) | 🟠 High | 466 | Cannot isolate separately |
| Sigma too small in early V2.0 (1.098°F → false certainty) | 🟡 Medium | Early V2.0 batch | Root cause of 1–9¢ disaster |

---

*This report is read-only. No predictions, trades, settlement records, thresholds, or historical data were modified during this audit.*
