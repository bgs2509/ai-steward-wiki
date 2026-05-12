---
feature: inbox-wiki-cron-bridge
bd_id: aisw-kcz
epic: aisw-t2r
phase: "Inbox-WIKI Phase-D"
status: draft
created: 2026-05-12
requirements:
  functional:
    - id: FR-1
      text: "A user message whose Stage-0 intent is REMINDER (e.g. «разбуди в 6», «напомни завтра в 9 позвонить врачу»), with confidence ≥ the configured threshold AND a successfully-parsed absolute time (classifier.time_parse → TimeParseResult.escalate == False), creates a one-shot reminder job: a row in jobs.db.jobs with kind='reminder_job', owner_telegram_id + chat_id from the TG update, scheduled_at_utc = parsed when_utc (UTC), status='pending', and a ReminderPayload (kind='reminder_job', message=<reminder text>, lead_time_min=<int, default 0>). An APScheduler one-shot trigger (DateTrigger at when_utc) is registered against the shared scheduler so the job fires at the right time."
    - id: FR-2
      text: "When the reminder fires, the firing handler loads the jobs.Job row by id, dispatches by kind, and for kind='reminder_job' delivers the message text to chat_id via the Telegram bot directly — NO Claude CLI run, NO WIKI workspace (per tech-spec §3: reminder_job is the only kind with wiki_id=null and no CLI). The job row is marked status='done' (started_at_utc / finished_at_utc set). A send failure marks status accordingly and is logged; reminder_job is single-shot so there is no auto-disable concern."
    - id: FR-3
      text: "Before the job is created the user gets a confirmation: an explicit-level inline-button recap «Поставлю напоминание на <локальное время в TZ юзера>: «<текст>». Подтверждаешь?» with «✅ Подтвердить» / «❌ Отмена» (the same 2-button route-confirm keyboard family from Phase-C — reuses ConfirmationService.request_explicit + build_route_confirm_keyboard). On confirm → the job is created (FR-1) and a short ack «Готово, напомню в <локальное время>.» is sent; on cancel → «Отменено.»; on stale/TTL-expired resolve → «Время на подтверждение истекло, пришли заново.». The pending row carries everything needed to create the job on confirm (category, telegram_id, chat_id, when_utc as ISO-UTC string, message, lead_time_min, correlation_id) in draft_json. (RESOLVED D-OQ-2: explicit confirm, not implicit-ack.)"
    - id: FR-4
      text: "If the time could not be parsed unambiguously (TimeParseResult.escalate == True) the bot does NOT create a job; it replies with a ru clarification «Не понял, на когда поставить напоминание — уточни время.» (re-using the existing classifier escalation copy). A BARE wall-clock time that resolves to the past (e.g. «разбуди в 6» at 21:00) is NOT rejected — parse_time is called with prefer_future=True (dateparser PREFER_DATES_FROM='future', TZ-aware/DST-safe) so it rolls forward to the next future occurrence (RESOLVED D-R-1). Only an explicitly-past ABSOLUTE date («напомни 5 мая» when it is already 12 мая) is rejected with «Эта дата уже прошла — назови будущую.» (no job created)."
    - id: FR-5
      text: "Recurring aggregator/digest requests («каждый день в 9 утра сводка», «каждый понедельник дайджест по здоровью») are recognised but are OUT of this phase's executable scope (see Scope/OUT and the recommended Phase-D.a / Phase-D.b split): for such a request the MVP replies with a ru 'not yet' notice «Регулярные сводки — скоро будет, пока могу только разовые напоминания.» rather than silently doing nothing. (If the Phase-D.b split is rejected and digest is kept in scope, FR-5 is replaced by full digest_job FRs in Brainstorming.)"
    - id: FR-6
      text: "Pydantic: storage/jobs/payloads.py gains a ReminderPayload variant (kind='reminder_job', message: str, lead_time_min: int = 0, extra='forbid', frozen) added to the JobPayload discriminated union; parse_job_payload validates it. The jobs.Job ORM row's payload column stores ReminderPayload.model_dump(mode='json'); reads validate via parse_job_payload. (Naming aligns with tech-spec §3 'reminder_job'; the bd description's free-form 'chat_id/message/lead_time' is mapped — chat_id lives on the Job row, not the payload, per the jobs.Job schema.)"
    - id: FR-7
      text: "Structlog anchors (all with correlation_id, telegram_id where available): classifier/pipeline — tg.pipeline.reminder.detected (intent, confidence, time_source, escalate), tg.pipeline.reminder.rejected_past, tg.pipeline.reminder.confirm_requested (pending_id, when_utc); confirm — tg.pipeline.reminder.confirm_created (pending_id, job_id, when_utc), tg.pipeline.reminder.confirm_cancelled/stale; scheduler/firing — scheduler.reminder.scheduled (job_id, when_utc), scheduler.reminder.fired (job_id, chat_id), scheduler.reminder.delivered / scheduler.reminder.deliver_failed (job_id, error_class)."
  non_functional:
    - id: NFR-1
      text: "Reuses what Phase-1..4 / Phase-C already built — ConfirmationService (request_explicit / resolve / get_pending / expire_due) + the route-confirm 2-button keyboard family + the CONFIRM_CALLBACK_PREFIX handler; the existing AsyncIOScheduler from __main__ (build_scheduler) + SQLAlchemyJobStore; classifier.time_parse.parse_time; the jobs.db engine/sessionmaker; PIIRedactor for log lines. NO new SQLite table (jobs.jobs already exists), at most ONE new Alembic migration only if jobs.Job needs a column it lacks (it does not — id/owner_telegram_id/chat_id/kind/status/priority/scheduled_at_utc/started_at_utc/finished_at_utc/payload/retry_count/last_error/created_at_utc cover it). NO new third-party dependency."
    - id: NFR-2
      text: "mypy --strict / ruff / ruff-format / grace lint clean; coverage stays ≥80%. All new behaviour unit-tested with fakes (FakeConfirmationService, a fake scheduler exposing add_job, a fake TgSender, a deterministic clock for now_utc) — no live Telegram / Claude / real APScheduler thread in unit tests. The firing handler is a plain async function tested by calling it directly with a fake bot + an in-memory jobs.db session."
    - id: NFR-3
      text: "Ru-only (D-032). All datetimes in jobs.db and in draft_json are UTC; the user's TZ (from users.toml UserRecord.tz, default a configured fallback) is applied only when (a) parsing the NL time and (b) rendering the confirmation recap. No bypass of pre-commit hooks. The recap keyboard removal on callback is best-effort (a failed edit_reply_markup must not break job creation)."
    - id: NFR-4
      text: "Restart-safety: APScheduler's SQLAlchemyJobStore persists the scheduled trigger across bot restarts, so the firing function MUST be a stable importable module-level callable (e.g. ai_steward_wiki.scheduler.firing:fire_job) taking only JSON-serialisable args (the job_id). On startup the bot already calls scheduler.start(); no extra re-scan of jobs.db rows is required for reminder_job because the APScheduler row IS the trigger record (the jobs.Job row is the domain/audit record). [If a discrepancy between the two stores is a concern, a reconciliation pass is an Open Question — OQ-4.]"
  constraints:
    - "The phase MUST hook into the EXISTING Stage-0 → (Stage-1a Router) → confirm pipeline, not introduce a parallel one. Two integration shapes are on the table (OQ-1): (A) a Stage-0 fast-path — when ClassifierResult.intent == REMINDER & confidence ≥ threshold & time parsed, _run_text_pipeline branches BEFORE the router into a reminder-confirm flow (matches tech-spec §6 'Fast-path — прямая запись в jobs без Stage-1a/1b'); (B) extend the Stage-1a Router (new RouterIntent.REMINDER + prompts/inbox.md) and a route_action variant, hooking _handle_route_confirm (matches the bd/epic note 'extended route_action payload'). Recommendation in Discovery: (A) for reminders (no router round-trip needed once Stage-0 already said 'reminder' and the time is parsed) — it is also what tech-spec §6 mandates; (B)'s router-extension is the right shape for the digest/aggregator case (which needs domain resolution) and belongs to Phase-D.b. Final call at the Brainstorming gate."
    - "jobs.Job is the SSoT domain record for a scheduled job; APScheduler's jobstore is the trigger engine. Phase-D writes BOTH (a jobs.Job row + an APScheduler job whose callable is fire_job(job_id)). It does NOT invent a separate 'planner' store and does NOT reuse ai-steward's planner.json (isolation per CLAUDE.md §Изоляция)."
    - "No CLI, no PriorityJobQueue worker loop is touched for reminder_job (it is TG-deliver-only). The asyncio.PriorityQueue worker that drains queued jobs into Claude runs is NOT yet implemented in the repo; building it is a prerequisite for digest_job and is therefore a strong reason to keep digest in Phase-D.b."
    - "Recurrence (daily/weekly/monthly) is NOT parsed in this phase — classifier.time_parse.parse_time returns a single absolute when_utc only. Recurring schedules require either an LLM-emitted structured recurrence or a rule-based recurrence parser, both deferred to Phase-D.b."
  risks:
    - id: R-1
      text: "Time-in-the-past / 'разбуди в 6' when it is already 07:00 — dateparser with RELATIVE_BASE=now will resolve '6:00' to today 06:00 (past). FR-4 rejects past times; but the user probably meant tomorrow 06:00. Mitigation: in Brainstorming decide whether to (a) reject with a hint, or (b) roll forward to the next future occurrence of that wall-clock time. (b) is friendlier but needs care with DST. Discovery leans (a) for the MVP (explicit, no surprises) with (b) as a fast-follow."
    - id: R-2
      text: "The reminder text to store (ReminderPayload.message) — is it the user's whole message, or a distilled 'позвонить врачу' part? The Stage-0 classifier already returns distilled_payload; if it carries a clean 'reminder_text' that is preferred, else fall back to the raw user text minus the time phrase (or just the raw text). Mitigation: prefer distilled_payload['reminder_text'] if present, else raw text; covered by a unit test. The classifier prompt may need a tiny addition to emit reminder_text (a prompt semver bump) — flag in Brainstorming."
    - id: R-3
      text: "User TZ unknown (UserRecord.tz is None / user not in users.toml yet) — parse_time needs a ZoneInfo. Mitigation: a configured default TZ (settings, e.g. Europe/Moscow) used as fallback; logged when the fallback is used. The recap message states the resolved local time so the user can catch a wrong TZ."
    - id: R-4
      text: "Double-confirm / re-send within the 10-min TTL — handled exactly as Phase-C: resolve() is race-safe (UPDATE … WHERE status='pending'); request_explicit is idempotent on (telegram_id, payload_hash). A second confirm tap → None → 'stale' reply, no duplicate job. Verify with a unit test."
    - id: R-5
      text: "Restart between job-create and fire — APScheduler SQLAlchemyJobStore persists the DateTrigger, so the reminder still fires. But if the bot is DOWN at the exact fire time, APScheduler's misfire_grace_time (currently 30s in job_defaults) would drop it. Mitigation: for reminder_job pass a generous misfire_grace_time (or None = run-on-startup-if-missed) when adding the job; decide the value in Brainstorming. Log scheduler.reminder.misfired if it happens."
    - id: R-6
      text: "Scope creep: 'while we're here' temptations — /reminders list, cancel-a-reminder, snooze, recurring schedules, the digest/aggregator. All explicitly OUT (see Scope). The phase is already borderline-large; keeping it to one-shot reminder_job end-to-end is the fit-not-fragment call. If Brainstorming finds even the reminder slice too big, split confirm-flow vs firing-handler — but they share so much context that one phase is the likely right size."
    - id: R-7
      text: "Firing handler needs a TgSender / aiogram Bot instance, but APScheduler jobs are module-level functions invoked by the scheduler thread/loop, not closures over the bot. Mitigation: either (a) a module-level 'set_bot_sender(...)' wired once at startup that fire_job reads, or (b) fire_job reconstructs a minimal sender from settings (bot token) on demand, or (c) pass the bot via APScheduler's job kwargs only if it's picklable (it is not — reject). Decide (a) vs (b) in Brainstorming; (a) is lighter and keeps one Bot instance."
  scope:
    in:
      - "ReminderPayload variant in storage/jobs/payloads.py added to JobPayload union + parse_job_payload coverage."
      - "A reminder-creation path: detect REMINDER intent + parsed future time in _run_text_pipeline (shape A or B per OQ-1) → build a reminder confirm draft → ConfirmationService.request_explicit with the 2-button keyboard → on confirm, create the jobs.Job row + register the APScheduler DateTrigger; on cancel/stale, ru notices."
      - "A new scheduler firing module (e.g. src/ai_steward_wiki/scheduler/firing.py) with fire_job(job_id) — loads the jobs.Job row, dispatches by kind, for reminder_job delivers the message to chat_id via the bot sender, marks the row done/failed, emits scheduler.reminder.* logs; plus a small job-creation helper (create_reminder_job(session, scheduler, ...)) that writes the row and adds the APScheduler job atomically."
      - "Wiring in __main__.py: a module-level bot-sender registration for the firing handler; pass the jobs sessionmaker + scheduler into the pipeline so the confirm callback can create jobs."
      - "Past-time / unparseable-time rejection copy; recurring-request 'not yet' copy (FR-5)."
      - "New structlog anchors per FR-7."
      - "Unit tests: REMINDER intent + future time → request_explicit called with the right draft, no job yet; confirm → jobs.Job row written with the right kind/payload/scheduled_at + scheduler.add_job called with a DateTrigger at when_utc; cancel → cancelled reply, no row; stale → stale reply; unparseable time → clarification, no confirm; past time → rejection, no confirm; recurring phrasing → 'not yet' reply; fire_job(reminder) → bot.send_message(chat_id, message) + row marked done; deliver failure → row marked + scheduler.reminder.deliver_failed; payload round-trip via parse_job_payload; double-confirm idempotency."
      - "An integration scenario extending tests/integration: a reminder message → recap → simulated confirm callback → assert a jobs.jobs row exists with kind='reminder_job' and the right scheduled_at_utc; (optionally) fast-forward / directly invoke fire_job and assert a TG send."
      - "GRACE: update M-STORAGE-JOBS-PAYLOADS / M-SCHEDULER (firing) / M-TG-PIPELINE-CLASSIFIER contracts + MODULE_MAPs, knowledge-graph CrossLinks (pipeline → scheduler.firing, scheduler.firing → storage.jobs), verification-plan refs (new tests + log anchors), development-plan Phase-D entry, ADR if a notable decision warrants one (fast-path-vs-router shape; jobs.Job+APScheduler dual-write; reminder confirm-vs-implicit)."
    out:
      - "digest_job / aggregator: recurrence parsing, CronTrigger jobs, --add-dir into multiple WIKIs, the PriorityJobQueue worker loop that drains queued CLI jobs, planner-semantics reads from jobs.db — RECOMMENDED to become Phase-D.b (a new bead under aisw-t2r). The MVP just replies 'not yet' (FR-5) for recurring requests."
      - "Reminder management UX: list / cancel / disable / snooze / edit a reminder, /reminders command, an inline 'cancel this reminder' button on the recap-after-create. Deferred."
      - "wiki_job (scheduled fixed-prompt Claude run, e.g. 'daily ingest') and the /cron_add NL flow — separate later work; not blocked by this phase but not in it."
      - "Roll-forward of a past wall-clock time to the next future occurrence (R-1 option b) — MVP rejects past times; roll-forward is a fast-follow."
      - "tracker_survey / tracker_followup / boundary_message kinds (the time-tracker layer) — out, separate epic."
      - "DLQ / auto-disable / retry plumbing for reminder_job — reminder_job is single-shot and TG-deliver-only; a delivery failure is logged and the row is marked, no DLQ row (consistent with tech-spec §3 which still lists DLQ=yes for reminder_job, but the MVP keeps it minimal — flag in Brainstorming if strict DLQ parity is wanted)."
  decided:
    - id: D-OQ-1
      text: "RESOLVED (Q&A 2026-05-12, /questions-answers): Integration shape = A (Stage-0 fast-path). _run_text_pipeline branches on Intent.REMINDER BEFORE the Stage-1a Router; the Router and prompts/inbox.md are NOT touched. Matches tech-spec §6."
    - id: D-OQ-2
      text: "RESOLVED: explicit confirm (FR-3) — reuse the Phase-C ConfirmationService.request_explicit + build_route_confirm_keyboard 2-button family. Job is created only after the user taps Подтвердить. (Implicit-ack rejected: keeps Phase-C symmetry + the race-safe machinery for free.)"
    - id: D-OQ-3
      text: "RESOLVED: SPLIT. aisw-kcz = reminder_job only (this discovery). digest_job → new bead 'Phase-D.b: digest_job' under epic aisw-t2r (recurrence parsing + PriorityJobQueue worker loop + multi-WIKI --add-dir). FR-5's 'not yet' reply for recurring requests stands."
    - id: D-R-1
      text: "RESOLVED: past wall-clock time → roll-forward via dateparser, NOT reject. Add a prefer_future: bool = False param to classifier.time_parse.parse_time; the reminder path calls it with prefer_future=True → dateparser settings PREFER_DATES_FROM='future' (TZ-aware, DST-safe). Reject is kept ONLY for an explicitly-past absolute date (e.g. «напомни 5 мая» when it is already 12 мая) where roll-forward is ambiguous. FR-4's blanket past-time rejection is narrowed accordingly."
  open_questions:
    - id: OQ-4
      text: "jobs.Job ↔ APScheduler jobstore consistency: do we need a startup reconciliation pass (e.g. drop orphaned APScheduler jobs whose jobs.Job row is done/cancelled, or re-add missing triggers)? Discovery says no for the MVP (single-shot, short-lived) but flags it."
    - id: OQ-5
      text: "Does the Stage-0 classifier prompt need a small bump to emit a clean reminder_text in distilled_payload (R-2)? Or is raw-text-minus-time good enough for the MVP? Affects whether prompts/classifier.md gets a semver bump in this phase. → Brainstorming."
    - id: OQ-6
      text: "Cancel-a-just-created reminder UX: explicit confirm means the job is created only on tap, so a 'призрачное' reminder can't exist — but should the post-create ack carry an inline «Отменить» button (cheap, since we have the job_id)? Or is that Phase-D.b/management-UX territory? → Brainstorming."
