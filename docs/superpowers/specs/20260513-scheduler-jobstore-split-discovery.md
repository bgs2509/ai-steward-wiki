---
feature: scheduler-jobstore-split
bd_id: aisw-6mi
module_id: M-SCHEDULER
status: stable
date: 2026-05-13
fr:
  - FR-1: Boot the bot without AttributeError from SQLAlchemyJobStore pickling
  - FR-2: Maintenance/retention/snapshot/media-sweep jobs run in a non-persistent (in-memory) jobstore
  - FR-3: User reminder jobs continue to persist in jobs.db (SQLAlchemyJobStore default)
  - FR-4: Idempotent registration on every boot via replace_existing=True (unchanged)
  - FR-5: One-time cleanup at boot — orphaned maintenance rows already in jobs.db are removed before re-registering into memory store
  - FR-6: Smoke test registers all maintenance jobs on an AsyncIOScheduler and asserts no pickle error
nfr:
  - NFR-1: No change to user-facing reminder behaviour or durability
  - NFR-2: structlog event scheduler.bootstrap.legacy_maintenance_purged with count
  - NFR-3: mypy --strict + ruff + grace lint clean
constraints:
  - APScheduler 3.x semantics; jobstore alias used at add_job time
  - SQLAlchemyJobStore must keep sync URL; MemoryJobStore takes no URL
risks:
  - Leftover maintenance rows in jobs.db block re-registration with same id in a different store → mitigated by FR-5 one-time cleanup
  - Future code paths that add maintenance jobs without jobstore="memory" silently regress → mitigated by smoke test
scope_in:
  - src/ai_steward_wiki/scheduler/core.py (build_scheduler — add memory jobstore)
  - src/ai_steward_wiki/scheduler/maintenance.py (purge_expired_pending + media sweep → memory)
  - src/ai_steward_wiki/ops/retention.py (register_retention_jobs → memory)
  - src/ai_steward_wiki/ops/snapshot.py (register_db_snapshot_job → memory)
  - src/ai_steward_wiki/__main__.py (boot-time legacy cleanup, after build_scheduler before register)
  - tests/unit/scheduler/test_maintenance_jobstore.py (new smoke test)
scope_out:
  - Refactor of run_purge to drop sessionmaker dependency
  - Migration of any user reminder logic
---

# Discovery: scheduler maintenance jobstore split (aisw-6mi)

## Symptom

```
AttributeError: Can't get local object 'create_engine.<locals>.connect'
```

at boot, in `apscheduler/jobstores/sqlalchemy.py: add_job → pickle.dumps(job.__getstate__())`.

## Root cause

`SQLAlchemyJobStore` persists `job.args/kwargs` via `pickle`. Maintenance registrars pass `async_sessionmaker` (and dicts of them) as kwargs/args:

- `scheduler/maintenance.py:149` — `args=[session_maker, ttl_days]`
- `ops/retention.py:271-279` — `kwargs={"db_makers": ..., "audit_maker": ...}`

`async_sessionmaker` holds a reference to an SQLAlchemy `Engine`, whose `connect` is a local closure inside `create_engine()` → not picklable.

## Why split jobstores (not "fix pickling")

Architecturally these are **infra cron jobs**: registered on every boot with `replace_existing=True`, owned by lifecycle, not by user data. They have no business in a durable jobstore. Splitting cleanly:
- removes pickle dependency for all maintenance args
- prevents stale jobstore rows from referencing dead engines after restart
- keeps `jobs.db` SSoT for user reminders only (matches D-022/D-030 intent)

## Open Questions
None — design fixed via `/best-option` (option 1, 88%).
