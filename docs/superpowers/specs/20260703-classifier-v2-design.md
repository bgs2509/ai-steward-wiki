---
feature: classifier-v2
bd_id: aisw-xi8
module_id: M-CLASSIFIER-STAGE0
status: stable
date: 2026-07-03
risk: high
evidence: strong
open_questions: []
context7_verified:
  - "apscheduler==3.11.0 — scheduler.reschedule_job(job_id, trigger=…) replaces ONLY the trigger, preserves job id/args, recalculates next run; add_job(replace_existing=True) required for persistent stores; DateTrigger=run-once, CronTrigger=recurring. Context7 MCP was unreachable this session (4× fetch failed) — verified against the official 3.x userguide (apscheduler.readthedocs.io/en/3.x/userguide.html, WebFetch) + in-repo working precedent firing.py:665 (reschedule_job), :213/:560 (add_job)."
  - "pydantic==2.9.2 — discriminated unions with Field(discriminator='kind'): adding a new member with a distinct Literal tag is supported; validation selects the member by tag; an unmatched tag raises union_tag_invalid (so NEW kinds never break OLD persisted rows). Verified against official unions docs for 2.9 (pydantic.dev/docs/validation/2.9/concepts/unions/, WebFetch) + in-repo precedent payloads.py CHANGELOG v0.0.3/v0.0.7 (additive members, no migration)."
  - "dateparser==1.2.0 — unchanged usage (D-010 pre-verified in prior iterations; no new API surface in this design)."
stack:
  - Python 3.11 + existing in-repo machinery only; no new external dependencies.
  - Reused: ConfirmationService + pending_confirms (tg/confirm.py), inline-keyboard picker precedent (wikipick, confirm.py:150-175), hint_match tokenizer (inbox/hint_match.py:104-110), firing bridges (scheduler/firing.py), cron_user queue + consumer (scheduler/cron_user.py, consumer.py, queue_payloads.py), Recurrence + humanize_recurrence, time_parse/parse_recurrence validators, ClaudeCliBackend (classifier/backend.py) for the regression harness.
  - structlog anchors extended (tg.pipeline.job.*, scheduler.recurring.*, scheduler.check_in.*, classifier.slots.invalid).
