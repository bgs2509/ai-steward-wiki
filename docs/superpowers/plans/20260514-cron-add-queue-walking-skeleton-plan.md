# Plan: `/cron_add` + queue consumer (walking skeleton) — aisw-02v

> SSoT for execution. Source design: `docs/superpowers/specs/20260514-cron-add-queue-walking-skeleton-design.md`.
> Discovery: `docs/superpowers/specs/20260514-cron-add-queue-walking-skeleton-discovery.md`.
> Knowledge graph + verification + development plan already updated (Step 7).
> TDD discipline: every code change preceded by failing test (RED → GREEN → REFACTOR).

## Phase plan (one context window — Opus 4.7 1M)

6 phases, ~500 lines of new src + ~800 lines of tests. Fits comfortably in one execution window — no split needed.

```
Phase 1 — payload widen + queue_payloads             (data foundation)
Phase 2 — scheduler/cron_user.py                     (producer)
Phase 3 — scheduler/consumer.py                      (consumer + spawn wrapper)
Phase 4 — tg/cron_add.py                             (TG handler)
Phase 5 — __main__.py wiring + prompts/cron_user.md  (integration glue)
Phase 6 — integration test + MODULE_CONTRACT headers + grace-refresh
```

Order respects DEPENDS: data types → producer (depends on types) → consumer (depends on types + queue) → handler (depends on producer) → wiring (depends on all) → integration (depends on wiring).

---

## Phase 1 — Data foundation

**Files touched**
1. `src/ai_steward_wiki/storage/jobs/payloads.py` (widen CronUserPayload in place)
2. `src/ai_steward_wiki/scheduler/queue_payloads.py` (new)

### 1.1 Test for widened CronUserPayload

`tests/unit/storage/test_payloads_cron_user.py` (new). Failing test asserts:
- `CronUserPayload(kind='cron_user', recurrence=<Recurrence>, command='hello', wiki_id=None)` validates
- `parse_job_payload({"kind":"cron_user","recurrence":{...},"command":"hello"})` returns instance with `wiki_id is None`
- `wiki_id='Health'` accepted; missing `command` → ValidationError; extra field → ValidationError (`extra='forbid'`)
- `cron_expr` and `user_text` field names REJECTED (extra='forbid' catches stale dicts)

```bash
uv run pytest tests/unit/storage/test_payloads_cron_user.py -q   # MUST FAIL (red)
```

### 1.2 GREEN — widen payloads.py

Edit `src/ai_steward_wiki/storage/jobs/payloads.py`:

```python
# In MODULE_MAP: replace the existing CronUserPayload bullet to reflect the new shape
#   CronUserPayload - user-defined NL-scheduled cron (recurrence:Recurrence, command, wiki_id?) — aisw-02v

# Body: replace the existing class with:
class CronUserPayload(_PayloadBase):
    kind: Literal["cron_user"] = "cron_user"
    recurrence: Recurrence
    command: str
    wiki_id: str | None = None
```

Bump VERSION header to next semver (e.g. 0.0.6 → 0.0.7), append CHANGE_SUMMARY line: `v0.0.7 - aisw-02v: widen CronUserPayload — typed Recurrence + free-form command + optional wiki_id; AD-05 no Alembic migration (JSON column, zero rows).`

```bash
uv run pytest tests/unit/storage/test_payloads_cron_user.py -q   # MUST PASS (green)
uv run pytest tests/unit/storage -q                              # regression check
```

### 1.3 Search-and-fix stale references

```bash
grep -rn "cron_expr\|user_text" src/ tests/ docs/Spec-WIKI/ 2>/dev/null
```

If any production code or tests still reference `.cron_expr` or `.user_text` on a `CronUserPayload` instance — update them. (Discovery confirmed no rows persist with the old shape; if grep is clean, no further action.)

### 1.4 Test for queue_payloads.py

`tests/unit/scheduler/test_queue_payloads.py` (new). Failing test asserts:
- `CronUserQueueMsg(kind='cron_user', job_id=42, owner_telegram_id=100, chat_id=100, command='hi', correlation_id='abc', scheduled_at_utc=datetime.now(UTC))` validates
- `QueueMsg` TypeAdapter round-trips via `model_dump_json()` → `validate_json()`
- `extra='forbid'` + `frozen=True` enforced
- discriminator `kind` is required and `cron_user` is the only valid current value

```bash
uv run pytest tests/unit/scheduler/test_queue_payloads.py -q     # MUST FAIL (red)
```

