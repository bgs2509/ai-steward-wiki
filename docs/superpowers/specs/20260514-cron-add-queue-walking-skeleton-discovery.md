---
feature: cron-add-queue-walking-skeleton
bd_id: aisw-02v
date: 2026-05-14
status: discovery
risk: medium
risk_justification: "3-4 modules touched (new M-TG-CRON-ADD, M-SCHEDULER-QUEUE-PRODUCER, M-SCHEDULER-QUEUE-CONSUMER; reuse M-CLASSIFIER-RECURRENCE), new user-facing TG command (public API surface), no DB schema change beyond reusing jobs.db SQLAlchemyJobStore, no auth/security change, reversible (purely additive)."
evidence: strong
evidence_sources:
  - "Q&A 4/4 decisions recorded 2026-05-14 (audit trail in aisw-02v notes)"
  - "Existing direct-fire pattern: src/ai_steward_wiki/scheduler/maintenance.py (digest_job, aisw-oqq commit 95672ab)"
  - "PriorityJobQueue stub: src/ai_steward_wiki/scheduler/queue.py (Lane enum, QueueItem, aisw-19o Phase-D.b)"
  - "M-CLASSIFIER-RECURRENCE typed monthly recurrence: commit e5e275a"
  - "tech-spec §3 D-011 (docs/Spec-WIKI/research/tech-spec-draft.md)"
  - "CLAUDE.md identity vocab D-042 (telegram_id/owner_telegram_id/chat_id/user_id)"
open_questions: []

functional_requirements:
  - FR-1: "TG handler `/cron_add <NL recurrence> | <command>` accepts user input, validates via M-CLASSIFIER-RECURRENCE NL parser, persists APScheduler job to jobs.db (SQLAlchemyJobStore), replies with job_id + parsed cron expression."
  - FR-2: "APScheduler CronTrigger fires on schedule → producer callback constructs CronJobPayload(chat_id, command, correlation_id, owner_telegram_id, job_id) → PriorityJobQueue.put(lane=USER, item=payload)."
  - FR-3: "Consumer async loop drains PriorityJobQueue (await get()), for each item: spawns systemd-run --scope cli-<job_id>.scope claude-code CLI with the command, captures stdout, timeout=600s."
  - FR-4: "Consumer delivers CLI result (or error message in Russian) via bot.send_message(chat_id, text). Long results chunked per Telegram 4096-char limit (reuse existing tg/output.py chunking)."
  - FR-5: "Both /cron_add and consumer log structlog events with correlation_id, owner_telegram_id, chat_id, job_id, wiki_id at every decision point (BLOCK markers per logging convention)."

non_functional_requirements:
  - NFR-1: "Lane=USER (priority 1, between URGENT and DIGEST). Consumer concurrency: single drain loop (asyncio.Task in bot lifecycle), at-most-once delivery for MVP."
  - NFR-2: "CLI timeout = 600s (D-011 §3). Timeout → consumer kills scope, sends '❌ Timeout' to chat. Exit-code != 0 → sends '❌ Error: <stderr tail>'."
  - NFR-3: "All datetimes in DB = UTC. NL recurrence parsed against user_tz from sessions.db (existing pattern). APScheduler job stores tz-aware trigger."
  - NFR-4: "Ru-only user-facing strings (MVP D-032). No i18n catalog."
  - NFR-5: "Mypy --strict on all new src/. Pydantic v2 discriminated union for CronJobPayload (add to existing payload union in scheduler/queue.py or sibling)."
  - NFR-6: "Tests: unit ≥80% coverage on producer callback + consumer drain loop; integration test that pushes 1 fake CronJobPayload and asserts bot.send_message called with stub CLI output."

