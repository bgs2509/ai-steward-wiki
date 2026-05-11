# FILE: src/ai_steward_wiki/ops/retention.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: APScheduler maintenance jobs implementing §10.4 retention table.
#   SCOPE: RetentionPolicy / RETENTION_POLICIES / run_purge / register_retention_jobs /
#          purge_trash_sweep / purge_staging.
#   DEPENDS: apscheduler, sqlalchemy.async, ai_steward_wiki.storage.{audit,jobs},
#            ai_steward_wiki.ops.pii.PIIRedactor
#   LINKS: D-034 §10.4, M-OPS-PII, M-SCHEDULER
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   DB_AUDIT - canonical name for audit.db retention target
#   DB_JOBS - canonical name for jobs.db retention target
#   RetentionPolicy - Pydantic v2 model declaring one rule
#   RETENTION_POLICIES - canonical list (§10.4) consumed by both code and tests
#   PurgeResult - Pydantic v2 model: store, deleted, dry_run, cutoff_utc, oldest_kept_utc
#   run_purge - execute one policy
#   register_retention_jobs - add all RETENTION_POLICIES to a scheduler
#   purge_trash_sweep - final tier-1/tier-2 sweep of _trash/ before rmtree
#   purge_staging - file mtime sweep wrapping inbox.staging.sweep_staging
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 13: §10.4 retention purge jobs
# END_CHANGE_SUMMARY

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.ops.pii import PIIRedactor
from ai_steward_wiki.storage.audit.models import AuditEvent

__all__ = [
    "DB_AUDIT",
    "DB_JOBS",
    "RETENTION_POLICIES",
    "PurgeResult",
    "RetentionPolicy",
    "purge_staging",
    "purge_trash_sweep",
    "register_retention_jobs",
    "run_purge",
]

