"""scheduler.maintenance.register_purge_expired_pending_job registers a daily cron."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.scheduler.core import build_scheduler
from ai_steward_wiki.scheduler.maintenance import (
    PURGE_PENDING_JOB_ID,
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
