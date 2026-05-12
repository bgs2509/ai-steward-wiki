# Inbox-WIKI Phase-D.a: reminder_job — cron bridge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. RED → GREEN → REFACTOR per task; never write production code before a failing test.

**Goal:** Let a user create a one-shot reminder by natural language («разбуди завтра в 6 позвонить врачу», «напомни через час…») via an explicit inline-button confirm, and deliver it at that time as a plain Telegram message (no Claude, no WIKI). Recurring digests are out of scope → `aisw-19o` (the bot answers them with a ru "not yet" line).

**Architecture (additive):**
- New `scheduler/firing.py` (`M-SCHEDULER-FIRING`): `set_firing_context` (module-level `(TgSender, jobs async_sessionmaker)` registry) + `create_reminder_job` (INSERT+commit a `jobs.Job` row, then `scheduler.add_job(fire_job, DateTrigger, args=[job_id], misfire_grace_time=None)`) + `fire_job(job_id: int)` (picklable int arg → load Job → guard `status=='pending'` → `send_message('🔔 Напоминание: …')` → mark `done`/`failed`).
- Extend `storage/jobs/payloads.py` (+`ReminderPayload`, `kind='reminder_job'`, `message: str`, `lead_time_min: int = 0` — added to the `JobPayload` union).
- Extend `classifier/time_parse.py` (`parse_time` gains `prefer_future: bool = False` → `dateparser` `PREFER_DATES_FROM='future'`).
- Extend `settings.py` (+`default_user_tz: str = "Europe/Moscow"`).
- Extend `tg/pipeline.py` (`DefaultPipeline`): a `reminder` fast-path in `_run_text_pipeline` BEFORE the routable branch; `on_confirm_callback` dispatches `category=='reminder'` rows to a new `_handle_reminder_confirm`; new ru copy + a `build_reminder_recap` helper; new injected deps (`time_parser`, `jobs_session_maker`, `scheduler`, `user_tz_lookup`, `clock`).
- Wire in `__main__.py`: after `scheduler.start()` build a `jobs` `async_sessionmaker`, `firing.set_firing_context(...)`, `scheduler.add_listener(_on_job_missed, EVENT_JOB_MISSED)`, pass the new deps into `DefaultPipeline`, add a `_TimeParserAdapter` wrapping `classifier.time_parse.parse_time` with the Haiku backend bound, and a `user_tz_lookup` over the loaded `UsersConfig`.

No new third-party dependency. No new SQLite table (`jobs.jobs` exists). No new Alembic migration. `prompts/*` unchanged.

**Tech Stack:** Python 3.11, APScheduler `AsyncIOScheduler` + `DateTrigger` + `EVENT_JOB_MISSED`, SQLAlchemy 2.0 async, pydantic v2 (discriminated union), aiogram 3.x (`TgSender`), `dateparser`, structlog, pytest/pytest-asyncio.

**bd:** aisw-kcz (epic aisw-t2r; blocks aisw-19o). Spec: `docs/superpowers/specs/20260512-inbox-wiki-cron-bridge-{discovery,design}.md`. Decisions: T-1..T-8 (design frontmatter), D-OQ-1/2/3/4, D-R-1, R-2. ADR-006 (written at Finish). D-002, D-010, D-022, tech-spec §3/§6.

**Conventions reminder:** all DB datetimes UTC; type hints + `mypy --strict` for `src/`; structlog with `correlation_id` (+ `telegram_id` where in a TG context) — log lengths/ids, never full user text; ru-only user strings; `# noqa: RUF001` on Cyrillic-homoglyph string literals where ruff flags them (see existing `ROUTE_CONFIRM_*` constants).

---

## Task 1: `ReminderPayload` in `storage/jobs/payloads.py`

**Files:**
- Modify: `src/ai_steward_wiki/storage/jobs/payloads.py` (add `ReminderPayload`; extend `JobPayload` union, `__all__`, MODULE_MAP, CHANGE_SUMMARY; bump `VERSION` 0.0.2 → 0.0.3)
- Test: `tests/unit/storage/jobs/test_payloads.py` (extend — or create if absent; check `tests/unit/storage/jobs/` layout first)

- [ ] **Step 1 (RED):** Add tests:
  - `parse_job_payload({"kind": "reminder_job", "message": "позвонить врачу"})` → `ReminderPayload(kind="reminder_job", message="позвонить врачу", lead_time_min=0)`.
  - `parse_job_payload({"kind": "reminder_job", "message": "x", "lead_time_min": 30})` → `lead_time_min == 30`.
  - extra key → `pydantic.ValidationError` (the union base is `extra="forbid"`).
  - `ReminderPayload` is frozen (assignment raises) and `.model_dump(mode="json")` round-trips through `parse_job_payload`.
  - the existing `WikiRunPayload`/`DigestPayload`/etc. tests still pass (discriminator unchanged for them).
  Run: `uv run pytest tests/unit/storage/jobs/test_payloads.py -q` → RED.

- [ ] **Step 2 (GREEN):** In `payloads.py`:
  ```python
  class ReminderPayload(_PayloadBase):
      kind: Literal["reminder_job"] = "reminder_job"
      message: str
      lead_time_min: int = Field(default=0, ge=0)

  JobPayload = Annotated[
      WikiRunPayload | DigestPayload | CronUserPayload | PurgePayload | ReminderPayload,
      Field(discriminator="kind"),
  ]
  ```
  Add `"ReminderPayload"` to `__all__`; add the MODULE_MAP line `#   ReminderPayload - one-shot reminder job: message + optional lead_time_min (aisw-kcz)`; update `#   JobPayload - Annotated discriminated union over the five above`; bump CHANGE_SUMMARY + VERSION.
  Run pytest → GREEN.

