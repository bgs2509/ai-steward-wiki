---
feature: cron-add-queue-walking-skeleton
bd_id: aisw-02v
date: 2026-05-14
status: design
risk: medium
risk_justification: "4 modules touched (M-TG-CRON-ADD new, M-SCHEDULER-CRON-USER-PRODUCER new, M-SCHEDULER-CONSUMER new, M-STORAGE-JOBS payload widened); 1 __main__ wiring change; new user-facing TG command /cron_add (public-API surface); no DB schema migration (CronUserPayload column is JSON, no rows exist with old shape); no auth/security change; additive only."
evidence: strong
evidence_sources:
  - "Existing pattern M-SCHEDULER-FIRING (src/ai_steward_wiki/scheduler/firing.py v0.6.0) — APScheduler add_job + picklable int callback + module-level _ctx registry; mirrored 1:1 for cron-user producer"
  - "Existing payload union src/ai_steward_wiki/storage/jobs/payloads.py — CronUserPayload already in discriminated union (no migration), widened to typed Recurrence (mirrors DigestPayload)"
  - "Existing Recurrence + parse_recurrence in src/ai_steward_wiki/classifier/recurrence.py v0.0.3 (aisw-r2k) — reused without contract change"
  - "Existing Lane.CRON_WRITE = 2 in scheduler/queue.py — semantically correct for cron-fired user CLI (corrected from discovery's 'USER' note)"
  - "Existing kill_with_sequence in scheduler/core.py (D-021) — reused for systemd-run subprocess timeout path"
  - "Existing tg/output.py ChainSplitter — text chunking helper reused standalone (full D-025 deliver_output deferred to follow-up issue with run-output persistence)"
  - "Context7 verified 2026-05-14: aiogram v3 Router/Command/CommandObject API unchanged (matches handlers.py v0.4.0); APScheduler 3.x AsyncIOScheduler.add_job(callback, trigger, args=[int], id=str, replace_existing=True) is the still-current shape for the version pinned in this repo"
  - "tech-spec §3 D-011 (5-lane queue, 600s CLI timeout) + D-021 (SIGTERM grace SIGKILL) + D-023 confirm pattern + D-042 identity vocab"
open_questions: []

stack:
  language: "Python 3.11+"
  package_manager: "uv (pyproject.toml + uv.lock)"
  runtime_libs:
    - name: "aiogram"
      version_pin: "3.x (as currently pinned in pyproject)"
      role: "TG handler /cron_add via Router(Command('cron_add')); CommandObject.args for input"
      context7_verified: true
    - name: "APScheduler"
      version_pin: "3.x (AsyncIOScheduler + SQLAlchemyJobStore — current pinned shape)"
      role: "Persistent CronTrigger jobs (jobs.db SQLAlchemyJobStore); add_job(fire_cron_user_job, CronTrigger(**recurrence.to_cron(), timezone=user_tz), args=[job_id], id=str(job_id), replace_existing=True)"
      context7_verified: true
      note: "Context7 default snippets surface APScheduler 4.x (AsyncScheduler/add_schedule). NOT used here — repo is locked to 3.x, and the existing firing.py pattern is the authoritative reference."
    - name: "Pydantic v2"
      version_pin: "current"
      role: "CronUserPayload widening (typed Recurrence) + in-memory CronQueueMsg discriminated union"
      context7_verified: false
      note: "API unchanged vs. existing payloads.py — no version sensitivity"
    - name: "structlog"
      version_pin: "current"
      role: "BLOCK-anchored logs with correlation_id, owner_telegram_id, chat_id, job_id"
      context7_verified: false
    - name: "asyncio"
      version_pin: "stdlib"
      role: "Subprocess spawn (asyncio.create_subprocess_exec), wait_for(timeout=600), consumer drain task"
  external_processes:
    - name: "systemd-run"
      mode: "--scope --collect"
      slice: "aisw-cli.slice (existing per D-021/D-035)"
      role: "Per-job CGroup scope; cleanup-on-exit via --collect; SIGTERM-grace-SIGKILL via kill_with_sequence"
    - name: "claude (Claude Code CLI)"
      auth: "subscription via CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code"
      argv_builder: "claude_cli/common.py (resolve_binary + build_env + system_prompt_argv + neutral_cwd) — reused"

