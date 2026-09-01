# Complete settled-V3 offline evidence report

Generated from the authoritative production export dated 2026-09-01. This is
read-only research evidence. No production behavior or historical record was
changed.

## Population and split

- Complete settled V3.0 population: **555 contracts, 173 wins, 382 losses
  (31.17%)**, grouped into **120 weather events**.
- Evidence classes remain separate: **1 OFFICIAL**, **445 RESEARCH_ONLY**, and
  **109 UNCLASSIFIED**.
- Chronological event split: 72 development events, 24 validation events, and
  24 untouched holdout events. The holdout contains 100 RESEARCH_ONLY contracts
  with 21 wins and 79 losses.
- The single OFFICIAL row is reported separately and is far too small for a
  trustworthy accuracy conclusion.

## Baseline and market benchmark

The principal research population contains 445 contracts and 92 events. V3's
win rate is **30.34% (135/445)**, contract Brier is **0.2092**, event-level
Brier is **0.2117**, log loss is **0.6782**, and mean absolute calibration error
is **36.58 percentage points**. V3 event-level directional accuracy is 45.65%.

Contemporaneous Kalshi probability is available for all 445 research rows. Its
contract Brier is **0.0995**, event-level Brier is **0.1013**, log loss is
**0.3069**, and mean absolute calibration error is **6.80 points**. On this
population, market probability is substantially better calibrated than V3.

The untouched research holdout contains 100 contracts / 24 events:

| Method | Contract Brier | Event Brier | Calibration error | Log loss | Event accuracy |
|---|---:|---:|---:|---:|---:|
| Kalshi benchmark | 0.0924 | 0.0980 | 12.99 pp | 0.2801 | 91.67% |
| 50% market blend | 0.1317 | 0.1409 | 22.01 pp | 0.4149 | 83.33% |
| Lead-time calibration | 0.1695 | 0.1779 | 28.89 pp | 0.5088 | 62.50% |
| Contract-type calibration | 0.1724 | 0.1797 | 27.09 pp | 0.5250 | 83.33% |
| Disagreement widening | 0.1927 | 0.2002 | 27.93 pp | 0.5613 | 58.33% |
| City shrinkage | 0.2122 | 0.2214 | 40.23 pp | 0.6172 | 54.17% |
| Global shrinkage | 0.2156 | 0.2263 | 36.76 pp | 0.6287 | 54.17% |
| Conservative caps | 0.2278 | 0.2411 | 37.24 pp | 0.6807 | 54.17% |
| V3 baseline | 0.2280 | 0.2412 | 37.37 pp | 0.6814 | 54.17% |

## Main weaknesses

- **Overconfidence is broad.** In the research population, V3 probabilities of
  80–89% won 46.77% of the time (N=62), and 90–100% won 67.79% (N=149).
- **Moderate V3 probabilities are especially unreliable.** The 50–59% bucket
  won 5.0% (N=20), and the 60–69% bucket won 9.09% (N=11).
- **Large model/market disagreement is dangerous.** At 20+ percentage points
  of disagreement, Brier rises to 0.3100 (N=232), versus 0.0994 at 10–19 points
  (N=213).
- **Two-to-three-day leads are weak.** Brier is 0.2451 (N=320), versus 0.1174
  for 0–1 day (N=125).
- **Range contracts are weaker by Brier.** Range Brier is 0.2464 (N=271),
  versus threshold Brier 0.1513 (N=174). Their raw win rates differ sharply
  (46.13% versus 5.75%), so win rate alone is not a probability-quality metric.
- Among cities with N >= 10, the highest Brier is Oklahoma City (0.2486,
  N=39), followed by Denver (0.2247, N=77). San Francisco's low Brier uses only
  nine rows and is explicitly insufficient N.
- Every research row has the same 6F+ uncertainty bucket, so this export cannot
  test whether recorded sigma itself discriminates risk.

## Recommendation

The **50% contemporaneous-market blend** is the only tested model candidate
that clearly deserves prospective V3.1 shadow validation next. It reduced
holdout Brier by 42.2% versus V3 (0.2280 to 0.1317) and improved event-level
Brier from 0.2412 to 0.1409. It still underperformed the Kalshi benchmark, so it
should remain shadow-only and should not be promoted from this retrospective
test.

The shadow protocol should freeze the 50% weight in advance, record V3,
market, and blended probabilities contemporaneously, and evaluate a new
event-grouped forward cohort without retuning on these 24 holdout events.

## #43 / PR #45

The exact retrospective “otherwise eligible except stale/missing quote” count
still cannot be reconstructed because decision-time correlated-exposure state
is not stored in this export. The exact missing input is the exposure-guard
decision/snapshot for each candidate at decision time.

There are no historical JIT observations in this export. PR #45's
`v3_jit_quote_audits` instrumentation must first be deployed; future rows from
that table are the required source for JIT quote-change, missing-ask, inactive,
failure, age, latency, and other-guard-pass measurements. Quote-freshness rules
and historical classifications must remain unchanged.