### 1.5 GREEN — create queue_payloads.py

```python
# FILE: src/ai_steward_wiki/scheduler/queue_payloads.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: In-memory PriorityJobQueue message types (Pydantic v2 discriminated union).
#   SCOPE: CronUserQueueMsg (kind='cron_user'); QueueMsg TypeAdapter (NFR-5).
#   DEPENDS: pydantic v2, datetime
#   LINKS: M-SCHEDULER-CONSUMER, M-SCHEDULER-CRON-USER, aisw-02v, D-011 §3
#   ROLE: TYPES
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   CronUserQueueMsg - cron-user fire payload (job_id, owner_telegram_id, chat_id, command, correlation_id, scheduled_at_utc)
#   QueueMsg - Annotated discriminated union (currently single member; future kinds extend without caller widening)
#   parse_queue_msg - validate a dict into the union
# END_MODULE_MAP

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

__all__ = ["CronUserQueueMsg", "QueueMsg", "parse_queue_msg"]


class _MsgBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CronUserQueueMsg(_MsgBase):
    kind: Literal["cron_user"] = "cron_user"
    job_id: int
    owner_telegram_id: int
    chat_id: int
    command: str
    correlation_id: str
    scheduled_at_utc: datetime


QueueMsg = Annotated[CronUserQueueMsg, Field(discriminator="kind")]
_adapter: TypeAdapter[QueueMsg] = TypeAdapter(QueueMsg)


def parse_queue_msg(value: dict[str, Any]) -> QueueMsg:
    return _adapter.validate_python(value)
```

```bash
uv run pytest tests/unit/scheduler/test_queue_payloads.py -q     # MUST PASS (green)
```

### Phase 1 gate

```bash
uv run mypy src/ai_steward_wiki/storage/jobs/payloads.py src/ai_steward_wiki/scheduler/queue_payloads.py
uv run ruff check src tests
uv run ruff format --check src tests
```

Commit: `feat(M-STORAGE-JOBS,M-SCHEDULER-CONSUMER): widen CronUserPayload + add queue_payloads (aisw-02v)`

---

## Phase 2 — Producer (`scheduler/cron_user.py`)

**Files touched**
1. `src/ai_steward_wiki/scheduler/cron_user.py` (new)

### 2.1 Tests (RED)

`tests/unit/scheduler/test_cron_user_producer.py` (new). Test cases:

1. **create_cron_user_job persists row + registers scheduler job**
   - Setup: in-memory jobs.db via `make_inmem_jobs_engine` fixture (reuse from existing scheduler tests), Mock AsyncIOScheduler, mock PriorityJobQueue.
   - Call: `await create_cron_user_job(owner_telegram_id=100, chat_id=100, recurrence=Recurrence(kind='daily', time_hhmm='09:00', tz='UTC'), command='hi', user_tz='UTC', wiki_id=None)`
   - Assert: scheduler.add_job called once with (callback==fire_cron_user_job, CronTrigger instance with hour=9 minute=0, args=[job_id], id=f'cron_user:{job_id}', replace_existing=True).
   - Assert: SELECT FROM jobs WHERE id=job_id → status='scheduled', kind='cron_user', payload['command']=='hi', payload['wiki_id'] is None.
   - Assert: log emitted `scheduler.cron_user.scheduled`.

2. **fire_cron_user_job happy path**
   - Pre-INSERT Job(id=1, status='scheduled', kind='cron_user', payload=CronUserPayload(...).model_dump(), owner_telegram_id=100, chat_id=100).
   - Set context with mock queue.
   - Call: `await fire_cron_user_job(1)`.
   - Assert: queue.put called once with (Lane.CRON_WRITE, CronUserQueueMsg with job_id=1, command, correlation_id len > 0).
   - Assert: SELECT → status='queued'.
   - Assert: log `scheduler.cron_user.fire`.

3. **fire_cron_user_job idempotent on missing row**
   - No row inserted.
   - Call: `await fire_cron_user_job(999)`.
   - Assert: queue.put NOT called. Log `scheduler.cron_user.fire.job_missing`.

4. **fire_cron_user_job idempotent on wrong status**
   - INSERT row status='queued' or 'running'.
   - Call: `await fire_cron_user_job(1)`.
   - Assert: queue.put NOT called. Log `scheduler.cron_user.fire.job_missing`.

5. **fire_cron_user_job raises if context unset**
   - Reset `_ctx` module-global to None (or use a separate module-reload fixture).
   - Call: `await fire_cron_user_job(1)`.
   - Assert: raises `CronUserContextNotInitialisedError`.

