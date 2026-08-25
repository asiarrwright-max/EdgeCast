# EdgeCast Autonomy Phase 3 — Cloud Repair + GREEN/YELLOW/RED Enforcement

## Goal

Move EdgeCast from autonomous monitoring/triage to autonomous **GREEN repair preparation** while keeping model, settlement, eligibility, OFFICIAL evidence, financial-risk, and real-money boundaries owner-gated.

The intended always-on loop is:

`schedule -> health failure -> issue -> RGY route -> GREEN cloud agent -> tested PR -> RGY PR gate -> owner only when needed`

The EdgeCast UI and ChatGPT do not need to be open for this flow.

## GREEN

GREEN is routine engineering maintenance that clearly preserves prediction, eligibility, settlement, and OFFICIAL evidence semantics.

Examples include UI/type/build defects, logging/diagnostics, defensive null handling, clearly broken API parsing with unchanged upstream meaning, transient retry handling, behavior-preserving refactors, and tests for existing intended behavior.

A GREEN issue can be labeled `green-candidate`. `.github/workflows/agent-dispatch.yml` converts that into `risk-green` + `agent-ready` and, when cloud-agent credentials are configured, requests assignment to GitHub Copilot cloud agent.
The workflow now fails closed: it only reports `GREEN — CLOUD REPAIR DISPATCHED` after a verifiable post-condition (assignee/implementation-PR signal) is observed; otherwise it reports `DISPATCH REQUESTED — NOT CONFIRMED` or `DISPATCH FAILED / STALLED` for retry/system attention.

The agent receives instructions to read `AGENTS.md` and `COLLABORATION.md`, make the smallest safe repair, run validation, and open a PR. It must not merge or deploy.

## YELLOW

YELLOW covers changes that could alter the experiment or recommendation behavior, including model probability, calibration, confidence/edge, verified cities, quote freshness, liquidity gates, eligibility, settlement source/station, settlement regimes, OFFICIAL/RESEARCH/LEGACY definitions, forward-test cohorts, or historical performance metrics.

YELLOW work may be investigated and a patch may be prepared, but behavioral activation requires explicit owner approval.

The PR risk gate labels these PRs `risk-yellow` and `owner-approval-required` and fails the gate until the repository owner explicitly applies `owner-approved-yellow` after review.

## RED

RED covers prohibited or integrity-critical behavior such as real-money order placement, automatic betting, bankroll/stake automation, fabricated data, rewriting OFFICIAL evidence for performance, changing settled outcomes without verified evidence, or weakening safeguards simply to increase trade volume.

RED issues are not automatically assigned to a coding agent. RED PR signatures fail the RGY risk gate. Real-money execution remains permanently prohibited.

## Deterministic PR safety gate

`.github/workflows/pr-risk-gate.yml` runs on PRs targeting `main`.

It applies a conservative deterministic classification based on changed paths and RED signatures:

- `risk-green`: no protected path/signature detected.
- `risk-yellow`: protected model/trading/settlement/eligibility-style path changed; owner approval required.
- `risk-red`: secret-like tracked file or possible real-money order-placement signature; autonomous merge is blocked.

This gate is intentionally conservative. A GREEN label is not proof of forecasting correctness; normal CI and code review still apply. When uncertain, classify upward.

## Custom GREEN repair agent

`.github/agents/edgecast-green-repair.agent.md` defines the repository-specific cloud agent behavior. It is explicitly instructed to stop and escalate if a seemingly routine issue actually requires YELLOW or RED changes.

## Cloud-agent credential activation

GitHub supports assigning issues to Copilot cloud agent through the issues API. The Phase 3 dispatcher is prepared to do this automatically using a repository Actions secret named:

`COPILOT_AGENT_TOKEN`

The token is deliberately **not** stored in the repository. It must be configured as a GitHub Actions secret after Copilot cloud agent is enabled for the repository.

Recommended permissions for a fine-grained user token used for Copilot assignment are the minimum GitHub requires for the assignment API: repository metadata read plus read/write access needed for actions, contents, issues, and pull requests. Follow current GitHub documentation because the Copilot assignment API is a preview feature and may change.

If the secret is absent, Phase 3 fails safe: GREEN issues receive `agent-ready` and `agent-token-needed`, no AI repair is invoked, and no unsafe fallback occurs.

## No automatic merge or deployment

Phase 3 does not auto-merge or auto-deploy any PR.

- GREEN: agent may prepare a tested PR.
- YELLOW: owner approval required before merge/activation.
- RED: autonomous implementation/activation blocked; real-money behavior prohibited.

## What remains for full activation

After this PR is merged to `main`:

1. Enable a paid GitHub Copilot plan/cloud agent for the repository.
2. Add `COPILOT_AGENT_TOKEN` as a repository Actions secret if using the API dispatcher.
3. Run `EdgeCast Agent Dispatch` manually against a harmless GREEN test issue to verify cloud-agent assignment.
4. Confirm the resulting agent PR is classified by `EdgeCast RGY Risk Gate` and standard `EdgeCast CI` passes.
5. Keep auto-merge and automated deployment disabled until a separate, explicitly approved phase.
