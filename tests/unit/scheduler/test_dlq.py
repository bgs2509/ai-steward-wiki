from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.scheduler.dlq import move_to_dlq
from ai_steward_wiki.scheduler.failure import FailureClass
from ai_steward_wiki.storage.jobs.engine import Base
from ai_steward_wiki.storage.jobs.models import Job, JobDLQ


@pytest.fixture
async def session_factory(tmp_path):
    db = tmp_path / "jobs.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seed_job(factory) -> int:
    async with factory() as s:
        job = Job(
            owner_telegram_id=1,
            chat_id=1,
            kind="wiki_job",
            status="failed",
            priority=2,
            payload={"kind": "wiki_run", "wiki_id": "W", "prompt_text": "p", "correlation_id": "c"},
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(job)
        await s.commit()
        return job.id


async def test_move_to_dlq_persists_row(session_factory) -> None:
    job_id = await _seed_job(session_factory)
    async with session_factory() as s:
        await move_to_dlq(
            s,
            job_id=job_id,
            reason="auto_disable_3_strikes",
            error_class=FailureClass.TRANSIENT,
            last_error="timeout",
        )
        await s.commit()
    async with session_factory() as s:
        rows = (await s.execute(JobDLQ.__table__.select())).all()
        assert len(rows) == 1
        row = rows[0]._mapping
        assert row["job_id"] == job_id
        assert row["reason"] == "auto_disable_3_strikes"
        assert row["error_class"] == "transient"
        assert row["last_error"] == "timeout"


async def test_move_to_dlq_accepts_string_class(session_factory) -> None:
    job_id = await _seed_job(session_factory)
    async with session_factory() as s:
        row = await move_to_dlq(
            s,
            job_id=job_id,
            reason="permanent_error",
            error_class="permanent",
            last_error=None,
        )
        await s.commit()
    assert row.error_class == "permanent"
