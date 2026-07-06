# FILE: src/ai_steward_wiki/scheduler/cron_user.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Cron-user job firing bridge — INSERT jobs.Job + register CronTrigger;
#            on fire (picklable int callback), push CronUserQueueMsg to
#            PriorityJobQueue(lane=CRON_WRITE). Does NOT execute CLI — execution
#            is M-SCHEDULER-CONSUMER's concern (R-1 separation of concerns). Also
#            hosts the check_in producer twin (create_check_in_job/fire_check_in_job,
#            aisw-xi8, DEC-8), mirroring the same shape for CheckInQueueMsg.
#   SCOPE: set_cron_user_context, create_cron_user_job, fire_cron_user_job,
#          create_check_in_job, fire_check_in_job, CronUserContextNotInitialisedError.
#          Module-level _ctx registry installed once at startup so the APScheduler
#          callback can stay picklable (only int args).
#   DEPENDS: apscheduler (AsyncIOScheduler, CronTrigger), sqlalchemy(.ext.asyncio),
#            structlog, uuid,
#            ai_steward_wiki.storage.jobs.models.Job,
#            ai_steward_wiki.storage.jobs.payloads.CronUserPayload/CheckInPayload,
#            ai_steward_wiki.classifier.recurrence.Recurrence,
#            ai_steward_wiki.scheduler.queue.PriorityJobQueue/Lane,
#            ai_steward_wiki.scheduler.queue_payloads.CronUserQueueMsg/CheckInQueueMsg
#   LINKS: M-SCHEDULER-CRON-USER, M-STORAGE-JOBS, M-SCHEDULER, M-CLASSIFIER-RECURRENCE,
#          aisw-02v, aisw-xi8, D-002, D-011 §3
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   CronUserContextNotInitialisedError - raised when fire_cron_user_job runs before set_cron_user_context()
#   set_cron_user_context - install the module-level (scheduler, queue, jobs_session_maker) registry once at startup
#   create_cron_user_job - INSERT+commit jobs.Job(kind='cron_user') + scheduler.add_job(CronTrigger, args=[job_id], replace_existing); returns job_id
#   fire_cron_user_job - APScheduler callback (picklable int): load Job, guard status=='scheduled', push CronUserQueueMsg to queue, mark status='queued'
#   create_check_in_job - INSERT+commit jobs.Job(kind='check_in') + scheduler.add_job(CronTrigger, args=[job_id], replace_existing); returns job_id
#   fire_check_in_job - APScheduler callback (picklable int): load Job, guard status=='scheduled', push CheckInQueueMsg to queue, mark status='queued'
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - aisw-xi8 (Phase-B, DEC-6/DEC-8): NEW create_check_in_job
#                / fire_check_in_job, mirroring create_cron_user_job/fire_cron_user_job
#                exactly. NO CLI invocation in fire_check_in_job — pushes a
#                CheckInQueueMsg onto Lane.CRON_WRITE; execution is
#                M-SCHEDULER-CONSUMER's concern (Task B7).
#   PREVIOUS:    v0.0.1 - aisw-02v: initial cron-user firing bridge (mirrors M-SCHEDULER-FIRING shape)
# END_CHANGE_SUMMARY

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.scheduler.queue import Lane, PriorityJobQueue
from ai_steward_wiki.scheduler.queue_payloads import CheckInQueueMsg, CronUserQueueMsg
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import CheckInPayload, CronUserPayload

__all__ = [
    "CronUserContextNotInitialisedError",
    "create_check_in_job",
    "create_cron_user_job",
    "fire_check_in_job",
    "fire_cron_user_job",
    "set_cron_user_context",
]

_log = structlog.get_logger("scheduler.cron_user")


class CronUserContextNotInitialisedError(RuntimeError):
    """Raised when create_cron_user_job/fire_cron_user_job runs before set_cron_user_context()."""


# Module-level registry — set once at startup by set_cron_user_context().
# Kept module-global (not instance state) so the APScheduler callback can take
# a single picklable int and still find its dependencies (SQLAlchemyJobStore-safe).
_ctx: tuple[AsyncIOScheduler, PriorityJobQueue, async_sessionmaker[AsyncSession]] | None = None


def set_cron_user_context(
    scheduler: AsyncIOScheduler,
    queue: PriorityJobQueue,
    jobs_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Install the (scheduler, queue, jobs_session_maker) triple for the firing bridge."""
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
    """INSERT jobs.Job(kind='cron_user') and register CronTrigger; returns int job_id.

    Commit-before-add_job: if APScheduler add_job raises, the row is still durable
    (it just lacks an active trigger — a future create_cron_user_job invocation
    or a /cron_repair would rebind it). Mirrors firing.create_digest_job ordering.
    """
    if _ctx is None:
        raise CronUserContextNotInitialisedError("set_cron_user_context not called")
    scheduler, _queue, session_maker = _ctx

    payload = CronUserPayload(recurrence=recurrence, command=command, wiki_id=wiki_id)
    # START_BLOCK_CRON_USER_INSERT
    async with session_maker() as session, session.begin():
        row = Job(
            owner_telegram_id=owner_telegram_id,
            chat_id=chat_id,
            kind="cron_user",
            status="scheduled",
            priority=int(Lane.CRON_WRITE),
            payload=payload.model_dump(mode="json"),
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
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
    """APScheduler callback (picklable int arg) — enqueue CronUserQueueMsg.

    Loads the Job row; guards status=='scheduled' (idempotent on APScheduler
    misfire-replay or duplicate fires); constructs CronUserQueueMsg with a
    fresh correlation_id; pushes onto PriorityJobQueue under Lane.CRON_WRITE;
    mutates status='scheduled' → 'queued' inside the same transaction so a
    queue.put failure rolls the status back (test-asserted).
    """
    if _ctx is None:
        raise CronUserContextNotInitialisedError("set_cron_user_context not called")
    _scheduler, queue, session_maker = _ctx

    # START_BLOCK_CRON_USER_FIRE
    async with session_maker() as session, session.begin():
        row = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        if row is None or row.status != "scheduled":
            _log.info(
                "scheduler.cron_user.fire.job_missing",
                job_id=job_id,
                found=row is not None,
                status=getattr(row, "status", None),
            )
            return
        try:
            payload = CronUserPayload(**row.payload)
        except Exception as exc:
            _log.warning(
                "scheduler.cron_user.fire.failed",
                job_id=job_id,
                error_class=type(exc).__name__,
                reason="payload_invalid",
            )
            raise
        msg = CronUserQueueMsg(
            job_id=job_id,
            owner_telegram_id=row.owner_telegram_id,
            chat_id=row.chat_id,
            command=payload.command,
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
                reason="queue_put",
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


async def create_check_in_job(
    *,
    owner_telegram_id: int,
    chat_id: int,
    recurrence: Recurrence,
    question_topic: str,
    user_tz: str,
    wiki_id: str | None,
) -> int:
    """INSERT jobs.Job(kind='check_in') and register CronTrigger; returns int job_id.

    Mirrors create_cron_user_job exactly (DEC-8) — Job row committed BEFORE
    scheduler.add_job.
    """
    if _ctx is None:
        raise CronUserContextNotInitialisedError("set_cron_user_context not called")
    scheduler, _queue, session_maker = _ctx

    payload = CheckInPayload(question_topic=question_topic, recurrence=recurrence, wiki_id=wiki_id)
    async with session_maker() as session, session.begin():
        row = Job(
            owner_telegram_id=owner_telegram_id,
            chat_id=chat_id,
            kind="check_in",
            status="scheduled",
            priority=int(Lane.CRON_WRITE),
            payload=payload.model_dump(mode="json"),
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        session.add(row)
        await session.flush()
        job_id = row.id

    cron_kwargs = recurrence.to_cron()
    scheduler.add_job(
        fire_check_in_job,
        CronTrigger(timezone=user_tz, **cron_kwargs),
        args=[job_id],
        id=f"check_in:{job_id}",
        replace_existing=True,
    )
    _log.info(
        "scheduler.check_in.scheduled",
        job_id=job_id,
        owner_telegram_id=owner_telegram_id,
        chat_id=chat_id,
        recurrence_kind=recurrence.kind,
        tz=user_tz,
    )
    return job_id


async def fire_check_in_job(job_id: int) -> None:
    """APScheduler callback (picklable int) — enqueue CheckInQueueMsg.

    NO CLI invocation here — execution is M-SCHEDULER-CONSUMER's concern
    (mirrors fire_cron_user_job's producer/consumer split, DEC-8).
    """
    if _ctx is None:
        raise CronUserContextNotInitialisedError("set_cron_user_context not called")
    _scheduler, queue, session_maker = _ctx

    async with session_maker() as session, session.begin():
        row = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        if row is None or row.status != "scheduled":
            _log.info(
                "scheduler.check_in.fire.job_missing",
                job_id=job_id,
                found=row is not None,
                status=getattr(row, "status", None),
            )
            return
        try:
            payload = CheckInPayload(**row.payload)
        except Exception as exc:
            _log.warning(
                "scheduler.check_in.fire.failed",
                job_id=job_id,
                error_class=type(exc).__name__,
                reason="payload_invalid",
            )
            raise
        msg = CheckInQueueMsg(
            job_id=job_id,
            owner_telegram_id=row.owner_telegram_id,
            chat_id=row.chat_id,
            question_topic=payload.question_topic,
            correlation_id=uuid4().hex,
            scheduled_at_utc=datetime.now(UTC),
        )
        try:
            await queue.put(Lane.CRON_WRITE, msg)
        except Exception as exc:
            _log.warning(
                "scheduler.check_in.fire.failed",
                job_id=job_id,
                error_class=type(exc).__name__,
                reason="queue_put",
            )
            raise
        row.status = "queued"

    _log.info(
        "scheduler.check_in.fired",
        job_id=job_id,
        owner_telegram_id=msg.owner_telegram_id,
        chat_id=msg.chat_id,
        correlation_id=msg.correlation_id,
    )