---

# Phase-D — Inbox-WIKI cron bridge (RouterDecision/Stage-0 → reminder job) — Discovery

> bd: **aisw-kcz** · epic: **aisw-t2r** (Inbox-WIKI routing wiring) · phase **Inbox-WIKI Phase-D**
> Depends on (done): aisw-zd9 (Phase-B route→ingest), aisw-e45 (Phase-C confirm loop).

## 1. What the user literally asked

"Wire the cron/job bridge: when a routed message carries a reminder/aggregator spec, insert a job into jobs.db via the scheduler API. Lightweight «разбуди в 6» → sendMessage-only job (no Claude); «каждый день в 9 сводка» → aggregator job with `--add-dir`."

## 2. What it actually means / current state

- **Today** `_run_text_pipeline` handles WIKI_INGEST/WIKI_QUERY/UNKNOWN via the Stage-1a Router → confirm → Librarian.ingest loop (Phases A/B/C). `Intent.REMINDER` and `Intent.DIGEST` exist in the classifier enum but are **not** in `_ROUTABLE_INTENTS` — they fall through to the generic `runner.run` flat-WIKI path, i.e. there is **no real handling of reminders or digests yet**. This phase closes that for reminders.
- `classifier.time_parse.parse_time` (dateparser → Haiku fallback → escalate, UTC invariant) already exists and is unused by the pipeline.
- `storage/jobs/models.py:Job` ORM (flat columns + JSON `payload`) exists and is **currently unused**. `storage/jobs/payloads.py` has `WikiRunPayload / DigestPayload / CronUserPayload / PurgePayload` — **no reminder payload**.
- `scheduler/core.py:build_scheduler` produces a configured `AsyncIOScheduler` + `SQLAlchemyJobStore` on `jobs.db`; `__main__.py` starts it and registers retention/snapshot jobs via `scheduler.add_job`. The 5-lane `PriorityJobQueue` exists but **no worker loop drains it into CLI runs** — so a CLI-backed `digest_job` would need that loop built first. `reminder_job` needs no CLI.
- The Stage-1a Router prompt (`prompts/inbox.md` v1.1.0) only emits `route|create_wiki|clarify|reject` — no reminder/aggregator intent.
- User TZ is available from `users.toml` (`UserRecord.tz`).

