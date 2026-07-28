---
name: Wouter catch-all routing in outer Switch
description: /:rest* only matches single-segment paths in regexparam v3; use (.*) for multi-segment catch-all in wouter outer Switch.
---

## Rule
In wouter v3 + regexparam v3, `/:rest*` compiles to `/^\/([^/]+?)\/?$/i` — it only
matches **one** path segment. A two-segment path like `/markets/KXTEMPCHIH-26JUL2800-T70.99`
does **not** match, so the outer `Switch` falls through and renders nothing → blank screen.

Use `(.*)` as the outer catch-all instead:
```tsx
// WRONG — only matches /dashboard, /markets (single segments)
<Route path="/:rest*" component={() => <ProtectedRoutes />} />

// CORRECT — matches /markets/:ticker and all other multi-segment paths
<Route path="(.*)" component={() => <ProtectedRoutes />} />
```

**Why:** regexparam v3 changed the `*` suffix semantics vs older versions. The `(.*)` raw
regex is the only reliable catch-all for SPA routes with multiple segments.

**How to apply:** Any wouter app that nests a Switch inside an outer catch-all Route.

## EdgeCast-specific: BASE_PATH = "/"
The EdgeCast artifact is served at root (`BASE_PATH = "/"`), so
`import.meta.env.BASE_URL = "/"` and `WouterRouter base = ""` (empty after `.replace(/\/$/, '')`).
All routes are at the root — `/markets/TICKER`, `/dashboard`, etc. There is no `/edgecast/` prefix
in actual user-facing URLs.
