---
feature: scheduler-jobstore-split
bd_id: aisw-6mi
module_id: M-SCHEDULER
status: stable
date: 2026-05-13
stack:
  - library: apscheduler
    version: 3.x (current uv.lock)
    used_for: AsyncIOScheduler + MemoryJobStore + SQLAlchemyJobStore
decisions:
  - D-local-1: Two named jobstores — "default" = SQLAlchemyJobStore (jobs.db, user reminders), "memory" = MemoryJobStore (infra cron)
  - D-local-2: All maintenance registrars pass jobstore="memory" to add_job
  - D-local-3: On boot, after build_scheduler and before register_all_retention_jobs, run a one-time cleanup that removes known maintenance job ids from the default (SQLAlchemy) jobstore
  - D-local-4: Cleanup pattern — exact id allowlist (PURGE_PENDING_JOB_ID, MEDIA_STAGING_SWEEP_JOB_ID, DB_SNAPSHOT_JOB_ID, retention.* via prefix on remove_all_jobs filter), not blanket purge of default store
---

# Design: scheduler maintenance jobstore split

## Approach

```python
# scheduler/core.py
jobstores = {
    "default": SQLAlchemyJobStore(url=jobs_db_sync_url, tablename=table_name),
    "memory":  MemoryJobStore(),
}
```

Each maintenance registrar adds `jobstore="memory"` to its `add_job` call. No other behaviour changes.

## Boot-time cleanup

Runs once at startup, before maintenance jobs are re-registered. Uses APScheduler's `remove_job(job_id, jobstore="default")` for each known id, swallowing `JobLookupError`. For retention jobs (variable count from `RETENTION_POLICIES`), iterate `scheduler.get_jobs(jobstore="default")` and remove ids matching the maintenance prefix set.

Why id-allowlist (not "purge default"): user reminder jobs live in default; a blanket purge would destroy them. Allowlist is explicit and safe.

## Test strategy

Single unit smoke test: build an AsyncIOScheduler with both jobstores using a tmp sqlite path, call `register_all_retention_jobs(...)` with real sessionmakers (in-memory engines), assert all jobs registered and `scheduler.get_jobs(jobstore="memory")` is non-empty. No actual job execution — registration is the failure mode we guard against.
