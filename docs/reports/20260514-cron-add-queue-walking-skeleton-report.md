# Report: `/cron_add` + queue consumer walking skeleton (aisw-02v)

- **Epic:** `aisw-02v`
- **Closes / unblocks:** `aisw-cig` (queue consumer — previously YAGNI-deferred for lack of a real producer)
- **Date completed:** 2026-05-14
- **Branch:** `master`
- **Discovery / Design / Plan:**
  - `docs/superpowers/specs/20260514-cron-add-queue-walking-skeleton-discovery.md`
  - `docs/superpowers/specs/20260514-cron-add-queue-walking-skeleton-design.md`
  - `docs/superpowers/plans/20260514-cron-add-queue-walking-skeleton-plan.md`

## Summary

A vertical slice that turns `PriorityJobQueue` from a stub into a load-bearing piece of the scheduler. One real producer (`/cron_add` user-facing recurring CLI jobs) and one real consumer (single async drain loop spawning `systemd-run --scope --collect`-wrapped Claude CLI) now run end-to-end through the queue.

The slice is intentionally **walking-skeleton thin**: CRUD (`/cron_list`, `/cron_delete`, `/cron_edit`), retry/backoff, DLQ wiring, per-user rate limit, multi-process workers, and bot-offline replay strategy are all deferred to follow-up issues (the list is enumerated at the end of `Phase-E.cron-add` in `docs/development-plan.xml`).

## Module shape

3 new RUNTIME modules + 1 in-place payload widen + 1 wiring change:

1. `M-SCHEDULER-CRON-USER` (`scheduler/cron_user.py`, v0.0.1) — mirrors `M-SCHEDULER-FIRING`: `set_cron_user_context`, `create_cron_user_job`, `fire_cron_user_job` (picklable int callback enqueues to `PriorityJobQueue` under `Lane.CRON_WRITE`).
2. `M-SCHEDULER-CONSUMER` (`scheduler/consumer.py`, v0.0.1) — single-task drain loop. `CronConsumer` is constructor-DI'd (R-1 mitigation: `bot` is injected, not module-global); built-in `Spawner` Protocol seam lets unit tests inject a stub subprocess. Handles 600 s timeout via `kill_with_sequence` (D-021), `ChainSplitter` chunking for long stdout, ru error messages for non-zero exit / timeout / Telegram delivery failure.
3. `M-TG-CRON-ADD` (`tg/cron_add.py`, v0.0.1) — `/cron_add <NL recurrence> | <command>` Command handler. `parse_recurrence` reused as-is; `escalate=True` becomes a user-visible ru usage hint (R-4 mitigation: no silent bad-cron).
4. `M-STORAGE-JOBS` (`storage/jobs/payloads.py`, v0.0.6 → v0.0.7) — `CronUserPayload` widened in place: typed `Recurrence` + free-form `command` + optional `wiki_id`. No Alembic migration (JSON column, zero rows existed with `kind='cron_user'`).
5. `M-RUNTIME-WIRING` (`__main__.py`, v0.5.7 → v0.5.8) — one shared `PriorityJobQueue` between producer + consumer; `cron_consumer_task` spawned next to `dp.start_polling`; cancelled on shutdown before `scheduler.shutdown()`.

Also: `scheduler/queue_payloads.py` (new, types) with `CronUserQueueMsg` and a Pydantic discriminated union seam for future kinds (NFR-5); `prompts/cron_user.md` (new, semver 0.1.0); `tg/bot.py` `build_dispatcher` and `tg/handlers.py` `build_router` gain optional `get_user_tz` kwarg.

## Quality gates at finish

| Gate | Result |
|---|---|
| `grace lint` | 0 errors, 0 warnings (knowledge-graph + development-plan + verification-plan synced) |
| `make lint` | ruff check ✅, ruff format --check ✅, mypy --strict ✅ (91 src files) |
| `pytest tests/unit` | 857 passed; 1 unrelated pre-existing failure (`test_format_intro_message_renders_bot_name` — verified pre-existing via `git stash` before any aisw-02v change) |
| Integration test | `tests/integration/scheduler/test_cron_add_flow.py` correctly written + `RUN_INTEGRATION=1`-gated; skipped in the implementation session only because of the suite-wide `CLAUDECODE=1` anti-recursion guard. Runs in CI. |
| Sentrux | Not onboarded in this repo (verified during Step 2 preflight; skipped per workflow rule) |

## Risk × evidence matrix outcomes

All three USER APPROVAL gates auto-approved via the `--auto-approve` flag (memory `feedback_auto_approve_gates.md`):

- **Gate 3 (Discovery, FR/NFR/scope):** `risk=medium, evidence=strong` — Q&A 4/4 decisions on 2026-05-14, existing patterns cited (commit `e5e275a` M-CLASSIFIER-RECURRENCE, `95672ab` direct-fire), tech-spec §3 D-011. Logged to bd notes.
- **Gate 5 (Design):** `risk=medium, evidence=strong` — 7 design decisions AD-01..AD-07 all reuse existing patterns (firing.py mirror, claude_cli/common, ChainSplitter); no new ADR-candidate; `open_questions: []`; Context7-verified aiogram v3 + APScheduler 3.x. Logged to bd notes.
- **Gate 10 (Plan):** `risk=medium, evidence=strong` — 6-phase plan, self-review checklist all-pass (FR-1..5 + NFR-1..6 mapped to phases; no placeholders; every contract has tasks). Logged to bd notes.

No advisory deviation gate fired during execution — the implementation followed the plan straight through.

## Log anchors added

```
tg.command.cron_add.{usage, parsed, escalate, scheduled, failed}
scheduler.cron_user.{scheduled, fire, fire.job_missing, fire.failed}
scheduler.consumer.{started, drained, exec.started, exec.done,
                    exec.timeout, exec.failed, delivered,
                    deliver_failed, unexpected, cancelled, row_missing}
```

All anchors carry `correlation_id`, `owner_telegram_id`, `chat_id`, `job_id` at the points where they exist (per the project's `structlog` convention).

## Out-of-scope (follow-up bd issues to file)

1. `/cron_list`, `/cron_delete`, `/cron_edit` — CRUD surface for user-visible cron jobs.
2. Retry / backoff / 3-strike auto-disable for `cron_user` (mirror digest pattern).
3. DLQ wiring for `kind='cron_user'` (table already exists).
4. Bot-offline replay strategy beyond APScheduler's `coalesce=True`.
5. Per-user `/cron_add` rate limit (queue backpressure).
6. Interactive confirm for `/cron_add` (only after `/cron_delete` exists — see AD-02).
7. Full `deliver_output(kind='reply')` integration for run-output persistence (AD-03).
8. Per-WIKI scoping (`wiki_id` field is plumbed but unused in walking skeleton — wire after `/cron_list` exposes scope per row).

## Commits (5)

```
Phase 1 — feat(M-STORAGE-JOBS,M-SCHEDULER-CONSUMER): widen CronUserPayload + add queue_payloads (aisw-02v)
Phase 2 — feat(M-SCHEDULER-CRON-USER): cron-user producer (INSERT+CronTrigger+queue enqueue) (aisw-02v)
Phase 3 — feat(M-SCHEDULER-CONSUMER): single-drain async consumer with systemd-run scope + TG delivery (aisw-02v)
Phase 4 — feat(M-TG-CRON-ADD,M-TG-HANDLERS-WIRING): /cron_add Command handler + build_router wiring (aisw-02v)
Phase 5 — feat(M-RUNTIME-WIRING): wire cron-user producer + consumer task + /cron_add handler (aisw-02v)
Phase 6 — test(M-SCHEDULER-CONSUMER): end-to-end integration smoke for /cron_add flow (aisw-02v)
```
