---
feature: inbox-wiki-cron-bridge
bd_id: aisw-kcz
epic: aisw-t2r
phase: "Inbox-WIKI Phase-D.a"
status: draft
created: 2026-05-12
discovery: docs/superpowers/specs/20260512-inbox-wiki-cron-bridge-discovery.md
supersedes_in_scope: "digest_job → split out to aisw-19o (Phase-D.b)"
technology:
  decisions:
    - id: T-1
      text: "Integration shape A (Stage-0 fast-path, tech-spec §6): _run_text_pipeline branches on ClassifierResult.intent == Intent.REMINDER BEFORE the Stage-1a Router. The Stage-1a Router and prompts/inbox.md are NOT touched. (Q&A D-OQ-1.)"
    - id: T-2
      text: "Explicit confirm via the Phase-C machinery: ConfirmationService.request_explicit(draft, keyboard_factory=build_route_confirm_keyboard) with category='reminder'; the jobs.Job row is created only on the confirm callback. No implicit-ack. (Q&A D-OQ-2.)"
    - id: T-3
      text: "jobs.Job (storage/jobs/models.py, already exists, currently unused) is the SSoT domain record for a scheduled reminder. APScheduler's SQLAlchemyJobStore (same jobs.db) holds the trigger. create_reminder_job writes the Job row + commits, THEN scheduler.add_job(...) — so a crash in the (millisecond) gap leaves at worst a pending row without a trigger (the reminder silently does not fire); no reconciliation pass in the MVP. (Q&A D-OQ-4 / R-1.)"
    - id: T-4
      text: "Firing handler is a stable module-level callable ai_steward_wiki.scheduler.firing:fire_job(job_id: int) taking only a picklable int — required for SQLAlchemyJobStore persistence. It reads the aiogram bot-sender + jobs sessionmaker from a module-level registry set once at startup via firing.set_firing_context(...). (Q&A D-OQ-3.) NOTE: this deliberately differs from scheduler/maintenance.py's retention jobs, which pass non-picklable session_makers as add_job args and get away with it because they are re-registered on every boot (replace_existing=True); a one-shot reminder cannot be re-registered, so it must persist correctly with picklable args only."
    - id: T-5
      text: "NL time: classifier.time_parse.parse_time gains a keyword arg prefer_future: bool = False threaded into dateparser settings as PREFER_DATES_FROM='future' when True. The reminder path calls it with prefer_future=True so a bare past wall-clock time («разбуди в 6» at 21:00) rolls forward to the next future occurrence (TZ-aware → DST-safe). User TZ from users.toml UserRecord.tz, falling back to a new Settings field default_user_tz: str = 'Europe/Moscow'. (Q&A D-R-1.)"
    - id: T-6
      text: "ReminderPayload.message = distilled_payload['reminder_text'] if it is a non-empty str, else the raw user text. prompts/classifier.md is NOT bumped — its distilled_payload is already documented as an opaque bag of extracted entities, so reading an optional reminder_text opportunistically needs no prompt change. (Q&A D-OQ-1 / R-2.)"
    - id: T-7
      text: "misfire_grace_time for the reminder DateTrigger = None (= 'run as soon as possible after a missed fire') so a reminder missed during downtime still arrives (late). An APScheduler EVENT_JOB_MISSED listener registered in __main__ logs scheduler.reminder.misfired. coalesce=True (job_defaults) means a backlog collapses to one fire."
    - id: T-8
      text: "No new third-party dependency. No new SQLite table (jobs.jobs exists). No new Alembic migration (jobs.Job columns suffice; APScheduler tables are owned by SQLAlchemyJobStore at runtime). One new Settings field (default_user_tz). prompts unchanged."
---

# Phase-D.a — Inbox-WIKI cron bridge: reminder_job — Design