- [ ] **Step 3 (REFACTOR):** `make lint` clean for this file (ruff/format/mypy). Commit gate at end of task: `uv run pytest tests/unit/storage -q` green.

---

## Task 2: `parse_time(..., prefer_future=...)` in `classifier/time_parse.py`

**Files:**
- Modify: `src/ai_steward_wiki/classifier/time_parse.py` (add `prefer_future` kwarg; bump VERSION 0.0.1 → 0.0.2; update CHANGE_SUMMARY + MODULE_MAP line)
- Test: `tests/unit/classifier/test_time_parse.py` (extend)

- [ ] **Step 1 (RED):** Add tests (use a deterministic `now_utc`, e.g. `datetime(2026, 5, 12, 18, 0, tzinfo=UTC)` = 21:00 Europe/Moscow):
  - `await parse_time("в 6", user_tz=ZoneInfo("Europe/Moscow"), now_utc=<21:00 MSK>, prefer_future=True)` → `when_utc` is **tomorrow** 06:00 MSK (i.e. `2026-05-13 03:00 UTC`), `escalate=False`, `source="dateparser"`. With `prefer_future=False` (default) the same call returns **today** 06:00 MSK (already past) — assert the two differ, proving the kwarg threads through.
  - `prefer_future=True` does not break an explicit future date («завтра в 9») — still parses to that date.
  - default behaviour (no kwarg) unchanged: existing tests still green.
  Run: `uv run pytest tests/unit/classifier/test_time_parse.py -q` → RED.

- [ ] **Step 2 (GREEN):** In `parse_time`, add `prefer_future: bool = False` to the signature (keyword-only — it is after `*`), and in the `dateparser.parse(..., settings={...})` dict add `**({"PREFER_DATES_FROM": "future"} if prefer_future else {})` (or set the key conditionally). The Haiku-fallback path is unaffected. Update the MODULE_MAP `parse_time` line to mention `prefer_future`; bump VERSION + CHANGE_SUMMARY.
  Run pytest → GREEN. Note: verify via Context7 (`dateparser`) that `PREFER_DATES_FROM='future'` is the current setting key — first contact with the dateparser setting this session.

- [ ] **Step 3 (REFACTOR):** `make lint` clean. Gate: `uv run pytest tests/unit/classifier -q` green.

---

## Task 3: `default_user_tz` in `settings.py`

**Files:**
- Modify: `src/ai_steward_wiki/settings.py` (add field; update MODULE_MAP/CHANGE_SUMMARY; bump VERSION)
- Test: `tests/unit/test_settings.py` (extend)

- [ ] **Step 1 (RED):** Test: `get_settings().default_user_tz == "Europe/Moscow"` by default; setting `AISW_DEFAULT_USER_TZ=Asia/Yekaterinburg` in env (use the existing env-override fixture pattern) → that value; `ZoneInfo(get_settings().default_user_tz)` does not raise. Run → RED.

- [ ] **Step 2 (GREEN):** Add `default_user_tz: str = "Europe/Moscow"` to the `Settings` model (near the other simple fields). Update the `type-Settings` MODULE_MAP comment + CHANGE_SUMMARY + VERSION. Run → GREEN.

- [ ] **Step 3 (REFACTOR):** `make lint`; gate `uv run pytest tests/unit/test_settings.py -q`.

---

## Task 4: `M-SCHEDULER-FIRING` — `scheduler/firing.py` (new module)

**Files:**
- Create: `src/ai_steward_wiki/scheduler/firing.py` (full MODULE_CONTRACT + MODULE_MAP + CHANGE_SUMMARY headers — see template below)
- Create: `tests/unit/scheduler/test_firing.py`
- (Do NOT add `firing` to `scheduler/__init__.py` barrel — it is a leaf module imported by path, like `maintenance.py`. Check: `maintenance` is not re-exported from `scheduler/__init__.py`; mirror that.)

**MODULE_CONTRACT header (write verbatim, adapt LINKS):**
```python
# FILE: src/ai_steward_wiki/scheduler/firing.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: One-shot reminder bridge — create a jobs.Job + APScheduler
#            DateTrigger, and deliver the reminder as a plain Telegram message
#            on fire (no Claude/WIKI). (aisw-kcz, Inbox-WIKI Phase-D.a.)
#   SCOPE: set_firing_context, create_reminder_job, fire_job. Module-level
#          (TgSender, jobs async_sessionmaker) registry set once at startup;
#          fire_job takes only a picklable int (SQLAlchemyJobStore-safe).
#   DEPENDS: apscheduler, sqlalchemy.ext.asyncio, structlog,
#            ai_steward_wiki.storage.jobs (Job, parse_job_payload),
#            ai_steward_wiki.scheduler.queue.Lane,
#            ai_steward_wiki.tg.bot.TgSender, ai_steward_wiki.storage.jobs.payloads.ReminderPayload
#   LINKS: M-SCHEDULER-FIRING, M-STORAGE-JOBS, M-SCHEDULER, M-TG-TEXT,
#          D-002, D-010, D-022, tech-spec §3/§6, ADR-006, aisw-kcz
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   set_firing_context - install the module-level (TgSender, jobs sessionmaker) registry
#   create_reminder_job - INSERT+commit a jobs.Job(kind='reminder_job') then add a DateTrigger; returns job_id
#   fire_job - APScheduler callback (picklable int): load Job, guard status, send the reminder, mark done/failed
#   FiringNotInitialisedError - raised by fire_job when set_firing_context was never called
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - aisw-kcz: reminder_job firing bridge (Stage-0 fast-path → confirm → DateTrigger → TG deliver)
# END_CHANGE_SUMMARY
```

- [ ] **Step 1 (RED):** Write `tests/unit/scheduler/test_firing.py`. Fixtures: an in-memory jobs DB (`build_engine("sqlite+aiosqlite://")` + `Base.metadata.create_all` via `run_sync` + `build_sessionmaker`); a `FakeScheduler` recording `add_job(func, *, trigger, id, args, misfire_grace_time, **kw)` calls; a `FakeTgSender` recording `send_message(chat_id, text)` (set it to raise for the failure case). Tests:
  - **`create_reminder_job`** with a fresh session + `FakeScheduler` + `when_utc=datetime(2026,5,13,3,0,tzinfo=UTC)`, `owner_telegram_id=42`, `chat_id=42`, `message="позвонить врачу"`, `lead_time_min=0` → returns an `int` `job_id`; a `jobs.jobs` row exists with `kind=="reminder_job"`, `status=="pending"`, `priority==Lane.USER_WRITE`, `scheduled_at_utc==when_utc`, `payload` round-trips through `parse_job_payload` to `ReminderPayload(message="позвонить врачу")`, `created_at_utc` set; `FakeScheduler.add_job` was called **once** with `func is fire_job`, `args == [job_id]`, `id == f"reminder:{job_id}"`, `misfire_grace_time is None`, and `trigger` is a `DateTrigger` whose `run_date` equals `when_utc` (compare in UTC). The Job commit must precede `add_job` (assert ordering — e.g. `FakeScheduler` reads the row count and asserts == 1 inside its `add_job`).
  - **`fire_job` happy path:** `set_firing_context(sender=FakeTgSender(), jobs_session_maker=<maker>)`; INSERT a `Job(kind="reminder_job", status="pending", chat_id=42, payload=ReminderPayload(message="позвонить врачу").model_dump(mode="json"), ...)` directly; `await fire_job(job_id)` → `FakeTgSender` got exactly one `send_message(42, "🔔 Напоминание: позвонить врачу")`; row reloaded → `status=="done"`, `started_at_utc` and `finished_at_utc` set; log `scheduler.reminder.fired` + `scheduler.reminder.delivered` emitted (assert via `caplog`/structlog capture as elsewhere in the suite).
  - **`fire_job` guard:** Job with `status="cancelled"` → `await fire_job(job_id)` sends nothing, leaves status unchanged, logs `scheduler.reminder.skipped`.
  - **`fire_job` missing row:** `await fire_job(999999)` → no send, logs `scheduler.reminder.skipped` (status `"missing"`), no raise.
  - **`fire_job` send fails:** `FakeTgSender.send_message` raises `RuntimeError("blocked")` → row → `status=="failed"`, `last_error` contains `"blocked"` (or the error class), logs `scheduler.reminder.deliver_failed` with `error_class`. `fire_job` does not re-raise (one-shot, no DLQ).
  - **`fire_job` no context:** call `fire_job(1)` without `set_firing_context` (reset the module registry in the fixture teardown) → raises `FiringNotInitialisedError` (a `RuntimeError` subclass) with a clear message.
  Run: `uv run pytest tests/unit/scheduler/test_firing.py -q` → RED.

- [ ] **Step 2 (GREEN):** Implement `firing.py`:
  - Module-level: `_ctx: tuple[TgSender, async_sessionmaker[AsyncSession]] | None = None`. `class FiringNotInitialisedError(RuntimeError): ...`.
  - `def set_firing_context(*, sender: TgSender, jobs_session_maker: async_sessionmaker[AsyncSession]) -> None:` sets `_ctx`.
  - `async def create_reminder_job(session: AsyncSession, scheduler: AsyncIOScheduler, *, owner_telegram_id: int, chat_id: int, when_utc: datetime, message: str, lead_time_min: int = 0) -> int:` —
    ```python
    payload = ReminderPayload(message=message, lead_time_min=lead_time_min).model_dump(mode="json")
    job = Job(owner_telegram_id=owner_telegram_id, chat_id=chat_id, kind="reminder_job",
              status="pending", priority=int(Lane.USER_WRITE), scheduled_at_utc=when_utc,
              payload=payload, created_at_utc=datetime.now(UTC).replace(tzinfo=None) if <DB stores naive> else datetime.now(UTC))
    session.add(job); await session.flush(); job_id = job.id; await session.commit()
    scheduler.add_job(fire_job, trigger=DateTrigger(run_date=when_utc, timezone="UTC"),
                      args=[job_id], id=f"reminder:{job_id}", misfire_grace_time=None)
    return job_id
    ```
    NOTE on datetime storage: check how the existing `Job` rows store `scheduled_at_utc`/`created_at_utc` (the column is bare `Mapped[datetime]` — SQLite via aiosqlite typically stores naive). Match the convention used by `scheduler/maintenance.py` / wherever `Job` rows are written today; if nowhere writes `Job` yet, store **aware UTC** consistently and document it. Be consistent within this module.
    Log `scheduler.reminder.scheduled` (job_id, when_utc, correlation_id if a caller passes one — add an optional `correlation_id: str = ""` param threaded into the log).
  - `async def fire_job(job_id: int) -> None:` —
    ```python
    if _ctx is None: raise FiringNotInitialisedError("firing context not initialised — call set_firing_context() at startup")
    sender, maker = _ctx
    async with maker() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status != "pending":
            _log.info("scheduler.reminder.skipped", job_id=job_id, status=(job.status if job else "missing"))
            return
        try:
            payload = parse_job_payload(job.payload)
        except ValidationError:  # corrupt payload — treat as failed
            job.status = "failed"; job.last_error = "bad payload"; await session.commit()
            _log.warning("scheduler.reminder.deliver_failed", job_id=job_id, error_class="ValidationError"); return
        message = payload.message if isinstance(payload, ReminderPayload) else str(job.payload)
        job.status = "in_progress"; job.started_at_utc = <now>; await session.commit()
        _log.info("scheduler.reminder.fired", job_id=job_id, chat_id=job.chat_id)
        try:
            await sender.send_message(job.chat_id, f"🔔 Напоминание: {message}")
        except Exception as exc:  # noqa: BLE001 — one-shot delivery, no retry/DLQ
            job.status = "failed"; job.last_error = f"{type(exc).__name__}: {exc}"; await session.commit()
            _log.warning("scheduler.reminder.deliver_failed", job_id=job_id, error_class=type(exc).__name__); return
        job.status = "done"; job.finished_at_utc = <now>; await session.commit()
        _log.info("scheduler.reminder.delivered", job_id=job_id)
    ```
  - `_log = structlog.get_logger("scheduler.firing")`. Imports: `from apscheduler.schedulers.asyncio import AsyncIOScheduler`, `from apscheduler.triggers.date import DateTrigger`, `from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker`, `from pydantic import ValidationError`, `from ai_steward_wiki.storage.jobs import Job, parse_job_payload`, `from ai_steward_wiki.storage.jobs.payloads import ReminderPayload`, `from ai_steward_wiki.scheduler.queue import Lane`, `from ai_steward_wiki.tg.bot import TgSender`. `__all__ = ["FiringNotInitialisedError", "create_reminder_job", "fire_job", "set_firing_context"]`.
  Run pytest → GREEN. Verify the `DateTrigger`/`add_job` kwarg names + `EVENT_JOB_MISSED` location via Context7 (`apscheduler`) — first contact this session.

- [ ] **Step 3 (REFACTOR):** Avoid a circular import (`tg.bot` ← … ← `scheduler`?). `TgSender` is a `Protocol` in `tg/bot.py`; importing it from `scheduler/firing.py` should be fine, but if it pulls aiogram at import time, import `TgSender` under `TYPE_CHECKING` and annotate as a string. `make lint` clean; `uv run pytest tests/unit/scheduler -q` green.

---

## Task 5: pipeline — `reminder` fast-path detection + confirm draft (`_run_text_pipeline`)

**Files:**
- Modify: `src/ai_steward_wiki/tg/pipeline.py` (new constants, `REMINDER_CONFIDENCE_THRESHOLD`, `_RECURRING_KEYWORDS`, `TimeParser` Protocol, `build_reminder_recap`, new `DefaultPipeline.__init__` params, the detection block in `_run_text_pipeline`; extend MODULE_CONTRACT DEPENDS/LINKS, MODULE_MAP, CHANGE_SUMMARY; bump VERSION 0.6.0 → 0.7.0)
- Test: `tests/unit/tg/test_pipeline_reminder.py` (new — mirror `tests/unit/tg/test_pipeline_route_confirm.py` fixtures/style; check that file for the `FakeSender`/`MagicMock` patterns)

**New module-level names in `pipeline.py`:**
```python
REMINDER_CONFIDENCE_THRESHOLD = 0.85
_RECURRING_KEYWORDS = frozenset({"кажд", "ежедневн", "еженедельн", "сводк", "дайджест"})  # substring match, ru-only (D-032)

REMINDER_RECAP_RU = "Поставлю напоминание на {when_local} ({tz}): «{message}». Подтверждаешь?"  # noqa: RUF001
REMINDER_ACK_RU = "Готово — напомню {when_local}."  # noqa: RUF001
REMINDER_UNPARSEABLE_RU = "Не понял, на когда поставить напоминание — уточни время."  # noqa: RUF001
REMINDER_PAST_RU = "Эта дата уже прошла — назови будущую."  # noqa: RUF001
REMINDER_RECURRING_RU = "Регулярные сводки — скоро будет, пока могу только разовые напоминания."  # noqa: RUF001
REMINDER_CONFIRM_CANCELLED_RU = "Отменено — напоминание не поставил."  # noqa: RUF001
REMINDER_CONFIRM_STALE_RU = "Время на подтверждение истекло — пришли заново."  # noqa: RUF001

class TimeParser(Protocol):
    async def parse_time(self, text: str, *, user_tz: ZoneInfo, now_utc: datetime,
                         prefer_future: bool = False, correlation_id: str = "") -> TimeParseResult: ...

def build_reminder_recap(*, when_utc: datetime, user_tz: ZoneInfo, message: str) -> str:
    when_local = when_utc.astimezone(user_tz).strftime("%d.%m %H:%M")
    return REMINDER_RECAP_RU.format(when_local=when_local, tz=str(user_tz), message=message)
```
Add all new public constants/`build_reminder_recap`/`TimeParser` to `__all__` + MODULE_MAP. Add `from zoneinfo import ZoneInfo`, `from datetime import UTC, datetime`, `from ai_steward_wiki.classifier.schema import TimeParseResult` (Intent already imported) to imports.

**New `DefaultPipeline.__init__` params (all optional, keyword-only, default `None` → feature off / sane fallback):**
- `time_parser: TimeParser | None = None`
- `jobs_session_maker: async_sessionmaker[AsyncSession] | None = None`
- `scheduler: AsyncIOScheduler | None = None`
- `user_tz_lookup: Callable[[int], str | None] | None = None`
- `default_user_tz: str = "Europe/Moscow"`
- `clock: Callable[[], datetime] = lambda: datetime.now(UTC)` (store as `self._clock`)

Helper on `DefaultPipeline`:
```python
def _resolve_user_tz(self, telegram_id: int) -> ZoneInfo:
    name = (self._user_tz_lookup(telegram_id) if self._user_tz_lookup else None) or self._default_user_tz
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — bad config TZ → safe default
        return ZoneInfo("Europe/Moscow")
```

**The detection block — inside `_run_text_pipeline`, placed BETWEEN `tg.pipeline.classify.done` and `# START_BLOCK_ROUTABLE_BRANCH`:**
```python
# START_BLOCK_REMINDER_FASTPATH (aisw-kcz, Inbox-WIKI Phase-D.a)
if (
    result.intent is Intent.REMINDER
    and result.confidence >= REMINDER_CONFIDENCE_THRESHOLD
    and self._time_parser is not None
):
    return await self._handle_reminder_intent(
        telegram_id=telegram_id, chat_id=chat_id, text=text,
        distilled_payload=result.distilled_payload, correlation_id=correlation_id,
    )
# END_BLOCK_REMINDER_FASTPATH
```
(When `self._time_parser is None` or confidence < threshold, fall through — REMINDER is not in `_ROUTABLE_INTENTS`, so it reaches the legacy runner branch; that is the current behaviour and an acceptable degraded fallback.)

**New private method `_handle_reminder_intent`:**
```python
async def _handle_reminder_intent(self, *, telegram_id, chat_id, text, distilled_payload, correlation_id) -> None:
    # recurring-digest phrasing → ru "not yet" (aisw-19o's job to do it properly)
    low = text.lower()
    if any(k in low for k in _RECURRING_KEYWORDS):
        await self._sender.send_message(chat_id, REMINDER_RECURRING_RU)
        _log.info("tg.pipeline.reminder.recurring_not_yet", correlation_id=correlation_id, telegram_id=telegram_id)
        return
    user_tz = self._resolve_user_tz(telegram_id)
    now_utc = self._clock()
    tp = await self._time_parser.parse_time(text, user_tz=user_tz, now_utc=now_utc, prefer_future=True, correlation_id=correlation_id)
    _log.info("tg.pipeline.reminder.detected", correlation_id=correlation_id, telegram_id=telegram_id,
              time_source=tp.source, escalate=tp.escalate)
    if tp.escalate or tp.when_utc is None:
        await self._sender.send_message(chat_id, REMINDER_UNPARSEABLE_RU)
        _log.info("tg.pipeline.reminder.unparseable", correlation_id=correlation_id, telegram_id=telegram_id)
        return
    if tp.when_utc <= now_utc:  # after prefer_future=True this means an explicitly-past absolute date
        await self._sender.send_message(chat_id, REMINDER_PAST_RU)
        _log.info("tg.pipeline.reminder.rejected_past", correlation_id=correlation_id, telegram_id=telegram_id)
        return
    raw_reminder_text = distilled_payload.get("reminder_text")
    message = raw_reminder_text if isinstance(raw_reminder_text, str) and raw_reminder_text.strip() else text
    when_iso = tp.when_utc.astimezone(UTC).isoformat()
    draft = {"when_utc": when_iso, "message": message, "lead_time_min": 0, "user_tz": str(user_tz),
             "correlation_id": correlation_id}
    confirm_draft = PendingConfirmDraft(telegram_id=telegram_id, chat_id=chat_id, category="reminder",
        draft=draft, recap_text=build_reminder_recap(when_utc=tp.when_utc, user_tz=user_tz, message=message))
    rec = await self._confirm.request_explicit(confirm_draft, keyboard_factory=build_route_confirm_keyboard)
    _log.info("tg.pipeline.reminder.confirm_requested", correlation_id=correlation_id, telegram_id=telegram_id,
              pending_id=rec.pending_id, when_utc=when_iso)
```
(`build_route_confirm_keyboard` is already imported from `tg.confirm` — reuse the 2-button keyboard.)

- [ ] **Step 1 (RED):** `tests/unit/tg/test_pipeline_reminder.py` — build a `DefaultPipeline` with: `FakeSender`, `MagicMock` idempotency (`check_update_id`→True, `check_content`→`("sha", None)`), a `MagicMock` `ConfirmationService` (`request_explicit` returns an object with `.pending_id=7`), a `MagicMock` `classifier` (`classify` returns `ClassifierResult(intent=Intent.REMINDER, confidence=0.93, distilled_payload={...}, backend="fake", model="m", prompt_semver="1", prompt_sha256="x", latency_ms=1)`), a fake `time_parser` (an object with an async `parse_time` returning a chosen `TimeParseResult`), `clock=lambda: datetime(2026,5,12,18,0,tzinfo=UTC)`. Tests on `on_text(...)` (which calls `_run_text_pipeline`):
  - **future time → confirm requested:** `time_parser` → `TimeParseResult(when_utc=datetime(2026,5,13,3,0,tzinfo=UTC), source="dateparser", escalate=False, raw="...", user_tz="Europe/Moscow")`; `distilled_payload={"reminder_text": "позвонить врачу"}` → `confirmation.request_explicit` called once with a `PendingConfirmDraft(category="reminder")` whose `draft["when_utc"]=="2026-05-13T03:00:00+00:00"`, `draft["message"]=="позвонить врачу"`, `recap_text` contains `"13.05 06:00"` and `"Europe/Moscow"`, and `keyboard_factory is build_route_confirm_keyboard`; no jobs.Job created (no `scheduler.add_job`); `FakeSender` got no message.
  - **message resolution:** `distilled_payload={}` → `draft["message"] == <the raw text passed to on_text>`. `distilled_payload={"reminder_text": "   "}` (blank) → falls back to raw text.
  - **escalate → clarification:** `time_parser` → `escalate=True, when_utc=None` → `FakeSender` got `REMINDER_UNPARSEABLE_RU`; `request_explicit` not called.
  - **explicitly-past absolute date → rejection:** `time_parser` → `when_utc=datetime(2026,5,1,9,0,tzinfo=UTC)` (before the clock) → `FakeSender` got `REMINDER_PAST_RU`; no confirm.
  - **recurring phrasing → not yet:** `on_text(text="каждый день в 9 сводка")` → `FakeSender` got `REMINDER_RECURRING_RU`; `time_parser.parse_time` not called; no confirm.
  - **confidence below threshold → not handled here:** `classify` returns `confidence=0.5` → `request_explicit` not called and `REMINDER_*` strings not sent (it falls through to the legacy runner branch — with `runner=None`/`output=None` it hits the ack fallback; just assert no reminder-specific behaviour). Alternatively wire a `MagicMock` runner+output and assert the runner was called — pick whichever matches the existing test style.
  - **`time_parser=None` → not handled here:** same as above (feature off).
  Run: `uv run pytest tests/unit/tg/test_pipeline_reminder.py -q` → RED.

- [ ] **Step 2 (GREEN):** Implement the constants, `TimeParser` Protocol, `build_reminder_recap`, the new `__init__` params + `_resolve_user_tz`, the `# START_BLOCK_REMINDER_FASTPATH` block, and `_handle_reminder_intent` as sketched. Update MODULE_CONTRACT DEPENDS (`+ ai_steward_wiki.scheduler.firing (create_reminder_job)`, note the new optional injections), LINKS (`+ aisw-kcz, D-010`), MODULE_MAP (new constants + `build_reminder_recap` + `TimeParser` + `_handle_reminder_intent` if you list privates — check whether the file lists privates; it lists some), CHANGE_SUMMARY, VERSION.
  Run pytest → GREEN.

- [ ] **Step 3 (REFACTOR):** Keep `_handle_reminder_intent` under one screen; extract nothing prematurely. `make lint` clean; `uv run pytest tests/unit/tg -q` green.

---

## Task 6: pipeline — confirm-callback dispatch + `_handle_reminder_confirm`

**Files:**
- Modify: `src/ai_steward_wiki/tg/pipeline.py` (`on_confirm_callback` dispatch on category; new `_handle_reminder_confirm`; MODULE_MAP/CHANGE_SUMMARY)
- Test: `tests/unit/tg/test_pipeline_reminder.py` (extend)

**`on_confirm_callback` — generalise the dispatch:**
```python
async def on_confirm_callback(self, *, telegram_id, chat_id, pending_id, action) -> None:
    pending = await self._confirm.get_pending(pending_id)
    category = getattr(pending, "category", None) if pending is not None else None
    if category == "route_ingest":
        await self._handle_route_confirm(telegram_id=telegram_id, chat_id=chat_id, pending_id=pending_id,
                                         action=action, draft_json=pending.draft_json)
        return
    if category == "reminder":
        await self._handle_reminder_confirm(telegram_id=telegram_id, chat_id=chat_id, pending_id=pending_id,
                                            action=action, draft_json=pending.draft_json)
        return
    status = await self._confirm.resolve(telegram_id, pending_id, action)
    _log.info("tg.pipeline.confirm", telegram_id=telegram_id, chat_id=chat_id, pending_id=pending_id, action=action, status=status)
```

**`_handle_reminder_confirm` (mirrors `_handle_route_confirm`):**
```python
async def _handle_reminder_confirm(self, *, telegram_id, chat_id, pending_id, action, draft_json) -> None:
    status = await self._confirm.resolve(telegram_id, pending_id, action)
    _log.info("tg.pipeline.confirm.reminder_dispatched", telegram_id=telegram_id, chat_id=chat_id,
              pending_id=pending_id, action=action, status=status)
    if status is None:
        await self._sender.send_message(chat_id, REMINDER_CONFIRM_STALE_RU)
        _log.info("tg.pipeline.reminder.confirm_stale", telegram_id=telegram_id, pending_id=pending_id); return
    if status != "confirmed":
        await self._sender.send_message(chat_id, REMINDER_CONFIRM_CANCELLED_RU)
        _log.info("tg.pipeline.reminder.confirm_cancelled", telegram_id=telegram_id, pending_id=pending_id, status=status); return
    draft = json.loads(draft_json or "{}")
    when_utc = datetime.fromisoformat(draft["when_utc"])
    message = str(draft.get("message") or "")
    lead = int(draft.get("lead_time_min") or 0)
    user_tz = ZoneInfo(str(draft.get("user_tz") or self._default_user_tz))
    correlation_id = str(draft.get("correlation_id") or f"reminder-confirm-{pending_id}-{telegram_id}")
    # reminder pending rows are only created when time_parser is wired; the scheduler+sessionmaker
    # are wired together in __main__ — guard defensively.
    if self._jobs_session_maker is None or self._scheduler is None:
        await self._sender.send_message(chat_id, ACK_RUNNER_ERR_RU)
        _log.error("tg.pipeline.reminder.confirm_misconfigured", telegram_id=telegram_id, pending_id=pending_id); return
    async with self._jobs_session_maker() as session:
        from ai_steward_wiki.scheduler.firing import create_reminder_job  # local import to keep scheduler dep lazy
        job_id = await create_reminder_job(session, self._scheduler, owner_telegram_id=telegram_id,
            chat_id=chat_id, when_utc=when_utc, message=message, lead_time_min=lead, correlation_id=correlation_id)
    when_local = when_utc.astimezone(user_tz).strftime("%d.%m %H:%M")
    await self._sender.send_message(chat_id, REMINDER_ACK_RU.format(when_local=when_local))
    _log.info("tg.pipeline.reminder.confirm_created", correlation_id=correlation_id, telegram_id=telegram_id,
              pending_id=pending_id, job_id=job_id, when_utc=draft["when_utc"])
```
(Use a module-level `import json` — already imported. `datetime`/`ZoneInfo` imported in Task 5.)

- [ ] **Step 1 (RED):** Tests on `on_confirm_callback`:
  - **confirmed → job created + ack:** `confirm.get_pending(7)` returns a fake with `.category="reminder"`, `.draft_json=json.dumps({"when_utc":"2026-05-13T03:00:00+00:00","message":"позвонить врачу","lead_time_min":0,"user_tz":"Europe/Moscow","correlation_id":"c"})`; `confirm.resolve(...)` → `"confirmed"`; pipeline built with an **in-memory jobs DB** sessionmaker + a `FakeScheduler`. Call `on_confirm_callback(telegram_id=42, chat_id=42, pending_id=7, action="confirm")` → a `jobs.jobs` row exists with `kind=="reminder_job"`, `scheduled_at_utc` matching the parsed time, payload round-trips; `FakeScheduler.add_job` called once (`id=="reminder:<id>"`, `misfire_grace_time is None`); `FakeSender` got the `REMINDER_ACK_RU` text containing `"13.05 06:00"`.
  - **cancel → no job, ru notice:** `resolve` → `"cancelled"` → no `add_job`, no jobs row, `FakeSender` got `REMINDER_CONFIRM_CANCELLED_RU`.
  - **stale (resolve→None) → ru notice:** `FakeSender` got `REMINDER_CONFIRM_STALE_RU`; no job.
  - **double-confirm idempotency:** second `on_confirm_callback` with the same `pending_id` after the row is resolved → `resolve` returns `None` (race-safe) → stale notice; no second job/`add_job`. (Drive this by making the `MagicMock` `resolve` return `"confirmed"` then `None`.)
  - **route_ingest still dispatches to `_handle_route_confirm`** (regression — keep an existing route-confirm test green, or add a tiny one asserting category routing).
  Run → RED.

- [ ] **Step 2 (GREEN):** Implement as sketched. Update MODULE_MAP (`_handle_reminder_confirm`), CHANGE_SUMMARY (fold into the v0.7.0 entry). Run → GREEN.

- [ ] **Step 3 (REFACTOR):** `make lint`; `uv run pytest tests/unit/tg -q` green.

---

## Task 7: `__main__.py` wiring

**Files:**
- Modify: `src/ai_steward_wiki/__main__.py` (after `scheduler.start()`: `firing.set_firing_context`, `EVENT_JOB_MISSED` listener, `_TimeParserAdapter`, `user_tz_lookup`, pass new deps to `DefaultPipeline`; extend MODULE_CONTRACT DEPENDS (`+ M-SCHEDULER-FIRING`... already added to the graph — mirror in the header), MODULE_MAP (`_on_job_missed`, `_TimeParserAdapter`), CHANGE_SUMMARY; bump VERSION)
- Test: `tests/unit/test_main_wiring.py` or wherever `__main__` is tested (extend — check the existing test module name). If `__main__` has no unit tests, the integration test in Task 8 is the safety net; still add at least a smoke test that `_amain` builds the pipeline with the new deps (using the existing fakes/patches).

- [ ] **Step 1 (RED):** Test: after bootstrap, `firing.set_firing_context` was called (patch it, assert called once with a `TgSender` + an `async_sessionmaker`); `scheduler.add_listener` was called with `EVENT_JOB_MISSED`; the constructed `DefaultPipeline` received non-None `time_parser`, `jobs_session_maker`, `scheduler`, and a `default_user_tz` equal to `Settings.default_user_tz`. (Adapt to however `__main__` tests currently assert wiring.) Run → RED.

- [ ] **Step 2 (GREEN):** In `_amain` (after the scheduler is started and the jobs engine/sessionmaker exist — a `jobs` `async_sessionmaker` may already be built for retention jobs; reuse it, else `build_sessionmaker(jobs_engine)`):
  ```python
  from apscheduler.events import EVENT_JOB_MISSED
  from ai_steward_wiki.scheduler import firing
  from ai_steward_wiki.classifier.time_parse import parse_time as _parse_time_fn

  firing.set_firing_context(sender=sender, jobs_session_maker=jobs_session_maker)

  def _on_job_missed(event: object) -> None:
      _log.warning("scheduler.reminder.misfired", job_id=getattr(event, "job_id", None))
  scheduler.add_listener(_on_job_missed, EVENT_JOB_MISSED)

  class _TimeParserAdapter:
      async def parse_time(self, text, *, user_tz, now_utc, prefer_future=False, correlation_id=""):
          return await _parse_time_fn(text, user_tz=user_tz, now_utc=now_utc, prefer_future=prefer_future,
              haiku_backend=classifier_backend, haiku_prompt_path=time_parse_prompt_path, correlation_id=correlation_id)

  _users_by_id = {u.telegram_id: u for u in users_config.users}
  def _user_tz_lookup(telegram_id: int) -> str | None:
      u = _users_by_id.get(telegram_id); return u.tz if u else None
  ```
  Then pass to `DefaultPipeline(...)`: `time_parser=_TimeParserAdapter()`, `jobs_session_maker=jobs_session_maker`, `scheduler=scheduler`, `user_tz_lookup=_user_tz_lookup`, `default_user_tz=settings.default_user_tz`. (`classifier_backend`/`time_parse_prompt_path`: reuse whatever Stage-0 backend + the NL-time Haiku prompt path the existing wiring already builds; if there is no dedicated NL-time prompt file, pass `haiku_prompt_path=None` so `parse_time` simply escalates on a dateparser miss — that is acceptable for the MVP.)
  Update header DEPENDS/MODULE_MAP/CHANGE_SUMMARY/VERSION. Run → GREEN.

- [ ] **Step 3 (REFACTOR):** Ensure no import cycle (`__main__` → `scheduler.firing` → `tg.bot` is fine; `tg.pipeline` already imports `tg.confirm`). `make lint` clean; `uv run pytest tests/unit -q` green.

---

## Task 8: integration test — end-to-end reminder flow

**Files:**
- Create: `tests/integration/test_reminder_flow.py` (no real Claude/Telegram needed → can also run as a slow unit; gate behind `RUN_INTEGRATION=1` to match the suite, or place under `tests/unit/` if the suite has a precedent for "integration-style unit" — check `tests/integration/test_e2e_pipeline.py` for the harness/fakes).

