# FILE: tests/unit/scheduler/test_check_in_producer.py
"""RED-first coverage for cron_user.create_check_in_job/fire_check_in_job (aisw-xi8, DEC-8).

Mirrors test_cron_user_producer.py's shape."""

from __future__ import annotations

import pytest
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.scheduler import cron_user
from ai_steward_wiki.scheduler.queue import Lane, PriorityJobQueue
from ai_steward_wiki.scheduler.queue_payloads import CheckInQueueMsg
from ai_steward_wiki.storage.jobs.engine import Base
from ai_steward_wiki.storage.jobs.models import Job


class _FakeScheduler:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def add_job(self, func, trigger, *, args, id, replace_existing):
        self.calls.append(
            {
                "func": func,
                "trigger": trigger,
                "args": args,
                "id": id,
                "replace_existing": replace_existing,
            }
        )


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield sm
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_ctx():
    cron_user._ctx = None
    yield
    cron_user._ctx = None


def _rec() -> Recurrence:
    return Recurrence(kind="daily", time_hhmm="21:00", tz="Europe/Moscow")


async def test_create_check_in_job_inserts_row_before_add_job(session_factory) -> None:
    scheduler = _FakeScheduler()
    queue = PriorityJobQueue()
    cron_user.set_cron_user_context(scheduler, queue, session_factory)

    job_id = await cron_user.create_check_in_job(
        owner_telegram_id=1,
        chat_id=99,
        recurrence=_rec(),
        question_topic="как прошёл день",
        user_tz="Europe/Moscow",
        wiki_id=None,
    )

    assert len(scheduler.calls) == 1
    call = scheduler.calls[0]
    assert call["func"] is cron_user.fire_check_in_job
    assert isinstance(call["trigger"], CronTrigger)
    assert call["id"] == f"check_in:{job_id}"
    assert call["args"] == [job_id]

    async with session_factory() as s:
        row = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert row.kind == "check_in"
    assert row.status == "scheduled"
    assert row.payload["question_topic"] == "как прошёл день"


async def test_fire_check_in_job_pushes_typed_queue_msg_and_marks_queued(session_factory) -> None:
    scheduler = _FakeScheduler()
    queue = PriorityJobQueue()
    cron_user.set_cron_user_context(scheduler, queue, session_factory)
    job_id = await cron_user.create_check_in_job(
        owner_telegram_id=1,
        chat_id=99,
        recurrence=_rec(),
        question_topic="принимала ли лекарства",
        user_tz="Europe/Moscow",
        wiki_id=None,
    )

    await cron_user.fire_check_in_job(job_id)

    item = await queue.get()
    assert item.lane == Lane.CRON_WRITE
    assert isinstance(item.payload, CheckInQueueMsg)
    assert item.payload.job_id == job_id
    assert item.payload.question_topic == "принимала ли лекарства"
    async with session_factory() as s:
        row = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert row.status == "queued"


async def test_fire_check_in_job_vanished_row_is_idempotent_noop(session_factory) -> None:
    scheduler = _FakeScheduler()
    queue = PriorityJobQueue()
    cron_user.set_cron_user_context(scheduler, queue, session_factory)
    await cron_user.fire_check_in_job(999999)  # no such row — must not raise
