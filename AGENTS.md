# EdgeCast AI Engineering Policy

## Purpose

EdgeCast is a weather-market forecasting and paper-trading decision-support system. This file defines what AI coding agents may change autonomously, what requires owner approval, and what must never be activated automatically.

The goal is to reduce human relay work while preserving experimental integrity, settlement correctness, and safety.

## Core rules

1. Preserve forward-test integrity before optimizing performance.
2. Never fabricate market prices, forecasts, observations, settlement results, timestamps, liquidity, or performance.
3. Treat settlement source, station identity, quote freshness, and data lineage as correctness-critical.
4. Do not silently change which trades count as OFFICIAL.
5. Keep OFFICIAL, RESEARCH_ONLY, and LEGACY populations separate.
6. Prefer a transparent skip/failure over a guessed value.
7. Autonomous changes must be testable, reversible, and auditable.
8. EdgeCast remains paper-trading only. Never add or activate real-money order placement.
9. Follow `COLLABORATION.md`: use feature branches and pull requests; never push feature work directly to `main`.
10. Scheduled maintenance may detect, classify, create/update issues, close recovered health issues, and dispatch approved GREEN issues without owner interaction. It may also dispatch a YELLOW issue after the repository owner has explicitly approved that specific issue with `owner-approved-yellow`; that dispatch is limited to the approved scope and does not authorize merge, deploy, broader protected behavior, or real-money execution.

## Change authority

### GREEN — may fix autonomously

Agents may diagnose, implement, test, and prepare a PR for these changes when the intended behavior is clear and forecasting/eligibility semantics are unchanged:

- UI rendering, responsive layout, copy, labels, formatting, and broken links.
- Null handling and defensive error handling.
- Logging, health checks, diagnostics, audit output, and observability.
- Performance improvements proven to preserve outputs.
- Removing already-expired markets before eligibility evaluation.
- Clearly accidental duplicate ingestion.
- API parsing repairs when upstream semantics are unchanged.
- Retry/backoff for transient upstream failures.
- Tests for existing intended behavior.
- Type/lint errors that do not alter business logic.
- Refactors proven by tests to preserve behavior.
- Stale UI counters/caches when the source of truth is unambiguous.

GREEN workflow: reproduce root cause, make the smallest safe change, add/update tests, run validation, document impact, and open a PR. A GREEN cloud agent may be assigned automatically, but it may not merge or deploy.

### YELLOW — investigate and prepare, but require owner approval before behavioral activation

This includes:

- Forecast probability calculation.
- Calibration or probability tuning.
- City-specific bias correction.
- Ensemble/model weighting or forecast-source substitution.
- Confidence scoring or edge calculation.
- Market ranking/opportunity scoring methodology.
- Quote freshness thresholds, including the 300-second gate.
- Minimum liquidity/available-quantity rules.
- Adding/removing cities from the verified/eligible set.
- Station mapping or settlement-station changes.
- Settlement-source transition logic.
- Reclassification among OFFICIAL, RESEARCH_ONLY, and LEGACY.
- Forward-test cohort definitions/start dates.
- Changes that alter historical performance metrics.
- Automated calibration using newly settled trades.

Agents may gather evidence, run read-only analysis/simulations, add tests, and prepare a PR, but must clearly state expected impact and wait for owner approval before merge/deploy. When the owner explicitly approves a specific YELLOW issue by applying `owner-approved-yellow`, a cloud agent may be assigned automatically to implement and test only the scope that was approved and prepare a PR. That issue-level approval does not authorize a new protected decision discovered during implementation, does not authorize broader forecasting/calibration/eligibility/settlement/recommendation/readiness-threshold changes, and does not authorize merge, deploy, or real-money execution. Any newly discovered protected decision must return to the owner for a separate approval. The deterministic PR gate may still require the `owner-approved-yellow` label on the resulting YELLOW PR before allowing it to pass.

### RED — never autonomously activate

Explicit owner approval is always required, and real-money execution remains prohibited:

- Real-money Kalshi order placement.
- Automatic betting/trading.
- Bankroll or automatic stake sizing.
- Financial-risk-limit changes.
- Converting preliminary/watch signals directly into executable wagers.
- Disabling safeguards to increase trade count.
- Backdating, rewriting, or deleting OFFICIAL records to improve performance.
- Changing settled outcomes to match a model or external dataset.
- Fabricating missing prices, quotes, observations, or settlement data.
- Lowering integrity gates solely because too few OFFICIAL trades are being generated.
- Optimizing rules against already-observed forward-test outcomes without an explicit new experiment boundary.

RED issues must not be automatically dispatched to a coding agent. RED PR signatures must fail the automated risk gate. Real-money execution is not made permissible by owner approval; it remains prohibited by project policy.

