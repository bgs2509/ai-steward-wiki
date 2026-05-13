---
feature: logging-coverage-chunk2
bd_id: aisw-nrt
status: draft
date: 2026-05-13
risk: medium
evidence: strong
approach: extend-in-place
stack:
  - structlog (existing)
  - apscheduler.events (existing dependency, listener API)
  - sqlalchemy.event (existing dependency, before/after_cursor_execute)
  - asyncio (existing — perf_counter for spawn duration)
  - hashlib (stdlib — sha256 for statement_sha8)
adrs: []
links:
  - chunk-1 logging_setup.py
  - logging_events.py SSoT
---

# Logging Coverage Chunk 2 — Design

## Approach

Extend the existing logging plumbing in three thin places. No new modules, no abstractions. Each hook is a single function in the relevant module's existing file.

## 1. APScheduler lifecycle listener (M-SCHEDULER)

**File:** `src/ai_steward_wiki/scheduler/core.py`

**Add:**
```python
def _scheduler_event_listener(event: SchedulerEvent) -> None:
    # dispatch on event.code → one of the four canonical events
    ...

def attach_lifecycle_logging(scheduler: AsyncIOScheduler) -> None:
    scheduler.add_listener(
        _scheduler_event_listener,
        EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES,
    )
```

**Wiring:** `build_scheduler()` calls `attach_lifecycle_logging(scheduler)` before returning. Single listener, branch on `event.code`. Fields per branch:

| Branch | event_key | level | extra fields |
|--------|-----------|-------|--------------|
| EVENT_JOB_EXECUTED | `scheduler.job.executed` | INFO | job_id, jobstore, scheduled_run_time, duration_ms |
| EVENT_JOB_ERROR | `scheduler.job.error` | ERROR | job_id, jobstore, scheduled_run_time, exc_info=True (when event.exception present) |
| EVENT_JOB_MISSED | `scheduler.job.missed` | WARNING | job_id, jobstore, scheduled_run_time |
| EVENT_JOB_MAX_INSTANCES | `scheduler.job.max_instances` | WARNING | job_id, jobstore |

`duration_ms` for EXECUTED is computed from `(event.scheduled_run_time, datetime.now(UTC))` difference. APScheduler does not give job-execution real duration in the event object — `scheduled_run_time` is the trigger fire time, not the start time; the diff is a reasonable proxy ("wall-clock from scheduled to completion") and is documented as such.

For EVENT_JOB_ERROR, APScheduler attaches `event.exception` and `event.traceback`. We do NOT pass `exc_info=event.exception` directly into structlog's exc_info (which expects a sys.exc_info()-style tuple or True-inside-except). Instead we attach `event.traceback` (already string) as a `traceback` field. Cleaner and avoids the structlog-vs-stdlib exception serialization edge case.

## 2. SQLAlchemy slow-query listener (M-STORAGE-{JOBS,AUDIT,SESSIONS})

**Shared helper:** new file `src/ai_steward_wiki/storage/slow_query.py`

```python
def attach_slow_query_logging(engine: AsyncEngine, *, db_name: str, threshold_ms: int) -> None:
    sync_engine = engine.sync_engine
    @sqlalchemy.event.listens_for(sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        context._aisw_t0 = time.perf_counter_ns()
    @sqlalchemy.event.listens_for(sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        t0 = getattr(context, "_aisw_t0", None)
        if t0 is None:
            return
        dur_ms = (time.perf_counter_ns() - t0) // 1_000_000
        if dur_ms <= threshold_ms:
            return
        sha8 = hashlib.sha256(statement.encode("utf-8", "replace")).hexdigest()[:8]
        _LOG.warning(
            EVENT_STORAGE_SLOW_QUERY,
            db_name=db_name,
            statement_sha8=sha8,
            duration_ms=int(dur_ms),
        )
```

Each per-DB `build_engine()` in `storage/{jobs,audit,sessions}/engine.py` calls `attach_slow_query_logging(engine, db_name="jobs"|"audit"|"sessions", threshold_ms=settings.storage_slow_query_threshold_ms)` immediately before returning.

**Statement normalization:** `statement` from SQLAlchemy is already the SQL string with `?` / `:param` placeholders (parameters are separate). Hashing the raw statement string is parameter-free by construction. No further normalization needed for v0.0.1.

**Why a helper, not inlined per file:** three engine factories would duplicate ~20 lines each — KISS pulls toward a single function. Helper is `MAP_MODE: EXPORTS` with one symbol.

## 3. Claude CLI subprocess spawn/exit (M-CLASSIFIER-STAGE0 + M-WIKI-RUNNER)

**Files:** `src/ai_steward_wiki/classifier/backend.py` (AsyncioSpawner.spawn) AND `src/ai_steward_wiki/wiki/runner.py` (AsyncioSpawner.spawn).

**Pattern:** inline two log calls at the spawn-site (NOT a decorator — these are not module entrypoints; @traced is already at the call boundary above them).

```python
async def spawn(self, argv, *, env, stdin, timeout_s, cwd=None):
    t0 = time.perf_counter_ns()
    log.info(
        EVENT_CLAUDE_CLI_SPAWN,
        argv_length=len(argv),
        env_keys_count=len(env),
        cwd=cwd,
    )
    try:
        proc = await asyncio.create_subprocess_exec(...)
        stdout, stderr = await asyncio.wait_for(...)
        rc = proc.returncode or 0
        dur_ms = (time.perf_counter_ns() - t0) // 1_000_000
        if rc != 0:
            log.error(
                EVENT_CLAUDE_CLI_ERROR,
                exit_code=rc, duration_ms=int(dur_ms),
                stdout_bytes=len(stdout), stderr_bytes=len(stderr),
            )
        log.info(
            EVENT_CLAUDE_CLI_EXIT,
            exit_code=rc, duration_ms=int(dur_ms),
            stdout_bytes=len(stdout), stderr_bytes=len(stderr),
        )
        return rc, stdout, stderr
    except TimeoutError:
        # existing kill + raise; unchanged behavior. log .error before raising.
        dur_ms = (time.perf_counter_ns() - t0) // 1_000_000
        log.error(EVENT_CLAUDE_CLI_ERROR, exit_code=None, duration_ms=int(dur_ms), reason="timeout")
        proc.kill(); await proc.wait()
        raise ...
```

