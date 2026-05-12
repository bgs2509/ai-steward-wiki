# FILE: src/ai_steward_wiki/scheduler/firing.py
# VERSION: 0.5.0
# START_MODULE_CONTRACT
#   PURPOSE: Cron/date job firing bridge — one-shot reminder (DateTrigger → plain
#            TG message, no Claude; aisw-kcz, Phase-D.a) and recurring digest
#            (CronTrigger → run Claude with --add-dir into the owner's WIKIs →
#            deliver via tg.output.deliver_output(kind='digest') with a real
#            jobs.db planner-window context; 3-strike auto-disable; aisw-oqq /
#            aisw-w3k, Phase-D.b.1/2a).
#   SCOPE: set_firing_context/create_reminder_job/fire_job; set_digest_context/
#          create_digest_job/fire_digest_job/_build_planner_context;
#          list_owner_digest_job_ids/run_section_expand (slash-command accessors,
#          aisw-269); get_owner_digest_prefs/set_owner_digest_section (digest
#          section toggles — aisw-pv8). Module-level registries set once at
#          startup; the firing callbacks take only a picklable int
#          (SQLAlchemyJobStore-safe).
#   DEPENDS: apscheduler, sqlalchemy(.ext.asyncio), structlog, pydantic,
#            ai_steward_wiki.storage.jobs.models.Job,
#            ai_steward_wiki.storage.jobs.payloads (ReminderPayload, DigestPayload, parse_job_payload),
#            ai_steward_wiki.storage.sessions.digest_prefs (get_digest_prefs, set_digest_section, DigestPrefs, SECTION_DISPLAY_NAME),
#            ai_steward_wiki.classifier.recurrence.Recurrence,
#            ai_steward_wiki.scheduler.queue.Lane, ai_steward_wiki.scheduler.dlq.move_to_dlq,
#            ai_steward_wiki.tg.output.deliver_output,
#            ai_steward_wiki.tg.bot.TgSender (typing only)
#   LINKS: M-SCHEDULER-FIRING, M-STORAGE-JOBS, M-STORAGE-SESSIONS, M-SCHEDULER, M-TG-TEXT, M-WIKI-RUNNER,
#          M-WIKI-LIFECYCLE, M-CLASSIFIER-RECURRENCE, D-002, D-010, D-019, D-022, D-024,
#          D-025, tech-spec §3/§6, ADR-006, ADR-007, ADR-024, ADR-025, ADR-026, aisw-kcz, aisw-oqq, aisw-w3k, aisw-269, aisw-pv8
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   set_firing_context - install the module-level (TgSender, jobs sessionmaker) registry for fire_job
#   create_reminder_job - INSERT+commit a jobs.Job(kind='reminder_job') then add a DateTrigger; returns job_id
#   fire_job - APScheduler callback (picklable int): load Job, guard status, send the reminder, mark done/failed
#   FiringNotInitialisedError - raised by fire_job when set_firing_context was never called
#   DigestRunner - Protocol: async callable running one Stage-1 digest session → assistant text
#   set_digest_context - install the digest registry (scheduler, runner, owner-WIKI resolver, jobs+audit+sessions sessionmakers, sender)
#   create_digest_job - INSERT+commit a jobs.Job(kind='digest_job', wiki_scope 'all'|list[str]) then add a CronTrigger; returns job_id
#   fire_digest_job - APScheduler callback (picklable int): resolve WIKIs (intersect with wiki_scope if a list), build planner ctx, append the ru section-skip directive when the owner disabled sections (degrade-to-all-on), run Claude, deliver via deliver_output, 3-strike auto-disable
#   list_owner_digest_job_ids - read-only: the owner's enabled digest_job ids (for /digest_now; aisw-269)
#   run_section_expand - re-run Claude scoped to one digest section over the owner's WIKIs via DigestRunner(section=...) (for /expand; aisw-269)
#   get_owner_digest_prefs - the owner's digest section toggles (DigestPrefs(True,True) if unset; for /digest_sections — aisw-pv8)
#   set_owner_digest_section - flip one digest section for the owner; returns the new DigestPrefs (for the digestsec: callback — aisw-pv8)
#   DigestNotInitialisedError - raised by fire_digest_job / the slash-command accessors when set_digest_context was never called
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.5.0 - aisw-pv8 (Phase-D.b.2c): set_digest_context +sessions_
#                session_maker (7-tuple _digest_ctx); get_owner_digest_prefs /
#                set_owner_digest_section accessors over storage.sessions.digest_
#                prefs; fire_digest_job appends the ru section-skip directive to
#                planner_context when the owner disabled sections (degrade-to-all-
#                on try/except → scheduler.digest.prefs_read_failed; otherwise
#                byte-identical) — new anchor scheduler.digest.sections_filtered;
#                runner(..., section=None) unchanged.
#   PREVIOUS:    v0.4.0 - aisw-269 (Phase-D.b.2b): DigestRunner Protocol +section
#                (None ⇒ full digest, byte-identical); fire_digest_job intersect-
#                and-filter on payload.wiki_scope when a list (scheduler.digest.
#                scope_filter; empty kept ⇒ ru notice, scheduler.digest.delivered
#                empty='scope_vanished', no strike); create_digest_job wiki_scope:
#                str|list[str] (+wiki_scope on scheduler.digest.scheduled);
#                list_owner_digest_job_ids + run_section_expand (slash-command
#                accessors for /digest_now and /expand).
#   PREVIOUS:    v0.3.0 - aisw-w3k (Phase-D.b.2a): fire_digest_job delivers via
#                tg.output.deliver_output(kind='digest') — D-024/D-025 (<b>-section
#                split + (n/m) + send_document fallthrough + data/runs/ persist +
#                audit.run_outputs row); set_digest_context +audit_session_maker;
#                _build_planner_context replaces the one-line planner stub; dropped
#                _DIGEST_TG_LIMIT. New log anchor scheduler.digest.planner_context;
#                scheduler.digest.delivered now carries run_id/n_messages/document_sent.
#   PREVIOUS:    v0.2.0 - aisw-oqq: digest_job firing bridge — set_digest_context /
#                create_digest_job (CronTrigger, replace_existing) / fire_digest_job
#                (resolve owner WIKI set, run_wiki_session via runner adapter with
#                extra_add_dirs, deliver assistant text, 3-strike auto-disable +
#                move_to_dlq + remove_job; D-019/D-024/D-025; ADR-007).
#   PREVIOUS:    v0.1.0 - aisw-kcz: reminder_job firing bridge (Stage-0 fast-path → confirm → DateTrigger → TG deliver)
# END_CHANGE_SUMMARY

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

