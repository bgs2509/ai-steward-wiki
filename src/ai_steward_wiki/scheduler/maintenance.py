# FILE: src/ai_steward_wiki/scheduler/maintenance.py
# VERSION: 0.0.5
# START_MODULE_CONTRACT
#   PURPOSE: Register periodic maintenance jobs on the APScheduler bootstrap.
#   SCOPE: register_purge_expired_pending_job, register_media_staging_sweep_job,
#          register_all_retention_jobs (aggregator).
#   DEPENDS: asyncio, apscheduler.triggers.cron, structlog,
#            ai_steward_wiki.auth.onboarding.purge_expired_pending,
#            ai_steward_wiki.inbox.staging.sweep_all_user_staging
#   LINKS: M-ONBOARD-ADMIN, M-SCHEDULER, M-INBOX, D-022, D-030
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   PURGE_PENDING_JOB_ID - stable id for the daily purge job
#   MEDIA_STAGING_SWEEP_JOB_ID - stable id for the daily media _staging sweep
#   register_purge_expired_pending_job - add daily cron job at 05:00 UTC
#   register_media_staging_sweep_job - daily cron sweeping stale staged media across
#                                      every per-user Inbox-WIKI under wiki_root (D-022)
#   register_all_retention_jobs - chunks 12-14 + media sweep wiring
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.5 - aisw-6mi: register maintenance jobs in the "memory"
#                jobstore (MAINTENANCE_JOBSTORE_ALIAS) instead of the default
#                SQLAlchemyJobStore — args carry async_sessionmaker which is
#                not picklable (create_engine.<locals>.connect closure).
#   PREVIOUS:    v0.0.4 - aisw-12t (Phase-E.a): media sweep iterates per-user
#                Inbox-WIKI/raw/media/_staging (was a single shared dir);
#                register_media_staging_sweep_job(staging_root→wiki_root),
#                register_all_retention_jobs(media_staging_root→wiki_root_for_media_sweep).
#   PREVIOUS:    v0.0.3 - aisw-8r9 (media chunk 4): register_media_staging_sweep_job —
#                daily cron sweeping <staging_root> entries older than 24h (D-022);
#                also included in register_all_retention_jobs.
#   PREVIOUS:    v0.0.2 - chunk 14: also register db_snapshot job
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.auth.onboarding import PENDING_USER_TTL_DAYS, purge_expired_pending
from ai_steward_wiki.inbox.staging import DEFAULT_STAGING_TTL_S, sweep_all_user_staging
from ai_steward_wiki.scheduler.core import MAINTENANCE_JOBSTORE_ALIAS

__all__ = [
    "MEDIA_STAGING_SWEEP_JOB_ID",
    "PURGE_PENDING_JOB_ID",
    "register_all_retention_jobs",
    "register_media_staging_sweep_job",
    "register_purge_expired_pending_job",
]

_log = structlog.get_logger("scheduler.maintenance")

PURGE_PENDING_JOB_ID = "auth.purge_expired_pending"
MEDIA_STAGING_SWEEP_JOB_ID = "inbox.media_staging_sweep"


async def _run_purge(session_maker: async_sessionmaker[AsyncSession], ttl_days: int) -> int:
    return await purge_expired_pending(session_maker, ttl_days=ttl_days)


async def _run_all_user_media_sweep(wiki_root: Path, ttl_s: int) -> int:
    """Sweep stale staged media across every per-user Inbox-WIKI under wiki_root (D-022).

    Sync file IO runs in a worker thread.
    """
    removed = await asyncio.to_thread(sweep_all_user_staging, wiki_root, ttl_s=ttl_s)
    _log.info("maintenance.media_sweep.done", wiki_root=str(wiki_root), removed=removed)
    return removed


def register_all_retention_jobs(
    scheduler: AsyncIOScheduler,
    *,
    audit_maker: async_sessionmaker[AsyncSession],
    jobs_maker: async_sessionmaker[AsyncSession],
    sessions_maker: async_sessionmaker[AsyncSession],
    dry_run: bool = False,
    snapshot_root: Path | None = None,
    db_urls_for_snapshot: dict[str, str] | None = None,
    snapshot_retention_days: int | None = None,
    wiki_root_for_media_sweep: Path | None = None,
) -> list[Any]:
    """Register chunks 12-14 maintenance jobs + the media _staging sweep.

    Idempotent: APScheduler `replace_existing=True` lets this run on every boot.
    ``snapshot_root`` + ``db_urls_for_snapshot`` are required to enable the
    chunk 14 db_snapshot job; when omitted the snapshot cron is not registered.
    ``wiki_root_for_media_sweep`` enables the D-022 per-user staging sweep
    (it iterates ``<wiki_root>/<user>/Inbox-WIKI/raw/media/_staging``); when
    omitted the sweep cron is not registered.
    """
    # Local imports to avoid a scheduler↔ops cycle at module load.
    from ai_steward_wiki.ops.retention import (
        DB_AUDIT,
        DB_JOBS,
        register_retention_jobs,
    )
    from ai_steward_wiki.ops.snapshot import (
        SNAPSHOT_RETENTION_DAYS,
        register_db_snapshot_job,
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
    if snapshot_root is not None and db_urls_for_snapshot is not None:
        jobs.append(
            register_db_snapshot_job(
                scheduler,
                snapshot_root=snapshot_root,
                db_urls=db_urls_for_snapshot,
                retention_days=snapshot_retention_days or SNAPSHOT_RETENTION_DAYS,
            )
        )
    if wiki_root_for_media_sweep is not None:
        jobs.append(
            register_media_staging_sweep_job(scheduler, wiki_root=wiki_root_for_media_sweep)
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
        jobstore=MAINTENANCE_JOBSTORE_ALIAS,
    )


def register_media_staging_sweep_job(
    scheduler: AsyncIOScheduler,
    *,
    wiki_root: Path,
    ttl_s: int = DEFAULT_STAGING_TTL_S,
    hour: int = 4,
    minute: int = 30,
) -> Any:
    """Idempotently schedule the daily per-user media _staging sweep at hour:minute UTC (D-022).

    Removes staged media files older than ``ttl_s`` (default 24h) — leftovers from
    no-WIKI intents, rejected confirms, or failed Stage-1 runs — across every
    ``<wiki_root>/<user>/Inbox-WIKI/raw/media/_staging`` directory.
    """
    return scheduler.add_job(
        _run_all_user_media_sweep,
        trigger=CronTrigger(hour=hour, minute=minute, timezone="UTC"),
        id=MEDIA_STAGING_SWEEP_JOB_ID,
        replace_existing=True,
        args=[wiki_root, ttl_s],
        jobstore=MAINTENANCE_JOBSTORE_ALIAS,
    )
