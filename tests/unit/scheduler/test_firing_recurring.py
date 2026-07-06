# FILE: tests/unit/scheduler/test_firing_recurring.py
"""RED-first coverage for firing.create_recurring_job/fire_recurring_job (aisw-xi8, DEC-7)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.scheduler import firing
from ai_steward_wiki.storage.jobs.engine import Base
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import parse_job_payload


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield sm
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_firing_ctx():
    firing._ctx = None
    yield
    firing._ctx = None


def _rec() -> Recurrence:
    return Recurrence(kind="daily", time_hhmm="08:00", tz="Europe/Moscow")


async def test_create_recurring_job_inserts_row_and_registers_cron_trigger(session_factory) -> None:
    scheduler = MagicMock()
    async with session_factory() as s, s.begin():
        job_id = await firing.create_recurring_job(
            s,
            scheduler,
            owner_telegram_id=42,
            chat_id=99,
            message="Принять таблетки",
            recurrence=_rec(),
            category="medication",
        )
    scheduler.add_job.assert_called_once()
    args, kwargs = scheduler.add_job.call_args
    assert args[0] is firing.fire_recurring_job
    assert isinstance(kwargs["trigger"], CronTrigger)
    assert kwargs["id"] == f"recurring:{job_id}"
    assert kwargs["args"] == [job_id]
    async with session_factory() as s:
        row = await s.get(Job, job_id)
    assert row.kind == "recurring_reminder"
    assert row.status == "scheduled"
    payload = parse_job_payload(row.payload)
    assert payload.message == "Принять таблетки"  # type: ignore[union-attr]
    assert payload.category == "medication"  # type: ignore[union-attr]


async def test_fire_recurring_job_sends_message_verbatim_no_llm(session_factory) -> None:
    sender = MagicMock()
    sender.send_message = AsyncMock(return_value=None)
    firing.set_firing_context(sender=sender, jobs_session_maker=session_factory)
    async with session_factory() as s, s.begin():
        row = Job(
            owner_telegram_id=1,
            chat_id=55,
            kind="recurring_reminder",
            status="scheduled",
            priority=2,
            scheduled_at_utc=None,
            payload={
                "kind": "recurring_reminder",
                "message": "Прими таблетки 💊 от давления!",
                "recurrence": _rec().model_dump(mode="json"),
                "category": "medication",
            },
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(row)
        await s.flush()
        job_id = row.id

    await firing.fire_recurring_job(job_id)

    sender.send_message.assert_awaited_once()
    args, kwargs = sender.send_message.call_args
    assert args[0] == 55
    assert "Прими таблетки 💊 от давления!" in args[1]  # byte-identical text, no LLM
    async with session_factory() as s:
        row_after = await s.get(Job, job_id)
    assert row_after.status == "scheduled"  # NOT a terminal transition — stays enabled


async def test_fire_recurring_job_two_failures_then_success_does_not_disable(
    session_factory,
) -> None:
    sender = MagicMock()
    sender.send_message = AsyncMock(side_effect=[RuntimeError("boom"), RuntimeError("boom"), None])
    firing.set_firing_context(sender=sender, jobs_session_maker=session_factory)
    async with session_factory() as s, s.begin():
        row = Job(
            owner_telegram_id=1,
            chat_id=55,
            kind="recurring_reminder",
            status="scheduled",
            priority=2,
            scheduled_at_utc=None,
            payload={
                "kind": "recurring_reminder",
                "message": "x",
                "recurrence": _rec().model_dump(mode="json"),
            },
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(row)
        await s.flush()
        job_id = row.id

    for _ in range(3):
        await firing.fire_recurring_job(job_id)

    async with session_factory() as s:
        row_after = await s.get(Job, job_id)
    assert row_after.status == "scheduled"  # a success reset the streak


async def test_fire_recurring_job_three_consecutive_failures_disables_and_dlqs(
    session_factory,
) -> None:
    scheduler = MagicMock()
    sender = MagicMock()
    sender.send_message = AsyncMock(side_effect=RuntimeError("blocked by user"))
    firing.set_firing_context(sender=sender, jobs_session_maker=session_factory)
    firing.set_recurring_scheduler(scheduler)
    async with session_factory() as s, s.begin():
        row = Job(
            owner_telegram_id=1,
            chat_id=55,
            kind="recurring_reminder",
            status="scheduled",
            priority=2,
            scheduled_at_utc=None,
            payload={
                "kind": "recurring_reminder",
                "message": "x",
                "recurrence": _rec().model_dump(mode="json"),
            },
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(row)
        await s.flush()
        job_id = row.id

    for _ in range(3):
        await firing.fire_recurring_job(job_id)

    scheduler.remove_job.assert_called_once_with(f"recurring:{job_id}")
    async with session_factory() as s:
        row_after = await s.get(Job, job_id)
    assert row_after.status == "disabled"
