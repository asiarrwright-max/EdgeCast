---
name: JSX files must use .tsx extension; Vite caches old resolution
description: Files with JSX must be .tsx; after rename, Vite keeps cached .ts resolution until workflow restart.
---

Vite/esbuild reject JSX syntax (`<>...</>`, `<Component />`) in `.ts` files — they must use `.tsx`.

**Why:** The design subagent may create a `lib/auth.ts` (or similar) containing React components/fragments. The `.ts` extension causes a build error immediately.

**Fix:** Rename to `.tsx`. Since imports use bare paths (`@/lib/auth`), no import strings need updating — TypeScript and Vite resolution handles it.

**Caveat:** After renaming, Vite may still have the old `.ts` path cached. The fix is to restart the edgecast workflow — Vite's module resolution cache is cleared on server restart.
