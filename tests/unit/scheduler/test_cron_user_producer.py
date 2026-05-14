"""M-SCHEDULER-CRON-USER producer tests (aisw-02v).

Mirrors test_firing.py shape: in-memory jobs.db, fake scheduler, fake queue,
module-level _ctx reset fixture. Picklable int callback for SQLAlchemyJobStore.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.scheduler import cron_user as cron_user_mod
from ai_steward_wiki.scheduler.cron_user import (
    CronUserContextNotInitialisedError,
    create_cron_user_job,
    fire_cron_user_job,
    set_cron_user_context,
)
from ai_steward_wiki.scheduler.queue import Lane
from ai_steward_wiki.scheduler.queue_payloads import CronUserQueueMsg
from ai_steward_wiki.storage.jobs.engine import Base
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import CronUserPayload


@pytest.fixture
async def session_factory(tmp_path: Any):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_cron_user_ctx():
    cron_user_mod._ctx = None
    yield
    cron_user_mod._ctx = None


class _FakeScheduler:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def add_job(
        self,
        func,
        trigger,
        *,
        args,
        id,
        replace_existing,
        **kw,
    ) -> None:
        self.calls.append(
            {
                "func": func,
                "trigger": trigger,
                "args": args,
                "id": id,
                "replace_existing": replace_existing,
            }
        )


class _FakeQueue:
    def __init__(self, *, fail: bool = False) -> None:
        self.puts: list[tuple[Lane, Any]] = []
        self._fail = fail

    async def put(self, lane: Lane, payload: Any) -> None:
        if self._fail:
            raise RuntimeError("queue closed")
        self.puts.append((lane, payload))


def _daily_9_utc() -> Recurrence:
    return Recurrence(kind="daily", time_hhmm="09:00", tz="UTC")


# --- create_cron_user_job --------------------------------------------------


async def test_create_cron_user_job_persists_and_schedules(session_factory) -> None:
    sched = _FakeScheduler()
    queue = _FakeQueue()
    set_cron_user_context(sched, queue, session_factory)  # type: ignore[arg-type]

    rec = _daily_9_utc()
    job_id = await create_cron_user_job(
        owner_telegram_id=100,
        chat_id=100,
        recurrence=rec,
        command="напомни выпить витамины",
        user_tz="UTC",
        wiki_id=None,
    )

    assert isinstance(job_id, int)
    assert job_id > 0
    # Row persisted.
    async with session_factory() as s:
        row = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert row.kind == "cron_user"
    assert row.status == "scheduled"
    assert row.priority == int(Lane.CRON_WRITE)
    assert row.owner_telegram_id == 100
    assert row.chat_id == 100
    payload = CronUserPayload(**row.payload)
    assert payload.command == "напомни выпить витамины"
    assert payload.recurrence == rec
    assert payload.wiki_id is None
    # scheduler.add_job called correctly.
    assert len(sched.calls) == 1
    call = sched.calls[0]
    assert call["func"] is fire_cron_user_job
    assert isinstance(call["trigger"], CronTrigger)
    assert call["args"] == [job_id]
    assert call["id"] == f"cron_user:{job_id}"
    assert call["replace_existing"] is True


async def test_create_cron_user_job_accepts_wiki_id(session_factory) -> None:
    sched = _FakeScheduler()
    queue = _FakeQueue()
    set_cron_user_context(sched, queue, session_factory)  # type: ignore[arg-type]
    job_id = await create_cron_user_job(
        owner_telegram_id=1,
        chat_id=1,
        recurrence=_daily_9_utc(),
        command="run",
        user_tz="UTC",
        wiki_id="Health",
    )
    async with session_factory() as s:
        row = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert CronUserPayload(**row.payload).wiki_id == "Health"


async def test_create_cron_user_job_raises_without_context() -> None:
    with pytest.raises(CronUserContextNotInitialisedError):
        await create_cron_user_job(
            owner_telegram_id=1,
            chat_id=1,
            recurrence=_daily_9_utc(),
            command="x",
            user_tz="UTC",
            wiki_id=None,
        )


# --- fire_cron_user_job ----------------------------------------------------


async def _insert_cron_user_job(factory, *, status: str = "scheduled", command: str = "hi") -> int:
    async with factory() as s:
        payload = CronUserPayload(
            recurrence=_daily_9_utc(),
            command=command,
            wiki_id=None,
        )
        job = Job(
            owner_telegram_id=200,
            chat_id=300,
            kind="cron_user",
            status=status,
            priority=int(Lane.CRON_WRITE),
            payload=payload.model_dump(mode="json"),
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(job)
        await s.commit()
        return job.id


async def test_fire_cron_user_job_pushes_msg_and_updates_status(session_factory) -> None:
    sched = _FakeScheduler()
    queue = _FakeQueue()
    set_cron_user_context(sched, queue, session_factory)  # type: ignore[arg-type]
    job_id = await _insert_cron_user_job(session_factory, command="напомни")

    await fire_cron_user_job(job_id)

    # Queue got a typed CronUserQueueMsg under CRON_WRITE lane.
    assert len(queue.puts) == 1
    lane, msg = queue.puts[0]
    assert lane == Lane.CRON_WRITE
    assert isinstance(msg, CronUserQueueMsg)
    assert msg.job_id == job_id
    assert msg.owner_telegram_id == 200
    assert msg.chat_id == 300
    assert msg.command == "напомни"
    assert msg.correlation_id
    assert len(msg.correlation_id) >= 8
    assert msg.scheduled_at_utc.tzinfo is not None
    # Status mutated to 'queued'.
    async with session_factory() as s:
        row = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert row.status == "queued"


async def test_fire_cron_user_job_idempotent_on_missing_row(session_factory) -> None:
    sched = _FakeScheduler()
    queue = _FakeQueue()
    set_cron_user_context(sched, queue, session_factory)  # type: ignore[arg-type]
    await fire_cron_user_job(99999)  # never inserted
    assert queue.puts == []


async def test_fire_cron_user_job_idempotent_on_wrong_status(session_factory) -> None:
    sched = _FakeScheduler()
    queue = _FakeQueue()
    set_cron_user_context(sched, queue, session_factory)  # type: ignore[arg-type]
    job_id = await _insert_cron_user_job(session_factory, status="queued")
    await fire_cron_user_job(job_id)
    assert queue.puts == []
    # Status untouched.
    async with session_factory() as s:
        row = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert row.status == "queued"


async def test_fire_cron_user_job_raises_without_context() -> None:
    with pytest.raises(CronUserContextNotInitialisedError):
        await fire_cron_user_job(1)


async def test_fire_cron_user_job_propagates_queue_put_failure(session_factory) -> None:
    sched = _FakeScheduler()
    queue = _FakeQueue(fail=True)
    set_cron_user_context(sched, queue, session_factory)  # type: ignore[arg-type]
    job_id = await _insert_cron_user_job(session_factory)
    with pytest.raises(RuntimeError, match="queue closed"):
        await fire_cron_user_job(job_id)
    # Status rolled back to 'scheduled' (transaction rollback).
    async with session_factory() as s:
        row = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert row.status == "scheduled"


def test_set_cron_user_context_idempotent_overwrite() -> None:
    sched1 = _FakeScheduler()
    sched2 = _FakeScheduler()
    queue = _FakeQueue()

    class _Maker:
        pass

    set_cron_user_context(sched1, queue, _Maker())  # type: ignore[arg-type]
    set_cron_user_context(sched2, queue, _Maker())  # type: ignore[arg-type]
    assert cron_user_mod._ctx is not None
    assert cron_user_mod._ctx[0] is sched2