6. **set_cron_user_context idempotent**
   - Call twice with same args — second call overwrites cleanly, no exception.

```bash
uv run pytest tests/unit/scheduler/test_cron_user_producer.py -q   # RED
```

### 2.2 GREEN — implement `scheduler/cron_user.py`

Follow firing.py shape closely (DigestRunner-style _ctx tuple, picklable int callback). Key blocks:

```python
# FILE: src/ai_steward_wiki/scheduler/cron_user.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Cron-user job firing bridge — INSERT jobs.Job + register CronTrigger;
#            on fire, push CronUserQueueMsg to PriorityJobQueue(lane=CRON_WRITE).
#   SCOPE: set_cron_user_context, create_cron_user_job, fire_cron_user_job,
#          CronUserContextNotInitialisedError.
#   DEPENDS: apscheduler, sqlalchemy(.ext.asyncio), structlog,
#            ai_steward_wiki.storage.jobs.models.Job,
#            ai_steward_wiki.storage.jobs.payloads.CronUserPayload,
#            ai_steward_wiki.classifier.recurrence.Recurrence,
#            ai_steward_wiki.scheduler.queue.PriorityJobQueue/Lane,
#            ai_steward_wiki.scheduler.queue_payloads.CronUserQueueMsg
#   LINKS: M-SCHEDULER-CRON-USER, M-STORAGE-JOBS, M-SCHEDULER, M-CLASSIFIER-RECURRENCE, aisw-02v, D-002, D-011 §3
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   set_cron_user_context - install (scheduler, queue, jobs_session_maker) registry once at startup
#   create_cron_user_job - INSERT+commit jobs.Job(kind='cron_user') + scheduler.add_job(CronTrigger, args=[job_id], replace_existing); returns job_id
#   fire_cron_user_job - APScheduler callback (picklable int): load Job, guard status=='scheduled', push CronUserQueueMsg to queue, mark 'queued'
#   CronUserContextNotInitialisedError - raised by fire_cron_user_job when set_cron_user_context never called
# END_MODULE_MAP

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.scheduler.queue import Lane, PriorityJobQueue
from ai_steward_wiki.scheduler.queue_payloads import CronUserQueueMsg
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import CronUserPayload

__all__ = [
    "CronUserContextNotInitialisedError",
    "create_cron_user_job",
    "fire_cron_user_job",
    "set_cron_user_context",
]

_log = structlog.get_logger("scheduler.cron_user")


class CronUserContextNotInitialisedError(RuntimeError):
    """Raised when fire_cron_user_job runs before set_cron_user_context()."""


_ctx: tuple[AsyncIOScheduler, PriorityJobQueue, async_sessionmaker[AsyncSession]] | None = None


def set_cron_user_context(
    scheduler: AsyncIOScheduler,
    queue: PriorityJobQueue,
    jobs_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    global _ctx
    _ctx = (scheduler, queue, jobs_session_maker)


async def create_cron_user_job(
    *,
    owner_telegram_id: int,
    chat_id: int,
    recurrence: Recurrence,
    command: str,
    user_tz: str,
    wiki_id: str | None,
) -> int:
    if _ctx is None:
        raise CronUserContextNotInitialisedError("set_cron_user_context not called")
    scheduler, _queue, session_maker = _ctx
    payload = CronUserPayload(recurrence=recurrence, command=command, wiki_id=wiki_id)
    # START_BLOCK_CRON_USER_INSERT
    async with session_maker() as session:
        async with session.begin():
            row = Job(
                owner_telegram_id=owner_telegram_id,
                chat_id=chat_id,
                kind="cron_user",
                status="scheduled",
                priority=int(Lane.CRON_WRITE),
                payload=payload.model_dump(mode="json"),
                created_at_utc=datetime.now(UTC),
            )
            session.add(row)
            await session.flush()
            job_id = row.id
    # END_BLOCK_CRON_USER_INSERT
    # START_BLOCK_CRON_USER_REGISTER
    cron_kwargs = recurrence.to_cron()
    scheduler.add_job(
        fire_cron_user_job,
        CronTrigger(timezone=user_tz, **cron_kwargs),
        args=[job_id],
        id=f"cron_user:{job_id}",
        replace_existing=True,
    )
    # END_BLOCK_CRON_USER_REGISTER
    _log.info(
        "scheduler.cron_user.scheduled",
        job_id=job_id,
        owner_telegram_id=owner_telegram_id,
        chat_id=chat_id,
        kind="cron_user",
        recurrence_kind=recurrence.kind,
        tz=user_tz,
    )
    return job_id


async def fire_cron_user_job(job_id: int) -> None:
    if _ctx is None:
        raise CronUserContextNotInitialisedError("set_cron_user_context not called")
    _scheduler, queue, session_maker = _ctx
    # START_BLOCK_CRON_USER_FIRE
    async with session_maker() as session:
        async with session.begin():
            row = (
                await session.execute(select(Job).where(Job.id == job_id))
            ).scalar_one_or_none()
            if row is None or row.status != "scheduled":
                _log.info(
                    "scheduler.cron_user.fire.job_missing",
                    job_id=job_id,
                    found=row is not None,
                    status=getattr(row, "status", None),
                )
                return
            msg = CronUserQueueMsg(
                job_id=job_id,
                owner_telegram_id=row.owner_telegram_id,
                chat_id=row.chat_id,
                command=CronUserPayload(**row.payload).command,
                correlation_id=uuid4().hex,
                scheduled_at_utc=datetime.now(UTC),
            )
            try:
                await queue.put(Lane.CRON_WRITE, msg)
            except Exception as exc:
                _log.warning(
                    "scheduler.cron_user.fire.failed",
                    job_id=job_id,
                    error_class=type(exc).__name__,
                )
                raise
            row.status = "queued"
    # END_BLOCK_CRON_USER_FIRE
    _log.info(
        "scheduler.cron_user.fire",
        job_id=job_id,
        owner_telegram_id=msg.owner_telegram_id,
        chat_id=msg.chat_id,
        correlation_id=msg.correlation_id,
    )
```

