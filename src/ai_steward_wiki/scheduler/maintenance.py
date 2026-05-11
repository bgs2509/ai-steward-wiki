# FILE: src/ai_steward_wiki/scheduler/maintenance.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Register periodic maintenance jobs on the APScheduler bootstrap.
#   SCOPE: register_purge_expired_pending_job(scheduler, session_maker, hour, minute).
#   DEPENDS: apscheduler.triggers.cron, ai_steward_wiki.auth.onboarding.purge_expired_pending
#   LINKS: M-ONBOARD-ADMIN, M-SCHEDULER, D-030
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   PURGE_PENDING_JOB_ID - stable id for the daily purge job
#   register_purge_expired_pending_job - add daily cron job at 05:00 UTC
#   register_all_retention_jobs - chunk 13 wiring: pending purge + §10.4 retention purges
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 12: pending_users daily 05:00 UTC purge
# END_CHANGE_SUMMARY

from __future__ import annotations

from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.auth.onboarding import PENDING_USER_TTL_DAYS, purge_expired_pending

__all__ = [
    "PURGE_PENDING_JOB_ID",
    "register_all_retention_jobs",
    "register_purge_expired_pending_job",
]

PURGE_PENDING_JOB_ID = "auth.purge_expired_pending"


async def _run_purge(session_maker: async_sessionmaker[AsyncSession], ttl_days: int) -> int:
    return await purge_expired_pending(session_maker, ttl_days=ttl_days)


def register_all_retention_jobs(
    scheduler: AsyncIOScheduler,
    *,
    audit_maker: async_sessionmaker[AsyncSession],
    jobs_maker: async_sessionmaker[AsyncSession],
    sessions_maker: async_sessionmaker[AsyncSession],
    dry_run: bool = False,
) -> list[Any]:
    """Register chunk 12 pending-users purge + chunk 13 §10.4 retention jobs.

    Idempotent: APScheduler `replace_existing=True` lets this run on every boot.
    """
    # Local import to avoid a scheduler↔ops cycle at module load.
    from ai_steward_wiki.ops.retention import (
        DB_AUDIT,
        DB_JOBS,
        register_retention_jobs,
    )

    jobs: list[Any] = []
    jobs.append(register_purge_expired_pending_job(scheduler, sessions_maker))
    jobs.extend(
        register_retention_jobs(
            scheduler,
            db_makers={DB_AUDIT: audit_maker, DB_JOBS: jobs_maker},
            audit_maker=audit_maker,
            dry_run=dry_run,
        )
    )
    return jobs


def register_purge_expired_pending_job(
    scheduler: AsyncIOScheduler,
    session_maker: async_sessionmaker[AsyncSession],
    *,
    ttl_days: int = PENDING_USER_TTL_DAYS,
    hour: int = 5,
    minute: int = 0,
) -> Any:
    """Idempotently schedule daily pending_users purge at hour:minute UTC (D-030)."""
    return scheduler.add_job(
        _run_purge,
        trigger=CronTrigger(hour=hour, minute=minute, timezone="UTC"),
        id=PURGE_PENDING_JOB_ID,
        replace_existing=True,
        args=[session_maker, ttl_days],
    )
