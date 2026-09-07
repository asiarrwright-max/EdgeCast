# Same-day V3 counterfactual artifact

Read-only report generated from committed settled V3 artifacts.

- Generated at: `2026-09-07T00:09:56.619125+00:00`
- Exact same-day supported: `False`

## Blocker

- Missing field(s): `lead_time_days`
- Why blocked: The committed settled_v3_main_cohort artifact preserves only the coarse 0-1d / 2-3d / 4-7d lead bucket, so it cannot separate exact same-day rows from next-day rows inside 0-1d.
- Minimum additional input: Re-run from the complete settled V3 export with lead_time_days preserved for each row.

## Available fallback context

- Cohort: **0-1d proxy (not exact same-day)**
- Rows available: **193**
- RESEARCH_ONLY rows available: **125**

This fallback uses the committed `0-1d` bucket only and must not be interpreted as an exact same-day result.
