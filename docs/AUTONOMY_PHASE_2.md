# EdgeCast Autonomy Phase 2

## Goal

EdgeCast should be able to monitor its repository health and create actionable engineering work while the EdgeCast UI and ChatGPT are both closed.

Phase 2 moves the monitoring/triage loop into GitHub Actions, which runs in GitHub's cloud infrastructure.

## What now runs without the app or ChatGPT open

`.github/workflows/autonomous-maintenance.yml` runs:

- every 6 hours,
- after a push to `main`, and
- manually through GitHub Actions when needed.

It checks:

1. backend test suite,
2. workspace dependency installation,
3. TypeScript/type checks,
4. production EdgeCast frontend build,
5. tracked secret-like files, and
6. obvious real-money order-placement code paths.

## Automatic triage behavior

If every check passes, the workflow records a successful run. If a prior autonomous health issue is still open, the workflow adds a recovery record and closes it.

If a check fails, the workflow creates or updates one deduplicated GitHub issue:

`[AUTO] EdgeCast repository health failure`

The issue contains:

- the commit SHA,
- the failed workflow run,
- each check's result,
- an autonomous safety classification, and
- an agent handoff that points back to `AGENTS.md`.

Routine engineering failures are labeled `green-candidate` for investigation. Secret/integrity guard failures receive `safety-block` and must not be auto-repaired or auto-merged.

## What Phase 2 deliberately does NOT do yet

Phase 2 does not silently merge, deploy, or change forecasting/trading behavior.

It also does not yet invoke a cloud coding agent automatically. The workflow is agent-ready: a connected GitHub Copilot coding agent or other approved cloud coding agent can later be attached to GREEN candidate issues so it can create a repair branch/PR without ChatGPT being open.

That future agent must still obey `AGENTS.md`:

- GREEN: investigate, repair, test, and prepare a PR.
- YELLOW: investigate/prepare but wait for owner approval before behavioral activation.
- RED: never activate automatically; real-money execution remains prohibited.

## Why this design

Monitoring and diagnosis should be autonomous, but experimental integrity must not depend on an AI deciding that a forecasting, settlement, eligibility, or financial safeguard is inconvenient.

The safe target state is:

`scheduled health check -> GitHub issue -> cloud coding agent -> tested branch/PR -> CI -> owner only when approval is required`

No phone, browser tab, EdgeCast UI, or ChatGPT session needs to remain open for the scheduled health/triage portion.