import structlog
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.scheduler.dlq import move_to_dlq
from ai_steward_wiki.scheduler.queue import Lane
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import (
    DigestPayload,
    ReminderPayload,
    parse_job_payload,
)
from ai_steward_wiki.storage.sessions.digest_prefs import (
    DigestPrefs,
    get_digest_prefs,
    set_digest_section,
)
from ai_steward_wiki.tg.output import deliver_output

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from pathlib import Path

    from ai_steward_wiki.tg.bot import TgSender

__all__ = [
    "DigestNotInitialisedError",
    "DigestRunner",
    "FiringNotInitialisedError",
    "create_digest_job",
    "create_reminder_job",
    "fire_digest_job",
    "fire_job",
    "get_owner_digest_prefs",
    "list_owner_digest_job_ids",
    "run_section_expand",
    "set_digest_context",
    "set_firing_context",
    "set_owner_digest_section",
]

_log = structlog.get_logger("scheduler.firing")

# Module-level firing context: set once at startup. fire_job() must take only a
# picklable int (SQLAlchemyJobStore persists job args), so the bot-sender and the
# jobs sessionmaker are read from here, not passed through APScheduler.
_ctx: tuple[TgSender, async_sessionmaker[AsyncSession]] | None = None


class FiringNotInitialisedError(RuntimeError):
    """Raised when fire_job runs before set_firing_context was called (mis-wired)."""


def set_firing_context(
    *, sender: TgSender, jobs_session_maker: async_sessionmaker[AsyncSession]
) -> None:
    """Install the (bot-sender, jobs sessionmaker) registry. Call once at startup."""
    global _ctx
    _ctx = (sender, jobs_session_maker)


def _now_naive_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# START_BLOCK_CREATE_REMINDER_JOB
async def create_reminder_job(
    session: AsyncSession,
    scheduler: AsyncIOScheduler,
    *,
    owner_telegram_id: int,
    chat_id: int,
    when_utc: datetime,
    message: str,
    lead_time_min: int = 0,
    correlation_id: str = "",
) -> int:
    """Persist a reminder Job row (committed) then register its DateTrigger.

    Ordering matters: the Job row is committed BEFORE scheduler.add_job, so a
    crash in the millisecond gap leaves at worst a pending row without a trigger
    (the reminder silently does not fire) rather than a trigger without a row.
    No reconciliation pass in the MVP (documented limitation).
    """
    payload = ReminderPayload(message=message, lead_time_min=lead_time_min).model_dump(mode="json")
    job = Job(
        owner_telegram_id=owner_telegram_id,
        chat_id=chat_id,
        kind="reminder_job",
        status="pending",
        priority=int(Lane.USER_WRITE),
        scheduled_at_utc=when_utc.astimezone(UTC).replace(tzinfo=None),
        payload=payload,
        created_at_utc=_now_naive_utc(),
    )
    session.add(job)
    await session.flush()
    job_id = job.id
    await session.commit()

    scheduler.add_job(
        fire_job,
        trigger=DateTrigger(run_date=when_utc),
        args=[job_id],
        id=f"reminder:{job_id}",
        misfire_grace_time=None,
    )
    _log.info(
        "scheduler.reminder.scheduled",
        correlation_id=correlation_id,
        job_id=job_id,
        owner_telegram_id=owner_telegram_id,
        when_utc=when_utc.astimezone(UTC).isoformat(),
    )
    return job_id


# END_BLOCK_CREATE_REMINDER_JOB


# START_BLOCK_FIRE_JOB
async def fire_job(job_id: int) -> None:
    """APScheduler callback for a one-shot reminder. Picklable int arg only.

    Guards on Job.status == 'pending' (idempotent against double fires / stale
    trigger rows); delivers the reminder text as a plain Telegram message; marks
    the row done / failed. One-shot — no retry, no DLQ row on a send failure.
    """
    if _ctx is None:
        raise FiringNotInitialisedError(
            "firing context not initialised — call set_firing_context() at startup"
        )
    sender, maker = _ctx
    async with maker() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status != "pending":
            _log.info(
                "scheduler.reminder.skipped",
                job_id=job_id,
                status=(job.status if job is not None else "missing"),
            )
            return
        try:
            payload = parse_job_payload(job.payload)
        except ValidationError:
            job.status = "failed"
            job.last_error = "bad payload"
            await session.commit()
            _log.warning(
                "scheduler.reminder.deliver_failed", job_id=job_id, error_class="ValidationError"
            )
            return
        message = payload.message if isinstance(payload, ReminderPayload) else str(job.payload)
        chat_id = job.chat_id
        job.status = "in_progress"
        job.started_at_utc = _now_naive_utc()
        await session.commit()
        _log.info("scheduler.reminder.fired", job_id=job_id, chat_id=chat_id)
        try:
            await sender.send_message(chat_id, f"\U0001f514 Напоминание: {message}")
        except Exception as exc:
            job.status = "failed"
            job.last_error = f"{type(exc).__name__}: {exc}"
            await session.commit()
            _log.warning(
                "scheduler.reminder.deliver_failed",
                job_id=job_id,
                error_class=type(exc).__name__,
            )
            return
        job.status = "done"
        job.finished_at_utc = _now_naive_utc()
        await session.commit()
        _log.info("scheduler.reminder.delivered", job_id=job_id)


# END_BLOCK_FIRE_JOB


# ---------------------------------------------------------------------------
# Recurring digest bridge (aisw-oqq, Inbox-WIKI Phase-D.b.1)
# ---------------------------------------------------------------------------

_DIGEST_EMPTY_RU = "\U0001f33f Сегодня дел нет."
_DIGEST_NO_WIKI_RU = "У тебя пока нет ни одной WIKI для сводки."  # noqa: RUF001
_DIGEST_SCOPE_VANISHED_RU = (
    "Сводка настроена по WIKI, которых сейчас нет. Создай их заново или настрой сводку ещё раз."
)
_DIGEST_MAX_STRIKES = 3


class DigestNotInitialisedError(RuntimeError):
    """Raised when fire_digest_job runs before set_digest_context was called (mis-wired)."""


class DigestRunner(Protocol):
    """Async callable running one Stage-1 digest session against the owner's WIKIs.

    The implementation (wired in __main__) calls wiki.runner.run_wiki_session with
    its own LockAcquirer (semaphore → memlock → flock) — fire_digest_job does NOT
    take a lock itself.
    """

    async def __call__(
        self,
        *,
        wiki_id: str,
        wiki_path: Path,
        extra_add_dirs: list[Path],
        planner_context: str,
        correlation_id: str,
        section: str | None = None,
    ) -> str: ...


# Module-level digest firing context: set once at startup. fire_digest_job takes
# only a picklable int, so everything else is read from here.
# tuple: (scheduler, runner, resolve_owner_wikis, jobs_session_maker,
#         audit_session_maker, sender, sessions_session_maker)
_digest_ctx: (
    tuple[
        AsyncIOScheduler,
        DigestRunner,
        Callable[[int], Awaitable[Sequence[tuple[str, Path]]]],
        async_sessionmaker[AsyncSession],
        async_sessionmaker[AsyncSession],
        TgSender,
        async_sessionmaker[AsyncSession],
    ]
    | None
) = None


def set_digest_context(
    *,
    scheduler: AsyncIOScheduler,
    runner: DigestRunner,
    resolve_owner_wikis: Callable[[int], Awaitable[Sequence[tuple[str, Path]]]],
    jobs_session_maker: async_sessionmaker[AsyncSession],
    audit_session_maker: async_sessionmaker[AsyncSession],
    sender: TgSender,
    sessions_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Install the digest firing registry. Call once at startup."""
    global _digest_ctx
    _digest_ctx = (
        scheduler,
        runner,
        resolve_owner_wikis,
        jobs_session_maker,
        audit_session_maker,
        sender,
        sessions_session_maker,
    )


async def get_owner_digest_prefs(owner_telegram_id: int) -> DigestPrefs:
    """The owner's digest section toggles (DigestPrefs(True, True) if unset).

    For the /digest_sections slash command (aisw-pv8). Reads the sessions
    sessionmaker from the digest context.
    """
    if _digest_ctx is None:
        raise DigestNotInitialisedError(
            "digest context not initialised — call set_digest_context() at startup"
        )
    sessions_maker = _digest_ctx[6]
    return await get_digest_prefs(sessions_maker, owner_telegram_id)


async def set_owner_digest_section(
    owner_telegram_id: int, *, section: str, enabled: bool
) -> DigestPrefs:
    """Flip one digest section for the owner; returns the new DigestPrefs.

    For the digestsec: callback (aisw-pv8).
    """
    if _digest_ctx is None:
        raise DigestNotInitialisedError(
            "digest context not initialised — call set_digest_context() at startup"
        )
    sessions_maker = _digest_ctx[6]
    return await set_digest_section(
        sessions_maker, owner_telegram_id, section=section, enabled=enabled
    )


async def list_owner_digest_job_ids(owner_telegram_id: int) -> list[int]:
    """Read-only: the ids of the owner's enabled (status=='scheduled') digest jobs.

    Used by the /digest_now slash command (aisw-269) to fire each one through
    fire_digest_job. Reads the jobs sessionmaker from the digest context.
    """
    if _digest_ctx is None:
        raise DigestNotInitialisedError(
            "digest context not initialised — call set_digest_context() at startup"
        )
    _, _, _, maker, _, _, _ = _digest_ctx
    async with maker() as session:
        rows = (
            (
                await session.execute(
                    select(Job.id).where(
                        Job.owner_telegram_id == owner_telegram_id,
                        Job.kind == "digest_job",
                        Job.status == "scheduled",
                    )
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


async def run_section_expand(owner_telegram_id: int, section: str) -> str | None:
    """Re-run Claude scoped to one digest section over the owner's WIKI set.

    Returns the assistant text, or None if the owner has no WIKI. Reuses the
    digest runner (DigestRunner(section=...)) and the per-WIKI lock it holds
    internally. Used by the /expand <section> slash command (aisw-269).
    """
    if _digest_ctx is None:
        raise DigestNotInitialisedError(
            "digest context not initialised — call set_digest_context() at startup"
        )
    _, runner, resolve_owner_wikis, _, _, _, _ = _digest_ctx
    wikis = list(await resolve_owner_wikis(owner_telegram_id))
    if not wikis:
        return None
    (primary_id, primary_path), *rest = wikis
    return await runner(
        wiki_id=primary_id,
        wiki_path=primary_path,
        extra_add_dirs=[p for _, p in rest],
        planner_context="",
        correlation_id=f"expand:{owner_telegram_id}:{section}",
        section=section,
    )


async def _build_planner_context(
    session: AsyncSession,
    *,
    owner_telegram_id: int,
    window_hours: int,
    now_utc: datetime,
    tz: str,
) -> str:
    """Build the ru planner block fed into prompts/digest.md.

    Lists the owner's one-shot scheduled jobs whose ``scheduled_at_utc`` falls
    inside the digest window, rendered «- HH:MM — <title>» in the owner's tz.
    Recurring jobs (``scheduled_at_utc IS NULL`` — cron-driven) are excluded.
    """
    horizon = now_utc + timedelta(hours=window_hours)
    rows = (
        (
            await session.execute(
                select(Job)
                .where(
                    Job.owner_telegram_id == owner_telegram_id,
                    Job.status == "scheduled",
                    Job.scheduled_at_utc.is_not(None),
                    Job.scheduled_at_utc <= horizon,
                )
                .order_by(Job.scheduled_at_utc)
            )
        )
        .scalars()
        .all()
    )
    zone = ZoneInfo(tz)
    lines: list[str] = []
    for job in rows:
        try:
            payload = parse_job_payload(job.payload)
        except ValidationError:
            continue
        title = (
            getattr(payload, "message", None) or getattr(payload, "prompt_hint", None) or job.kind
        )
        scheduled = job.scheduled_at_utc
        assert scheduled is not None  # narrowed by the WHERE clause
        local = scheduled.replace(tzinfo=UTC).astimezone(zone)
        lines.append(f"- {local:%H:%M} — {title}")
    if not lines:
        return f"На ближайшие {window_hours} ч ничего не запланировано."  # noqa: RUF001
    return f"Запланировано на ближайшие {window_hours} ч:\n" + "\n".join(lines)


# START_BLOCK_CREATE_DIGEST_JOB
async def create_digest_job(
    session: AsyncSession,
    scheduler: AsyncIOScheduler,
    *,
    owner_telegram_id: int,
    chat_id: int,
    recurrence: Recurrence,
    wiki_scope: str | list[str] = "all",
    window_hours: int = 24,
    correlation_id: str = "",
) -> int:
    """Persist a digest Job row (committed) then register its CronTrigger.

    Same ordering invariant as create_reminder_job: the Job row is committed
    BEFORE scheduler.add_job. ``replace_existing=True`` makes re-registration on
    boot idempotent. No reconciliation pass in the MVP (documented limitation).
    ``wiki_scope`` is 'all' (every owner WIKI minus Inbox) or an explicit
    non-empty list of WIKI dir-stems (aisw-269).
    """
    payload = DigestPayload(
        wiki_scope="all" if wiki_scope == "all" else list(wiki_scope),
        recurrence=recurrence,
        window_hours=window_hours,
    ).model_dump(mode="json")
    job = Job(
        owner_telegram_id=owner_telegram_id,
        chat_id=chat_id,
        kind="digest_job",
        status="scheduled",
        priority=int(Lane.DIGEST),
        scheduled_at_utc=None,
        payload=payload,
        created_at_utc=_now_naive_utc(),
    )
    session.add(job)
    await session.flush()
    job_id = job.id
    await session.commit()

    scheduler.add_job(
        fire_digest_job,
        trigger=CronTrigger(timezone=recurrence.tz, **recurrence.to_cron()),
        args=[job_id],
        id=f"digest:{job_id}",
        replace_existing=True,
    )
    _log.info(
        "scheduler.digest.scheduled",
        correlation_id=correlation_id,
        job_id=job_id,
        owner_telegram_id=owner_telegram_id,
        recurrence=recurrence.model_dump(mode="json"),
        wiki_scope=payload["wiki_scope"],
    )
    return job_id


# END_BLOCK_CREATE_DIGEST_JOB


async def _digest_strike(
    session: AsyncSession,
    scheduler: AsyncIOScheduler,
    job: Job,
    *,
    exc: BaseException,
) -> None:
    """Record a digest run failure: bump retry_count, and at the strike limit
    disable the job (remove its trigger + DLQ row). Never raises."""
    job.retry_count = (job.retry_count or 0) + 1
    job.finished_at_utc = _now_naive_utc()
    job.last_error = f"{type(exc).__name__}: {exc}"
    disabled = job.retry_count >= _DIGEST_MAX_STRIKES
    if disabled:
        job.status = "disabled"
    await session.commit()
    if disabled:
        with contextlib.suppress(JobLookupError):
            scheduler.remove_job(f"digest:{job.id}")
        await move_to_dlq(
            session,
            job_id=job.id,
            reason="auto_disable",
            error_class=type(exc).__name__,
            last_error=job.last_error,
        )
        await session.commit()
        _log.warning(
            "scheduler.digest.disabled",
            job_id=job.id,
            error_class=type(exc).__name__,
            retry_count=job.retry_count,
        )
    _log.warning(
        "scheduler.digest.failed",
        job_id=job.id,
        error_class=type(exc).__name__,
        retry_count=job.retry_count,
        disabled=disabled,
    )


# START_BLOCK_FIRE_DIGEST_JOB
async def fire_digest_job(job_id: int) -> None:
    """APScheduler callback for a recurring digest. Picklable int arg only.

    Loads the Job, guards status=='scheduled', resolves the owner's WIKI set
    (Inbox-WIKI already excluded by the resolver), runs the Stage-1 digest via
    the runner adapter (which holds its own lock), delivers the assistant text
    to Telegram, and keeps the row 'scheduled' on success. On a run/delivery
    failure or a bad payload it strikes the job (3 strikes → disabled + DLQ +
    remove_job). Never propagates an exception (the scheduler must stay alive).
    """
    if _digest_ctx is None:
        raise DigestNotInitialisedError(
            "digest context not initialised — call set_digest_context() at startup"
        )
    scheduler, runner, resolve_owner_wikis, maker, audit_maker, sender, sessions_maker = _digest_ctx
    async with maker() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status != "scheduled":
            _log.info(
                "scheduler.digest.skipped",
                job_id=job_id,
                status=(job.status if job is not None else "missing"),
            )
            return
        try:
            payload = parse_job_payload(job.payload)
        except ValidationError:
            job.status = "disabled"
            job.last_error = "bad payload"
            await session.commit()
            await move_to_dlq(
                session,
                job_id=job_id,
                reason="bad_payload",
                error_class="ValidationError",
                last_error="bad payload",
            )
            await session.commit()
            _log.warning(
                "scheduler.digest.failed",
                job_id=job_id,
                error_class="ValidationError",
                disabled=True,
            )
            return
        if not isinstance(payload, DigestPayload):
            job.status = "disabled"
            await session.commit()
            _log.warning(
                "scheduler.digest.failed",
                job_id=job_id,
                error_class="WrongPayloadKind",
                disabled=True,
            )
            return

        owner_id = job.owner_telegram_id
        chat_id = job.chat_id
        job.started_at_utc = _now_naive_utc()
        await session.commit()
        _log.info("scheduler.digest.fired", job_id=job_id, owner_telegram_id=owner_id)

        wikis = list(await resolve_owner_wikis(owner_id))
        if not wikis:
            await sender.send_message(chat_id, _DIGEST_NO_WIKI_RU)
            job.finished_at_utc = _now_naive_utc()
            await session.commit()
            _log.info("scheduler.digest.delivered", job_id=job_id, empty="no_wiki")
            return
        if isinstance(payload.wiki_scope, list):
            wanted = {name.lower() for name in payload.wiki_scope}
            kept = [(wid, p) for (wid, p) in wikis if wid.lower() in wanted]
            vanished = sorted(wanted - {wid.lower() for wid, _ in kept})
            _log.info(
                "scheduler.digest.scope_filter",
                job_id=job_id,
                requested=list(payload.wiki_scope),
                kept=[wid for wid, _ in kept],
                vanished=vanished,
            )
            if not kept:
                await sender.send_message(chat_id, _DIGEST_SCOPE_VANISHED_RU)
                job.finished_at_utc = _now_naive_utc()
                await session.commit()
                _log.info("scheduler.digest.delivered", job_id=job_id, empty="scope_vanished")
                return
            wikis = kept
        (primary_id, primary_path), *rest = wikis
        extra_dirs = [p for _, p in rest]
        planner_context = await _build_planner_context(
            session,
            owner_telegram_id=owner_id,
            window_hours=payload.window_hours,
            now_utc=_now_naive_utc(),
            tz=payload.recurrence.tz,
        )
        _log.info(
            "scheduler.digest.planner_context",
            job_id=job_id,
            owner_telegram_id=owner_id,
            n_planned=planner_context.count("\n- "),
        )

        try:
            text = await runner(
                wiki_id=primary_id,
                wiki_path=primary_path,
                extra_add_dirs=extra_dirs,
                planner_context=planner_context,
                correlation_id=f"digest:{job_id}",
            )
        except Exception as exc:
            await _digest_strike(session, scheduler, job, exc=exc)
            return

        body = (text or "").strip() or _DIGEST_EMPTY_RU
        run_id = f"digest-{uuid4().hex[:12]}"
        try:
            receipt = await deliver_output(
                sender=sender,
                chat_id=chat_id,
                telegram_id=owner_id,
                wiki_id=primary_id,
                run_id=run_id,
                text=body,
                runs_dir=primary_path / "data" / "runs",
                audit_session_maker=audit_maker,
                kind="digest",
                job_id=job_id,
            )
        except Exception as exc:
            await _digest_strike(session, scheduler, job, exc=exc)
            return

        job.retry_count = 0
        job.finished_at_utc = _now_naive_utc()
        # status stays 'scheduled' — recurring.
        await session.commit()
        _log.info(
            "scheduler.digest.delivered",
            job_id=job_id,
            run_id=run_id,
            n_messages=receipt.n_messages,
            document_sent=receipt.document_sent,
        )


# END_BLOCK_FIRE_DIGEST_JOB