modules:
  M-TG-CRON-ADD:
    file: "src/ai_steward_wiki/tg/cron_add.py"
    role: "RUNTIME"
    purpose: "/cron_add TG handler — parse NL recurrence + command, persist Job via producer, reply with parsed schedule + job_id."
    depends:
      - aiogram (Router, Command, CommandObject, Message)
      - structlog
      - ai_steward_wiki.classifier.recurrence.parse_recurrence
      - ai_steward_wiki.scheduler.cron_user.create_cron_user_job
      - ai_steward_wiki.storage.sessions.users (user_tz resolution)
    inputs:
      register_cron_add_handlers:
        router: "aiogram.Router (caller-owned, registered in tg/handlers.py build_router)"
        get_user_tz: "Callable[[int], Awaitable[str]] (owner_telegram_id → IANA tz)"
    outputs:
      register_cron_add_handlers: "None — side-effect: handler registered on router"
    side_effects:
      - "Persists 1 jobs.Job row via create_cron_user_job"
      - "Registers 1 APScheduler CronTrigger job"
      - "Sends 1-2 TG messages via message.answer()"
    log_anchors:
      - "tg.command.cron_add (entry; usage_invalid|escalate|parsed|scheduled|failed)"
      - "tg.command.cron_add.parsed (recurrence resolved)"
      - "tg.command.cron_add.escalate (parser returned escalate=True)"
      - "tg.command.cron_add.scheduled (job persisted)"
      - "tg.command.cron_add.failed (exception)"

  M-SCHEDULER-CRON-USER:
    file: "src/ai_steward_wiki/scheduler/cron_user.py"
    role: "RUNTIME"
    purpose: "Cron-user job firing bridge — INSERT jobs.Job + register CronTrigger on AsyncIOScheduler; on fire, push CronQueueMsg to PriorityJobQueue(lane=CRON_WRITE)."
    depends:
      - apscheduler (AsyncIOScheduler, CronTrigger)
      - sqlalchemy.ext.asyncio (AsyncSession, async_sessionmaker)
      - structlog, pydantic
      - ai_steward_wiki.storage.jobs.models.Job
      - ai_steward_wiki.storage.jobs.payloads.CronUserPayload
      - ai_steward_wiki.classifier.recurrence.Recurrence
      - ai_steward_wiki.scheduler.queue.PriorityJobQueue, Lane
    inputs:
      set_cron_user_context:
        scheduler: "AsyncIOScheduler"
        queue: "PriorityJobQueue"
        jobs_session_maker: "async_sessionmaker[AsyncSession]"
      create_cron_user_job:
        owner_telegram_id: int
        chat_id: int
        recurrence: "Recurrence"
        command: str
        user_tz: "str (IANA tz, e.g. 'Europe/Moscow')"
        wiki_id: "str | None"
    outputs:
      create_cron_user_job: "int (jobs.id)"
      fire_cron_user_job: "None"
    side_effects:
      - "INSERT 1 row into jobs.jobs (kind='cron_user', status='scheduled')"
      - "scheduler.add_job(... id=str(job_id), replace_existing=True)"
      - "On fire: queue.put(Lane.CRON_WRITE, CronQueueMsg)"
    log_anchors:
      - "scheduler.cron_user.scheduled (job row + cron registered)"
      - "scheduler.cron_user.fire (callback executed; enqueued)"
      - "scheduler.cron_user.fire.job_missing (jobs row vanished)"
      - "scheduler.cron_user.fire.failed (push to queue raised)"
    notes:
      - "Mirrors firing.py shape: module-level _ctx registry installed by set_cron_user_context; fire callback takes ONLY picklable int (SQLAlchemyJobStore-safe)."
      - "Does NOT execute CLI — only enqueues. CLI execution is the consumer's concern."

  M-SCHEDULER-CONSUMER:
    file: "src/ai_steward_wiki/scheduler/consumer.py"
    role: "RUNTIME"
    purpose: "Single async drain loop over PriorityJobQueue — for each item: spawn `systemd-run --scope --collect` wrapping claude CLI, capture stdout, timeout=600s, deliver result to chat_id via bot.send_message (chunked)."
    depends:
      - asyncio
      - structlog, pydantic
      - aiogram (Bot — typing-only at module level; runtime via constructor DI per R-1)
      - sqlalchemy.ext.asyncio (AsyncSession, async_sessionmaker)
      - ai_steward_wiki.scheduler.queue.PriorityJobQueue, QueueItem
      - ai_steward_wiki.scheduler.core.kill_with_sequence
      - ai_steward_wiki.claude_cli.common (resolve_binary, build_env, neutral_cwd, system_prompt_argv, truncate_stderr)
      - ai_steward_wiki.tg.output.ChainSplitter (standalone reuse, ≤PART_MAX_CHARS chunking)
      - ai_steward_wiki.storage.jobs.models.Job (status updates: 'running' → 'finished'|'failed')
    inputs:
      CronConsumer(constructor):
        queue: "PriorityJobQueue"
        bot: "aiogram.Bot"
        claude_binary: "str"
        claude_config_dir: "pathlib.Path"
        prompt_path: "pathlib.Path"
        jobs_session_maker: "async_sessionmaker[AsyncSession]"
        timeout_s: "int = 600"
        slice_name: "str = 'aisw-cli.slice'"
      run: "()  # blocks until cancelled"
    outputs:
      run: "None (raises CancelledError on shutdown)"
    side_effects:
      - "Per-item: 1 systemd-run subprocess; 1-3 bot.send_message calls (chunked output)"
      - "Per-item: jobs.jobs row status mutated 'scheduled' → 'running' → 'finished'|'failed'"
    log_anchors:
      - "scheduler.consumer.drained (got queue item)"
      - "scheduler.consumer.exec.started (subprocess spawned)"
      - "scheduler.consumer.exec.done (exit_code=0; duration_ms)"
      - "scheduler.consumer.exec.timeout (killed after 600s)"
      - "scheduler.consumer.exec.failed (exit_code != 0; stderr tail)"
      - "scheduler.consumer.delivered (bot.send_message OK; n_chunks)"
      - "scheduler.consumer.deliver_failed (TelegramAPIError)"

  M-STORAGE-JOBS-PAYLOAD-WIDEN:
    file: "src/ai_steward_wiki/storage/jobs/payloads.py"
    role: "TYPES"
    purpose: "Widen CronUserPayload to mirror DigestPayload shape — typed Recurrence + free-form command + optional wiki_id."
    change:
      before: |
        class CronUserPayload(_PayloadBase):
            kind: Literal["cron_user"] = "cron_user"
            wiki_id: str
            cron_expr: str
            user_text: str
      after: |
        class CronUserPayload(_PayloadBase):
            kind: Literal["cron_user"] = "cron_user"
            recurrence: Recurrence            # typed (was: cron_expr: str)
            command: str                       # was: user_text
            wiki_id: str | None = None         # was: required str
    migration: "None — JSON column, zero existing rows, no Alembic step needed. parse_job_payload unchanged signature."

