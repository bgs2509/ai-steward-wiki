# FILE: src/ai_steward_wiki/ops/snapshot.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Daily db_snapshot maintenance job — VACUUM INTO state/snapshots/<UTC>/
#            for jobs.db / audit.db / sessions.db with 7d rolling retention.
#   SCOPE: snapshot_databases, purge_old_snapshots, register_db_snapshot_job,
#          DB_SNAPSHOT_JOB_ID, SnapshotResult.
#   DEPENDS: apscheduler, sqlite3 stdlib, structlog
#   LINKS: M-OPS-BACKUP, tech-spec §10.2, D-037, INV-2 (db_snapshot kind), INV-3
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   DB_SNAPSHOT_JOB_ID - stable APScheduler job id
#   SNAPSHOT_RETENTION_DAYS - rolling 7d retention constant
#   SNAPSHOT_DIR_MODE - chmod applied to snapshot root + per-day dir (0700)
#   SnapshotResult - pydantic v2 frozen model with per-store outcomes
#   extract_sqlite_path - parse a SQLAlchemy SQLite URL into a Path
#   snapshot_databases - run VACUUM INTO for each configured DB
#   purge_old_snapshots - rolling 7d retention sweep
#   register_db_snapshot_job - register cron 03:00 UTC daily
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 14: M-OPS-BACKUP initial implementation
# END_CHANGE_SUMMARY

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict

__all__ = [
    "DB_SNAPSHOT_JOB_ID",
    "SNAPSHOT_DIR_MODE",
    "SNAPSHOT_RETENTION_DAYS",
    "SnapshotResult",
    "extract_sqlite_path",
    "purge_old_snapshots",
    "register_db_snapshot_job",
    "snapshot_databases",
]

DB_SNAPSHOT_JOB_ID = "ops.db_snapshot"
SNAPSHOT_RETENTION_DAYS = 7
SNAPSHOT_DIR_MODE = 0o700
_DATE_FMT = "%Y-%m-%d"

_log = structlog.get_logger(__name__)


class SnapshotResult(BaseModel):
    """Outcome of one db_snapshot run."""

    model_config = ConfigDict(frozen=True)

    snapshot_dir: Path
    stores: dict[str, Path]
    purged_dirs: list[Path]
    taken_at_utc: datetime


def extract_sqlite_path(url: str) -> Path:
    """Pull the file path out of a SQLAlchemy SQLite URL.

    Accepts ``sqlite+aiosqlite:///path``, ``sqlite:///path`` and absolute or
    relative paths. Boundary parser only — no DSN parsing library to avoid
    pulling SQLAlchemy in for a 3-line operation.
    """
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            return Path(url[len(prefix) :])
    return Path(url)


def _vacuum_into(src_path: Path, dst_path: Path) -> None:
    # `VACUUM INTO` is the canonical SQLite hot-backup (no WAL stop, snapshot
    # is point-in-time consistent). sqlite3 stdlib is sufficient — we do not
    # want SQLAlchemy connection pooling for a one-shot maintenance op.
    conn = sqlite3.connect(str(src_path))
    try:
        conn.execute("VACUUM INTO ?", (str(dst_path),))
        conn.commit()
    finally:
        conn.close()


def snapshot_databases(
    snapshot_root: Path,
    db_urls: dict[str, str],
    *,
    retention_days: int = SNAPSHOT_RETENTION_DAYS,
    now_utc: datetime | None = None,
) -> SnapshotResult:
    """Take a consistent VACUUM INTO snapshot of each configured DB.

    ``db_urls`` keys are store names (``jobs``, ``audit``, ``sessions``).
    Caller passes Settings-resolved URLs; we extract the file path locally.
    Stale snapshot dirs older than ``retention_days`` are purged after a
    successful run.
    """
    now = now_utc or datetime.now(UTC)
    day_dir = snapshot_root / now.strftime(_DATE_FMT)
    snapshot_root.mkdir(parents=True, exist_ok=True)
    snapshot_root.chmod(SNAPSHOT_DIR_MODE)
    day_dir.mkdir(parents=True, exist_ok=True)
    day_dir.chmod(SNAPSHOT_DIR_MODE)

    stores: dict[str, Path] = {}
    for name, url in db_urls.items():
        src = extract_sqlite_path(url)
        if not src.exists():
            # No DB yet → first-boot before any migration ran. Skip silently;
            # next run picks it up.
            _log.info(
                "[ops.snapshot][snapshot_databases][SKIP_MISSING] src missing",
                store=name,
                src=str(src),
            )
            continue
        dst = day_dir / f"{name}.db"
        if dst.exists():
            # Re-run on same UTC day → overwrite.
            dst.unlink()
        _vacuum_into(src, dst)
        dst.chmod(0o600)
        stores[name] = dst

    purged = purge_old_snapshots(snapshot_root, retention_days=retention_days, now_utc=now)
    _log.info(
        "[ops.snapshot][snapshot_databases][DONE] db_snapshot ok",
        snapshot_dir=str(day_dir),
        stores=list(stores.keys()),
        purged=len(purged),
    )
    return SnapshotResult(
        snapshot_dir=day_dir,
        stores=stores,
        purged_dirs=purged,
        taken_at_utc=now,
    )


def purge_old_snapshots(
    snapshot_root: Path,
    *,
    retention_days: int = SNAPSHOT_RETENTION_DAYS,
    now_utc: datetime | None = None,
) -> list[Path]:
    """Delete snapshot day-dirs whose UTC date is older than retention_days.

    Dirs whose name does not parse as ``YYYY-MM-DD`` are left alone (operators
    may stash manual artefacts beside the rolling snapshots — we do not own
    those paths).
    """
    if not snapshot_root.exists():
        return []
    now = now_utc or datetime.now(UTC)
    cutoff = (now - timedelta(days=retention_days)).date()
    purged: list[Path] = []
    for child in sorted(snapshot_root.iterdir()):
        if not child.is_dir():
            continue
        try:
            day = datetime.strptime(child.name, _DATE_FMT).date()
        except ValueError:
            continue
        if day < cutoff:
            shutil.rmtree(child)
            purged.append(child)
    return purged


async def _run_db_snapshot(
    snapshot_root: Path, db_urls: dict[str, str], retention_days: int
) -> None:
    snapshot_databases(snapshot_root, db_urls, retention_days=retention_days)


def register_db_snapshot_job(
    scheduler: AsyncIOScheduler,
    *,
    snapshot_root: Path,
    db_urls: dict[str, str],
    retention_days: int = SNAPSHOT_RETENTION_DAYS,
    hour: int = 3,
    minute: int = 0,
) -> Any:
    """Idempotently register the daily db_snapshot cron at hour:minute UTC."""
    return scheduler.add_job(
        _run_db_snapshot,
        trigger=CronTrigger(hour=hour, minute=minute, timezone="UTC"),
        id=DB_SNAPSHOT_JOB_ID,
        replace_existing=True,
        args=[snapshot_root, db_urls, retention_days],
    )
