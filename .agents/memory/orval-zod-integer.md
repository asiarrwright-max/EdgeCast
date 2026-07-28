---
name: Orval generates zod.int() for integer types — breaks Zod v3
description: OpenAPI type:integer causes Orval to emit zod.int() which doesn't exist in Zod v3 (3.x); use type:number instead.
---

Orval v8 generates `z.int()` for OpenAPI fields declared as `type: integer`. `z.int()` does not exist in Zod v3 (3.25.x) — only in Zod v4+.

**Why:** The workspace uses `zod@3.25.76`. Running codegen with any `type: integer` field will produce generated files that fail TypeScript compilation with "Property 'int' does not exist on type 'ZodType'".

**Fix:** In `lib/api-spec/openapi.yaml`, use `type: number` everywhere you would use `type: integer`. This affects schema fields (ids, counts, limits) and query params. The frontend treats them as `number` in TypeScript, which is fine for all use cases here.

**How to apply:** Before running codegen, grep the spec for `type: integer` and replace with `type: number`.
