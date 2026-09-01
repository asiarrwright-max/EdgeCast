# V3.1 prospective shadow-validation protocol

## Frozen candidate

The candidate is frozen before prospective collection as:

`0.50 × V3 chosen-side probability + 0.50 × contemporaneous Kalshi chosen-side executable price`

The version key is `v31-shadow-pr49-50v3-50market-v1`. The weight is not
retuned from the untouched historical holdout or from incoming forward data.
A different formula or weight requires a new versioned cohort.

## Prospective cohort boundary

Only new `v3_paper_trades` created after this instrumentation is deployed are
recorded. There is no historical backfill. Each record freezes the source V3
and market probabilities, blend, disagreement, city/station, contract type,
target date, lead time, direction, evidence class, and eligibility reason at
decision time. Correlated contracts share the same
`city|target-date|weather-variable` event key used by PR #49.

Settlement outcome remains authoritative in the linked `v3_paper_trades` row.
The read-only report joins the prospective observation to that eventual
outcome by immutable trade ID.

## Evidence separation and milestones

`OFFICIAL`, `RESEARCH_ONLY`, and `UNCLASSIFIED` are always reported as three
separate populations. The event count, rather than correlated contract count,
governs the interpretation milestones:

- 25 settled events: minimum initial comparative signal
- 50 settled events: intermediate stability check
- 100 settled events: stronger forward evidence

These are shadow evidence labels only and do not alter EdgeCast readiness
semantics. Below 25 settled events, the endpoint explicitly marks the sample
too small for a comparative conclusion.

## Progress endpoint

Authenticated `GET /api/analytics/v3/v31-shadow-validation` reports cumulative
N, event N, wins/losses, Brier score, event-level Brier score, calibration,
log loss, and event directional accuracy for V3, the frozen blend, and Kalshi.

## JIT quote instrumentation coordination

This track is stacked on PR #45. PR #45 independently performs a read-only JIT
fetch only for stale/missing-quote diagnostics. The V3.1 shadow cohort uses the
already-stored contemporaneous chosen-side price from the unchanged V3
decision path. It does not duplicate the JIT fetch, consume a JIT result, or
change the approved quote-freshness requirement. PR #45 must land before this
stacked change, or this branch can be rebased after #45 lands.

## Safety boundary

The shadow table and report are not read by forecasting, probability display,
eligibility, recommendations, entry pricing, settlement, readiness, bankroll,
or execution logic. Shadow persistence occurs in an isolated transaction only
after the source V3 paper-trade transaction commits, and failures are swallowed
after logging. No real-money order capability exists.