risks:
  - R-1:
      desc: "aiogram Bot instance ref leaking into scheduler/ layer (coupling)."
      mitigation: "Inject bot via constructor/DI into consumer. Already precedent in maintenance.py — not a new coupling."
  - R-2:
      desc: "systemd-run --scope cleanup failures (orphaned scopes on consumer crash)."
      mitigation: "Use `--scope --collect` flag; structured log on every spawn/exit; aisw-bot.service Restart=on-failure already configured."
  - R-3:
      desc: "APScheduler SQLAlchemyJobStore + asyncio race conditions on persistent jobs."
      mitigation: "AsyncIOScheduler is officially supported; jobs.db WAL+busy_timeout already set; reuse existing init pattern from scheduler/core.py."
  - R-4:
      desc: "NL recurrence parsing edge cases (ambiguous user input) — bad cron silently scheduled."
      mitigation: "M-CLASSIFIER-RECURRENCE returns parsed expression; /cron_add replies with HUMAN-readable rendition (e.g. 'каждый день в 09:00 МСК') and asks confirm (reuse confirm.py pattern); reject on parser error with clear ru message."
  - R-5:
      desc: "Bot offline when CronTrigger fires → producer cannot push to in-memory queue."
      mitigation: "APScheduler persistent jobstore replays missed_executions=1 on startup; for MVP at-most-once is acceptable (D-032 scope). Document as known limitation."
  - R-6:
      desc: "User schedules many cron jobs → queue backpressure."
      mitigation: "PriorityJobQueue is unbounded asyncio.PriorityQueue; lane=USER drains in FIFO within lane; out-of-scope: per-user rate limit (follow-up issue)."

scope:
  in:
    - "TG handler /cron_add"
    - "Persistence via existing APScheduler SQLAlchemyJobStore (jobs.db)"
    - "Producer callback (APScheduler→queue)"
    - "Consumer drain loop"
    - "systemd-run --scope CLI invocation"
    - "bot.send_message delivery (text chunking via tg/output.py)"
    - "structlog instrumentation"
    - "Unit + 1 integration test (skeleton)"
  out:
    - "/cron_list, /cron_delete, /cron_edit (separate follow-up bd issues)"
    - "Retry/backoff policy (separate follow-up)"
    - "Dead-letter queue for failed jobs (separate)"
    - "Multi-user share / ACL"
    - "Per-user rate limit"
    - "i18n"
    - "Multi-process / distributed worker"
  later:
    - "After this epic closes — create bd issues for CRUD + retry policy + DLQ wiring"

dependencies:
  affects:
    - "M-CLASSIFIER-RECURRENCE (reused as-is, no contract change)"
    - "scheduler/queue.py (PriorityJobQueue gets first real consumer + producer)"
    - "scheduler/core.py (AsyncIOScheduler wiring, may need consumer task lifecycle hook)"
    - "tg/handlers.py (new /cron_add handler registration)"
    - "tg/pipeline.py (route /cron_add command before/after existing pipeline)"
  breaks: []

stakeholders:
  - "Bot users wanting recurring CLI execution (e.g. daily health brief)"
  - "Future maintenance/digest jobs that may migrate to queue (out of scope here)"

best_practices:
  - "Walking Skeleton (Cockburn) — vertical slice end-to-end"
  - "Vertical Slice Architecture (Jimmy Bogard) — feature contains all layers thin"
  - "YAGNI — defer CRUD/retry/DLQ until evidence of need"
  - "Producer-first queue design (Celery/RQ/arq evolution pattern)"
  - "Pydantic discriminated union for typed message payloads (FastAPI pattern, also matches existing QueueItem design)"

common_mistakes_avoided:
  - "Building consumer without producer (caught by initial YAGNI gate)"
  - "Designing speculative payload shape (driven by real /cron_add contract instead)"
  - "Building CRUD before walking skeleton works"
  - "Tight coupling consumer to specific delivery channel (mitigated by bot DI)"
---

# Discovery: `/cron_add` + Queue Consumer (Walking Skeleton)

## 1. Intent