## Autonomous label semantics

- `green-candidate`: scheduled maintenance believes the issue may be routine engineering maintenance.
- `risk-green`: approved for autonomous diagnosis/repair preparation under the GREEN rules.
- `agent-ready`: may be handed to an approved cloud coding agent.
- `risk-yellow`: owner approval is required before protected behavior can merge or activate.
- `risk-red`: autonomous implementation/activation is blocked.
- `owner-approval-required`: owner must review before protected behavior can proceed.
- `owner-approved-yellow`: explicit repository-owner approval for the specific YELLOW issue or PR after review. On an issue, it permits scoped agent implementation/testing and PR preparation only; it does not by itself authorize merge/deploy or any broader protected behavior.
- `safety-block`: integrity/safety failure; do not auto-repair, auto-merge, or deploy.

Labels are routing aids, not permission to ignore this policy. If the actual required fix is more sensitive than its incoming label, classify upward immediately.

## OFFICIAL forward-test integrity

OFFICIAL results are append-only experimental evidence except for a proven data-integrity correction. Any correction must retain an audit trail with original value, corrected value, reason, evidence, timestamp, and code version.

Never mix RESEARCH_ONLY or LEGACY trades into OFFICIAL readiness, win rate, ROI, Brier score, calibration, or official performance metrics.

A lack of OFFICIAL trades is a valid result. Do not weaken rules merely to create more observations.

## Market and quote integrity

A candidate must not be treated as executable/paper-eligible when its required Kalshi quote is absent, invalid, stale under the approved rule, or associated with an expired/inactive contract.

Never infer or fabricate a price from model probability, neighboring contracts, previous scans, or other markets.

Expired contracts may be filtered before paper-trade eligibility evaluation when that preserves the approved eligibility definition.

## Settlement integrity

Settlement must follow the source and station specified by the relevant market rules for the applicable contract date.

When Kalshi changes settlement methodology/source, treat that as a versioned rule boundary. Do not retroactively apply a newer settlement regime to older contracts unless official market rules require it.

ERA5 and similar external datasets may be used for diagnostics/research, but disagreement alone is not grounds to overwrite a Kalshi settlement. Verify against the contract's official settlement source/station first.

## Verified cities

Production-facing recommendations should use only cities whose market-to-settlement-station mapping has been explicitly verified under current rules.

Agents may investigate additional cities but must not promote one into the verified set without evidence and owner approval.

## Bet Watch / recommendation layer

Bet Watch must communicate plainly and distinguish among research signal, preliminary/watch signal, eligible paper-trade signal, and validated readiness.

It must not imply real-money readiness before the defined evidence thresholds are met.

Recommendations should expose the evidence behind ranking, including model probability, market price when available, edge, quote freshness, market status, city verification status, and model/readiness confidence.

## Autonomous repair workflow

1. Reproduce/verify the issue from source data, logs, and code.
2. Classify the required repair as GREEN, YELLOW, or RED before editing behavior.
3. GREEN: implement the smallest safe repair, add/update tests, run validation, and open a PR.
4. YELLOW: investigate and prepare evidence/patches, and require explicit owner approval before protected behavioral activation. After the owner explicitly approves the specific issue, an agent may implement/test only that approved scope and prepare a PR; newly discovered protected decisions require a new owner approval.
5. RED: stop before activation and require explicit owner approval where project policy allows a next step. Real-money execution remains prohibited.
6. Never hide failed tests or unresolved integrity concerns.
7. A scheduled health workflow may automatically open/update/close engineering issues.
8. A cloud agent may be automatically assigned to GREEN work, or to a YELLOW issue only after explicit repository-owner approval is recorded for that specific issue. Approved YELLOW assignment is limited to the approved scope and PR preparation; if the work expands beyond that scope or reaches a RED boundary, the agent must stop and escalate.
9. No autonomous workflow or coding agent may merge or deploy a PR.

## Validation expectations

Before a code change is complete, run applicable automated checks including backend tests, frontend/type checks, production build checks, and targeted regression tests.

If pre-existing failures exist, distinguish them from failures introduced by the change.

A successful build alone does not prove forecasting correctness. Data/decision changes require domain-level assertions where practical.

## Auditability

Material changes should preserve enough information to reconstruct what changed, why, initiator, commit/version, tests performed, OFFICIAL impact, and whether owner approval was required/obtained.

## Default rule when uncertain

If a change could alter forecasts, probabilities, calibration, settlement, eligibility, OFFICIAL evidence, financial risk, or the meaning of a trade, classify it upward and ask for approval.

Autonomy is encouraged for engineering maintenance. Experimental integrity and safety take priority over autonomy.
