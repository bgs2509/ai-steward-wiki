# FILE: src/ai_steward_wiki/scheduler/firing.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: One-shot reminder bridge — create a jobs.Job + APScheduler
#            DateTrigger, and deliver the reminder as a plain Telegram message
#            on fire (no Claude/WIKI). (aisw-kcz, Inbox-WIKI Phase-D.a.)
#   SCOPE: set_firing_context, create_reminder_job, fire_job. Module-level
#          (TgSender, jobs async_sessionmaker) registry set once at startup;
#          fire_job takes only a picklable int (SQLAlchemyJobStore-safe).
#   DEPENDS: apscheduler, sqlalchemy.ext.asyncio, structlog, pydantic,
#            ai_steward_wiki.storage.jobs.models.Job,
#            ai_steward_wiki.storage.jobs.payloads (ReminderPayload, parse_job_payload),
#            ai_steward_wiki.scheduler.queue.Lane,
#            ai_steward_wiki.tg.bot.TgSender (typing only)
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

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.scheduler.queue import Lane
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import ReminderPayload, parse_job_payload

if TYPE_CHECKING:
    from ai_steward_wiki.tg.bot import TgSender

__all__ = [
    "FiringNotInitialisedError",
    "create_reminder_job",
    "fire_job",
    "set_firing_context",
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
