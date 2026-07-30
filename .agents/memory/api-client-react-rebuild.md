---
name: api-client-react rebuild requirement
description: Adding a new .ts file to lib/api-client-react/src/ requires running tsc --build in that package before dependent apps see the new exports.
---

`lib/api-client-react` uses TypeScript composite project references (`"composite": true`, `"emitDeclarationOnly": true`). The `dist/` directory must be regenerated after adding or changing source files.

**Why:** The edgecast app resolves types from `dist/`, not directly from `src/`. If `dist/` is stale, `tsc --noEmit` in edgecast reports "has no exported member" even though the source is correct.

**How to apply:** After editing any file in `lib/api-client-react/src/`, run:
```
cd /home/runner/workspace/lib/api-client-react && pnpm tsc --build
```
Then re-run `pnpm tsc --noEmit` in `artifacts/edgecast` to verify.
