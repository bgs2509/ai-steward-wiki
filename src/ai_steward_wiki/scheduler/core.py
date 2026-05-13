# FILE: src/ai_steward_wiki/scheduler/core.py
# VERSION: 0.0.5
# START_MODULE_CONTRACT
#   PURPOSE: APScheduler AsyncIOScheduler factory (D-003) + SIGTERM→grace→SIGKILL
#            kill-sequence (D-021).
#   SCOPE: build_scheduler(jobs_db_url, ...) and kill_with_sequence(proc, grace).
#          APScheduler in-process; SQLAlchemyJobStore on jobs.db for user reminders
#          ("default"), plus a non-persistent MemoryJobStore for infra cron
#          ("memory") — maintenance/retention/snapshot/media-sweep.
#   DEPENDS: apscheduler, sqlalchemy, asyncio, signal
#   LINKS: M-SCHEDULER, M-STORAGE-JOBS
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   DEFAULT_TERM_GRACE_SECONDS - default SIGTERM grace before SIGKILL (D-021)
#   MAINTENANCE_JOBSTORE_ALIAS - "memory", target for infra cron jobs (aisw-6mi)
#   build_scheduler - AsyncIOScheduler with default (SQLAlchemy) + memory jobstores
#   kill_with_sequence - SIGTERM, wait grace seconds, SIGKILL fallback
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.5 - aisw-6mi: add MemoryJobStore("memory") next to
#                SQLAlchemyJobStore("default"); fixes pickle crash for maintenance
#                jobs that pass async_sessionmaker as job args.
#   PREVIOUS:    v0.0.4 - chunk 4: scheduler factory + kill-sequence (D-003, D-021)
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import contextlib
from typing import Protocol

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

DEFAULT_TERM_GRACE_SECONDS = 10.0
MAINTENANCE_JOBSTORE_ALIAS = "memory"


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
    return AsyncIOScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults=job_defaults,
        timezone=timezone,
    )


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
    "build_scheduler",
    "kill_with_sequence",
]