functional_design:
  ux_flow_cron_add: |
    1. User: `/cron_add каждый день в 9 утра | напомни выпить витамины`
    2. Handler splits on first `|`: schedule_text="каждый день в 9 утра", command="напомни выпить витамины"
    3. parse_recurrence(schedule_text, user_tz=resolve_tz(owner)) → Recurrence | escalate
       3a. escalate → reply "Не понял расписание. Попробуй: каждый день в 9, каждую среду в 14:00, каждого 5го в 10:00" → return
       3b. invalid command (empty after pipe) → reply usage → return
    4. create_cron_user_job(owner, chat, recurrence, command, user_tz, wiki_id=None) → job_id (int)
    5. Reply: "✅ Запланировано (id={job_id}): {recurrence_humanized}. Команда: {command_preview}"
       recurrence_humanized examples (ru):
         daily      → "каждый день в 09:00 (Europe/Moscow)"
         weekly     → "каждую среду в 14:00 (Europe/Moscow)"
         monthly    → "каждое 5-е число в 10:00 (Europe/Moscow)"

  ux_flow_cron_fire: |
    1. APScheduler fires CronTrigger at scheduled UTC moment.
    2. fire_cron_user_job(job_id) callback:
       - SELECT jobs.Job WHERE id=job_id; if status != 'scheduled' or row missing → log + return (idempotent on replay)
       - Build CronQueueMsg(job_id, owner, chat, command, correlation_id=uuid4().hex, scheduled_at_utc=now())
       - await queue.put(Lane.CRON_WRITE, msg)
       - UPDATE jobs.Job SET status='queued', started_at_utc=NULL — leaving 'running' transition to consumer
    3. Consumer drain loop:
       - item = await queue.get()
       - UPDATE jobs.Job SET status='running', started_at_utc=now()
       - argv = ['systemd-run', '--scope', '--collect', '--slice=aisw-cli.slice',
                 '--unit=cli-{job_id}',
                 '--setenv=CLAUDE_CONFIG_DIR=...',
                 '--', claude_binary, *system_prompt_argv(prompt_path), command]
       - proc = await asyncio.create_subprocess_exec(*argv, stdout=PIPE, stderr=PIPE, cwd=neutral_cwd, env=build_env(...))
       - try: stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
         except TimeoutError: await kill_with_sequence(proc); reply "❌ Тайм-аут 10 минут" → status='failed'
       - exit=0 → ChainSplitter.split(stdout.decode()) → bot.send_message * n; status='finished'
       - exit!=0 → reply "❌ Ошибка ({code}): {truncate_stderr(stderr)}"; status='failed'

  module_map_visual: |
    TG /cron_add ──→ tg/cron_add.py (M-TG-CRON-ADD)
                          │ parse_recurrence + validate
                          ▼
              scheduler/cron_user.py::create_cron_user_job
                          │ INSERT Job + scheduler.add_job
                          ▼
                 [APScheduler CronTrigger fires]
                          │
                          ▼
              scheduler/cron_user.py::fire_cron_user_job (callback)
                          │ build CronQueueMsg
                          │ queue.put(Lane.CRON_WRITE, msg)
                          ▼
              scheduler/queue.py::PriorityJobQueue (asyncio.PriorityQueue)
                          │
                          ▼
              scheduler/consumer.py::CronConsumer.run (drain loop)
                          │ systemd-run --scope --collect -- claude ...
                          │ asyncio.wait_for(communicate, 600s)
                          ▼
              aiogram.Bot.send_message (chunked via ChainSplitter)

