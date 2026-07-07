# FILE: tests/unit/scheduler/test_manage.py
"""RED-first coverage for the NEW scheduler/manage.py job-management surface (aisw-xi8, DEC-9)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from apscheduler.jobstores.base import JobLookupError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.scheduler.manage import (
    _job_key,
    cancel_job,
    list_owner_jobs,
    match_jobs_by_needle,
    reschedule_once,
    reschedule_recurring,
)
from ai_steward_wiki.storage.jobs.engine import Base
from ai_steward_wiki.storage.jobs.models import Job


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield sm
    await engine.dispose()


def _rec(hhmm: str = "09:00") -> Recurrence:
    return Recurrence(kind="daily", time_hhmm=hhmm, tz="Europe/Moscow")


async def _insert(
    sm, *, owner: int, kind: str, status: str, payload: dict, scheduled_at_utc=None
) -> int:
    async with sm() as s, s.begin():
        row = Job(
            owner_telegram_id=owner,
            chat_id=owner,
            kind=kind,
            status=status,
            priority=2,
            scheduled_at_utc=scheduled_at_utc,
            payload=payload,
            created_at_utc=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(row)
        await s.flush()
        return row.id


# --- _job_key ---------------------------------------------------------------


def test_job_key_matches_existing_literals() -> None:
    """SSoT check: must match the id= strings already hardcoded at firing.py
    (reminder:/recurring:/digest:) and cron_user.py (cron_user:/check_in:)."""
    assert _job_key("reminder_job", 5) == "reminder:5"
    assert _job_key("recurring_reminder", 5) == "recurring:5"
    assert _job_key("check_in", 5) == "check_in:5"
    assert _job_key("digest_job", 5) == "digest:5"
    assert _job_key("cron_user", 5) == "cron_user:5"


# --- list_owner_jobs ----------------------------------------------------------


async def test_list_owner_jobs_returns_only_enabled_user_facing_kinds(session_factory) -> None:
    owner = 111
    await _insert(
        session_factory,
        owner=owner,
        kind="reminder_job",
        status="pending",
        payload={
            "kind": "reminder_job",
            "message": "напомнить",
            "lead_time_min": 0,
            "category": "generic",
        },
        scheduled_at_utc=datetime(2026, 8, 1, 6, 0),
    )
    await _insert(
        session_factory,
        owner=owner,
        kind="digest_job",
        status="scheduled",
        payload={
            "kind": "digest",
            "wiki_scope": "all",
            "recurrence": _rec().model_dump(mode="json"),
            "window_hours": 24,
        },
    )
    # excluded kinds even though same owner:
    await _insert(
        session_factory,
        owner=owner,
        kind="purge",
        status="scheduled",
        payload={"kind": "purge", "target": "x", "older_than_hours": 1},
    )
    await _insert(
        session_factory,
        owner=owner,
        kind="wiki_run",
        status="scheduled",
        payload={"kind": "wiki_run", "wiki_id": "1", "prompt_text": "x", "correlation_id": "c"},
    )
    # a finished (not enabled) reminder must be excluded:
    await _insert(
        session_factory,
        owner=owner,
        kind="reminder_job",
        status="done",
        payload={
            "kind": "reminder_job",
            "message": "старое",
            "lead_time_min": 0,
            "category": "generic",
        },
    )
    async with session_factory() as s:
        jobs = await list_owner_jobs(s, owner)
    assert {j.kind for j in jobs} == {"reminder_job", "digest_job"}


async def test_list_owner_jobs_owner_isolation(session_factory) -> None:
    await _insert(
        session_factory,
        owner=1,
        kind="reminder_job",
        status="pending",
        payload={"kind": "reminder_job", "message": "a", "lead_time_min": 0, "category": "generic"},
        scheduled_at_utc=datetime(2026, 8, 1, 6, 0),
    )
    await _insert(
        session_factory,
        owner=2,
        kind="reminder_job",
        status="pending",
        payload={"kind": "reminder_job", "message": "b", "lead_time_min": 0, "category": "generic"},
        scheduled_at_utc=datetime(2026, 8, 1, 6, 0),
    )
    async with session_factory() as s:
        jobs = await list_owner_jobs(s, 1)
    assert len(jobs) == 1
    assert jobs[0].payload.message == "a"  # type: ignore[union-attr]


async def test_list_owner_jobs_renders_recurring_reminder_verbatim(session_factory) -> None:
    await _insert(
        session_factory,
        owner=7,
        kind="recurring_reminder",
        status="scheduled",
        payload={
            "kind": "recurring_reminder",
            "message": "Принять таблетки",
            "recurrence": _rec("08:00").model_dump(mode="json"),
            "category": "medication",
        },
    )
    async with session_factory() as s:
        jobs = await list_owner_jobs(s, 7)
    assert len(jobs) == 1
    assert "Принять таблетки" in jobs[0].rendered
    assert "08:00" in jobs[0].rendered


# --- match_jobs_by_needle -----------------------------------------------------


async def test_match_jobs_by_needle_single_clear_winner(session_factory) -> None:
    a = await _insert(
        session_factory,
        owner=1,
        kind="recurring_reminder",
        status="scheduled",
        payload={
            "kind": "recurring_reminder",
            "message": "Принять таблетки от давления",
            "recurrence": _rec().model_dump(mode="json"),
        },
    )
    await _insert(
        session_factory,
        owner=1,
        kind="digest_job",
        status="scheduled",
        payload={
            "kind": "digest",
            "wiki_scope": "all",
            "recurrence": _rec().model_dump(mode="json"),
            "window_hours": 24,
        },
    )
    async with session_factory() as s:
        jobs = await list_owner_jobs(s, 1)
    matches = match_jobs_by_needle(jobs, "про таблетки")
    assert len(matches) == 1
    assert matches[0].id == a


def test_match_jobs_by_needle_empty_needle_matches_nothing() -> None:
    from ai_steward_wiki.scheduler.manage import OwnerJob
    from ai_steward_wiki.storage.jobs.payloads import RecurringReminderPayload

    job = OwnerJob(
        id=1,
        kind="recurring_reminder",
        payload=RecurringReminderPayload(message="Принять таблетки", recurrence=_rec()),
        scheduled_at_utc=None,
        rendered="каждый день в 09:00 — Принять таблетки",
    )
    assert match_jobs_by_needle([job], "") == []
    assert match_jobs_by_needle([job], "   ") == []


def test_match_jobs_by_needle_no_match_returns_empty() -> None:
    from ai_steward_wiki.scheduler.manage import OwnerJob
    from ai_steward_wiki.storage.jobs.payloads import RecurringReminderPayload

    job = OwnerJob(
        id=1,
        kind="recurring_reminder",
        payload=RecurringReminderPayload(message="Принять таблетки", recurrence=_rec()),
        scheduled_at_utc=None,
        rendered="каждый день в 09:00 — Принять таблетки",
    )
    assert match_jobs_by_needle([job], "покормить хомяка") == []


def test_match_jobs_by_needle_tie_returns_all_ranked() -> None:
    from ai_steward_wiki.scheduler.manage import OwnerJob
    from ai_steward_wiki.storage.jobs.payloads import RecurringReminderPayload

    j1 = OwnerJob(
        id=1,
        kind="recurring_reminder",
        payload=RecurringReminderPayload(message="Принять таблетки утром", recurrence=_rec()),
        scheduled_at_utc=None,
        rendered="каждый день в 09:00 — Принять таблетки утром",
    )
    j2 = OwnerJob(
        id=2,
        kind="recurring_reminder",
        payload=RecurringReminderPayload(message="Принять таблетки вечером", recurrence=_rec()),
        scheduled_at_utc=None,
        rendered="каждый день в 21:00 — Принять таблетки вечером",
    )
    matches = match_jobs_by_needle([j1, j2], "принять таблетки")
    assert {m.id for m in matches} == {1, 2}


# --- cancel_job / reschedule_once / reschedule_recurring ----------------------


async def test_cancel_job_removes_trigger_and_marks_cancelled(session_factory) -> None:
    job_id = await _insert(
        session_factory,
        owner=1,
        kind="recurring_reminder",
        status="scheduled",
        payload={
            "kind": "recurring_reminder",
            "message": "x",
            "recurrence": _rec().model_dump(mode="json"),
        },
    )
    scheduler = MagicMock()
    async with session_factory() as s:
        jobs = await list_owner_jobs(s, 1)
        await cancel_job(scheduler, s, jobs[0])
    scheduler.remove_job.assert_called_once_with(f"recurring:{job_id}")
    async with session_factory() as s:
        jobs_after = await list_owner_jobs(s, 1)
    assert jobs_after == []  # no longer 'scheduled'


async def test_cancel_job_tolerates_missing_apscheduler_entry(session_factory) -> None:
    """Race: the APScheduler entry is already gone. remove_job's JobLookupError
    must be swallowed — the DB row is still marked cancelled (idempotent)."""
    job_id = await _insert(
        session_factory,
        owner=1,
        kind="digest_job",
        status="scheduled",
        payload={
            "kind": "digest",
            "wiki_scope": "all",
            "recurrence": _rec().model_dump(mode="json"),
            "window_hours": 24,
        },
    )
    scheduler = MagicMock()
    scheduler.remove_job.side_effect = JobLookupError(f"digest:{job_id}")
    async with session_factory() as s:
        jobs = await list_owner_jobs(s, 1)
        await cancel_job(scheduler, s, jobs[0])  # must not raise
    async with session_factory() as s:
        assert await list_owner_jobs(s, 1) == []


async def test_reschedule_once_moves_date_trigger(session_factory) -> None:
    job_id = await _insert(
        session_factory,
        owner=1,
        kind="reminder_job",
        status="pending",
        payload={"kind": "reminder_job", "message": "x", "lead_time_min": 0, "category": "generic"},
        scheduled_at_utc=datetime(2026, 8, 1, 6, 0),
    )
    scheduler = MagicMock()
    new_when = datetime(2026, 8, 2, 7, 30, tzinfo=UTC)
    async with session_factory() as s:
        jobs = await list_owner_jobs(s, 1)
        await reschedule_once(scheduler, s, jobs[0], new_when)
    scheduler.reschedule_job.assert_called_once()
    args, kwargs = scheduler.reschedule_job.call_args
    assert args[0] == f"reminder:{job_id}"
    async with session_factory() as s:
        jobs_after = await list_owner_jobs(s, 1)
    assert jobs_after[0].scheduled_at_utc == new_when.replace(tzinfo=None)


async def test_list_owner_jobs_finds_a_real_create_digest_job_row(session_factory) -> None:
    """Regression guard for the digest_job kind-literal mismatch (aisw-xi8 Step-12
    review): seed the row via the REAL firing.create_digest_job (the actual
    production writer), not a hand-rolled kind= guess — so a future drift between
    the DB Job.kind literal firing.py persists ('digest_job') and the one
    manage.py's _USER_FACING_KINDS/_JOB_KEY_PREFIX expect fails loudly here
    instead of silently no-oping list/cancel/reschedule forever."""
    from ai_steward_wiki.scheduler.firing import create_digest_job

    scheduler = MagicMock()
    async with session_factory() as s:
        job_id = await create_digest_job(
            s, scheduler, owner_telegram_id=1, chat_id=10, recurrence=_rec()
        )
    scheduler.add_job.assert_called_once()
    assert scheduler.add_job.call_args.kwargs["id"] == f"digest:{job_id}"

    async with session_factory() as s:
        jobs = await list_owner_jobs(s, 1)
    assert len(jobs) == 1
    assert jobs[0].id == job_id
    assert jobs[0].kind == "digest_job"

    # cancel_job must remove the SAME apscheduler id create_digest_job registered.
    async with session_factory() as s:
        jobs2 = await list_owner_jobs(s, 1)
        await cancel_job(scheduler, s, jobs2[0])
    scheduler.remove_job.assert_called_once_with(f"digest:{job_id}")


async def test_reschedule_recurring_rewrites_payload_recurrence(session_factory) -> None:
    """Closes the measured #35/#91/#99 digest-control defect cluster (resolved Q3)."""
    job_id = await _insert(
        session_factory,
        owner=1,
        kind="digest_job",
        status="scheduled",
        payload={
            "kind": "digest",
            "wiki_scope": "all",
            "recurrence": _rec("08:00").model_dump(mode="json"),
            "window_hours": 24,
        },
    )
    scheduler = MagicMock()
    new_rec = _rec("08:30")
    async with session_factory() as s:
        jobs = await list_owner_jobs(s, 1)
        await reschedule_recurring(scheduler, s, jobs[0], new_rec)
    scheduler.reschedule_job.assert_called_once()
    args, kwargs = scheduler.reschedule_job.call_args
    assert args[0] == f"digest:{job_id}"
    async with session_factory() as s:
        jobs_after = await list_owner_jobs(s, 1)
    assert jobs_after[0].payload.recurrence.time_hhmm == "08:30"  # type: ignore[union-attr]
