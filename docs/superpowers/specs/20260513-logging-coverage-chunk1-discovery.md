---
feature: logging-coverage-chunk1
bd_id: aisw-er6
module_id: M-FOUNDATION-LOGGING
status: draft
date: 2026-05-13
fr:
  - FR-1: A reusable @traced decorator wraps async and sync callables, emitting one INFO event on entry (event="<module>.<func>.start"), one INFO on success (event="<module>.<func>.done", duration_ms=int), and one ERROR on exception (event="<module>.<func>.error", duration_ms=int, exc_info=True), then re-raises.
  - FR-2: The decorator MUST NOT log function arguments or return values automatically (PII safety). Caller may pass an optional `bind={"key": "value", ...}` parameter at call site via structlog.contextvars.bound_contextvars for non-PII fields only.
  - FR-3: A logging_events.py module exposes a stable catalog of canonical event-key constants (snake_case dotted), at minimum covering the events emitted by @traced, by aiogram middleware, and by AllowlistMiddleware (existing 3 events). All other modules MAY adopt constants incrementally — Chunk 1 does NOT mass-rename existing string literals.
  - FR-4: A new aiogram BaseMiddleware (CorrelationMiddleware) runs BEFORE AllowlistMiddleware on every Update; it generates a fresh correlation_id (uuid4), binds correlation_id + telegram_id + chat_id + update_id into structlog.contextvars.bound_contextvars for the lifetime of one update, and clears the binding on exit (even on exception).
  - FR-5: @traced is applied to public entrypoints of currently-covered modules where boundary observability is missing: tg/pipeline.py top-level pipeline function(s), classifier stage entrypoints, inbox staging entrypoints, wiki run entrypoints. Internal helpers, getters, pure functions are NOT decorated.
  - FR-6: Tests using structlog.testing.capture_logs assert that for at least one representative @traced callable (one async, one sync, one raising) the three lifecycle events appear with stable event keys, duration_ms is an int ≥ 0, correlation_id flows through from CorrelationMiddleware, and arguments do NOT appear in event_dict.
  - FR-7: CorrelationMiddleware integration test: one TG Update flow emits records whose correlation_id matches across CorrelationMiddleware, AllowlistMiddleware, and a representative downstream @traced function.
nfr:
  - NFR-1: PII safety — @traced does not introspect args/kwargs/return; no positional argument values logged; only call-site explicit `bind` accepted, and bind values are caller-responsibility (documented in docstring).
  - NFR-2: Cardinality — event keys are bounded constants from logging_events.py; high-cardinality values (user_id, update_id) ride as structured fields, never as part of event string.
  - NFR-3: Performance — @traced overhead < 200 µs per call for sync no-op (measured informally; not a benchmark gate). No allocation in hot path beyond one perf_counter pair + one log call on success.
  - NFR-4: Test isolation — tests bind/clear contextvars cleanly (use structlog.contextvars.clear_contextvars in fixture teardown) to prevent cross-test correlation_id leakage.
  - NFR-5: mypy --strict on src/, ruff check, ruff format clean. Pre-existing mypy error in tg/handlers.py:139 is NOT addressed in this chunk (out-of-scope; tracked separately).
  - NFR-6: Zero regression in existing event keys — Chunk 1 ADDS catalog constants, does NOT rewrite literal strings already emitted by storage/scheduler/inbox/etc.
constraints:
  - structlog ≥ 24.x already configured in logging_setup.py with merge_contextvars + _inject_correlation_id processors; reuse, do not duplicate.
  - aiogram 3.15 middleware API (BaseMiddleware.__call__(handler, event, data)); middleware order is registration order on Dispatcher.
  - correlation_id ContextVar in logging_setup.py is separate from structlog.contextvars binding — the explicit ContextVar predates the contextvars processor. Chunk 1 keeps both (no breakage of legacy bind_correlation_id callers), but the new middleware uses structlog.contextvars.bound_contextvars as the primary mechanism going forward.
  - Conventional Commits + GRACE MODULE_ID scope — commits use feat(M-FOUNDATION-LOGGING): or feat(M-TG-MIDDLEWARE):.
  - No timeline-blocking: this is a foundation feature; downstream chunks (APScheduler listener, SQLAlchemy events, Claude CLI subprocess wrapper) depend on @traced + events catalog being in place first.