> bd **aisw-kcz** · epic **aisw-t2r** · depends-on: aisw-zd9, aisw-e45 (done) · blocks: aisw-19o (Phase-D.b digest_job)
> Discovery: `docs/superpowers/specs/20260512-inbox-wiki-cron-bridge-discovery.md`. All cross-cutting decisions (D-OQ-1/2/3/4, D-R-1, R-2) resolved there + in two `/questions-answers` rounds; this doc records the resulting technology shape.

## 1. Goal

When the Stage-0 classifier says `intent=reminder` (confidence ≥ 0.85) and the time parses to a concrete future moment, let the user create a one-shot reminder by natural language («разбуди завтра в 6», «напомни через час позвонить врачу») — via an explicit inline-button confirm — and deliver it at that time as a plain Telegram message (no Claude, no WIKI). Recurring digests («каждый день в 9 сводка») are out of scope here → `aisw-19o`; the bot answers them with a ru "not yet" line.

## 2. Architecture

Five units, each with one job:

| Unit | File | Responsibility | Depends on |
|------|------|----------------|------------|
| `ReminderPayload` | `src/ai_steward_wiki/storage/jobs/payloads.py` (extend) | Pydantic variant in the `JobPayload` discriminated union: `kind='reminder_job'`, `message: str`, `lead_time_min: int = 0`. | pydantic |
| `parse_time(..., prefer_future=...)` | `src/ai_steward_wiki/classifier/time_parse.py` (extend) | Add the `prefer_future` kwarg → `dateparser` `PREFER_DATES_FROM='future'`. | dateparser |
| `create_reminder_job(...)` | `src/ai_steward_wiki/scheduler/firing.py` (new) | Atomically: INSERT+commit a `jobs.Job` row (`kind='reminder_job'`, `owner_telegram_id`, `chat_id`, `status='pending'`, `priority=Lane.USER_WRITE`, `scheduled_at_utc=when_utc`, `payload=ReminderPayload(...).model_dump(mode='json')`, `created_at_utc=now`) → then `scheduler.add_job(fire_job, DateTrigger(run_date=when_utc, timezone='UTC'), args=[job_id], id=f'reminder:{job_id}', misfire_grace_time=None)`. Returns the `job_id`. | sqlalchemy.async, apscheduler |
| `fire_job(job_id)` + `set_firing_context(...)` | `src/ai_steward_wiki/scheduler/firing.py` (new) | Module-level callable APScheduler invokes on fire. Reads `(sender, jobs_session_maker)` from a module-level `_ctx`; loads the `Job` row; **guard:** if `status != 'pending'` → log `scheduler.reminder.skipped` and return; else parse `payload` via `parse_job_payload`, mark `status='in_progress'` + `started_at_utc`, `await sender.send_message(chat_id, "🔔 Напоминание: <message>")`, mark `status='done'` + `finished_at_utc` (or `status='failed'` + `last_error` on send error), emit `scheduler.reminder.fired` / `.delivered` / `.deliver_failed`. | aiogram (via TgSender), sqlalchemy.async |
| reminder branch in pipeline | `src/ai_steward_wiki/tg/pipeline.py` (extend `_run_text_pipeline`) | Detect `result.intent is Intent.REMINDER and result.confidence ≥ THRESHOLD` BEFORE the routable branch → `parse_time(text, user_tz=..., now_utc=..., prefer_future=True, haiku_backend=..., haiku_prompt_path=..., correlation_id=...)`; on `escalate` → ru clarification; on a parsed but explicitly-past *absolute date* → ru rejection; else build a `reminder` confirm draft + recap and call `ConfirmationService.request_explicit(...)`. The confirm callback (existing `on_confirm_callback`) gets a new `category=='reminder'` branch → `_handle_reminder_confirm` → on `'confirmed'`: reconstruct the draft, `create_reminder_job(...)`, send the ack; on cancel/stale: ru notices (mirrors `_handle_route_confirm`). | classifier.time_parse, tg.confirm, scheduler.firing |

