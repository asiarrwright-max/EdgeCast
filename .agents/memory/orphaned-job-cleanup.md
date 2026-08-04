---
name: Orphaned job cleanup on startup
description: How to handle job_runs rows stuck in 'running' state after a deployment restart kills a mid-flight collection job.
---

## The problem
The `/jobs/collect` endpoint checks `job_runs WHERE status='running'` before starting a new job — if any row is stuck in "running" (because the previous process was killed mid-job during a deployment), it returns that stale row instead of starting a fresh collection. The in-memory `asyncio.Lock()` resets on each new process, but the DB rows persist.

## The fix
`_cleanup_orphaned_jobs(session)` runs inside `init_db()` (after `_enable_required_flags`) on every startup. It selects all `status='running'` rows and marks them `status='failed'` with an explanatory error_message. This is idempotent and safe — a new process starts with a clean slate.

**Why:** Deployments on Replit send SIGTERM/SIGKILL to the running process. Any long-running background task (collection takes ~300s) will be killed mid-flight, leaving the job_run row stuck in "running" forever.

**How to apply:** Any time you add a long-running background job tracked in a DB table with a status field, add a startup cleanup for orphaned rows — otherwise deployments will permanently block future manual triggers.
