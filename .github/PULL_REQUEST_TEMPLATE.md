## EdgeCast change summary

Describe what changed and why.

## Autonomous classification

- [ ] GREEN — engineering maintenance only; no forecasting/eligibility/settlement semantics changed
- [ ] YELLOW — behavioral/model/eligibility/settlement change; owner approval required
- [ ] RED — financial/integrity critical; never autonomously activate

## Safety and integrity checklist

- [ ] I read `AGENTS.md` before making this change.
- [ ] No real-money Kalshi order placement or automatic wagering was added.
- [ ] OFFICIAL, RESEARCH_ONLY, and LEGACY evidence populations remain correctly separated.
- [ ] The 300-second quote freshness rule is unchanged, or any proposed change is explicitly classified YELLOW and owner-gated.
- [ ] Settlement source/station/regime behavior is unchanged, or any proposed change is explicitly classified YELLOW and owner-gated.
- [ ] No OFFICIAL historical records were silently rewritten/deleted.
- [ ] No safety/test gate was weakened merely to make validation pass.
- [ ] No credential, secret, token, private key, or connection string was committed.

## Validation

- [ ] Backend tests pass.
- [ ] Workspace TypeScript/type checks pass.
- [ ] Production EdgeCast build passes.
- [ ] Safety guard scan passes.

## Autonomous-maintenance impact

- [ ] This change does not disable or bypass `.github/workflows/autonomous-maintenance.yml`.
- [ ] If this PR fixes an `edgecast-auto` health issue, the issue/run is linked below.

Related issue/run:
