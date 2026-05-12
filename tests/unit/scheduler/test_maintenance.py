"""scheduler.maintenance.register_purge_expired_pending_job registers a daily cron."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.inbox.staging import stage_media
from ai_steward_wiki.scheduler.core import build_scheduler
from ai_steward_wiki.scheduler.maintenance import (
    MEDIA_STAGING_SWEEP_JOB_ID,
    PURGE_PENDING_JOB_ID,
    _run_media_sweep,
    register_media_staging_sweep_job,
    register_purge_expired_pending_job,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
async def sessions_maker(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    monkeypatch.setenv("AISW_SESSIONS_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "sessions" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "sessions"))
    command.upgrade(cfg, "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_register_purge_job_adds_daily_0500_utc(tmp_path, sessions_maker) -> None:
    jobs_db = tmp_path / "jobs.db"
    sched = build_scheduler(f"sqlite:///{jobs_db}")
    job = register_purge_expired_pending_job(sched, sessions_maker)
    assert job.id == PURGE_PENDING_JOB_ID
    trig = job.trigger
    fields = {f.name: str(f) for f in trig.fields}
    assert fields["hour"] == "5"
    assert fields["minute"] == "0"
    assert str(trig.timezone) == "UTC"


def test_register_purge_job_idempotent(tmp_path, sessions_maker) -> None:
    jobs_db = tmp_path / "jobs.db"
    sched = build_scheduler(f"sqlite:///{jobs_db}")
    register_purge_expired_pending_job(sched, sessions_maker)
    # Second call with replace_existing=True must not raise.
    register_purge_expired_pending_job(sched, sessions_maker)


def test_register_media_sweep_job_adds_daily_0430_utc(tmp_path) -> None:
    jobs_db = tmp_path / "jobs.db"
    sched = build_scheduler(f"sqlite:///{jobs_db}")
    job = register_media_staging_sweep_job(sched, staging_root=tmp_path / "media-staging")
    assert job.id == MEDIA_STAGING_SWEEP_JOB_ID
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "4"
    assert fields["minute"] == "30"
    assert str(job.trigger.timezone) == "UTC"
    # Idempotent.
    register_media_staging_sweep_job(sched, staging_root=tmp_path / "media-staging")


async def test_run_media_sweep_invokes_sweep_staging(tmp_path) -> None:
    import os
    from datetime import UTC, datetime, timedelta

    staging_root = tmp_path / "media-staging"
    fresh = stage_media(
        b"fresh", ext="ogg", run_id="fresh", inbox_root=staging_root, mime="audio/ogg"
    )
    old = stage_media(b"old", ext="ogg", run_id="old", inbox_root=staging_root, mime="audio/ogg")
    past = (datetime.now(UTC) - timedelta(hours=48)).timestamp()
    os.utime(old.staging_path, (past, past))

    removed = await _run_media_sweep(staging_root, 24 * 60 * 60)
    assert removed == 1
    assert fresh.staging_path.exists()
    assert not old.staging_path.exists()
