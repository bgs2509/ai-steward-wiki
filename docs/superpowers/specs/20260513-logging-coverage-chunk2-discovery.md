---
feature: logging-coverage-chunk2
bd_id: aisw-nrt
module_id: M-FOUNDATION-LOGGING
status: draft
date: 2026-05-13
risk: medium
evidence: strong
fr:
  - FR-1: scheduler/core.py registers a single APScheduler listener on the AsyncIOScheduler returned by build_scheduler() that handles EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED, EVENT_JOB_MAX_INSTANCES and emits stable structlog events scheduler.job.executed / scheduler.job.error / scheduler.job.missed / scheduler.job.max_instances with fields job_id, jobstore (alias), scheduled_run_time (ISO-8601 UTC string), duration_ms (executed branch only — derived from event.scheduled_run_time vs event-end timestamp).
  - FR-2: The .error branch logs at ERROR with exc_info=True (so structlog format_exc_info renders the traceback); the other three branches log at WARNING (missed, max_instances) or INFO (executed). No job kwargs, no job return value, no exception message text in fields — only standard event metadata.
  - FR-3: storage event listeners attach before_cursor_execute / after_cursor_execute on each AsyncEngine returned by build_engine() in storage/{jobs,audit,sessions}/engine.py. When duration_ms > settings.storage_slow_query_threshold_ms (default 200), emit storage.slow_query at WARNING with fields db_name ("jobs"|"audit"|"sessions"), statement_sha8 (sha256[:8] hex of the normalized SQL statement template — parameters stripped via sqlalchemy's compiled str OR raw statement text without parameters), duration_ms (int). NEVER log raw SQL, never log parameter values.
  - FR-4: Both AsyncioSpawner.spawn implementations (classifier/backend.py and wiki/runner.py — the two physical claude CLI subprocess invocation points) emit claude_cli.spawn at INFO before subprocess creation with fields argv_length (len(argv)), env_keys_count (len(env)), cwd (string or None), and emit claude_cli.exit at INFO on normal completion with fields exit_code (int), duration_ms (int), stdout_bytes (len(stdout)), stderr_bytes (len(stderr)). On non-zero exit also emit claude_cli.error at ERROR with the same fields plus exc_info if available. NEVER log argv items, env values, env keys, stdin/stdout/stderr content.
  - FR-5: logging_events.py SSoT catalog is extended with new Final[str] constants for every event key introduced in FR-1..FR-4. Module map is updated; CHANGE_SUMMARY bumped to v0.0.2.
  - FR-6: ops/pii.py is reviewed for new fields introduced in Chunk 2 (statement_sha8, argv_length, env_keys_count, stdout_bytes, stderr_bytes, exit_code, duration_ms, job_id, jobstore, scheduled_run_time, db_name, cwd). All are metadata-only — confirmed NOT in tier-1 DROP / tier-2 MASK redaction surface. A unit test asserts a representative Chunk-2 record passes through PIIRedactor.redact_event without altering these fields, AND that any user-supplied free-text field on the same record (e.g. a fake cwd containing an email) IS redacted.
  - FR-7: docs/verification-plan.xml is regenerated via `grace-refresh --verify` after code lands; Chunk-1 + Chunk-2 log anchors are cross-referenced with semantic BLOCK markers (LDD evidence rule). XML commit is separate from code commits.
nfr:
  - NFR-1: PII safety — bytes-counts and sha8 only. No raw SQL, no parameter values, no argv items, no env values, no stdin/stdout/stderr content, no exception message strings outside of exc_info-rendered traceback.
  - NFR-2: All new event keys are bounded constants from logging_events.py; high-cardinality values (job_id, statement_sha8) ride as structured fields.
  - NFR-3: mypy --strict on src/ stays clean. make lint stays clean. Pre-existing mypy error in tg/handlers.py (out-of-scope chunk-1 carryover) untouched.
  - NFR-4: APScheduler listener overhead < 1 ms per job-end on local dev machine (no benchmark gate; sanity only). SQLAlchemy listener overhead negligible when below threshold (single perf_counter pair + comparison).
  - NFR-5: Slow-query threshold is configurable via settings (settings.storage_slow_query_threshold_ms: int = 200) so prod can tune without code changes (Fail-Fast on settings load via pydantic int constraint).
  - NFR-6: Tests use structlog.testing.capture_logs + AsyncMock/MagicMock for APScheduler events and a real engine for storage event listeners. Tests assert event key + presence of fields, do NOT assert absolute duration values.
risks:
  - R-1 (low): APScheduler event payload fields differ between minor versions — pin call sites to documented public attrs (job_id, jobstore, scheduled_run_time, exception, traceback). Verified via apscheduler 3.x SchedulerEvent docs in design phase.
  - R-2 (low): SQLAlchemy connection_record / context API changes — use the documented before_cursor_execute / after_cursor_execute signature (conn, cursor, statement, parameters, context, executemany), store start time on context.
  - R-3 (medium): statement_sha8 collisions in observability tooling for high-volume queries — 8 hex chars = 32 bits, ~1 collision per 65k unique statements at 50% birthday probability. Acceptable for slow-query log (low volume by definition); revisit if SLO needed.
  - R-4 (low): grace-refresh --verify may rewrite verification-plan.xml broader than chunk scope — commit XML diff carefully, verify only Chunk 1/2 anchors are touched.
scope_in:
  - scheduler/core.py — add lifecycle listener registration on the returned AsyncIOScheduler
  - storage/jobs/engine.py, storage/audit/engine.py, storage/sessions/engine.py — attach before/after_cursor_execute listeners in build_engine
  - classifier/backend.py — instrument AsyncioSpawner.spawn
  - wiki/runner.py — instrument AsyncioSpawner.spawn
  - logging_events.py — extend SSoT constants
  - settings.py — add storage_slow_query_threshold_ms
  - ops/pii.py — verification test only (no code change unless gap found)
  - docs/verification-plan.xml — regenerated, committed separately
scope_out:
  - Mass adoption of @traced to all storage/*, claude_cli/* internal helpers (Chunk 3 or never)
  - Sampling / rate-limiting of log events
  - Log shipping / external sink configuration
  - Rewriting existing event-key string literals into catalog constants (gradual migration)
  - Pre-existing WIP in templates/*.ru.md, tests/unit/test_templates.py, src/ai_steward_wiki/tg/handlers.py — not staged with `git add -u`
  - Pre-existing tests/unit/auth/test_onboarding.py failure (related to templates WIP)
constraints:
  - TDD throughout: RED → GREEN → REFACTOR.
  - No pre-commit hook bypass (no --no-verify, no SKIP=...).
  - Conventional Commits + GRACE MODULE_ID scope.
  - mypy --strict on src/ stays clean; make lint stays clean.
  - All new event keys via logging_events.py SSoT.
  - USER APPROVAL gates auto-approved per session memory feedback_auto_approve_gates.md.
open_questions: []
references:
  - chunk-1 PR commits: a193fc1..0fa0372
  - logging_setup.py @traced decorator: src/ai_steward_wiki/logging_setup.py
  - logging_events.py SSoT: src/ai_steward_wiki/logging_events.py
  - APScheduler events: https://apscheduler.readthedocs.io/en/3.x/modules/events.html
  - SQLAlchemy core events: https://docs.sqlalchemy.org/en/20/core/events.html#sqlalchemy.events.ConnectionEvents
---

# Logging Coverage Chunk 2 — Discovery

## Real intent

Close ~100% of LDD (Log-Driven Design) blind spots remaining after Chunk 1: cron-style scheduler executions, slow database queries, and Claude CLI subprocess invocations. All three are async / out-of-band boundaries where a missing log anchor today means an unobservable failure tomorrow.

## Why these three

Chunk 1 covered request-flow entrypoints (TG update → pipeline → classifier → wiki run). Three categories of work happen OUTSIDE that flow and were therefore left dark:

1. **APScheduler-fired jobs** — maintenance, retention, snapshot, media-sweep, user reminders. A job that silently misses or max_instances itself produces zero application logs today.
2. **SQLAlchemy queries** — N+1 detection, lock contention, pragma misconfiguration all manifest as slow queries. Without a per-query duration anchor, we only see them via wall-clock symptoms.
3. **Claude CLI subprocess** — the most expensive operation in the system, the only one that talks to an external service, and currently the only one without lifecycle-event anchors at the spawn boundary. Stage-0/Stage-1 timeouts and non-zero exits surface as exceptions, but spawn-side metadata (argv length, env shape) needed for replay is lost.

## Critical PII line

Everything observable on these boundaries that touches user content is OUT. The contract is **shape, not substance**:

| Field | Shape (✓ log) | Substance (✗ never) |
|-------|---------------|---------------------|
| SQL | `statement_sha8` | raw SQL, parameter values |
| argv | `argv_length` (int) | argv items |
| env | `env_keys_count` (int) | env keys, env values |
| stdin/stdout/stderr | `*_bytes` (int) | content |
| job | `job_id`, `jobstore` | job args, return value |
| exceptions | exc_info traceback | exception .message body when serialized as a field |

Tier-1/Tier-2 PII redactor (D-034) remains the second line of defence on any string field that does sneak through.

## Scope boundary with Chunk 3

Out-of-scope: instrumenting internal helpers of storage/*, claude_cli/common.py, wiki/runner.py beyond the spawn-site. Boundary events are sufficient — internals are reachable via the standard structlog binding chain from the entrypoint `@traced`.

## Stakeholders

- Operator (Gennady) — primary reader of slow-query and scheduler logs.
- Future debugger (any agent or human) — needs the spawn metadata to replay a failed Claude CLI invocation.
