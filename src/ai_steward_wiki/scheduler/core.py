# FILE: src/ai_steward_wiki/scheduler/core.py
# VERSION: 0.0.6
# START_MODULE_CONTRACT
#   PURPOSE: APScheduler AsyncIOScheduler factory (D-003) + SIGTERM→grace→SIGKILL
#            kill-sequence (D-021) + lifecycle logging listener (chunk 2).
#   SCOPE: build_scheduler(jobs_db_url, ...) and kill_with_sequence(proc, grace).
#          APScheduler in-process; SQLAlchemyJobStore on jobs.db for user reminders
#          ("default"), plus a non-persistent MemoryJobStore for infra cron
#          ("memory") — maintenance/retention/snapshot/media-sweep.
#          Lifecycle listener emits scheduler.job.{executed,error,missed,max_instances}.
#   DEPENDS: apscheduler, sqlalchemy, asyncio, signal, structlog
#   LINKS: M-SCHEDULER, M-STORAGE-JOBS, M-FOUNDATION-LOGGING
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   DEFAULT_TERM_GRACE_SECONDS - default SIGTERM grace before SIGKILL (D-021)
#   MAINTENANCE_JOBSTORE_ALIAS - "memory", target for infra cron jobs (aisw-6mi)
#   build_scheduler - AsyncIOScheduler with default+memory jobstores AND lifecycle listener
#   kill_with_sequence - SIGTERM, wait grace seconds, SIGKILL fallback
#   attach_lifecycle_logging - register the lifecycle listener on a given scheduler
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.6 - aisw-nrt (chunk 2): APScheduler lifecycle listener emits
#                scheduler.job.{executed,error,missed,max_instances} (PII-safe).
#   PREVIOUS:    v0.0.5 - aisw-6mi: add MemoryJobStore("memory") next to
#                SQLAlchemyJobStore("default"); fixes pickle crash for maintenance
#                jobs that pass async_sessionmaker as job args.
#   PREVIOUS:    v0.0.4 - chunk 4: scheduler factory + kill-sequence (D-003, D-021)
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Protocol

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
    SchedulerEvent,
)
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ai_steward_wiki.logging_events import (
    SCHEDULER_JOB_ERROR,
    SCHEDULER_JOB_EXECUTED,
    SCHEDULER_JOB_MAX_INSTANCES,
    SCHEDULER_JOB_MISSED,
)
from ai_steward_wiki.logging_setup import get_logger

DEFAULT_TERM_GRACE_SECONDS = 10.0
MAINTENANCE_JOBSTORE_ALIAS = "memory"

_LOG = get_logger(__name__)

_LIFECYCLE_MASK = EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES


def _srt_iso(event: SchedulerEvent) -> str | None:
    """Best-effort scheduled_run_time → ISO-8601 UTC string.

    JobExecutionEvent has .scheduled_run_time (datetime).
    JobSubmissionEvent has .scheduled_run_times (list[datetime]); take the first.
    """
    srt = getattr(event, "scheduled_run_time", None)
    if srt is None:
        srts = getattr(event, "scheduled_run_times", None)
        if srts:
            srt = srts[0]
    if srt is None:
        return None
    return srt.isoformat() if isinstance(srt, datetime) else str(srt)


def _scheduler_event_listener(event: SchedulerEvent) -> None:
    """Single APScheduler listener: dispatch on event.code, emit canonical log.

    PII-safe: only standard metadata (job_id, jobstore, scheduled_run_time,
    duration_ms, traceback for errors). Never logs job args/kwargs, retval,
    or exception .args body.
    """
    job_id = getattr(event, "job_id", None)
    jobstore = getattr(event, "jobstore", None)
    srt_iso = _srt_iso(event)

    if event.code == EVENT_JOB_EXECUTED:
        # APScheduler does not expose execution start/end timestamps; use
        # (now - scheduled_run_time) as a wall-clock proxy for duration_ms.
        # When scheduled_run_time is unavailable, log 0 to keep the field shape.
        if isinstance(event, JobExecutionEvent) and event.scheduled_run_time:
            delta = datetime.now(UTC) - event.scheduled_run_time
            duration_ms = max(0, int(delta.total_seconds() * 1000))
        else:
            duration_ms = 0
        _LOG.info(
            SCHEDULER_JOB_EXECUTED,
            job_id=job_id,
            jobstore=jobstore,
            scheduled_run_time=srt_iso,
            duration_ms=duration_ms,
        )
    elif event.code == EVENT_JOB_ERROR:
        traceback_text = getattr(event, "traceback", None)
        _LOG.error(
            SCHEDULER_JOB_ERROR,
            job_id=job_id,
            jobstore=jobstore,
            scheduled_run_time=srt_iso,
            traceback=traceback_text,
        )
    elif event.code == EVENT_JOB_MISSED:
        _LOG.warning(
            SCHEDULER_JOB_MISSED,
            job_id=job_id,
            jobstore=jobstore,
            scheduled_run_time=srt_iso,
        )
    elif event.code == EVENT_JOB_MAX_INSTANCES:
        _LOG.warning(
            SCHEDULER_JOB_MAX_INSTANCES,
            job_id=job_id,
            jobstore=jobstore,
            scheduled_run_time=srt_iso,
        )
    # Unknown codes: ignored. Defensive — listener is registered with a mask,
    # but a future maintainer might widen it without updating dispatch.


def attach_lifecycle_logging(scheduler: AsyncIOScheduler) -> None:
    """Register the lifecycle log listener on ``scheduler`` with the canonical mask."""
    scheduler.add_listener(_scheduler_event_listener, _LIFECYCLE_MASK)


# Re-export for tests + callers that need direct access.
__listener__ = _scheduler_event_listener


class _Killable(Protocol):
    """Subset of asyncio.subprocess.Process / multiprocessing.Process we need."""

    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    async def wait(self) -> int: ...


def build_scheduler(
    jobs_db_sync_url: str,
    *,
    timezone: str = "UTC",
    table_name: str = "apscheduler_jobs",
) -> AsyncIOScheduler:
    """Build AsyncIOScheduler backed by SQLAlchemyJobStore on jobs.db (D-003).

    Note: SQLAlchemyJobStore expects a sync URL (e.g. ``sqlite:///data/jobs.db``).
    Caller converts the async aiosqlite URL accordingly.
    """
    jobstores = {
        "default": SQLAlchemyJobStore(url=jobs_db_sync_url, tablename=table_name),
        MAINTENANCE_JOBSTORE_ALIAS: MemoryJobStore(),
    }
    executors = {"default": AsyncIOExecutor()}
    job_defaults = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 30}
    scheduler = AsyncIOScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults=job_defaults,
        timezone=timezone,
    )
    attach_lifecycle_logging(scheduler)
    return scheduler


async def kill_with_sequence(
    proc: _Killable,
    *,
    grace_seconds: float = DEFAULT_TERM_GRACE_SECONDS,
) -> int:
    """SIGTERM, then SIGKILL after grace_seconds if still running (D-021)."""
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    try:
        return await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        return await proc.wait()


__all__ = [
    "DEFAULT_TERM_GRACE_SECONDS",
    "MAINTENANCE_JOBSTORE_ALIAS",
    "attach_lifecycle_logging",
    "build_scheduler",
    "kill_with_sequence",
]