DB_AUDIT = "audit"
DB_JOBS = "jobs"


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class RetentionPolicy(BaseModel):
    """A single §10.4 retention rule.

    `table` and `ts_column` drive a parametric DELETE; the matrix test feeds
    each policy through `run_purge` so the spec table and the code stay
    aligned (single source of truth, no drift).
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str
    db: str  # DB_AUDIT or DB_JOBS
    table: str
    ts_column: str
    retention: timedelta
    cron_hour: int
    cron_minute: int
    audit_event: str = "retention.purge"


class PurgeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    store: str
    deleted: int
    dry_run: bool
    cutoff_utc: datetime
    oldest_kept_utc: datetime | None = None


# Canonical §10.4 retention list. `pending_users_purge` is owned by chunk 12
# (see scheduler/maintenance.py: register_purge_expired_pending_job).
RETENTION_POLICIES: list[RetentionPolicy] = [
    RetentionPolicy(
        name="chat_log_purge",
        db=DB_AUDIT,
        table="chat_log",
        ts_column="created_at_utc",
        retention=timedelta(days=30),
        cron_hour=4,
        cron_minute=0,
    ),
    RetentionPolicy(
        name="tg_updates_purge",
        db=DB_AUDIT,
        table="tg_updates",
        ts_column="received_at_utc",
        retention=timedelta(hours=24),
        cron_hour=-1,  # hourly
        cron_minute=7,
    ),
    RetentionPolicy(
        name="seen_files_purge",
        db=DB_AUDIT,
        table="seen_files",
        ts_column="first_seen_at_utc",
        retention=timedelta(days=30),
        cron_hour=4,
        cron_minute=10,
    ),
    RetentionPolicy(
        name="dedup_hits_purge",
        db=DB_AUDIT,
        table="dedup_hits",
        ts_column="created_at_utc",
        retention=timedelta(days=90),
        cron_hour=4,
        cron_minute=15,
    ),
    RetentionPolicy(
        name="audit_purge",
        db=DB_AUDIT,
        table="audit_events",
        ts_column="created_at_utc",
        retention=timedelta(days=90),
        cron_hour=4,
        cron_minute=20,
    ),
    RetentionPolicy(
        name="admin_events_purge",
        db=DB_AUDIT,
        table="admin_events",
        ts_column="created_at_utc",
        retention=timedelta(days=90),
        cron_hour=4,
        cron_minute=25,
    ),
    RetentionPolicy(
        name="job_outputs_purge",
        db=DB_AUDIT,
        table="job_outputs",
        ts_column="fired_at_utc",
        retention=timedelta(days=90),
        cron_hour=4,
        cron_minute=30,
    ),
    RetentionPolicy(
        name="run_outputs_purge",
        db=DB_AUDIT,
        table="run_outputs",
        ts_column="started_at_utc",
        retention=timedelta(days=180),
        cron_hour=4,
        cron_minute=35,
    ),
    RetentionPolicy(
        name="onboarding_purge",
        db=DB_AUDIT,
        table="onboarding_events",
        ts_column="shown_at_utc",
        retention=timedelta(days=180),
        cron_hour=4,
        cron_minute=40,
    ),
    RetentionPolicy(
        name="tracker_purge",
        db=DB_JOBS,
        table="tracker_answers",
        ts_column="created_at_utc",
        retention=timedelta(days=90),
        cron_hour=4,
        cron_minute=45,
    ),
]


# START_CONTRACT: run_purge
#   PURPOSE: Execute one RetentionPolicy: count rows older than cutoff,
#            delete them (unless dry_run), audit the result.
#   INPUTS: { policy, db_makers, audit_maker, dry_run, now }
#   OUTPUTS: { PurgeResult }
#   SIDE_EFFECTS: DELETE on target table; INSERT into audit_events.
#   LINKS: D-034 §10.4
# END_CONTRACT: run_purge
async def run_purge(
    policy: RetentionPolicy,
    *,
    db_makers: dict[str, async_sessionmaker[AsyncSession]],
    audit_maker: async_sessionmaker[AsyncSession],
    dry_run: bool = False,
    now: datetime | None = None,
) -> PurgeResult:
    ts = now or _utcnow_naive()
    cutoff = ts - policy.retention
    # SQLite stores datetimes as ISO strings via SQLAlchemy default; bind as str
    # to keep WHERE comparison lexicographic-correct (ISO sorts == chronological).
    cutoff_str = cutoff.isoformat()
    sm = db_makers[policy.db]
    async with sm() as session, session.begin():
        count_sql = text(f"SELECT COUNT(*) FROM {policy.table} WHERE {policy.ts_column} < :cutoff")
        deleted = int((await session.execute(count_sql, {"cutoff": cutoff_str})).scalar() or 0)
        if not dry_run and deleted:
            await session.execute(
                text(f"DELETE FROM {policy.table} WHERE {policy.ts_column} < :cutoff"),
                {"cutoff": cutoff_str},
            )
        oldest_kept = (
            await session.execute(text(f"SELECT MIN({policy.ts_column}) FROM {policy.table}"))
        ).scalar()
    # Audit (separate transaction; never block purge on audit row failure).
    async with audit_maker() as a, a.begin():
        a.add(
            AuditEvent(
                kind=policy.audit_event,
                target=f"{policy.db}.{policy.table}",
                payload_json=(
                    f'{{"deleted":{deleted},"dry_run":{str(dry_run).lower()},'
                    f'"cutoff_utc":"{cutoff.isoformat()}"}}'
                ),
                created_at_utc=ts,
            )
        )
    # Normalise oldest_kept (SQLite returns str for datetime-cols via raw text()).
    parsed_kept: datetime | None
    if oldest_kept is None:
        parsed_kept = None
    elif isinstance(oldest_kept, datetime):
        parsed_kept = oldest_kept
    else:
        parsed_kept = datetime.fromisoformat(str(oldest_kept))
    return PurgeResult(
        store=f"{policy.db}.{policy.table}",
        deleted=deleted,
        dry_run=dry_run,
        cutoff_utc=cutoff,
        oldest_kept_utc=parsed_kept,
    )


def register_retention_jobs(
    scheduler: AsyncIOScheduler,
    db_makers: dict[str, async_sessionmaker[AsyncSession]],
    audit_maker: async_sessionmaker[AsyncSession],
    *,
    policies: list[RetentionPolicy] | None = None,
    dry_run: bool = False,
) -> list[Any]:
    """Register every RETENTION_POLICY as an idempotent cron job (D-034)."""
    plist = policies if policies is not None else RETENTION_POLICIES
    jobs: list[Any] = []
    for p in plist:
        if p.cron_hour < 0:
            trigger = CronTrigger(minute=p.cron_minute, timezone="UTC")
        else:
            trigger = CronTrigger(hour=p.cron_hour, minute=p.cron_minute, timezone="UTC")
        job = scheduler.add_job(
            run_purge,
            trigger=trigger,
            kwargs={
                "policy": p,
                "db_makers": db_makers,
                "audit_maker": audit_maker,
                "dry_run": dry_run,
            },
            id=f"retention.{p.name}",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=600,
            jitter=30,
        )
        jobs.append(job)
    return jobs


async def purge_staging(staging_dir: Path, *, ttl: timedelta, now: datetime | None = None) -> int:
    """Delete _staging/ files older than ttl (mtime-based)."""
    ts = now or _utcnow_naive()
    cutoff_ts = (ts - ttl).timestamp()
    if not staging_dir.exists():
        return 0
    removed = 0
    for child in staging_dir.iterdir():
        if not child.is_file():
            continue
        try:
            if child.stat().st_mtime < cutoff_ts:
                child.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    return removed


async def purge_trash_sweep(
    trash_dir: Path,
    *,
    ttl: timedelta,
    redactor: PIIRedactor,
    audit_maker: async_sessionmaker[AsyncSession] | None = None,
    now: datetime | None = None,
) -> int:
    """Final tier-1/2 sweep of `<trash_dir>/*` older than ttl, then rmtree.

    Steps per entry:
    1. Find every text-ish file (`.md`, `.txt`, `.json`, `.csv`).
    2. Read → redact → write-back (idempotent overwrite).
    3. rmtree the entry.
    """
    ts = now or _utcnow_naive()
    cutoff_ts = (ts - ttl).timestamp()
    if not trash_dir.exists():
        return 0
    redacted_files = 0
    removed_entries = 0
    text_exts = {".md", ".txt", ".json", ".csv", ".log"}
    for entry in trash_dir.iterdir():
        try:
            mtime = entry.stat().st_mtime
        except FileNotFoundError:
            continue
        if mtime >= cutoff_ts:
            continue
        # final-sweep pass over text content
        if entry.is_dir():
            for f in entry.rglob("*"):
                if f.is_file() and f.suffix.lower() in text_exts:
                    try:
                        body = f.read_text(encoding="utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue
                    redacted = redactor.redact(body)
                    if redacted != body:
                        f.write_text(redacted, encoding="utf-8")
                        redacted_files += 1
            shutil.rmtree(entry, ignore_errors=True)
        else:
            if entry.suffix.lower() in text_exts:
                try:
                    body = entry.read_text(encoding="utf-8")
                    redacted = redactor.redact(body)
                    if redacted != body:
                        entry.write_text(redacted, encoding="utf-8")
                        redacted_files += 1
                except (UnicodeDecodeError, OSError):
                    pass
            entry.unlink(missing_ok=True)
        removed_entries += 1

    if audit_maker is not None:
        async with audit_maker() as a, a.begin():
            a.add(
                AuditEvent(
                    kind="trash_final_sweep",
                    target=str(trash_dir),
                    payload_json=(
                        f'{{"entries":{removed_entries},"redacted_files":{redacted_files}}}'
                    ),
                    created_at_utc=ts,
                )
            )
    return removed_entries


# Silence unused-import lint (kept for future selects).
_ = (select, delete, func)