- [ ] **Step 1 (RED):** Build a `DefaultPipeline` with real `ConfirmationService` (over an in-memory sessions DB), real in-memory jobs DB sessionmaker, a real `AsyncIOScheduler` configured with a `MemoryJobStore` (or the real `SQLAlchemyJobStore` over the in-memory jobs DB — prefer the latter to exercise pickling of `fire_job` + `[job_id]`), a `FakeTgSender`, a fake `classifier` that returns `intent=REMINDER, confidence=0.95, distilled_payload={"reminder_text":"позвонить врачу"}`, a fake `time_parser` returning a near-future `when_utc`. Flow:
  1. `await pipeline.on_text(telegram_id=42, chat_id=42, update_id=1, text="напомни завтра в 9 позвонить врачу")` → a `pending_confirms` row with `category="reminder"` exists; `FakeTgSender` got no message yet.
  2. Read the `pending_id` from the row; `await pipeline.on_confirm_callback(telegram_id=42, chat_id=42, pending_id=pending_id, action="confirm")`.
  3. Assert: a `jobs.jobs` row with `kind="reminder_job"`, `status="pending"`, `scheduled_at_utc == when_utc`, payload round-trips to `ReminderPayload(message="позвонить врачу")`; the scheduler has a job `id == f"reminder:{job_id}"`; `FakeTgSender` got the `REMINDER_ACK_RU` text.
  4. `firing.set_firing_context(sender=FakeTgSender2, jobs_session_maker=<jobs maker>)`; `await firing.fire_job(job_id)` → `FakeTgSender2` got exactly one `send_message(42, "🔔 Напоминание: позвонить врачу")`; the row flipped to `status="done"` with `started_at_utc`/`finished_at_utc` set.
  Run → RED (until Tasks 4–7 are in).

- [ ] **Step 2 (GREEN):** Make it pass (no production changes expected beyond Tasks 1–7; if the real `SQLAlchemyJobStore` complains about the async jobs engine, use a separate sync sqlite URL for the jobstore as `__main__` already does via `_sync_url_for_jobstore`, or fall back to `MemoryJobStore` for this test and leave a comment).

- [ ] **Step 3 (REFACTOR):** Add a `make` target hook only if the suite expects it (the existing `make integration` likely already globs `tests/integration/`). `make lint` clean.

---

## Task 9: GRACE refresh + full gate

- [ ] `grace lint --failOn errors` → exit 0 (the XML deltas were already added during planning; this re-checks after the new source files exist — `M-SCHEDULER-FIRING` markers must match the graph node).
- [ ] `grace-refresh --verify` (or `grace module find scheduler/firing.py` to confirm it resolves) — reconcile `knowledge-graph.xml` / `verification-plan.xml` with the actual code (MODULE_MAP entries, real test paths). Hand-fix any drift the planning-time entries got wrong.
- [ ] `make lint` (ruff + ruff format --check + mypy --strict src/ + grace lint) — clean.
- [ ] `uv run pytest tests/unit -q` — all green, coverage of the new modules ≥ 80% (`scheduler/firing.py`, the new pipeline paths).
- [ ] `RUN_INTEGRATION=1 uv run pytest tests/integration/test_reminder_flow.py -q` — green.
- [ ] `bd update aisw-kcz --notes` with the execution outcome; do NOT close yet (Review + Finish steps remain in the workflow).

---

## Out of scope (→ later, do not implement here)

`digest_job` / recurring aggregator / recurrence parsing / `CronTrigger` user jobs / the `PriorityJobQueue` worker loop / multi-WIKI `--add-dir` (→ `aisw-19o`, Phase-D.b). Reminder management UX (`/reminders` list/cancel/snooze/edit, post-create cancel button). `wiki_job` & `/cron_add`. `tracker_*` / `boundary_message`. DLQ/retry parity for `reminder_job`. Roll-forward of explicitly-past absolute dates (rejected instead). Startup `jobs.db` ↔ APScheduler reconciliation pass (the known millisecond-gap silent-miss limitation is documented, not fixed).

---

## Risk register

1. **`SQLAlchemyJobStore` pickling.** `fire_job` must be importable at module level and take only `[int]`. Mitigated by Task 4's design + the integration test using the real jobstore. *If* APScheduler can't pickle the `DateTrigger` with `timezone="UTC"` — pass a `datetime` already in UTC and omit `timezone`, or pass `pytz.utc`/`ZoneInfo("UTC")`; verify via Context7.
2. **Datetime naive/aware mismatch** between `Job.scheduled_at_utc` (SQLite stores naive) and the `DateTrigger`/comparisons. Mitigated by picking ONE convention in `firing.py` and asserting it in tests; `_run_text_pipeline` always works in aware UTC and only serialises ISO strings into the draft.
3. **No NL-time Haiku prompt wired** → `parse_time` escalates on every dateparser miss → more "уточни время" replies than ideal. Acceptable for MVP; flagged in Task 7 Step 2.
4. **Import cycle** `scheduler.firing → tg.bot`. Mitigated by `TYPE_CHECKING`-guarding the `TgSender` import if needed (Task 4 Step 3).
5. **`user_tz` from `users.toml`** requires `__main__` to have the `UsersConfig` in scope at pipeline-construction time — it does (`_load_users_config` runs in bootstrap). If a user has no `tz` → `default_user_tz`. If the configured TZ string is invalid → `_resolve_user_tz` falls back to `Europe/Moscow` (never raises).
6. **`distilled_payload` shape.** `reminder_text` is read opportunistically; `prompts/classifier.md` is NOT bumped (T-6). If the key is absent or non-string → raw user text is the reminder message. Covered by a Task 5 test.
