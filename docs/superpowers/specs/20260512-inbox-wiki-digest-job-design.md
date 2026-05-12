---
feature: inbox-wiki-digest-job
bd_id: aisw-oqq
epic: aisw-t2r
parent: aisw-19o
status: stable
date: 2026-05-12
follows: aisw-kcz
technology_stack:
  - python: "3.11+"
  - apscheduler: "AsyncIOScheduler + SQLAlchemyJobStore + CronTrigger"
  - pydantic: "v2 (discriminated union; Recurrence model)"
  - aiogram: "3.x"
  - claude_cli: "2.1.139 (subscription auth, run_wiki_session)"
  - dateparser: "fallback corpus for time-of-day inside recurrence phrasings"
approach: direct-fire-digest-job
---

# Design — Inbox-WIKI Phase-D.b.1: `digest_job` vertical slice

## Decision

Implement the recurring-digest path as a vertical slice mirroring aisw-kcz (`reminder_job`), with three building blocks:

1. **NL recurrence parsing** — a new `classifier/recurrence.py`: a `Recurrence` Pydantic model (`kind: Literal["daily","weekly"]`, `time_hhmm: str`, `weekdays: tuple[int, ...] = ()`, `tz: str`) with `to_cron() -> dict[str, str]` (APScheduler `CronTrigger` kwargs), and `parse_recurrence(text, *, user_tz, now_utc=..., haiku_backend=None, correlation_id="") -> RecurrenceParseResult` — conservative rule-based ru regex for daily / weekly-by-weekdays, an optional Haiku fallback (same shape as `time_parse.parse_time`'s fallback), `escalate=True` on monthly / interval / raw-cron / ambiguous.
2. **`DigestPayload` widening** — `kind='digest'` gains `wiki_scope: Literal["all"] = "all"`, `recurrence: Recurrence`, `window_hours: int = Field(24, ge=1, le=24*7)`, `prompt_hint: str | None = None`; `extra='forbid'` + `frozen=True` kept. No Alembic migration (JSON payload widening only; the recurrence trigger is owned by `SQLAlchemyJobStore` in `jobs.db`).
3. **`create_digest_job` / `fire_digest_job`** in `scheduler/firing.py` — direct-fire (NOT via the `PriorityJobQueue`, which has no consumer today): `create_digest_job` INSERT+commits a `jobs.Job(kind='digest_job', status='scheduled', priority=Lane.DIGEST, payload=DigestPayload(...))` then `scheduler.add_job(fire_digest_job, trigger=CronTrigger(**recurrence.to_cron(), timezone=recurrence.tz), args=[job_id], id=f'digest:{job_id}', replace_existing=True)` (commit-before-add_job, same invariant as aisw-kcz); `fire_digest_job(job_id: int)` is a picklable int callback that reads its deps from a module-level digest-context registry (runner adapter, `Semaphore`, `WikiLockManager`, owner-WIKI-set resolver, jobs sessionmaker, `TgSender`), resolves the owner's WIKI set (all `<Name>-WIKI/` dirs under the owner's workspace root minus `Inbox-WIKI`) → primary `wiki_path` + `extra_add_dirs`, queries `jobs.db` for the owner's upcoming rows inside the window → planner-semantics text, assembles `prompts/digest.md` (+ that context) as the Stage-1 overlay, runs `run_wiki_session(...)` under `Semaphore(MAX_CONCURRENT_CLI)` + `WikiLockManager` (primary WIKI only — the rest are read-only `--add-dir` context), extracts the assistant text (or «Сегодня дел нет 🌿» if empty), delivers via the existing `tg/output` policy (`ChainSplitter`/`send_document`), and on success keeps `status='scheduled'` + `retry_count=0` / on failure or 600s timeout bumps `retry_count` and at 3 strikes sets `status='disabled'` + `scheduler.remove_job` + `move_to_dlq` (D-019; timeout is a `Transient` strike).

The TG entry point is the recurring-digest keyword branch in `tg/pipeline.py:_handle_reminder_intent` (currently → `REMINDER_RECURRING_RU` "пока не умею"), promoted to a real `_handle_digest_intent` that builds a `category='digest'` `PendingConfirmDraft` and goes through the Phase-C `ConfirmationService.request_explicit` + 2-button keyboard; `on_confirm_callback` dispatches `category=='digest'` → `_handle_digest_confirm` → `create_digest_job` → ru ack.

## Why this over alternatives

1. **Direct-fire under `Semaphore` + `WikiLockManager` (chosen)** — the spec's tech-spec §3 producer/consumer (APScheduler → `asyncio.PriorityQueue` → worker pool) is the eventual design, but the queue has **no consumer today** and `/cron_add` (the other CLI-cron producer) isn't built — building the worker loop now is YAGNI. `scheduler/maintenance.py` already runs its jobs by calling functions directly from the APScheduler callback; `digest_job` does the same, just with concurrency bounded by the existing `Semaphore` and per-WIKI `WikiLockManager`. The `PriorityJobQueue` consumer is deferred to its own future bd issue (de-scoped out of aisw-19o).
2. **Route digest through `PriorityJobQueue` now** — rejected: requires building + wiring + testing a worker-pool consumer for a single producer; over-engineering for MVP volume (a handful of cron digests/day).
3. **Reuse `time_parse.parse_time` for recurrence** — rejected: `parse_time` returns one absolute instant; recurrence is a different shape (cron fields). A dedicated `Recurrence` model is also reused by future `cron_user` / `tracker_*` kinds.
4. **Enforce the full D-024 structure (TL;DR section, actionable cards, `<b>`-header 4096 split) in this phase** — rejected: that's a substantial UX chunk; deferred to aisw-w3k. This phase asks the prompt for a scan-friendly summary and ships it through the existing `tg/output` policy.
5. **Add `enabled`/`cron_expr` columns to `jobs.jobs` for recurring jobs** — rejected: the actual MVP `Job` schema is flat (no such columns); the APScheduler `SQLAlchemyJobStore` (persisted in `jobs.db`) IS the recurrence record; the `Job` row is the domain SSoT (`status` ∈ `scheduled` → `disabled`, `retry_count` for strikes). No migration.

## Affected modules

- `M-CLASSIFIER-RECURRENCE` *(new — `classifier/recurrence.py`)* — `Recurrence`, `RecurrenceParseResult`, `parse_recurrence`.
- `M-STORAGE-JOBS` (`storage/jobs/payloads.py`) — `DigestPayload` widened (imports `Recurrence`); VERSION bump.
- `M-SCHEDULER-FIRING` (`scheduler/firing.py`) — `set_digest_context`, `create_digest_job`, `fire_digest_job`; VERSION bump. (Reuses `FiringNotInitialisedError` semantics with a parallel `DigestNotInitialisedError` or a widened context.)
- `M-WIKI-RUNNER` (`wiki/runner.py`) — `run_wiki_session(..., extra_add_dirs: list[Path] | None = None)`; `_build_argv` appends them after the primary `--add-dir` (next to `media_dirs`); VERSION bump.
- `M-TG-PIPELINE-CLASSIFIER` (`tg/pipeline.py`) — `_handle_digest_intent` (replaces the recurring stub), `_handle_digest_confirm`, `build_digest_recap`, ru strings (`DIGEST_RECAP_RU`, `DIGEST_ACK_RU`, `DIGEST_UNPARSEABLE_RU`, `DIGEST_CONFIRM_CANCELLED_RU`, `DIGEST_CONFIRM_STALE_RU`, `DIGEST_EMPTY_RU`), `on_confirm_callback` `category=='digest'` dispatch, new `DefaultPipeline.__init__` kwarg(s) for the recurrence parser; VERSION bump.
- `M-RUNTIME-WIRING` (`__main__.py`) — `firing.set_digest_context(...)`, `_RecurrenceParserAdapter` (wraps `parse_recurrence` + Stage-0 backend + `prompts/recurrence.md` if present), pass new pipeline kwargs; VERSION bump.
- `prompts/digest.md` *(new)* — Stage-1 overlay; `semver:` line required (`assemble_prompt` validates it).
- `prompts/recurrence.md` *(new, optional)* — Haiku-fallback prompt for `parse_recurrence`; if absent, the parser escalates on a miss (mirrors `time-parse.md`).

## Data flow

See the architecture block in the brainstorming summary (this section is the SSoT going forward):