**Key implication:** the reminder slice (one-shot `reminder_job`, TG-deliver-only) is self-contained and the right size for one phase. The digest/aggregator slice pulls in recurrence parsing, the PriorityJobQueue worker loop, and multi-WIKI `--add-dir` resolution — Discovery **recommends splitting** it out as **Phase-D.b** (new bead under aisw-t2r). FR-5 makes the MVP say "not yet" for recurring requests so behaviour is honest.

## 3. Tech-spec alignment

- `reminder_job` is the only `kind` with `wiki_id=null` and no CLI; firing handler delivers a TG message directly (tech-spec §3 table + §6 fast-path).
- §6 fast-path: `intent=reminder & confidence ≥ 0.85 & time parsed` → **direct write to `jobs`, no Stage-1a/1b**. This is the basis for OQ-1 recommendation (shape A).
- All `jobs.db` datetimes UTC; user-TZ only on input/output (D-010, repo convention #6).
- Graduated confirmations (§8): writes get an explicit confirm — basis for FR-3 (OQ-2 may soften to implicit-ack).

## 4. Requirements & scope

See YAML frontmatter (FR-1..7, NFR-1..4, constraints, risks R-1..7, scope IN/OUT, open questions OQ-1..5). Headline:

- **IN:** `ReminderPayload` + union; detect REMINDER intent + parsed future time → reminder confirm → on confirm create `jobs.Job` row + APScheduler `DateTrigger`; `scheduler/firing.py` `fire_job(job_id)` → for `reminder_job` send the message to `chat_id` via the bot; `__main__` wiring; reject past/unparseable times; "not yet" for recurring; new log anchors; full unit + one integration test; GRACE refresh.
- **OUT:** `digest_job` / aggregator / recurrence / PriorityJobQueue worker loop (→ Phase-D.b); reminder management UX (list/cancel/snooze); `wiki_job` & `/cron_add`; roll-forward of past times; tracker/boundary kinds; DLQ/retry for `reminder_job`.

## 5. Recommended decisions going into Brainstorming

1. **OQ-1 → shape A** (Stage-0 fast-path) for reminders — no router round-trip; matches tech-spec §6.
2. **OQ-2 → explicit confirm** (FR-3) for symmetry with Phase-C and "writes get a confirm"; revisit if it feels heavy.
3. **OQ-3 → split**: this bead (aisw-kcz) = `reminder_job` only; create **aisw-??? Phase-D.b** for `digest_job`.
4. **OQ-4 → no reconciliation pass** in the MVP.
5. **OQ-5 → use raw-text-minus-time** for the MVP (no classifier prompt bump) unless `distilled_payload` already carries a clean reminder text.
6. **R-1 → reject past times** with a hint (roll-forward deferred).
7. **R-7 → module-level bot-sender registration** for `fire_job`.

## 6. Preflight (recorded)

- Pre-commit: `.pre-commit-config.yaml` present (trailing-whitespace, eof-fixer, check-yaml/toml, large-files, ruff + ruff-format, mypy --strict src, gitleaks); a `.git/hooks/pre-commit` is also installed via `core.hooksPath=.beads/hooks`. ✅ alive.
- Lint baseline: `make lint` → ruff ✅ (all checks passed), ruff-format ✅ (182 files), mypy --strict ✅ (68 source files, no issues). **0 errors** — any drift during the phase must be fixed in the same PR.
- Sentrux: no `.sentrux/rules.toml` in repo — Sentrux preflight skipped (project not onboarded).