```bash
uv run pytest tests/unit/scheduler/test_cron_user_producer.py -q   # GREEN
uv run mypy src/ai_steward_wiki/scheduler/cron_user.py
```

### Phase 2 gate

```bash
uv run pytest tests/unit/scheduler -q
uv run ruff check src tests && uv run ruff format --check src tests
```

Commit: `feat(M-SCHEDULER-CRON-USER): cron-user producer (INSERT+CronTrigger+queue enqueue) (aisw-02v)`

---

## Phase 3 — Consumer (`scheduler/consumer.py`)

**Files touched**
1. `src/ai_steward_wiki/scheduler/consumer.py` (new)

### 3.1 Tests (RED)

`tests/unit/scheduler/test_consumer.py` (new). Test cases:

1. **happy path: exit=0 → bot.send_message called with stdout chunks**
   - Inject `spawn=` fake Spawner returning `(stdout=b'hello\n', stderr=b'', returncode=0)`.
   - Pre-INSERT Job(id=1, status='queued', kind='cron_user', payload=..., owner_telegram_id=100, chat_id=100).
   - Put `CronUserQueueMsg(job_id=1, ..., command='hi')` on a real PriorityJobQueue.
   - Run one iteration of consumer (`await consumer._execute_one(await queue.get())`).
   - Assert: bot.send_message called once with `chat_id=100`, text containing 'hello'.
   - Assert: SELECT → status='finished', finished_at_utc set.
   - Assert: log `scheduler.consumer.exec.started`, `scheduler.consumer.exec.done`, `scheduler.consumer.delivered`.

2. **timeout: TimeoutError → kill_with_sequence + ❌ Тайм-аут message**
   - Fake Spawner returns a process whose `communicate()` raises asyncio.TimeoutError when wrapped in wait_for.
   - Assert: bot.send_message called with text starting `❌ Тайм-аут` (ru).
   - Assert: status='failed', last_error contains 'timeout'.
   - Assert: log `scheduler.consumer.exec.timeout`.

3. **non-zero exit: stderr surfaced in error message**
   - Fake Spawner returns (stdout=b'', stderr=b'boom', returncode=1).
   - Assert: bot.send_message with `❌ Ошибка (1): boom`.
   - Assert: status='failed', last_error stored.
   - Assert: log `scheduler.consumer.exec.failed`.

4. **chunking: long stdout → multiple send_message calls**
   - stdout = b'A' * 12000 → ChainSplitter splits into ≤3 parts.
   - Assert: bot.send_message called ≥2 times.

5. **TelegramAPIError on send → caught, logged, no propagation**
   - bot.send_message raises TelegramAPIError on first call.
   - Assert: loop survives (no exception escapes _execute_one).
   - Assert: log `scheduler.consumer.deliver_failed`.

