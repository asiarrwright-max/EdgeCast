# Settled V3 Accuracy Lab evidence status

Status: **COMPLETE PRODUCTION EXPORT ANALYZED.**

The prior one-row OFFICIAL run remains rejected as incomplete. The authoritative
555-row production CSV was verified: every row is SETTLED and v3.0, with 1
OFFICIAL, 445 RESEARCH_ONLY, and 109 UNCLASSIFIED rows.

Measured results and reproducible machine-readable outputs are in
[`settled_v3_complete/`](settled_v3_complete/). The principal RESEARCH_ONLY
population has N=445 / 92 events, V3 Brier 0.2092, event Brier 0.2117, and
calibration error 36.58pp. The Kalshi benchmark has Brier 0.0995 and event Brier
0.1013. On the untouched holdout (N=100 / 24 events), V3 Brier is 0.2280,
Kalshi is 0.0924, and the 50% market-blend candidate is 0.1317.

The recommendation is prospective shadow validation of the frozen 50% market
blend only. This branch does not activate or change forecasting, calibration,
eligibility, settlement, evidence classification, readiness, quote freshness,
historical outcomes, or execution behavior.

## Remaining #43 / PR #45 limitation

The exact retrospective “otherwise eligible except stale/missing quote” count
still needs the decision-time correlated-exposure guard result or exposure
snapshot, which is not stored in this CSV. Production JIT observations also
cannot exist until PR #45's `v3_jit_quote_audits` instrumentation is deployed.
Historical rows must not be backfilled or reclassified.