queue_msg_design:
  module: "src/ai_steward_wiki/scheduler/queue_payloads.py (new, small)"
  rationale: "Keep scheduler/queue.py pure (Lane/QueueItem/PriorityJobQueue). Pydantic payload types in sibling module so future kinds extend the union without touching queue.py."
  shape: |
    class _MsgBase(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)

    class CronUserQueueMsg(_MsgBase):
        kind: Literal["cron_user"] = "cron_user"
        job_id: int
        owner_telegram_id: int
        chat_id: int
        command: str
        correlation_id: str
        scheduled_at_utc: datetime  # UTC

    QueueMsg = Annotated[CronUserQueueMsg, Field(discriminator="kind")]
    # discriminator left in place so future kinds add a sibling without
    # widening callers — NFR-5.

approach_decisions:
  - id: "AD-01"
    title: "Producer/consumer split (not direct fire)"
    options:
      A: "Producer enqueues + separate consumer dequeues+executes [chosen]"
      B: "APScheduler callback directly spawns CLI + delivers (skip queue) [rejected]"
      C: "Two-tier consumer with separate exec + deliver queues [rejected]"
    decision: "A"
    rationale: "Q&A 2026-05-14 chose Variant1 (walking skeleton with real queue). B defeats aisw-02v's purpose (queue must be load-bearing). C is premature abstraction — single failure mode + single consumer is simpler and meets NFR-1 (concurrency=1)."

  - id: "AD-02"
    title: "No interactive confirm in walking skeleton"
    options:
      A: "Auto-schedule on parse-success; reply renders parsed recurrence as proof [chosen]"
      B: "Explicit ConfirmationService (D-023) inline keyboard confirm/cancel [rejected]"
      C: "1-button cancel keyboard with cron_cancel:<id> callback [rejected]"
    decision: "A"
    rationale: "Discovery scope.out excludes /cron_delete and /cron_edit. Adding confirm without delete creates UX asymmetry. Parsed-cron echo in reply (humanized ru) gives sufficient visual verification — parser is rule-based (deterministic) per M-CLASSIFIER-RECURRENCE. If parser returns escalate=True, handler rejects with usage hint (no silent failure). /cron_confirm + /cron_delete + /cron_edit are a single follow-up bd issue."

  - id: "AD-03"
    title: "Reuse ChainSplitter standalone vs. full deliver_output(kind='reply')"
    options:
      A: "ChainSplitter + bot.send_message [chosen]"
      B: "Full deliver_output (persistence + audit run_outputs) [rejected]"
    decision: "A"
    rationale: "Full D-025 persistence is concerned with run-output indexing for digest re-render. Cron-user runs are user-initiated and ephemeral — no need for /expand or document fallback in walking skeleton. ChainSplitter call signature is stable (ChainSplitter().split(text) returns list[str]); swap to deliver_output is a clean refactor in a future iteration when run-history matters."

  - id: "AD-04"
    title: "Lane.CRON_WRITE (=2), not 'USER' as in discovery"
    options:
      A: "Lane.CRON_WRITE [chosen]"
      B: "Lane.USER_WRITE [rejected — wrong semantics]"
    decision: "A"
    rationale: "queue.py enum already has CRON_WRITE=2 between USER_WRITE=1 (interactive user write) and DIGEST=3. Cron-fired jobs are scheduled writes, not interactive — discovery's 'USER' was a paraphrase, not the canonical enum name. CRON_WRITE = 2 keeps USER_WRITE = 1 reserved for synchronous user-driven WIKI writes (the inbox path)."

  - id: "AD-05"
    title: "Widen CronUserPayload (typed Recurrence) — no Alembic migration"
    options:
      A: "Widen in-place [chosen]"
      B: "New CronUserV2Payload, deprecate old [rejected]"
    decision: "A"
    rationale: "CronUserPayload is currently never persisted (no row exists with kind='cron_user'). Pydantic discriminated union is read at parse time only; the column is JSON. Zero rows, zero serialized references — replacing the model in place is safe. Test suite must grep for any test using .cron_expr / .user_text and update."

  - id: "AD-06"
    title: "Consumer ownership of jobs.Job status transitions"
    options:
      A: "Producer sets 'queued' on enqueue; consumer flips 'running' → 'finished'|'failed' [chosen]"
      B: "Producer sets 'running'; consumer flips terminal [rejected]"
    decision: "A"
    rationale: "Producer enqueues to in-memory queue (microsecond op); 'running' should reflect actual subprocess spawn. Three terminal states give better observability for follow-up cron_list. Status enum extension: 'scheduled' (initial after cron_add) → 'queued' (producer enqueued) → 'running' (consumer spawned process) → 'finished' | 'failed' | 'cancelled' (future)."

  - id: "AD-07"
    title: "Single consumer task in bot lifecycle, NOT separate process"
    options:
      A: "asyncio.create_task in __main__ startup, cancel on shutdown [chosen]"
      B: "Separate systemd service [rejected]"
    decision: "A"
    rationale: "Bot already owns aiogram.Bot ref required for delivery. Single-process keeps in-memory queue intact (vs requiring IPC). NFR-1 specifies single-drain at-most-once. Acceptable for walking skeleton; multi-worker is a follow-up if backpressure becomes real."