Wiring in `src/ai_steward_wiki/__main__.py`: after `scheduler.start()` — build a `jobs` `async_sessionmaker` (the `jobs_engine` already exists), `firing.set_firing_context(sender=<TgSender over the Bot>, jobs_session_maker=<…>)`; register `scheduler.add_listener(_on_job_missed, EVENT_JOB_MISSED)` where `_on_job_missed` logs `scheduler.reminder.misfired`; pass the `jobs` sessionmaker + `scheduler` into `DefaultPipeline` so the confirm callback can call `create_reminder_job`. Add `default_user_tz: str = "Europe/Moscow"` to `Settings`.

## 3. Data flow (happy path)

```
user: «разбуди завтра в 6 позвонить врачу»
 → handlers.on_text → pipeline._run_text_pipeline
 → classifier.classify → ClassifierResult(intent=reminder, confidence=0.93, distilled_payload={...})
 → [reminder branch] parse_time(text, user_tz=Europe/Moscow, now_utc=…, prefer_future=True)
        → TimeParseResult(when_utc=<tomorrow 03:00Z>, escalate=False)
 → message = distilled_payload.get("reminder_text") or text   # → "позвонить врачу" if present else raw
 → ConfirmationService.request_explicit(
        PendingConfirmDraft(telegram_id, chat_id, category="reminder",
            draft={"when_utc": "...Z", "message": "...", "lead_time_min": 0, "correlation_id": "..."},
            recap_text="Поставлю напоминание на 12.05 06:00 (Europe/Moscow): «позвонить врачу». Подтверждаешь?"),
        keyboard_factory=build_route_confirm_keyboard)               # ✅ Подтвердить / ❌ Отмена
 → log tg.pipeline.reminder.confirm_requested(pending_id, when_utc)
 ── user taps ✅ ──
 → handlers (CONFIRM_CALLBACK_PREFIX) → pipeline.on_confirm_callback
        → pending.category == "reminder" → _handle_reminder_confirm
        → ConfirmationService.resolve(...) == "confirmed"
        → create_reminder_job(jobs_session, scheduler, owner_telegram_id, chat_id, when_utc, message, lead_time_min)
              → INSERT jobs.Job(kind="reminder_job", status="pending", scheduled_at_utc=when_utc, payload={...}) ; commit ; job_id
              → scheduler.add_job(firing.fire_job, DateTrigger(when_utc), args=[job_id], id=f"reminder:{job_id}", misfire_grace_time=None)
        → log scheduler.reminder.scheduled(job_id, when_utc)
        → sender.send_message(chat_id, "Готово, напомню 12.05 в 06:00.")
        → log tg.pipeline.reminder.confirm_created(pending_id, job_id, when_utc)
 ── tomorrow 03:00Z ──
 → APScheduler fires firing.fire_job(job_id)
        → _ctx loaded ; Job row loaded ; status == "pending" ✓
        → status="in_progress" ; sender.send_message(chat_id, "🔔 Напоминание: позвонить врачу")
        → status="done" ; log scheduler.reminder.fired / .delivered
        → APScheduler removes the one-shot trigger row
```

## 4. Edge cases & error handling

