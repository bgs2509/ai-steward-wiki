---
feature: inbox-wiki-digest-job
bd_id: aisw-oqq
epic: aisw-t2r
parent: aisw-19o
status: stable
date: 2026-05-12
follows: aisw-kcz
functional_requirements:
  - FR-1: NL recurrence parsing — given ru text like «каждый день в 9 утра сводка», «присылай дайджест по будням в 19:00», «еженедельная сводка по понедельникам в 8» → produce a structured `Recurrence` (kind daily|weekly, time HH:MM in the owner's TZ, optional weekday set) and an `apscheduler.triggers.cron.CronTrigger(timezone=<owner TZ>)`. Ambiguous / unparseable → escalate (clarify). MVP supports daily and weekly-by-weekdays only; monthly / interval / raw-cron-string → "пока не умею" or escalate.
  - FR-2: `DigestPayload` (discriminated-union member `kind='digest'`) widened — gains a WIKI scope (`wiki_ids: list[str]` or a `wiki_scope: Literal['all']` sentinel), a serialised `recurrence`, retains `window_hours`, optional `prompt_hint`. `extra='forbid'` + `frozen=True` kept. No Alembic migration (JSON payload only).
  - FR-3: `create_digest_job(session, scheduler, *, owner_telegram_id, chat_id, recurrence, wiki_scope, window_hours, correlation_id) -> int` — INSERT+commit a `jobs.Job(kind='digest_job', status enabled, priority=Lane.DIGEST, payload=DigestPayload(...))`, then `scheduler.add_job(fire_digest_job, trigger=CronTrigger(...), args=[job_id], id=f'digest:{job_id}', replace_existing=True)`; log `scheduler.digest.scheduled`. Commit-before-add_job ordering (crash in the gap → a pending row with no trigger; no MVP reconciler).
  - FR-4: `fire_digest_job(job_id: int)` — picklable APScheduler callback. Load the `Job`, guard it's enabled (else `scheduler.digest.skipped`), resolve the owner's WIKI set (all sibling `<Name>-WIKI/` dirs under the owner's workspace root, excluding `Inbox-WIKI`), pick a primary `wiki_path` + `extra_add_dirs` for the rest, build a digest prompt overlay (`prompts/digest.md`) with planner-semantics context (the owner's pending/recurring `jobs` rows in the window), `await run_wiki_session(...)` under `Semaphore(MAX_CONCURRENT_CLI)` + `WikiLockManager` (MVP: lock the primary WIKI only), extract assistant text, deliver to TG via the existing `tg/output` policy (`ChainSplitter` / `send_document` fallback), mark `done`/`failed`, enforce the 600s timeout (→ kill-sequence), on failure → DLQ + 3-strike auto-disable via the existing `FailureCounter`. Log `scheduler.digest.fired|delivered|failed`.
  - FR-5: `run_wiki_session(...)` gains `extra_add_dirs: list[Path] | None` appended to the CLI argv `--add-dir` list (alongside the existing `media_dirs`).
  - FR-6: TG pipeline — the recurring-digest keyword branch in `_handle_reminder_intent` (currently → `REMINDER_RECURRING_RU` "not yet") becomes `_handle_digest_intent`: parse recurrence (FR-1); unparseable → clarify; else build a `category='digest'` `PendingConfirmDraft` (recap «Буду присылать сводку <recurrence-human> по WIKI: <list>. Подтверждаешь?») → `request_explicit` with the existing 2-button keyboard; `on_confirm_callback` dispatches `category=='digest'` → `_handle_digest_confirm` → on `confirmed`: `create_digest_job(...)` → ru ack «Готово — буду присылать сводку <recurrence-human>.». Stale / cancelled mirror the reminder paths.
  - FR-7: `__main__.py` wiring — extend the firing context registry (or a parallel one) with whatever `fire_digest_job` needs (owner workspace-root / WIKI-set resolver, runner adapter, `Semaphore`, `WikiLockManager`, jobs session maker, `TgSender`); pass the new `DefaultPipeline` kwargs (recurrence parser, jobs session maker, scheduler — several already wired for aisw-kcz).
non_functional_requirements:
  - NFR-1 (observability): structlog anchors `scheduler.digest.scheduled|fired|delivered|failed|skipped` and `tg.pipeline.digest.detected|confirm_requested|confirm_created|confirm_cancelled|confirm_stale`, each with `correlation_id`, `owner_telegram_id`, `job_id`.
  - NFR-2 (concurrency): concurrent digest CLI runs never exceed `MAX_CONCURRENT_CLI`; the per-WIKI flock prevents concurrent writes to the same WIKI.
  - NFR-3 (failure isolation): a digest run failure never crashes the scheduler / event loop; 3 consecutive failures auto-disable the job (D-019); a 600s timeout counts as a strike.
  - NFR-4 (TZ correctness): recurrence cron triggers fire in the owner's TZ (`UserRecord.tz` | `Settings.default_user_tz`); all DB datetimes UTC.
  - NFR-5 (ordering): the `jobs` row is committed before `scheduler.add_job` (same invariant as aisw-kcz).
  - NFR-6 (idempotency): boot re-scheduling uses `replace_existing=True`; double-confirm is race-safe via `ConfirmationService.resolve`.
risks:
  - R-1: widening `DigestPayload` breaking an existing producer/consumer. Mitigation — verified 2026-05-12: no producer exists (only the bare schema + `OutputKind`/`classifier.schema` `digest` literals + queue test); safe to widen freely.
  - R-2: `fire_digest_job` must be picklable for `SQLAlchemyJobStore` → module-level callable taking only a picklable int + a module-level context registry (mirrors `firing.fire_job` / `set_firing_context`); the runner adapter / semaphore / lock manager are reached via the registry, not closed over.
  - R-3: ru NL recurrence-parsing accuracy — `dateparser` does not do recurrence. Rule-based regex (daily / weekly-by-weekdays) + optional Haiku fallback; conservative match, escalate on anything else; unit tests over a phrasing corpus.
  - R-4: WIKI-set resolution — "owner's relevant WIKIs" = MVP all `<Name>-WIKI/` sibling dirs under the owner's workspace root excluding `Inbox-WIKI`. `wiki/lifecycle.py` already lists `-WIKI` dirs; reuse / extend it. Verified the listing logic exists 2026-05-12.
  - R-5: oversized digest output (>4096) — `tg/output.py` already implements the D-025 hybrid policy (`ChainSplitter` with `(i/M)` footers, `send_document`, Haiku-summary fallback); reuse it. No new split logic needed in this phase (the section-boundary `<b>`-header split is aisw-w3k).
  - R-6: context-window budget — this slice is ~6 source files + tests + a new prompt + GRACE; if Writing Plans estimates overflow, split further (recurrence parsing → its own micro-phase before the digest machinery).
scope:
  in:
    - FR-1..FR-7, NFR-1..NFR-6.
    - Recurrence: daily + weekly-by-weekdays only.
    - WIKI scope: an `'all'` sentinel (all the owner's `<Name>-WIKI` dirs minus `Inbox-WIKI`).
    - Digest delivery: the raw Stage-1 assistant text (the prompt asks for a scan-friendly summary), routed through the existing `tg/output` policy.
    - Confirm UX reusing the Phase-C `PendingConfirmDraft` / `request_explicit` / 2-button keyboard machinery; firing reusing the Phase-D.a `firing` registry pattern.
    - Failure / DLQ / 3-strike auto-disable wiring via the existing `FailureCounter`; 600s timeout + kill-sequence.
    - `__main__` wiring; GRACE (knowledge-graph, verification-plan, development-plan) updates; ADR; `prompts/digest.md`.
  out:
    - "→ aisw-w3k (Phase-D.b.2): D-024 actionable inline cards for ±2h items; TL;DR-as-a-distinct-section formatting contract; section-boundary 4096 split with (n/m) continuity markers; /expand <section>; /digest_now; per-user section toggles; rich jobs.db planner-semantics querying beyond a simple window list."
    - "→ separate future issue: the asyncio.PriorityQueue worker-loop consumer (tech-spec §3 producer/consumer) — bundled into the aisw-19o description but has no consumer today (/cron_add wiki_job not built; interactive needs streaming and is not a fit for a deferred queue) → building it now is YAGNI; digest_job MVP uses direct-fire under Semaphore+WikiLockManager like maintenance jobs already do. Also: /cron_add wiki_job; monthly/interval recurrence; reminder/digest management UX (/jobs_list, cancel/snooze/edit); admin shadow channel (D-020); tracker_* jobs."
    - "LATER: startup jobs.jobs ↔ APScheduler reconciliation."
---

# Discovery — Inbox-WIKI Phase-D.b.1: `digest_job` vertical slice

## Problem

aisw-kcz (Phase-D.a) shipped the one-shot `reminder_job` (scheduler + `sendMessage`, no Claude). Its sibling — the **recurring aggregator** «*каждый день в 9 утра сводка*» (smart-inbox-routing §8.1 class 2; tech-spec §3 `digest_job`, lane `digest`/3, timeout 600s, CLI=yes; D-024) — does not exist. The recurring-keyword branch in `tg/pipeline.py:_handle_reminder_intent` is currently a placeholder returning `REMINDER_RECURRING_RU` ("пока не умею").

The full `aisw-19o` (Phase-D.b) bundles three not-yet-built sub-systems (NL recurrence parsing; the asyncio.PriorityQueue worker-loop consumer; WIKI-set `--add-dir` resolution) plus `DigestPayload` widening, D-024 presentation (HTML, TL;DR, actionable cards, 4096 section-split) and TG delivery — well over one context window per the Plan-Sizing budget. It is split into **aisw-oqq** (this phase — the runnable vertical slice) and **aisw-w3k** (D-024 presentation polish). The PriorityQueue worker-loop is de-scoped out of `aisw-19o` entirely (YAGNI — no consumer today; `digest_job` MVP direct-fires under the existing `Semaphore` + `WikiLockManager`, matching how `scheduler/maintenance.py` jobs already run).

## Current state (verified 2026-05-12)

1. `scheduler/firing.py` (aisw-kcz) — `set_firing_context(sender, jobs_session_maker)` module-level registry + `create_reminder_job` / `fire_job` (picklable int callback, `DateTrigger`). The pattern `fire_digest_job` extends.
2. `storage/jobs/payloads.py` — `DigestPayload(kind='digest', wiki_id: str, window_hours: int)` in the `JobPayload` discriminated union; **no producer anywhere** (grep: only the schema, `tg/output.OutputKind` `"digest"`, `classifier/schema.py` `DIGEST`, a queue unit test).
3. `scheduler/queue.py` — `Lane` enum (`DIGEST = 3`), `PriorityJobQueue` (exists, **no runtime consumer wired in `__main__`**); `scheduler/{locks,failure,dlq}.py` — `WikiLockManager` (semaphore→memlock→flock), `FailureCounter` (3-strike auto-disable, timeout counted), `move_to_dlq`, `kill_with_sequence` — all built and exported.
4. `wiki/runner.py:run_wiki_session(...)` — runs one Stage-1a/1b session against a single `wiki_path`; already appends `media_paths`' parent dirs to `--add-dir` (`_build_argv(media_dirs=...)`); accepts a per-call `timeout_s` override. Needs an additional `extra_add_dirs` param.
5. `wiki/lifecycle.py` — already enumerates `<Name>-WIKI/` dirs under `<wiki_root>/<owner>/`; reuse for WIKI-set resolution.
6. `tg/output.py` — D-025 hybrid output policy: `ChainSplitter` (≤N parts at semantic boundaries, `(i/M)` footer), `send_document`, >10k Haiku-summary. Reuse for digest delivery.
7. `tg/pipeline.py` — Phase-C confirm machinery (`PendingConfirmDraft`, `ConfirmationService.request_explicit/resolve`, `build_route_confirm_keyboard` 2-button), Phase-D.a reminder fast-path + `_handle_reminder_confirm`; `_handle_reminder_intent` has the recurring-digest keyword branch to replace.
8. `prompts/` — `classifier.md`, `wiki.md`, `domain-*.md`, `inbox.md`, `time-parse.md`. A new `prompts/digest.md` overlay is needed.

## Approach (for Brainstorming)

Vertical slice mirroring aisw-kcz: a recurrence parser (rule-based + Haiku fallback) → `CronTrigger`; widen `DigestPayload`; `create_digest_job` / `fire_digest_job` in `scheduler/firing.py` with `fire_digest_job` direct-firing the CLI (via a runner adapter held in the firing-context registry) under the existing `Semaphore` + `WikiLockManager`, then delivering through `tg/output`; replace the pipeline recurring-stub with a `category='digest'` confirm flow; wire `__main__`; add `prompts/digest.md`; update GRACE; ADR.

## Open questions (for Brainstorming)

1. Recurrence representation — a dedicated `Recurrence` Pydantic model serialised into the payload, vs. storing the cron fields directly on the payload? (Lean: a small `Recurrence` model — reused by future `cron_user`/`tracker_*` kinds.)
2. Rule-based recurrence parser placement — extend `classifier/time_parse.py`, or a new `classifier/recurrence.py`? (Lean: new module — `time_parse` is single-absolute-instant; recurrence is a different shape.)
3. WIKI-set for `digest_job` — MVP `'all'` sentinel only, or also allow the user to name a subset in the same turn? (Lean: `'all'` only this phase; named-subset → aisw-w3k.)
4. `fire_digest_job` WIKI lock scope — lock just the primary WIKI, or every WIKI in the `--add-dir` set? The digest only *reads* the others. (Lean: primary only — the others are read-only context.)
5. Digest output in this phase — deliver Claude's raw assistant text via `tg/output` (no D-024 structure enforced), with the prompt merely *asking* for a TL;DR + sections? (Lean: yes — structured contract + cards are aisw-w3k.)
6. New `digest_job` lifecycle state — does `jobs.Job` already have an `enabled`/`status` notion compatible with "recurring, can be auto-disabled"? Confirm against the jobs schema during Brainstorming.
7. Empty digest — emit «сегодня дел нет 🌿» (D-024 `notify_policy=always`) in this phase or defer? (Lean: include the one-liner now; it's trivial.)