```
on_text → _run_text_pipeline → (Intent.REMINDER, conf≥0.85, recurring keywords) → _handle_digest_intent
   parse_recurrence(text, user_tz=_resolve_user_tz(tid)) → Recurrence | escalate→clarify
   PendingConfirmDraft(category='digest', draft={recurrence(serialised), wiki_scope:'all', window_hours:24, user_tz, correlation_id}, recap=build_digest_recap(...))
   ConfirmationService.request_explicit(draft, keyboard_factory=build_route_confirm_keyboard)   [Phase-C, reused]
on_confirm_callback → category=='digest' → _handle_digest_confirm
   resolve race-safely → confirmed → reconstruct draft → guard digest deps wired
   async with jobs_session_maker() as s: create_digest_job(s, scheduler, owner_telegram_id=tid, chat_id=cid, recurrence=..., wiki_scope='all', window_hours=24, correlation_id=...)
        Job(kind='digest_job', status='scheduled', priority=Lane.DIGEST, scheduled_at_utc=None, payload=DigestPayload(...).model_dump(mode='json'))  → flush → commit
        scheduler.add_job(fire_digest_job, CronTrigger(**rec.to_cron(), timezone=rec.tz), args=[job_id], id=f'digest:{job_id}', replace_existing=True)
        log scheduler.digest.scheduled
   → DIGEST_ACK_RU «Готово — буду присылать сводку <human>.»

[cron] fire_digest_job(job_id):                                  # picklable int; deps from module digest-context registry
   load Job; guard status=='scheduled' (else scheduler.digest.skipped)
   parse_job_payload → DigestPayload (bad → status='disabled', move_to_dlq, log scheduler.digest.failed, return)
   owner_wikis = resolve_owner_wikis(owner_telegram_id)          # wiki/lifecycle listing, minus Inbox-WIKI
   if not owner_wikis: deliver «У тебя пока нет ни одной WIKI для сводки.»; finished_at_utc=now; return  (no strike)
   primary, *rest = owner_wikis; extra_add_dirs = [w.path for w in rest]
   upcoming = query jobs.db for owner rows in window  → planner_context text
   overlay = assemble prompts/digest.md (+ planner_context)      # via assemble_prompt → atomic temp
   started_at_utc=now; log scheduler.digest.fired
   async with sem, wiki_lock(primary):
       result = await run_wiki_session(wiki_id=primary.id, wiki_path=primary.path, base_prompt_path=..., overlay_prompt_path=overlay, extra_add_dirs=extra_add_dirs, timeout_s=600, ...)
   text = aggregate_text(result.events) or DIGEST_EMPTY_RU
   await deliver_output(sender, chat_id, text, run_id=result.run_id, ...)   # tg/output ChainSplitter/send_document, reused
   success: status='scheduled', retry_count=0, finished_at_utc=now; log scheduler.digest.delivered
   except (WikiRunnerError | WikiRunnerTimeoutError | TG send error):
       retry_count += 1; finished_at_utc=now; last_error=...
       if retry_count >= 3: status='disabled'; scheduler.remove_job(f'digest:{job_id}'); move_to_dlq(session, job_id, reason='auto_disable', error_class=..., last_error=...)
       log scheduler.digest.failed (error_class, retry_count, disabled=<bool>)
```

`Recurrence.to_cron()` mapping: `daily` → `{"hour": HH, "minute": MM}`; `weekly` → `{"day_of_week": "mon,fri", "hour": HH, "minute": MM}` (APScheduler accepts comma-joined 3-letter day names). `tz` passed as `CronTrigger(timezone=...)`.

## Error handling

- Unparseable / ambiguous recurrence (or monthly/interval/cron-string) → `DIGEST_UNPARSEABLE_RU` clarify; no job; log `tg.pipeline.digest.unparseable`.
- `fire_digest_job` before `set_digest_context` → `DigestNotInitialisedError` (mis-wiring; surfaces in APScheduler logs).
- Owner has no `*-WIKI` → friendly ru line, mark finished, **no strike, no disable** (not a failure).
- `WikiRunnerError` / `WikiRunnerTimeoutError` (600s kill-sequence inside `run_wiki_session`) → strike++; at 3 → `disabled` + `remove_job` + DLQ (`Transient`, D-019).
- TG delivery failure → same strike path.
- Bad/parse-failing payload → `disabled` + DLQ immediately (`Permanent`).
- A digest run failure never propagates out of the APScheduler callback (caught, logged, row updated) → scheduler/event loop unaffected (NFR-3).

## Verification

- `tests/unit/classifier/test_recurrence.py` *(new)* — ru phrasing corpus («каждый день в 9», «по будням в 19:00», «еженедельно по понедельникам и пятницам в 8», «каждое утро» → 09:00? or escalate), `to_cron()` correctness incl. weekday joining, monthly/interval/cron → `escalate`, Haiku-fallback path, TZ propagation, defaults unchanged.
- `tests/unit/storage/test_payloads.py` *(+)* — digest round-trip with `Recurrence` (dict ↔ model, `model_dump(mode='json')` round-trip), `wiki_scope` literal accepted/rejected, `window_hours` bounds, extra-field forbidden, frozen.
- `tests/unit/scheduler/test_firing.py` *(+)* — `create_digest_job` writes the row + a `CronTrigger` job with `id='digest:<id>'` / `args=[id]` / `replace_existing=True`; commit-before-add_job ordering (fresh sqlite connection in the fake scheduler); `fire_digest_job` invokes the runner-adapter under sem+lock, delivers via the sender, leaves `status='scheduled'`+`retry_count=0`; empty-WIKI-set → friendly line, no strike; runner error → `retry_count++`; third runner error → `status='disabled'` + `remove_job` called + DLQ row; bad payload → `disabled`+DLQ; no-context → `DigestNotInitialisedError`.
- `tests/unit/wiki/test_runner.py` *(+)* — `extra_add_dirs` appear in argv immediately after the primary `--add-dir <wiki_path>` and before/after `media_dirs` (assert exact placement); `None` → unchanged argv.
- `tests/unit/tg/test_pipeline_digest.py` *(new)* — recurring phrasing → `request_explicit(category='digest')` with the right draft + 2-button keyboard, no job; unparseable → clarify, no `request_explicit`; below-threshold / `recurrence_parser=None` → not handled here; confirm callback → `create_digest_job` called once + `DIGEST_ACK_RU`; cancel → no job; stale (`resolve`→`None`) → `DIGEST_CONFIRM_STALE_RU`; double-confirm idempotent; non-`digest` category → existing generic `resolve`.
- `tests/unit/tg/test_digest_e2e.py` *(new)* — full chain over an alembic-migrated sessions DB + a real jobs DB + a fake scheduler + a fake runner-adapter (returns canned assistant events): `on_text` → `pending_confirms` row → `on_confirm_callback("confirm")` → `jobs.jobs` row (`kind='digest_job'`, `status='scheduled'`) + `add_job` + ack → `set_digest_context` + `fire_digest_job(job_id)` → one fake CLI run → one delivery through `tg/output` (assert `send_message`/`send_document` called) → row still `status='scheduled'`, `retry_count=0`, `finished_at_utc` set.
- Gates: `make lint` (ruff check + ruff format --check + mypy src), `grace lint --failOn errors`, `make inv-lint`, `uv run pytest tests/unit`.
- Integration (`RUN_INTEGRATION=1`, real Claude CLI): not run in this iteration (nightly gate); the digest flow needs nothing external and is covered by `test_digest_e2e.py` as an in-process slow unit (mirrors how aisw-kcz handled its e2e).

## Scope (mirrors discovery)

IN: `Recurrence` model + `parse_recurrence` (daily + weekly-by-weekdays), `DigestPayload` widening, `create_digest_job`/`fire_digest_job` direct-fire under sem+lock, `run_wiki_session` `extra_add_dirs`, `_handle_digest_intent`/`_handle_digest_confirm` confirm flow, owner-WIKI-set resolution (`'all'` sentinel), raw-Claude-text delivery via `tg/output`, empty-digest line, failure/DLQ/3-strike auto-disable, 600s timeout, `__main__` wiring, `prompts/digest.md` (+ optional `prompts/recurrence.md`), GRACE updates, ADR-007.

OUT → aisw-w3k (Phase-D.b.2): D-024 actionable inline cards for ±2h items, TL;DR-as-a-distinct-section formatting contract, `<b>`-header section-boundary 4096 split with `(n/m)` markers, `/expand <section>`, `/digest_now`, per-user section toggles, rich jobs.db planner-semantics querying.

OUT → separate future issue: the `asyncio.PriorityQueue` worker-loop consumer (tech-spec §3); `/cron_add` wiki_job; monthly/interval recurrence; reminder/digest management UX (`/jobs_list`, cancel/snooze/edit); admin shadow channel (D-020); tracker_* jobs.

LATER: startup `jobs.jobs ↔ APScheduler` reconciliation.