risks:
  - R-1 — Double correlation_id sources: legacy ContextVar (logging_setup._correlation_id, injected by _inject_correlation_id processor) AND new structlog.contextvars binding from CorrelationMiddleware. If both set different values, _inject_correlation_id wins because it runs AFTER merge_contextvars in processor order. → Mitigation: CorrelationMiddleware also calls bind_correlation_id(uuid) so both paths agree. Test asserts equality.
  - R-2 — @traced on coroutines vs sync: a single @overload pair (or runtime inspect.iscoroutinefunction) decorator must dispatch correctly. → Mitigation: write both paths, test both, type with ParamSpec + TypeVar.
  - R-3 — Test cross-contamination via contextvars — failing test leaves correlation_id bound. → Mitigation: autouse fixture in conftest clears structlog contextvars between tests.
  - R-4 — Cardinality blow-up if developers later embed user_id inside event keys (event=f"user.{id}.action"). → Mitigation: logging_events.py docstring + ADR-light note (decision can stay in design.md, not a full ADR for Chunk 1).
  - R-5 — aiogram middleware ordering — if CorrelationMiddleware registered AFTER AllowlistMiddleware, allowlist deny events lose correlation_id binding. → Mitigation: explicit ordering in tg/bot.py register block + integration test asserting order.
scope_in:
  - src/ai_steward_wiki/logging_setup.py — add traced decorator (sync + async dispatch) + helper context manager for bound fields
  - src/ai_steward_wiki/logging_events.py — NEW module, SSoT catalog of stable event-key constants
  - src/ai_steward_wiki/tg/middleware_correlation.py — NEW CorrelationMiddleware
  - src/ai_steward_wiki/tg/bot.py — register CorrelationMiddleware BEFORE AllowlistMiddleware
  - Apply @traced to a small representative set (NOT a mass rollout): tg/pipeline.py public entrypoint(s), classifier stage entrypoints, inbox staging entrypoints, wiki run entrypoints
  - tests/unit/test_logging_traced.py — NEW
  - tests/unit/test_logging_events_catalog.py — NEW
  - tests/unit/tg/test_middleware_correlation.py — NEW
  - tests/integration/test_correlation_id_flow.py — NEW (light, no real TG)
  - tests/conftest.py — add autouse fixture to clear structlog contextvars between tests
