"""M-TG-CALLBACKS unit tests (aisw-163 P4).

CAS state mutation on inline reminder cards + snooze reschedule + cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.storage.jobs.engine import Base
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import ReminderPayload
from ai_steward_wiki.tg.callbacks import (
    CallbackContext,
    on_reminder_card,
    parse_reminder_callback,
    set_callback_context,
)

OWNER = 111
OTHER = 222


@pytest.fixture
async def jobs_session_maker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@dataclass
class _AddedJob:
    func: Any
    trigger: Any
    args: list[Any]
    id: str
    misfire_grace_time: Any
    replace_existing: bool


@dataclass
class _FakeScheduler:
    added: list[_AddedJob] = field(default_factory=list)

    def add_job(
        self,
        func,
        *,
        trigger=None,
        args=None,
        id=None,
        misfire_grace_time=None,
        replace_existing=False,
        **_kw,
    ):
        self.added.append(
            _AddedJob(
                func=func,
                trigger=trigger,
                args=list(args or []),
                id=id or "",
                misfire_grace_time=misfire_grace_time,
                replace_existing=replace_existing,
            )
        )


@dataclass
class _FakeCallback:
    """Just enough of aiogram.types.CallbackQuery for the handler."""

    data: str | None
    from_user_id: int | None = OWNER
    answers: list[str] = field(default_factory=list)

    @property
    def from_user(self):
        if self.from_user_id is None:
            return None

        @dataclass
        class _U:
            id: int

        return _U(id=self.from_user_id)

    async def answer(self, text: str | None = None, **_kw):
        self.answers.append(text or "")


async def _add_reminder(
    maker,
    *,
    owner: int = OWNER,
    when: datetime | None = None,
    state: str = "pending",
    snooze_count: int = 0,
    message: str = "do thing",
) -> int:
    when = when or datetime(2026, 5, 13, 12, 0, 0)
    async with maker() as s:
        j = Job(
            owner_telegram_id=owner,
            chat_id=owner,
            kind="reminder_job",
            status="scheduled",
            priority=2,
            scheduled_at_utc=when,
            payload=ReminderPayload(message=message, category="generic").model_dump(),
            user_state=state,
            snooze_count=snooze_count,
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(j)
        await s.commit()
        return j.id


async def _read_job(maker, job_id: int) -> Job:
    async with maker() as s:
        row = (await s.execute(select(Job).where(Job.id == job_id))).scalar_one()
        return row


# -- parse_reminder_callback ------------------------------------------------


def test_parse_callback_data_valid():
    assert parse_reminder_callback("r:42:done") == (42, "done")
    assert parse_reminder_callback("r:1:snz") == (1, "snz")
    assert parse_reminder_callback("r:99:skp") == (99, "skp")


def test_parse_invalid():
    assert parse_reminder_callback("") is None
    assert parse_reminder_callback("x:42:done") is None  # wrong prefix
    assert parse_reminder_callback("r:42:foo") is None  # foreign action
    assert parse_reminder_callback("r:abc:done") is None  # non-int id
    assert parse_reminder_callback("r:42") is None  # too few parts
    assert parse_reminder_callback("r:42:done:extra") is None  # too many parts
    assert parse_reminder_callback("confirm:42:yes") is None


# -- on_reminder_card -------------------------------------------------------


@pytest.fixture
def fake_ctx(jobs_session_maker):
    scheduler = _FakeScheduler()
    ctx = CallbackContext(scheduler=scheduler, jobs_session_maker=jobs_session_maker)
    set_callback_context(ctx)
    yield ctx
    set_callback_context(None)


async def test_done_marks_state_done_cas(jobs_session_maker, fake_ctx):
    job_id = await _add_reminder(jobs_session_maker)
    cb = _FakeCallback(data=f"r:{job_id}:done")
    await on_reminder_card(cb)  # type: ignore[arg-type]
    assert (await _read_job(jobs_session_maker, job_id)).user_state == "done"
    assert cb.answers, "must ack the callback"

    # Second press → idempotent_noop, no state change, still acks.
    cb2 = _FakeCallback(data=f"r:{job_id}:done")
    await on_reminder_card(cb2)  # type: ignore[arg-type]
    assert (await _read_job(jobs_session_maker, job_id)).user_state == "done"
    assert cb2.answers


async def test_skip_marks_skipped(jobs_session_maker, fake_ctx):
    job_id = await _add_reminder(jobs_session_maker)
    cb = _FakeCallback(data=f"r:{job_id}:skp")
    await on_reminder_card(cb)  # type: ignore[arg-type]
    assert (await _read_job(jobs_session_maker, job_id)).user_state == "skipped"


async def test_snooze_reschedules_30min_and_increments_count(jobs_session_maker, fake_ctx):
    when = datetime(2026, 5, 13, 12, 0, 0)
    job_id = await _add_reminder(jobs_session_maker, when=when)
    cb = _FakeCallback(data=f"r:{job_id}:snz")
    await on_reminder_card(cb)  # type: ignore[arg-type]

    row = await _read_job(jobs_session_maker, job_id)
    assert row.user_state == "pending"  # back in queue
    assert row.snooze_count == 1
    # scheduled_at_utc bumped by ~30min from now (test runs at real `now`, so
    # compare via the rescheduled APScheduler trigger instead — see below).

    # Exactly one new APScheduler job registered, pointing at the same job_id,
    # ~30min in the future.
    assert len(fake_ctx.scheduler.added) == 1
    added = fake_ctx.scheduler.added[0]
    assert added.args == [job_id]
    assert str(job_id) in added.id
    # Trigger.run_date should be ~30min ahead of "now". Pull whichever attr
    # the apscheduler.DateTrigger exposes.
    run_date = getattr(added.trigger, "run_date", None)
    assert run_date is not None
    delta = run_date - datetime.now(UTC)
    assert timedelta(minutes=29) <= delta <= timedelta(minutes=31)


async def test_snooze_cap_3_collapses_to_skip(jobs_session_maker, fake_ctx):
    job_id = await _add_reminder(jobs_session_maker, snooze_count=3)
    cb = _FakeCallback(data=f"r:{job_id}:snz")
    await on_reminder_card(cb)  # type: ignore[arg-type]

    row = await _read_job(jobs_session_maker, job_id)
    assert row.user_state == "skipped"
    assert row.snooze_count == 3  # unchanged
    assert fake_ctx.scheduler.added == []  # no reschedule


async def test_owner_mismatch_silent_ack(jobs_session_maker, fake_ctx):
    job_id = await _add_reminder(jobs_session_maker, owner=OWNER)
    cb = _FakeCallback(data=f"r:{job_id}:done", from_user_id=OTHER)
    await on_reminder_card(cb)  # type: ignore[arg-type]
    # state untouched
    assert (await _read_job(jobs_session_maker, job_id)).user_state == "pending"
    # but the callback was acked (otherwise TG shows a spinner forever)
    assert cb.answers


async def test_bad_data_silent_ack(jobs_session_maker, fake_ctx):
    cb = _FakeCallback(data="r:not-an-int:done")
    await on_reminder_card(cb)  # type: ignore[arg-type]
    assert cb.answers


async def test_unknown_job_silent_ack(jobs_session_maker, fake_ctx):
    cb = _FakeCallback(data="r:99999:done")
    await on_reminder_card(cb)  # type: ignore[arg-type]
    assert cb.answers