6. **CronUserQueueMsg validation failure → skip + log**
   - Put a dict that fails Pydantic validation.
   - Assert: no subprocess spawned, log `scheduler.consumer.unexpected`.

7. **cancellation: CancelledError exits .run() cleanly**
   - Start consumer.run() as task; cancel after 1 ms.
   - Assert: task awaits cleanly, no leaked subprocess.

```bash
uv run pytest tests/unit/scheduler/test_consumer.py -q   # RED
```

### 3.2 GREEN — implement `scheduler/consumer.py`

```python
# FILE: src/ai_steward_wiki/scheduler/consumer.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Single async drain loop over PriorityJobQueue — spawns systemd-run --scope
#            wrapped Claude CLI per item, captures stdout (timeout 600s), delivers via
#            aiogram.Bot.send_message (chunked).
#   SCOPE: CronConsumer (constructor-DI bot/queue/jobs_session_maker); .run() drain loop;
#          ._execute_one per-item executor; Spawner Protocol seam for unit tests.
#   DEPENDS: asyncio, pydantic, aiogram(.Bot, .exceptions.TelegramAPIError), sqlalchemy.ext.asyncio,
#            ai_steward_wiki.scheduler.queue.PriorityJobQueue/QueueItem,
#            ai_steward_wiki.scheduler.queue_payloads.CronUserQueueMsg,
#            ai_steward_wiki.scheduler.core.kill_with_sequence,
#            ai_steward_wiki.claude_cli.common.{resolve_binary,build_env,neutral_cwd,system_prompt_argv,truncate_stderr},
#            ai_steward_wiki.tg.output.ChainSplitter,
#            ai_steward_wiki.storage.jobs.models.Job
#   LINKS: M-SCHEDULER-CONSUMER, M-SCHEDULER, M-STORAGE-JOBS, M-TG-TEXT, M-CLAUDE-CLI-COMMON,
#          aisw-02v, D-011 §3, D-021
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT

# (Full module body — implementation outline; final source per the design's
#  approach_decisions AD-03 and AD-07.)
```

Key implementation notes:
- Spawner Protocol seam: `async def spawn(self, argv: Sequence[str], *, cwd, env) -> _Killable` — so tests can inject a fake without touching `asyncio.create_subprocess_exec`. Default impl is a thin wrapper around `asyncio.create_subprocess_exec(*argv, stdout=PIPE, stderr=PIPE, cwd=cwd, env=env)`.
- `_build_argv(item)` → list[str] composed as documented in design §functional_design.ux_flow_cron_fire.
- `_deliver(text)` → `ChainSplitter().split(text)` (reuse class from `tg/output.py`) → `for chunk in chunks: await self.bot.send_message(chat_id, chunk)`.
- `_update_status(session, job_id, status, *, last_error=None, finished=False)` — small helper that wraps the UPDATE in a `session.begin()`.
- Logging anchors at every decision point per design.

```bash
uv run pytest tests/unit/scheduler/test_consumer.py -q   # GREEN
uv run mypy src/ai_steward_wiki/scheduler/consumer.py
```

### Phase 3 gate

```bash
uv run pytest tests/unit/scheduler -q
uv run ruff check src tests && uv run ruff format --check src tests
```

Commit: `feat(M-SCHEDULER-CONSUMER): single-drain async consumer with systemd-run scope + TG delivery (aisw-02v)`

---

## Phase 4 — TG handler (`tg/cron_add.py`)

**Files touched**
1. `src/ai_steward_wiki/tg/cron_add.py` (new)
2. `src/ai_steward_wiki/tg/handlers.py` (call `register_cron_add_handlers(router, get_user_tz=...)` from `build_router`)

### 4.1 Tests (RED)

`tests/unit/tg/test_cron_add_handler.py`. Cases:

1. **happy path** — `/cron_add каждый день в 9 утра | напомни выпить витамины`:
   - Stub `get_user_tz` returns 'Europe/Moscow'.
   - Stub `create_cron_user_job` returns 42.
   - Build aiogram.Message mock with text + from_user.id=100 + chat.id=100.
   - Assert: `create_cron_user_job` called with `recurrence.kind=='daily'`, `command='напомни выпить витамины'`, `user_tz='Europe/Moscow'`, `wiki_id is None`.
   - Assert: `message.answer` called with text starting `✅ Запланировано (id=42)`.
   - Assert: log `tg.command.cron_add.parsed`, `tg.command.cron_add.scheduled`.

2. **no pipe** — `/cron_add каждый день в 9` → reply CRON_ADD_USAGE_RU + log `tg.command.cron_add.usage`.