1. **`escalate=True`** (time ambiguous / unparseable) — no job, no confirm; reply «Не понял, на когда поставить напоминание — уточни время.» (re-use the existing classifier escalation copy if one exists, else this string). Log `tg.pipeline.reminder.unparseable`.
2. **Bare past wall-clock time** («разбуди в 6» at 21:00) — NOT an error: `prefer_future=True` rolls it to tomorrow 06:00. Log `time_source` from `TimeParseResult.source`.
3. **Explicitly-past absolute date** («напомни 5 мая» when it is already 12 мая) — reply «Эта дата уже прошла — назови будущую.», no job. (Detection: `when_utc <= now_utc` AND the matched text looks like an absolute date rather than a bare time — pragmatic heuristic: after `prefer_future=True`, if `when_utc` is still `<= now_utc`, it was an explicit past date; reject.) Log `tg.pipeline.reminder.rejected_past`.
4. **Double-confirm / re-send within the 10-min TTL** — `ConfirmationService.resolve` is race-safe (`UPDATE … WHERE status='pending'`); `request_explicit` is idempotent on `(telegram_id, payload_hash)` → a second confirm tap → `None` → «Время на подтверждение истекло, пришли заново.» (stale), no second job. (Same as Phase-C.)
5. **`fire_job` runs but the `Job` row is gone / not `pending`** (e.g. cancelled by a future management feature, or a stale APScheduler row) — guard: log `scheduler.reminder.skipped`, return. No send.
6. **`fire_job` send fails** (TG API error, chat blocked) — `status='failed'`, `last_error=<str>`, log `scheduler.reminder.deliver_failed(job_id, error_class)`. No retry, no DLQ row (reminder_job is single-shot, TG-deliver-only; strict DLQ parity with tech-spec §3 is deferred).
7. **Bot down past the fire time** — `misfire_grace_time=None` → APScheduler runs it on the next startup; `coalesce=True` collapses a backlog; `EVENT_JOB_MISSED` only fires when grace is finite, so with `None` it effectively won't — but if it ever does, `_on_job_missed` logs `scheduler.reminder.misfired`. Known limitation: a crash in the millisecond gap between the `Job` commit and `scheduler.add_job` leaves a pending row with no trigger → silent miss; documented, no reconciliation in the MVP.
8. **`firing.set_firing_context` not called** (mis-wired) — `fire_job` raises a clear `RuntimeError("firing context not initialised")` (fail-fast). Covered by the `__main__` wiring + a unit test that asserts the raise.
9. **Recurring-digest phrasing** detected (heuristic: `parse_time` escalates or the text contains «каждый»/«ежедневно»/«еженедельно»/«сводк»/«дайджест» — keep it simple, a small ru keyword set) — reply «Регулярные сводки — скоро будет, пока могу только разовые напоминания.» Log `tg.pipeline.reminder.recurring_not_yet`. (This is best-effort; precise digest recognition is `aisw-19o`'s job.)

## 5. Logging anchors (final)

All with `correlation_id`; `telegram_id` where in a TG context; PII filenames not involved (text only — but the recap/message text itself is user content, so log lengths/ids, not full text, in line with the existing pipeline anchors).

- pipeline: `tg.pipeline.reminder.detected` (intent, confidence, time_source, escalate), `tg.pipeline.reminder.unparseable`, `tg.pipeline.reminder.rejected_past`, `tg.pipeline.reminder.recurring_not_yet`, `tg.pipeline.reminder.confirm_requested` (pending_id, when_utc), `tg.pipeline.reminder.confirm_created` (pending_id, job_id, when_utc), `tg.pipeline.reminder.confirm_cancelled` (pending_id, status), `tg.pipeline.reminder.confirm_stale` (pending_id), `tg.pipeline.confirm.reminder_dispatched` (pending_id, action) — emitted from `on_confirm_callback` when `category=='reminder'`.
- scheduler/firing: `scheduler.reminder.scheduled` (job_id, when_utc), `scheduler.reminder.fired` (job_id, chat_id), `scheduler.reminder.delivered` (job_id), `scheduler.reminder.deliver_failed` (job_id, error_class), `scheduler.reminder.skipped` (job_id, status), `scheduler.reminder.misfired` (job_id).

## 6. Testing

Unit (fakes; deterministic `now_utc` clock; `FakeConfirmationService` recording `request_explicit`; a fake scheduler exposing `add_job`/`add_listener`; `FakeTgSender`; in-memory `jobs.db` session):
- REMINDER intent + future time → `request_explicit` called with the right draft (`category='reminder'`, `when_utc`, `message`) + the 2-button keyboard factory; no `Job` row yet.
- `message` resolution: `distilled_payload={"reminder_text":"X"}` → `message=="X"`; absent → `message==raw text`.
- `prefer_future`: «разбуди в 6» with `now` at 21:00 → `when_utc` is tomorrow 06:00 local; explicit past absolute date → rejection reply, no confirm.
- `escalate=True` → clarification reply, no confirm.
- recurring phrasing → "not yet" reply, no confirm.
- confirm callback `category='reminder'` + `resolve→'confirmed'` → `create_reminder_job` writes a `Job` row (`kind='reminder_job'`, payload round-trips through `parse_job_payload`, `scheduled_at_utc==when_utc`) + `scheduler.add_job` called once with a `DateTrigger` at `when_utc`, `id=='reminder:<id>'`, `misfire_grace_time is None`; ack sent.
- cancel callback → cancelled reply, no `Job` row; stale (`resolve→None`) → stale reply.
- double-confirm idempotency.
- `fire_job`: context set, `Job` `pending` → `sender.send_message(chat_id, "🔔 Напоминание: <message>")` + row `done`; `Job` not `pending` → skipped, no send; send raises → row `failed` + `scheduler.reminder.deliver_failed`; context not set → `RuntimeError`.
- `ReminderPayload` in `parse_job_payload`: valid dict → `ReminderPayload`; extra key → `ValidationError`.

Integration (extend `tests/integration`, behind `RUN_INTEGRATION=1` — but this scenario needs no real Claude/Telegram, so it can run as a slow unit too): a reminder message → `request_explicit` → simulate the confirm callback → assert a `jobs.jobs` row exists with `kind='reminder_job'` and `scheduled_at_utc` matching the parsed time; then directly invoke `firing.fire_job(job_id)` with a fake sender → assert one `send_message(chat_id, "🔔 Напоминание: …")` and the row flipped to `done`.

## 7. GRACE deltas

- New module `M-SCHEDULER-FIRING` (`scheduler/firing.py`) — MODULE_CONTRACT (PURPOSE: load a jobs.Job by id and dispatch its delivery; SCOPE: `set_firing_context`, `fire_job`, `create_reminder_job`; DEPENDS: storage.jobs, scheduler.queue (Lane), aiogram TgSender, apscheduler; LINKS: D-002, D-010, tech-spec §3/§6, aisw-kcz) + MODULE_MAP.
- Extend `M-STORAGE-JOBS-PAYLOADS` MODULE_MAP (+`ReminderPayload`), `M-CLASSIFIER-STAGE0` (`parse_time` gains `prefer_future`), `M-TG-PIPELINE-CLASSIFIER` (reminder branch + `_handle_reminder_confirm`), `M-FOUNDATION-*`/`Settings` CONFIG (+`default_user_tz`), `__main__` MODULE_CONTRACT DEPENDS (+`scheduler.firing`).
- `knowledge-graph.xml`: new node `M-SCHEDULER-FIRING`; CrossLinks pipeline→firing, firing→storage.jobs, firing→scheduler.queue, __main__→firing.
- `verification-plan.xml`: the new tests + the new log anchors (§5).
- `development-plan.xml`: a `Phase-D.a` entry for `aisw-kcz`; a stub `Phase-D.b` entry for `aisw-19o`.
- ADR: one ADR is warranted — **ADR-006: reminder_job — Stage-0 fast-path, jobs.Job + APScheduler dual-store, module-level firing context** (records T-1/T-3/T-4 — the fast-path-vs-router choice contradicts a literal reading of the bd title, and the dual-store + picklable-args-only constraint is a non-obvious binding decision worth a record). Written in Step 13 (Finish) or Step 8 (Q&A Contracts) per the workflow.

## 8. Out of scope (→ later)

`digest_job` / recurring aggregator / recurrence parsing / the `PriorityJobQueue` worker loop / multi-WIKI `--add-dir` (→ `aisw-19o`, Phase-D.b). Reminder management UX (list/cancel/snooze/edit, `/reminders`, post-create cancel button). `wiki_job` & `/cron_add`. `tracker_survey`/`tracker_followup`/`boundary_message`. DLQ/retry parity for `reminder_job`. Roll-forward of explicitly-past absolute dates (rejected instead). Startup `jobs.db` ↔ APScheduler reconciliation.
