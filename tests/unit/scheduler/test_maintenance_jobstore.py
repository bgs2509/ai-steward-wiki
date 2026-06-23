"""Regression guard for aisw-6mi.

Before the fix, ``register_all_retention_jobs`` crashed at boot with
``AttributeError: Can't get local object 'create_engine.<locals>.connect'``
because maintenance/retention/snapshot jobs were inserted into the default
``SQLAlchemyJobStore``, which pickles ``job.args`` (containing
``async_sessionmaker``). The fix routes these jobs into a dedicated
``MemoryJobStore`` ("memory") instead.

The smoke test calls ``scheduler.start()`` BEFORE registration — that's the
only path that exercises the SQLAlchemyJobStore pickle code (``add_job``
buffers in ``_pending_jobs`` until the scheduler is running). Existing
tests don't trigger it because they never start the scheduler.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.scheduler.core import MAINTENANCE_JOBSTORE_ALIAS, build_scheduler
from ai_steward_wiki.scheduler.maintenance import (
    MEDIA_STAGING_SWEEP_JOB_ID,
    PURGE_PENDING_JOB_ID,
    register_all_retention_jobs,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# Each alembic env.py reads its sync URL from this env var first, falling back
# to settings (which defaults to the relative ``data/<db>.db`` path). Setting
# ``sqlalchemy.url`` via Config is NOT enough — env.py clobbers it. Point every
# DB at tmp_path so the test never touches the gitignored ``./data`` dir.
_DB_URL_ENV = {
    "sessions": "AISW_SESSIONS_DB_URL_SYNC",
    "jobs": "AISW_JOBS_DB_URL_SYNC",
    "audit": "AISW_AUDIT_DB_URL_SYNC",
}


def _upgrade(db_path: Path, ini: str, monkeypatch) -> None:
    monkeypatch.setenv(_DB_URL_ENV[ini], f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / ini / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / ini))
    command.upgrade(cfg, "head")


@pytest.fixture
def db_makers(tmp_path, monkeypatch):
    sessions_db = tmp_path / "sessions.db"
    jobs_db = tmp_path / "jobs.db"
    audit_db = tmp_path / "audit.db"
    _upgrade(sessions_db, "sessions", monkeypatch)
    _upgrade(jobs_db, "jobs", monkeypatch)
    _upgrade(audit_db, "audit", monkeypatch)
    sm = async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{sessions_db}"))
    jm = async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{jobs_db}"))
    am = async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{audit_db}"))
    return {"sessions": sm, "jobs": jm, "audit": am, "_paths": (sessions_db, jobs_db, audit_db)}


async def test_register_all_retention_jobs_does_not_pickle_sessionmaker(
    tmp_path, db_makers
) -> None:
    """The bug: SQLAlchemyJobStore.add_job pickles async_sessionmaker → AttributeError.

    The fix: maintenance jobs land in the MemoryJobStore — no pickling.
    """
    _, jobs_db, _ = db_makers["_paths"]
    scheduler = build_scheduler(f"sqlite:///{jobs_db}")
    scheduler.start()  # crucial — without start(), add_job buffers; with start(), it pickles.
    try:
        jobs = register_all_retention_jobs(
            scheduler,
            audit_maker=db_makers["audit"],
            jobs_maker=db_makers["jobs"],
            sessions_maker=db_makers["sessions"],
            dry_run=False,
            snapshot_root=tmp_path / "snapshots",
            db_urls_for_snapshot={
                "jobs": f"sqlite+aiosqlite:///{jobs_db}",
            },
            wiki_root_for_media_sweep=tmp_path / "wiki_root",
        )
        # All maintenance jobs must live in the memory jobstore, not default.
        mem_ids = {j.id for j in scheduler.get_jobs(jobstore=MAINTENANCE_JOBSTORE_ALIAS)}
        default_ids = {j.id for j in scheduler.get_jobs(jobstore="default")}
        assert PURGE_PENDING_JOB_ID in mem_ids
        assert MEDIA_STAGING_SWEEP_JOB_ID in mem_ids
        assert "ops.db_snapshot" in mem_ids
        assert any(jid.startswith("retention.") for jid in mem_ids)
        # Default jobstore stays empty of infra cron.
        assert PURGE_PENDING_JOB_ID not in default_ids
        assert not any(jid.startswith("retention.") for jid in default_ids)
        assert jobs  # non-empty
    finally:
        scheduler.shutdown(wait=False)