3. **empty command** — `/cron_add каждый день в 9 |   ` → usage reply + log `tg.command.cron_add.usage`.

4. **empty schedule** — `/cron_add  | run` → usage + log `tg.command.cron_add.usage`.

5. **parser escalate** — `/cron_add как-то нерегулярно | run` → `parse_recurrence` returns escalate=True → reply with CRON_ADD_USAGE_RU_HINT + log `tg.command.cron_add.escalate`.

6. **create_cron_user_job raises** — defensive try/except → reply _GENERIC_ERR_RU + log `tg.command.cron_add.failed`.

7. **`_humanize_recurrence` pure tests** for daily/weekly/monthly ru output.

```bash
uv run pytest tests/unit/tg/test_cron_add_handler.py -q   # RED
```

### 4.2 GREEN — implement `tg/cron_add.py`

```python
# FILE: src/ai_steward_wiki/tg/cron_add.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: aiogram Router add-on — /cron_add <NL recurrence> | <command> Command handler.
#   SCOPE: register_cron_add_handlers(router, get_user_tz); _humanize_recurrence (pure ru);
#          CRON_ADD_USAGE_RU + CRON_ADD_USAGE_RU_HINT constants.
#   DEPENDS: aiogram (Router, Command, CommandObject, Message), structlog,
#            ai_steward_wiki.classifier.recurrence.parse_recurrence/Recurrence,
#            ai_steward_wiki.scheduler.cron_user.create_cron_user_job
#   LINKS: M-TG-CRON-ADD, M-CLASSIFIER-RECURRENCE, M-SCHEDULER-CRON-USER, aisw-02v
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT

# Implementation follows design.functional_design.ux_flow_cron_add. Key points:
# - register_cron_add_handlers(router, get_user_tz): registers Command('cron_add') on router.
# - Inside the handler: split args on first '|', validate, parse_recurrence, dispatch.
# - All exceptions wrapped in try/except → _GENERIC_ERR_RU + log tg.command.cron_add.failed.
# - Anchors: tg.command.cron_add.usage / .escalate / .parsed / .scheduled / .failed.
```

`_humanize_recurrence(rec: Recurrence) -> str`:
- daily → `f"каждый день в {rec.time_hhmm} ({rec.tz})"`
- weekly → ru weekday list:
  - `(0,1,2,3,4)` → `"по будням"`
  - `(5,6)` → `"по выходным"`
  - else → comma-joined: `пн|вт|ср|чт|пт|сб|вс` (sorted by weekday number)
  - render: `f"{period} в {rec.time_hhmm} ({rec.tz})"`
- monthly → `f"каждое {rec.day_of_month}-е число в {rec.time_hhmm} ({rec.tz})"`

### 4.3 Wire into `build_router`

Edit `src/ai_steward_wiki/tg/handlers.py`:
1. Add import: `from ai_steward_wiki.tg.cron_add import register_cron_add_handlers`
2. Inside `build_router(...)`, add optional kwarg `get_user_tz: Callable[[int], Awaitable[str]] | None = None`
3. Inside `build_router`, after creating `router`, call `if get_user_tz is not None: register_cron_add_handlers(router, get_user_tz=get_user_tz)`
4. Update MODULE_CONTRACT header (DEPENDS, MODULE_MAP) and CHANGE_SUMMARY (`v0.5.0 - aisw-02v: build_router gains optional get_user_tz kwarg + registers /cron_add via register_cron_add_handlers`).

Update handlers test (no breakage — kwarg is optional + default `None` keeps current behaviour).

```bash
uv run pytest tests/unit/tg -q   # GREEN
uv run mypy src/ai_steward_wiki/tg/cron_add.py src/ai_steward_wiki/tg/handlers.py
```

### Phase 4 gate

```bash
uv run pytest tests/unit/tg -q
uv run ruff check src tests && uv run ruff format --check src tests
```

Commit: `feat(M-TG-CRON-ADD,M-TG-HANDLERS-WIRING): /cron_add Command handler + build_router wiring (aisw-02v)`

---

## Phase 5 — Runtime wiring + system prompt

**Files touched**
1. `prompts/cron_user.md` (new — system prompt for the consumer-spawned Claude CLI)
2. `src/ai_steward_wiki/settings.py` (new optional setting `cron_user_prompt_path`; default to `prompts/cron_user.md` relative to repo)
3. `src/ai_steward_wiki/__main__.py` (wire producer context + spawn consumer task + pass `get_user_tz` into `build_router`)

