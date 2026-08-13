## Summary

Describe what changed and why.

## Change classification

- [ ] GREEN — engineering maintenance; no forecasting/eligibility semantics changed
- [ ] YELLOW — model/data/eligibility behavior; owner approval required before merge/deploy
- [ ] RED — financial/integrity boundary; explicit owner approval required and real-money execution remains prohibited

## Safety impact

- [ ] No real-money order placement added or enabled
- [ ] OFFICIAL / RESEARCH_ONLY / LEGACY populations remain correctly separated
- [ ] No historical settled outcomes were rewritten
- [ ] Quote freshness rules are unchanged, or any proposed change is explicitly called out
- [ ] Settlement source/station logic is unchanged, or any proposed change is explicitly called out
- [ ] No secrets or credentials are included

## Validation

- [ ] Backend: `cd artifacts/api-server && python -m pytest tests/ -q`
- [ ] TypeScript: `pnpm typecheck`
- [ ] Build: `pnpm build`
- [ ] Relevant targeted regression tests added/updated

## Owner approval

Required before merge for YELLOW or RED changes.

- Approval status: `NOT REQUIRED / PENDING / APPROVED`