decisions:
  - DEC-1 (dispatch shape) — _run_text_pipeline becomes a flat ordered if/elif over the 6 intents, each branch a thin delegation to a per-intent handler method (_handle_wiki / _handle_job / _handle_web / _handle_chat / _handle_admin / _handle_unknown), following the existing _handle_reminder_intent/_handle_digest_intent precedent. Dispatch-table dict rejected (over-abstraction for 6 static branches, KISS); fully-inline blocks rejected (pipeline.py is already ~2900 lines).
  - DEC-2 (sub-threshold gate, resolved Q1) — REMINDER_CONFIDENCE_THRESHOLD is renamed CLASSIFIER_CONFIDENCE_THRESHOLD (module const, 0.85, NFR-7) and applied ONCE, immediately after tg.pipeline.classify.done: confidence < threshold AND intent ∈ {JOB, ADMIN} → ru clarification reply + return (new anchor tg.pipeline.subthreshold.clarify). Structural double-guarantee: the JOB branch has NO fall-through to the generic runner at all — every job path terminates in a deterministic handler, clarify, or confirm (kills #78/#96 by construction, FR-10).
  - DEC-3 (routable predicate) — frozenset _ROUTABLE_INTENTS is replaced by a predicate: routable ⇔ intent is UNKNOWN, or intent is WIKI with action ∈ {ingest, catalog} or action missing. wiki/query + wiki/lint → generic answer runner (streaming tail, as wiki_query today). The hint fast-path is additionally gated on action == "ingest" (catalog/None goes straight to the Sonnet router — a catalog request has no content to keyword-match; conservative). wiki/catalog thus reuses the existing Stage-1a RouterIntent.LIST_WIKIS path with zero router changes (FR-11).
  - DEC-4 (slots contracts, Fail Fast with graceful default) — two frozen Pydantic models in classifier/schema.py: WikiSlots(action: Literal["ingest","query","lint","catalog"] | None = None) and JobSlots(action: Literal["create","cancel","list","reschedule"] = "create", kind: Literal["once","recurring","check_in","digest"] = "once", time_expr: str = "", schedule_expr: str = "", text: str = "", needle: str = ""). Parsed at the pipeline boundary by a lenient helper parse_slots(model, distilled_payload): unknown keys ignored, ValidationError → default instance + log anchor classifier.slots.invalid (never a user-facing error). wiki.action=None ⇒ router path (covers the measured 99/100 miss — «Покажи мои вики» with empty action still lands on list_wikis); job.kind default "once" (dominant case; time/recurrence validators still gate before anything is scheduled). Strict-fail rejected: LLM output must not 500 the turn.
  - DEC-5 (Protocol widening) — see OQ-1. WikiRunner.run / StreamingDelivery.run_and_deliver gain action: str | None = None; __main__ adapter branches become (intent is WIKI and action == "query") for adaptive scoping and (intent is WEB) for the WebSearch read-only config. All other adapter mechanics untouched.
  - DEC-6 (new storage payloads, resolved Q2) — two ADDITIVE union members in storage/jobs/payloads.py: RecurringReminderPayload(kind="recurring_reminder", message: str, recurrence: Recurrence, category: Literal["medication","event","generic"]="generic") and CheckInPayload(kind="check_in", question_topic: str, recurrence: Recurrence, wiki_id: str | None = None). No Alembic migration (JSON column, additive tag — pydantic verified); existing kinds untouched (FR-15).
  - DEC-7 (recurring firing) — new fire_recurring_job callback in firing.py (picklable int arg, CronTrigger id=f"recurring:{job_id}"), plain TG send of payload.message, NO terminal status transition (job stays enabled), NO LLM (NFR-2). Delivery-failure policy converged on the digest precedent: 3 consecutive send failures → disable + move_to_dlq + remove_job (deterministic; protects against user-blocked-bot infinite retries). fire_job is NOT generalized — its once-semantics (terminal done, user_state card guard aisw-z0s) stay separate (three similar lines > premature abstraction).
  - DEC-8 (check_in firing) — mirrors cron_user mechanics end-to-end: create_check_in_job (Job row kind='check_in' committed BEFORE add_job, CronTrigger id=f"check_in:{job_id}") → on fire push a new additive CheckInQueueMsg(kind="check_in", job_id, owner_telegram_id, chat_id, question_topic, correlation_id, scheduled_at_utc) into PriorityJobQueue Lane.CRON_WRITE → consumer.py gains a per-kind branch: CLI run generating ONE ru question about question_topic (prompt assembled from a new prompts/check_in.md); on exit≠0 / timeout → deterministic ru fallback «Хотел спросить: {question_topic}» sent verbatim (FR-6, new anchor scheduler.check_in.fallback). QueueMsg union widens additively (its contract explicitly reserved the discriminator for future kinds, queue_payloads.py:6-8). Lane sharing with cron_user (concurrency=1) accepted at family scale — KISS, revisit if queue latency shows in logs.
  - DEC-9 (job management module) — NEW module src/ai_steward_wiki/scheduler/manage.py (M-SCHEDULER-MANAGE), pure functions over an AsyncSession + AsyncIOScheduler: list_owner_jobs(session, owner_telegram_id) → enabled user-facing jobs (kinds reminder_job|recurring_reminder|check_in|digest|cron_user; purge/wiki_run excluded), render via humanize_recurrence / local time; match_jobs_by_needle(jobs, needle) → casefold whole-token overlap scoring reusing the hint_match tokenizer (promote inbox/hint_match._tokens to a public tokens() helper — one-line rename, tests keep passing); cancel_job(...) → scheduler.remove_job(job_key) + Job.status='cancelled'; reschedule_once(...) → scheduler.reschedule_job(key, DateTrigger(new)) + row update; reschedule_recurring(...) → scheduler.reschedule_job(key, CronTrigger(**rec.to_cron())) + payload.recurrence JSON rewrite (resolved Q3: both shapes in MVP). Pipeline handlers stay thin (pipeline.py must not grow logic). Job-key format registry: reminder:{id}, recurring:{id}, check_in:{id}, digest:{id}, cron_user:{id} — a single _job_key(kind, id) helper in manage.py, matching the existing literals (firing.py:217, :613; cron_user.py:121).
  - DEC-10 (disambiguation + destructive confirm UX) — existing pending_confirms machinery, two NEW categories: 'job_cancel' (explicit Confirm/Cancel keyboard before any mutation — destructive, FR-8) and 'job_pick' (numbered one-column inline keyboard jobpick:<pending_id>:<idx> when needle matches >1 job; wikipick precedent confirm.py:150-175; the pending draft carries the pending action cancel|reschedule, the candidate job ids, and the pre-parsed new schedule for reschedule). 0 matches → ru "not found" + the rendered list (no pending row). Numbered-text-reply rejected (stateful free-text parsing, worse a11y for elderly users than tapping a button). Old categories route_ingest|reminder|digest keep their exact strings and handlers — in-flight rows survive the deploy (R-3).
  - DEC-11 (create-confirm flows for new kinds) — job/create kind=recurring and kind=check_in each get an explicit confirm mirroring reminder/digest: parse schedule_expr via parse_recurrence (validator), recap via humanize_recurrence, categories 'job_recurring' / 'job_checkin'; kind=once and kind=digest reuse today's 'reminder' / 'digest' categories and handlers unchanged (FR-4/FR-7 — only the ENTRY into them moves from regex to classifier slots; time_expr/schedule_expr feed the validators instead of raw text where present, raw-text fallback kept per aisw-2mg NFR-2 precedent).
  - DEC-12 (prompt 2.0.0) — productionise /home/bgs/.claude/jobs/226e4379/tmp/classifier_minimal.md into prompts/classifier.md with: semver 2.0.0 + CHANGELOG entry (FR-14); an explicit verbatim-language rule for ALL free-text slots (time_expr/schedule_expr/text/needle — «NEVER translate; copy the user's words») (FR-12); a wiki/catalog worked example fixing the 99/100 miss; the per-intent distilled_payload contract section in the 1.4.0 house style; the chat-trap negative list and the canonical recurring-negative kept from the draft (FR-9).
  - DEC-13 (regression harness, resolved Q4) — corpus committed at tests/corpus/classifier/questions.json (session corpus format: id, text, expected {intent, action?, kind?}, optional accept[] for labelled-ambiguous cases) + runner scripts/classifier_regress.py: async bounded-concurrency (5) over the REAL ClaudeCliBackend (exercises the prod path incl. fenced-JSON unwrap), per-cluster report, exit≠0 when the gate fails. Gate: intent accuracy 100% (accept-lists honoured), intent+action+kind ≥ 99%. Makefile target classifier-regress; NOT in total-test — manual mandatory gate before any classifier.md commit, documented in the prompt CHANGELOG header. pytest-parametrized runner rejected (100 sequential CLI calls, poor cluster reporting).
  - DEC-14 (test migration) — mechanical mapping table (below) applied to ~20 files; a shared factory make_classifier_result(intent, action=None, kind=None, confidence=0.95, **slots) added to tests helpers so future taxonomy changes touch one place. Obsolete & deleted with their code: _detect_digest_action/_DIGEST_*_RE tests, _RECURRING_KEYWORDS punt tests, REMINDER_RECURRING_RU "not yet" tests. New RED-first suites: slots parsing, sub-threshold clarify, job list/cancel/reschedule + needle disambiguation + confirm, recurring firing (incl. 3-strike), check_in enqueue + consumer fallback, catalog routable path, verbatim-slot eval assertions.
---

# Design — classifier v2.0: 6 artifact-anchored intents (aisw-xi8)

Макро-подход зафиксирован пользователем (6 интентов, единственный Haiku-классификатор,
плоский switch, фиксы классификации только промптом) — здесь спроектирована оставшаяся
архитектура. Все повторно используемые API верифицированы (in-repo Read/Grep + official
docs; Context7 MCP в этой сессии недоступен — см. context7_verified).

## Module map

```
classifier/schema.py                 (M-CLASSIFIER-STAGE0)
├── Intent (v2: WIKI|JOB|WEB|CHAT|ADMIN|UNKNOWN)
├── WikiSlots / JobSlots             # DEC-4, frozen, lenient parse_slots()
└── ClassifierResult                 # unchanged shape

tg/pipeline.py                       (M-TG-PIPELINE-CLASSIFIER)
└── _run_text_pipeline
    ├── classify → classify.done (intent + action/kind logged, FR-18)
    ├── SUBTHRESHOLD gate            # DEC-2: conf<0.85 & intent∈{JOB,ADMIN} → clarify
    ├── CHAT   → canned ru reply     (ex-SMALLTALK block, renamed)
    ├── JOB    → _handle_job         # switch on JobSlots.action
    │   ├── create: once→reminder flow | digest→digest flow (existing)
    │   │           recurring/check_in→new confirm flows (DEC-11)
    │   ├── list / cancel / reschedule → scheduler/manage.py (DEC-9, DEC-10)
    ├── ADMIN  → ACK_ADMIN_RU        (unchanged)
    ├── WIKI   → routable predicate  # DEC-3: ingest/catalog/None→hint-fastpath*/router
    │            query/lint → generic runner (streaming tail)
    ├── WEB    → generic runner      (web config in adapter)
    └── UNKNOWN→ router              (unchanged)

storage/jobs/payloads.py             (M-STORAGE-JOBS)
├── + RecurringReminderPayload (kind="recurring_reminder")     # DEC-6
└── + CheckInPayload           (kind="check_in")

scheduler/firing.py                  (M-SCHEDULER-FIRING)
├── + create_recurring_job / fire_recurring_job                # DEC-7
scheduler/cron_user.py + consumer.py (M-SCHEDULER-CRON-USER/-CONSUMER)
├── + create_check_in_job; consumer check_in branch + ru fallback  # DEC-8
scheduler/queue_payloads.py
└── + CheckInQueueMsg (additive union member)

scheduler/manage.py (NEW, M-SCHEDULER-MANAGE)                  # DEC-9
├── list_owner_jobs / match_jobs_by_needle / cancel_job
├── reschedule_once / reschedule_recurring
└── _job_key(kind, id)  # reminder:|recurring:|check_in:|digest:|cron_user:

__main__.py — adapter re-anchoring on (intent, action)         # DEC-5 / OQ-1
prompts/classifier.md 2.0.0 + prompts/check_in.md              # DEC-12, DEC-8
tests/corpus/classifier/questions.json + scripts/classifier_regress.py  # DEC-13
```

## Data flow — job management (cancel example)

1. «убери напоминание про таблетки» → Haiku → intent=job, action=cancel, needle=«про таблетки».
2. Sub-threshold gate пройден (≥0.85) → `_handle_job` → `manage.list_owner_jobs` →
   `match_jobs_by_needle(jobs, "про таблетки")` (casefold token overlap, hint_match tokenizer).
3. Ровно 1 матч → pending row category='job_cancel' + recap «Отменить „…" (каждый день в 9:00)?»
   + Confirm/Cancel; on confirm → `manage.cancel_job` (remove_job + status='cancelled') + ru ack.
4. \>1 матч → pending row category='job_pick' + нумерованная inline-клавиатура
   `jobpick:<pending_id>:<idx>`; tap → тот же cancel-путь для выбранного job.
5. 0 матчей → ru «не нашёл» + отрендеренный список задач (без pending row).

Reschedule идентичен, плюс валидаторы: time_expr → parse_time (once) или schedule_expr →
parse_recurrence (recurring); неоднозначное время → существующие unparseable-реплики.

## Data flow — check_in

create: «спрашивай меня каждый вечер, что я ел» → job/create/check_in, schedule_expr=«каждый
вечер», text=«что я ел» → parse_recurrence → confirm 'job_checkin' → CheckInPayload + CronTrigger.
fire: CronTrigger → CheckInQueueMsg → Lane.CRON_WRITE → consumer: CLI генерирует ОДИН ru-вопрос
по question_topic (prompts/check_in.md) → send; exit≠0/timeout → «Хотел спросить: {topic}» (FR-6).

## Error handling

1. Невалидные slots → default-инстанс + `classifier.slots.invalid` (никогда не user-error).
2. Sub-threshold job/admin → ru-уточнение (`tg.pipeline.subthreshold.clarify`), никакого runner.
3. recurring: 3 подряд TG-send-фейла → disable + DLQ (`scheduler.recurring.auto_disabled`).
4. check_in CLI-фейл → детерминированный ru fallback (`scheduler.check_in.fallback`).
5. Старые pending-категории продолжают диспетчеризоваться после деплоя (R-3).

## Test-migration mapping (DEC-14)

```
old intent      → new (intent, action, kind)
reminder        → job, create, once
digest          → job, create, digest
wiki_ingest     → wiki, ingest, -
wiki_query      → wiki, query, -
wiki_lint       → wiki, lint, -
web_task        → web, -, -
smalltalk       → chat, -, -
admin           → admin, -, -
unknown         → unknown, -, -
```

## Verification sketch

1. Unit: schema slots (valid/invalid/None-action), pipeline flat switch per intent,
   sub-threshold gate, manage.py (list/needle/cancel/reschedule×2), firing recurring
   (fire + 3-strike), consumer check_in (ok + fallback), confirm categories new+legacy.
2. Integration (RUN_INTEGRATION): real-CLI classify smoke на 6 интентов.
3. Regression: make classifier-regress (100 корпус-кейсов, gate intent=100%, +action/kind ≥99%).
4. e2e log anchors: classify.done{intent,action,kind}, job.*, subthreshold.clarify,
   scheduler.recurring.*, scheduler.check_in.*.

## Resolved decisions (Gate 5, 2026-07-03, approved by user)

1. **OQ-1 (Protocol widening):** APPROVED — WikiRunner.run / StreamingDelivery.run_and_deliver gain `action: str | None = None` (backward-compatible defaulted param). This is a deliberate deviation from ADR-034 / aisw-o6m FR-7 ("WikiRunner Protocol MUST NOT change") and MUST be recorded as a new ADR at Step 8/13. Alternative (encoding action into Intent enum) rejected: recreates the taxonomy being deleted.