### 5.1 Create `prompts/cron_user.md`

Minimal, ru-leaning, semver: line. ~10 lines. Example:

```markdown
# Cron-user CLI prompt
semver: 0.0.1

Ты — персональный помощник пользователя. Получаешь свободно-сформулированную команду из его cron-расписания.
Если команда — «напомни …», «скажи …» — ответь коротким сообщением на русском.
Если команда требует данных, которых у тебя нет, — честно скажи об этом одной фразой.
Не вызывай инструменты, файлы и сеть. Только текстовый ответ.
```

### 5.2 Settings extension

In `src/ai_steward_wiki/settings.py`, add (within the existing Pydantic settings class — find the section near other prompt paths):

```python
cron_user_prompt_path: Path = Field(
    default_factory=lambda: Path(__file__).resolve().parents[2] / "prompts" / "cron_user.md"
)
```

Add a unit test for default resolution: `tests/unit/test_settings.py::test_cron_user_prompt_path_default` (RED then GREEN).

### 5.3 `__main__.py` wiring

Add to `_amain()` after `scheduler.start()` and after `firing.set_firing_context(...)`:

```python
from ai_steward_wiki.scheduler import cron_user as cron_user_module
from ai_steward_wiki.scheduler.consumer import CronConsumer
from ai_steward_wiki.scheduler.queue import PriorityJobQueue

queue = PriorityJobQueue()  # if not already created higher up
cron_user_module.set_cron_user_context(scheduler, queue, jobs_session_maker)
consumer = CronConsumer(
    queue=queue,
    bot=bot,
    claude_binary=settings.claude_binary,
    claude_config_dir=settings.claude_config_dir,
    prompt_path=settings.cron_user_prompt_path,
    jobs_session_maker=jobs_session_maker,
    timeout_s=600,
    slice_name="aisw-cli.slice",
)
consumer_task = asyncio.create_task(consumer.run(), name="aisw.cron_consumer")
```

Pass `get_user_tz` adapter into `build_router(... get_user_tz=_resolve_user_tz)` — `_resolve_user_tz(telegram_id)` reads `Settings.default_user_tz` for MVP (sessions.db users table tz read is acceptable enhancement but out of scope; design specifies sessions resolution but a default-tz fallback for users without a row is required).

On shutdown, after `stop_event.wait()` and before scheduler.shutdown():

```python
consumer_task.cancel()
with contextlib.suppress(asyncio.CancelledError, Exception):
    await consumer_task
```

Update `M-RUNTIME-WIRING` MODULE_CONTRACT header (DEPENDS += M-SCHEDULER-CRON-USER, M-SCHEDULER-CONSUMER, M-TG-CRON-ADD; new MODULE_MAP entries; CHANGE_SUMMARY append).

### 5.4 Integration smoke run (manual)

```bash
uv run python -m ai_steward_wiki --help 2>&1 | head -5   # importable
uv run mypy src/ai_steward_wiki/__main__.py              # strict
```

### Phase 5 gate

```bash
uv run pytest tests/unit -q
uv run ruff check src tests && uv run ruff format --check src tests
```

Commit: `feat(M-RUNTIME-WIRING): wire cron-user producer + consumer task + /cron_add handler (aisw-02v)`

---

## Phase 6 — Integration test + GRACE refresh

**Files touched**
1. `tests/integration/scheduler/test_cron_add_flow.py` (new)
2. `docs/knowledge-graph.xml`, `docs/development-plan.xml`, `docs/verification-plan.xml` (auto-refresh — XMLs already updated in Step 7; verify lint stays clean)
3. Source file MODULE_CONTRACT headers (Phase 1-5 created them; this phase verifies via `grace lint` + spot-check)

### 6.1 Integration test (skeleton, requires no real Claude CLI)

`tests/integration/scheduler/test_cron_add_flow.py`. Gated by `RUN_INTEGRATION=1`.

Flow:
1. Build real AsyncIOScheduler + in-mem jobs.db + real PriorityJobQueue.
2. Install cron_user context.
3. `await create_cron_user_job(...)` with `Recurrence(kind='daily', time_hhmm='00:00', tz='UTC')`.
4. Advance time: invoke `fire_cron_user_job(job_id)` directly (bypass APScheduler timer for determinism) — verifies producer-side end of the seam.
5. `item = await asyncio.wait_for(queue.get(), 1)` — assert one item present.
6. Inject a stub Spawner into CronConsumer that returns `(stdout=b'OK', stderr=b'', exit=0)` without calling systemd-run.
7. `await consumer._execute_one(item)`.
8. Assert: mock `bot.send_message(chat_id, 'OK')` called once.
9. Assert: SELECT FROM jobs WHERE id=job_id → status='finished'.