The error log on non-zero exit is emitted BEFORE the `.exit` info so both records show up; alternative would be branch-exclusive but `.exit` is the canonical anchor for replay and we want it always.

## 4. PII processor verification (M-OPS-PII)

No code change to `ops/pii.py` expected. New test:

`tests/unit/ops/test_pii_chunk2_fields.py` — constructs a representative event dict containing every new field from FR-1..FR-4, runs `PIIRedactor().redact_event(event_dict)`, asserts:
1. `statement_sha8`, `argv_length`, `env_keys_count`, `stdout_bytes`, `stderr_bytes`, `exit_code`, `duration_ms`, `job_id`, `jobstore`, `db_name` — unchanged.
2. A planted email in `cwd` field → MASKed (tier-2 path).
3. A planted bearer-token-shaped string in `traceback` field → tier-1 DROP.

If any new field would unexpectedly trigger redaction (e.g. an IBAN-shaped `job_id`), the test surfaces the gap; only then ops/pii.py is patched.

## 5. settings extension

```python
# storage slow-query threshold (Chunk 2)
storage_slow_query_threshold_ms: int = 200
```

Single new field. No env-var consumer changes needed (auto-loads via AISW_STORAGE_SLOW_QUERY_THRESHOLD_MS).

## 6. logging_events.py extension

```python
# APScheduler lifecycle (chunk 2)
SCHEDULER_JOB_EXECUTED: Final[str] = "scheduler.job.executed"
SCHEDULER_JOB_ERROR: Final[str] = "scheduler.job.error"
SCHEDULER_JOB_MISSED: Final[str] = "scheduler.job.missed"
SCHEDULER_JOB_MAX_INSTANCES: Final[str] = "scheduler.job.max_instances"

# Storage slow-query (chunk 2)
STORAGE_SLOW_QUERY: Final[str] = "storage.slow_query"

# Claude CLI subprocess (chunk 2)
CLAUDE_CLI_SPAWN: Final[str] = "claude_cli.spawn"
CLAUDE_CLI_EXIT: Final[str] = "claude_cli.exit"
CLAUDE_CLI_ERROR: Final[str] = "claude_cli.error"
```

Version bumped 0.0.1 → 0.0.2.

## Test strategy (TDD, RED → GREEN → REFACTOR)

| # | Test file | What it asserts |
|---|-----------|-----------------|
| T1 | `tests/unit/scheduler/test_lifecycle_logging.py` | Mock SchedulerEvent for each of the 4 codes → `_scheduler_event_listener` emits expected event keys + fields via capture_logs. |
| T2 | `tests/unit/scheduler/test_lifecycle_logging.py` | `attach_lifecycle_logging` registers the listener with the correct event-code mask. |
| T3 | `tests/unit/storage/test_slow_query_logging.py` | In-memory sqlite engine + listener attached with threshold=0 → at least one `storage.slow_query` event with correct fields. |
| T4 | `tests/unit/storage/test_slow_query_logging.py` | Threshold above measured duration → no event. |
| T5 | `tests/unit/classifier/test_spawn_logging.py` | Use a fake Spawner-replacement OR a real `python -c 'pass'` subprocess; patch the logger; assert `.spawn` and `.exit` events with correct field shapes. |
| T6 | `tests/unit/classifier/test_spawn_logging.py` | Non-zero exit (`python -c 'import sys; sys.exit(7)'`) → `.error` event with `exit_code=7`. |
| T7 | `tests/unit/wiki/test_spawn_logging.py` | Same shape as T5/T6 for wiki/runner.py:AsyncioSpawner. |
| T8 | `tests/unit/ops/test_pii_chunk2_fields.py` | PII redactor leaves metadata fields untouched; redacts free-text fields. |

T5/T6 use a real subprocess (cheap, deterministic) rather than mocking `asyncio.create_subprocess_exec` — closer to truth, no asyncio-internals coupling.

## Commit plan

1. `feat(M-FOUNDATION-LOGGING): extend logging_events catalog for chunk 2`
2. `feat(M-FOUNDATION-SETTINGS): add storage_slow_query_threshold_ms`
3. `feat(M-SCHEDULER): structured logging for APScheduler lifecycle events`
4. `feat(M-STORAGE): slow-query log listener (>200ms, sha8 only)`
5. `feat(M-CLASSIFIER-STAGE0): structured logging on claude CLI spawn/exit/error`
6. `feat(M-WIKI-RUNNER): structured logging on claude CLI spawn/exit/error`
7. `test(M-OPS-PII): chunk-2 metadata fields pass through redactor`
8. `chore(verification-plan): refresh after chunk-2 log anchors`

## Deviation note

User spec said "claude_cli/* (both files)" but `src/ai_steward_wiki/claude_cli/` physically contains only `common.py` + `__init__.py` (pure-function primitives, no subprocess spawn). The actual subprocess invocation lives in `classifier/backend.py:AsyncioSpawner` and `wiki/runner.py:AsyncioSpawner`. Logging there respects the spirit of the spec (cover claude CLI invocations). Documented in Discovery scope_in.