verification_plan_sketch:
  unit_tests:
    - "tests/unit/test_cron_add_handler.py — parse path (happy / escalate / no pipe / empty command)"
    - "tests/unit/test_cron_user_producer.py — create_cron_user_job inserts row + scheduler.add_job called with correct CronTrigger; fire_cron_user_job pushes to mock queue"
    - "tests/unit/test_cron_consumer.py — fake queue + fake bot + stub spawn (Spawner Protocol): happy path (exit=0 → send_message), timeout path (kill_with_sequence called), non-zero exit (error message sent)"
    - "tests/unit/test_queue_payloads.py — Pydantic discriminator round-trip"
    - "tests/unit/test_payloads_cron_user_widened.py — Recurrence field, command field, wiki_id optional"
  integration_test:
    - "tests/integration/test_cron_add_flow.py — register handler on real Router, simulate /cron_add message, advance APScheduler clock manually, assert queue gets item, run 1 consumer iteration with stub Spawner returning canned stdout, assert bot.send_message(chat_id, expected_text) was called"
  coverage_target: "≥80% on the 3 new modules (per NFR-6)"
---

# Design: `/cron_add` + Queue Consumer (Walking Skeleton)

## 1. Summary

3 new modules + 1 payload widen + 1 wiring step in `__main__.py`. Mirrors `M-SCHEDULER-FIRING` shape (proven by digest_job since aisw-oqq). Queue becomes load-bearing on first commit.