```bash
RUN_INTEGRATION=1 uv run pytest tests/integration/scheduler/test_cron_add_flow.py -q
```

### 6.2 GRACE final sync + total-test

```bash
grace lint            # MUST be 0/0
make total-test       # lint + grace + inv-lint + coverage ≥80% + integration
```

If `make total-test` is unavailable as a target, run the equivalent:
```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
grace lint
uv run pytest --cov=ai_steward_wiki --cov-report=term --cov-fail-under=80 tests/unit
RUN_INTEGRATION=1 uv run pytest tests/integration
```

### Phase 6 gate

All quality gates green.

Commit (covers any small fix-ups + headers polish):
`test(M-SCHEDULER-CONSUMER): end-to-end integration smoke for /cron_add flow (aisw-02v)`

---

## Self-review checklist (Step 9 SSoT)

- [x] **Every MODULE_CONTRACT → has task(s):** M-SCHEDULER-CRON-USER (Ph 2), M-SCHEDULER-CONSUMER (Ph 3), M-TG-CRON-ADD (Ph 4), M-STORAGE-JOBS payload widen (Ph 1), M-RUNTIME-WIRING update (Ph 5).
- [x] **Every FR from Discovery → covered:** FR-1 (Ph 4 handler + Ph 1 payload), FR-2 (Ph 2 producer fire callback), FR-3 (Ph 3 consumer + Ph 5 systemd-run argv build), FR-4 (Ph 3 ChainSplitter + bot.send_message), FR-5 (every Phase has log anchors enumerated).
- [x] **Every NFR → has verification step:** NFR-1 single-drain (Ph 3.1 test #7 cancellation + NFR-1 explicit in design), NFR-2 600s timeout (Ph 3.1 test #2), NFR-3 UTC + tz (Ph 2.1 test #1 + Recurrence.tz), NFR-4 ru-only (Ph 4 humanize + ru error strings), NFR-5 mypy + Pydantic discriminator (Ph 1 queue_payloads + each phase mypy gate), NFR-6 coverage ≥80% (Ph 6 total-test).
- [x] **Verification plan → every test/trace reflected:** V-M-SCHEDULER-CRON-USER (Ph 2.1), V-M-SCHEDULER-CONSUMER (Ph 3.1 + Ph 6.1), V-M-TG-CRON-ADD (Ph 4.1). All log markers from the V-* entries appear in the test assertions of their respective phases.
- [x] **Log anchors from design → included:** every BLOCK marker and log event mentioned in design.functional_design + V-* log-markers is asserted in at least one phase test.
- [x] **ADR decisions → implemented:** AD-01..AD-07 all manifest in the plan (no interactive confirm in Ph 4, ChainSplitter standalone in Ph 3, Lane.CRON_WRITE in Ph 2, payload widen no-migration in Ph 1, consumer task in __main__ in Ph 5).
- [x] **Task order respects DEPENDS:** Phase 1 (types) before Phase 2 (uses types) before Phase 3 (uses types + queue) before Phase 4 (uses Ph 2 producer) before Phase 5 (uses all) before Phase 6 (integration).
- [x] **No placeholders / TODO comments:** plan body has zero `TODO` / `<placeholder>` / `(to be done)` markers.
- [x] **Context window:** plan body ~17K tokens of prose + ~2× that for inline code + tests → comfortably within Opus 4.7's 1M window with src files (~25K) + headers (~5K) + reference modules read into context.

---

## Out-of-scope (follow-up bd issues to file when this epic closes)

1. `/cron_list`, `/cron_delete`, `/cron_edit` — CRUD for user-visible cron jobs.
2. Retry/backoff policy for failed cron_user runs (mirror digest 3-strike).
3. DLQ wiring for `kind='cron_user'` (table already exists).
4. Bot-offline replay strategy beyond APScheduler's `coalesce=True` default.
5. Per-user rate limit on `/cron_add` (queue backpressure).
6. Interactive confirm for `/cron_add` (only after /cron_delete exists — see AD-02).
7. Full `deliver_output(kind='reply')` integration for run-output persistence (see AD-03).
8. Per-WIKI scoping (`wiki_id` field is plumbed but unused in walking skeleton — wire after /cron_list shows scope per row).