User wants `aisw-cig` (PriorityJobQueue consumer loop) executed. `aisw-cig` was explicitly YAGNI-deferred because **no producer exists**. Q&A converged on building a real producer in the same iteration: `/cron_add` user-facing recurring CLI jobs. The combined epic justifies the queue's existence and produces a tested, end-to-end vertical slice.

## 2. Real Goal

Promote `PriorityJobQueue` from stub to load-bearing scheduler infrastructure by giving it one real producer (`/cron_add`) and one real consumer (`aisw-cig` consumer loop), end-to-end tested with Telegram delivery. Establish the pattern other producers (future maintenance migration, ad-hoc CLI jobs) can follow.

## 3. Implicit Assumptions Surfaced

1. User assumes bot is always running when their cron fires → mitigated by APScheduler missed_executions replay (NFR-3) + documented limitation (R-5).
2. User assumes CLI command takes < 10 min → enforced by 600s timeout (NFR-2).
3. User assumes natural-language recurrence ("каждый день в 9 утра") works → reuse of M-CLASSIFIER-RECURRENCE covers; ambiguous inputs handled by confirm flow (R-4).
4. User assumes results are delivered to the chat that registered the cron → encoded in `chat_id` in payload (FR-4).

## 4. Blind Spots

1. **Orphan systemd scopes** on bot crash mid-job (R-2 mitigated via `--collect`).
2. **Bot restart between fire and drain** — APScheduler replays trigger, but queue is in-memory → missed item lost. Acceptable for MVP (at-most-once, documented).
3. **Large CLI outputs** — must chunk to fit 4096 TG limit; reuse `tg/output.py` (already does this for digest_job).
4. **Concurrent fires for same user** — single consumer drain serializes, but multiple users compete on lane=USER FIFO. Fair-enough for MVP.

## 5. Constraints (Cannot Change)

1. CLAUDE.md identity vocab (D-042) — payload must use `chat_id` for delivery, `owner_telegram_id` for ownership.
2. UTC in DB, user_tz on I/O.
3. Ru-only user strings (D-032).
4. Mypy --strict + Pydantic at boundaries.
5. Subscription auth Claude CLI via `CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code`.
6. structlog with correlation_id, owner_telegram_id, chat_id, job_id, wiki_id.

## 6. Lint / Sentrux Baseline (Step 2 preflight 2026-05-14)

1. `make lint`: ruff check ✓, ruff format ✓ (242 files), mypy strict ✓ (87 src files) — clean.
2. Sentrux: `.sentrux/` absent — project not onboarded, preflight skipped per workflow rules.
3. Pre-commit infra: `core.hooksPath = .beads/hooks` (beads chain), `.pre-commit-config.yaml` present, `pre-commit` binary available.

## 7. Architecture Sketch (will formalize in Step 4 design)

```
TG /cron_add ──→ M-TG-CRON-ADD (validate + parse NL + confirm)
                     │
                     ▼
              APScheduler.add_job(CronTrigger, ...)
                     │
              [fires on schedule]
                     │
                     ▼
            M-SCHEDULER-QUEUE-PRODUCER (callback)
                     │ PriorityJobQueue.put(lane=USER, CronJobPayload)
                     ▼
            M-SCHEDULER-QUEUE-CONSUMER (drain loop)  ← aisw-cig
                     │ systemd-run --scope --collect claude ...
                     │ capture stdout, timeout 600s
                     ▼
              bot.send_message(chat_id, result_or_error)
```

## 8. Coverage Map FR → Module

| FR | Owner module |
|----|--------------|
| FR-1 | M-TG-CRON-ADD (new) |
| FR-2 | M-SCHEDULER-QUEUE-PRODUCER (new), reuses scheduler/core.py wiring |
| FR-3 | M-SCHEDULER-QUEUE-CONSUMER (new, this is `aisw-cig`) |
| FR-4 | M-SCHEDULER-QUEUE-CONSUMER + tg/output.py (existing chunker) |
| FR-5 | All three new modules (logging contract) |