scope_out:
  - Chunk 2 work: APScheduler EVENT_JOB_EXECUTED/ERROR/MISSED listener; SQLAlchemy slow-query log; Claude CLI subprocess wrapper logging; PII processor extension for new fields; grace-refresh --verify sync of log anchors ↔ BLOCK markers in verification-plan.xml
  - Mass adoption of @traced on storage/* and claude_cli/* — explicitly deferred (covered by Chunk 2 or later)
  - Rewriting existing event-key literal strings to constants from logging_events.py (deferred — gradual migration)
  - Fix of pre-existing mypy error src/ai_steward_wiki/tg/handlers.py:139 (out-of-scope)
  - sampling, rate-limiting of logs, log shipping
  - Performance benchmarking gates (informal NFR-3 only)
---

# Discovery: structured logging coverage Chunk 1 (aisw-er6)

## Background

`~/.claude/CLAUDE.md` and `ai-steward-wiki/CLAUDE.md` both prescribe Log-Driven Design (LDD): logs as verification evidence, stable snake_case event keys, mandatory `correlation_id` + `user_id` + `chat_id` + `wiki_id` + `job_id` fields, PII redaction, log anchors ↔ semantic BLOCK markers in `verification-plan.xml`. The `_logging` skill formalises these as 11 LDD principles.

Current state audit (2026-05-13, evidence in conversation):

1. `src/ai_steward_wiki/logging_setup.py` already configures structlog with `merge_contextvars`, ISO-UTC timestamps, JSON renderer, pluggable PII processor, and a legacy `_correlation_id: ContextVar` injected via custom processor.
2. **41% module coverage** (30/73 src files import structlog). Strong: `tg/` (10/11). Blind: `storage/` (0/14), `claude_cli/` (0/2). Partial: `scheduler/` (2/8), `ops/` (2/6), `classifier/`, `inbox/`, `wiki/`, `auth/` (50–56%).
3. `tg/middleware_auth.py` (AllowlistMiddleware) logs three events but does NOT bind into `structlog.contextvars` — it passes `telegram_id` / `chat_id` as explicit fields on every log call. No middleware exists for `correlation_id`.
4. NO `@traced` decorator or equivalent exists.
5. Event keys are **already consistently snake_case dotted** (`tg.pipeline.skip.l1_dup`, `auth.deny.bypass_public`, `scheduler.reminder.fired`). Ready for SSoT catalog without renames.
6. Tests already use `structlog.testing.capture_logs()` (`tests/unit/scheduler/test_firing.py`).

## Problem (the real intent, not the literal ask)

The literal ask: add logging everywhere.

The real intent: close the gap between the LDD contract documented in CLAUDE.md and the actual codebase, where 59% of modules are silent and the existing 41% emit logs WITHOUT a centralised correlation_id binding mechanism, so traces don't connect across handler → classifier → scheduler → DB. Without correlation_id propagation and a boundary-observability decorator, post-mortem from production logs is a manual grep job, not a deterministic agent-readable trace.

What user did NOT say but should have:
- This must be PII-safe — bot processes voice transcripts, OCR'd photos, and free-text user messages. Auto-logging args would be a privacy regression. Discovery enforces PII safety as NFR-1.
- This is a **foundation feature** — Chunk 2 work (APScheduler listener, SQLAlchemy slow-query, subprocess wrapper) DEPENDS on `@traced` + events catalog. Chunk 1 ordering matters.
- Existing 7+ `@asynccontextmanager`-decorated functions (`tg/handlers.py` lines 110, 129; `scheduler/locks.py`; `wiki/acquire.py`) should be candidates for `@traced` instrumentation IN A LATER CHUNK — NOT this one. Scope discipline.

## Stakeholders

1. Future debugger (human or AI) reading production journald JSON — primary beneficiary.
2. Verification harness (`verification-plan.xml`) — Chunk 2 will hook log anchors into BLOCK markers; Chunk 1 sets the stable-key foundation.
3. PII / privacy posture — `pii_processor` already in pipeline; new `@traced` must NOT undermine it by auto-logging args.

## Best-practices research (structlog + async + aiogram + 2026)

1. **Boundary-only decorator** with no auto-arg-introspection is standard for PII-sensitive systems (e.g. Sentry's `trace` decorator, OpenTelemetry semantic conventions, datadog ddtrace `@tracer.wrap()` with explicit tag binding).
2. **structlog.contextvars.bound_contextvars** is the canonical 2024+ pattern for request-scoped fields in async Python — replaces explicit `extra={...}` proliferation.
3. **aiogram middleware ordering** matters: `correlation_id` must bind before any other middleware (auth, throttling) logs an event, otherwise early deny events have null correlation_id.
4. **Catalog of event keys as constants** (vs scattered string literals) — standard SSoT pattern; ranges across observability libraries (e.g. Honeycomb, Sentry). Eliminates typos like `tg.pipline.start` and supports `grep` from `verification-plan.xml` to source.
5. **Anti-pattern guard:** never put high-cardinality values (user_id, timestamp, hash) INSIDE the `event=` string. Cardinality discipline is the #1 cause of log-aggregation backend cost blow-up.

## Out of scope (Chunk 2 markers)

These are intentionally deferred. Each will be a separate `feature-workflow` iteration:

1. APScheduler `EVENT_JOB_EXECUTED` / `EVENT_JOB_ERROR` / `EVENT_JOB_MISSED` listener attached in `scheduler/core.py`.
2. SQLAlchemy `before_cursor_execute` / `after_cursor_execute` slow-query log (~200 ms threshold) in `storage/engines.py`.
3. Claude CLI subprocess wrapper logging (`claude_cli/*`) — exit code, duration_ms, stdin/stdout byte sizes, NO content.
4. PII processor extension for new fields appearing through `@traced` + middleware.
5. `grace-refresh --verify` — synchronise log anchors with semantic BLOCK markers in `verification-plan.xml`. Chunk 1 lays the catalog; Chunk 2 wires the verification cross-link.

## Open questions

None blocking — all design decisions resolved at Discovery time:

1. ContextVar vs structlog.contextvars duality — **keep both, ensure agreement.** Documented in R-1 mitigation.
2. Decorator surface on sync vs async — **single `@traced` dispatched by `inspect.iscoroutinefunction`.**
3. Catalog completeness — **only new events from Chunk 1 are MANDATORY constants.** Existing literals migrate gradually in later chunks.

Proceeding to Brainstorming (auto-approved per session memory: `feedback_auto_approve_gates`).
