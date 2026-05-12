from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.scheduler import firing
from ai_steward_wiki.scheduler.firing import (
    FiringNotInitialisedError,
    create_reminder_job,
    fire_job,
    set_firing_context,
)
from ai_steward_wiki.scheduler.queue import Lane
from ai_steward_wiki.storage.jobs.engine import Base
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import ReminderPayload, parse_job_payload

WHEN = datetime(2026, 5, 13, 3, 0, tzinfo=UTC)


@pytest.fixture
async def session_factory(tmp_path: Any):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_firing_ctx():
    firing._ctx = None
    yield
    firing._ctx = None


class _FakeScheduler:
    def __init__(self, session_factory) -> None:
        self.calls: list[dict[str, Any]] = []
        self._sf = session_factory

    def add_job(self, func, *, trigger, args, id, misfire_grace_time, **kw) -> None:
        self.calls.append(
            {
                "func": func,
                "trigger": trigger,
                "args": args,
                "id": id,
                "misfire": misfire_grace_time,
            }
        )


class _FakeSender:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[int, str]] = []
        self._fail = fail

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> object:
        if self._fail:
            raise RuntimeError("chat blocked")
        self.sent.append((chat_id, text))
        return object()


async def _insert_job(factory, *, status: str = "pending", message: str = "позвонить врачу") -> int:
    async with factory() as s:
        job = Job(
            owner_telegram_id=42,
            chat_id=42,
            kind="reminder_job",
            status=status,
            priority=int(Lane.USER_WRITE),
            scheduled_at_utc=WHEN.replace(tzinfo=None),
            payload=ReminderPayload(message=message).model_dump(mode="json"),
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(job)
        await s.commit()
        return job.id


# --- create_reminder_job ---------------------------------------------------


async def test_create_reminder_job_writes_row_and_schedules(session_factory) -> None:
    sched = _FakeScheduler(session_factory)
    async with session_factory() as s:
        job_id = await create_reminder_job(
            s,
            sched,
            owner_telegram_id=42,
            chat_id=42,
            when_utc=WHEN,
            message="позвонить врачу",
            lead_time_min=0,
        )
    assert isinstance(job_id, int)
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row is not None
        assert row.kind == "reminder_job"
        assert row.status == "pending"
        assert row.priority == int(Lane.USER_WRITE)
        assert row.scheduled_at_utc == WHEN.replace(tzinfo=None)
        assert row.created_at_utc is not None
        payload = parse_job_payload(row.payload)
        assert isinstance(payload, ReminderPayload)
        assert payload.message == "позвонить врачу"
    assert len(sched.calls) == 1
    call = sched.calls[0]
    assert call["func"] is fire_job
    assert call["args"] == [job_id]
    assert call["id"] == f"reminder:{job_id}"
    assert call["misfire"] is None
    # trigger is a DateTrigger at WHEN
    assert getattr(call["trigger"], "run_date", None) is not None
    assert call["trigger"].run_date.astimezone(UTC) == WHEN


async def test_create_commits_row_before_add_job(session_factory, tmp_path: Any) -> None:
    # The Job row must be committed BEFORE scheduler.add_job runs: the fake
    # scheduler opens a fresh sqlite3 connection and asserts the row is visible.
    import sqlite3

    db_path = str(tmp_path / "jobs.db")
    seen: list[int] = []

    class _Probe:
        def add_job(self, func, **kw: Any) -> None:
            with sqlite3.connect(db_path) as conn:
                seen.append(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])

    async with session_factory() as s:
        await create_reminder_job(
            s, _Probe(), owner_telegram_id=1, chat_id=1, when_utc=WHEN, message="x"
        )
    assert seen == [1]  # row was already committed when add_job fired


# --- fire_job --------------------------------------------------------------


async def test_fire_job_delivers_and_marks_done(session_factory) -> None:
    sender = _FakeSender()
    set_firing_context(sender=sender, jobs_session_maker=session_factory)
    job_id = await _insert_job(session_factory)
    await fire_job(job_id)
    assert sender.sent == [(42, "\U0001f514 Напоминание: позвонить врачу")]
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row is not None
        assert row.status == "done"
        assert row.started_at_utc is not None
        assert row.finished_at_utc is not None


async def test_fire_job_skips_non_pending(session_factory) -> None:
    sender = _FakeSender()
    set_firing_context(sender=sender, jobs_session_maker=session_factory)
    job_id = await _insert_job(session_factory, status="cancelled")
    await fire_job(job_id)
    assert sender.sent == []
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row is not None
        assert row.status == "cancelled"


async def test_fire_job_missing_row_is_noop(session_factory) -> None:
    sender = _FakeSender()
    set_firing_context(sender=sender, jobs_session_maker=session_factory)
    await fire_job(999_999)
    assert sender.sent == []


async def test_fire_job_send_failure_marks_failed(session_factory) -> None:
    sender = _FakeSender(fail=True)
    set_firing_context(sender=sender, jobs_session_maker=session_factory)
    job_id = await _insert_job(session_factory)
    await fire_job(job_id)  # must not raise
    async with session_factory() as s:
        row = await s.get(Job, job_id)
        assert row is not None
        assert row.status == "failed"
        assert row.last_error is not None
        assert "chat blocked" in row.last_error or "RuntimeError" in row.last_error


async def test_fire_job_without_context_raises(session_factory) -> None:
    job_id = await _insert_job(session_factory)
    with pytest.raises(FiringNotInitialisedError):
        await fire_job(job_id)