```
tg/cron_add.py   → scheduler/cron_user.py → [APScheduler] → scheduler/cron_user.py(fire)
                                                                      │
                                                                      ▼
                                                         scheduler/queue.py (CRON_WRITE)
                                                                      │
                                                                      ▼
                                                         scheduler/consumer.py (drain)
                                                                      │ systemd-run scope
                                                                      ▼
                                                            aiogram.Bot.send_message
```

## 2. Risks & Mitigations (unchanged from discovery — restated for traceability)

1. **R-1 Bot/scheduler coupling** → constructor-DI bot into CronConsumer (mirrors firing.py's TgSender registry pattern; not a new coupling).
2. **R-2 Orphan scopes** → `systemd-run --scope --collect` + `kill_with_sequence` on timeout.
3. **R-3 APScheduler+asyncio race** → AsyncIOScheduler+SQLAlchemyJobStore proven by digest_job; reuse existing factory `scheduler.core.build_scheduler`.
4. **R-4 NL parse ambiguity** → `parse_recurrence` returns `RecurrenceParseResult(escalate=True, reason=...)` on ambiguity; handler rejects with usage hint (no silent bad-cron).
5. **R-5 Bot offline at fire** → APScheduler persistent jobstore replays on bot restart; at-most-once delivery accepted for MVP (documented in `/help` / runbook update — out of scope here, follow-up issue).
6. **R-6 Queue backpressure** → unbounded `asyncio.PriorityQueue`; single consumer drains FIFO within lane. Rate limit is follow-up.

## 3. Open Questions

None. All Q&A from 2026-05-14 resolved; no new architectural fork in the chosen variant.

## 4. ADR Candidates

None. All decisions in `approach_decisions` reuse existing patterns (firing.py shape, claude_cli/common, ChainSplitter). No new architectural ground is broken.

## 5. Next Step (Step 6: GRACE Ask)

Targeted `grace-refresh` to sync the knowledge-graph for the 4 affected modules + `grace-ask` to confirm no similar logic exists elsewhere that should be consolidated.
