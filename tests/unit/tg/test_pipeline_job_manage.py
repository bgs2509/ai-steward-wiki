# FILE: tests/unit/tg/test_pipeline_job_manage.py
"""RED-first coverage for job/list|cancel|reschedule + needle disambiguation +
destructive confirm (aisw-xi8, DEC-9/DEC-10, Phase-C.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.classifier.recurrence import Recurrence
from ai_steward_wiki.classifier.schema import Intent
from ai_steward_wiki.storage.jobs.engine import Base as JobsBase
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.sessions.engine import Base as SessionsBase
from ai_steward_wiki.tg.confirm import ConfirmationService
from ai_steward_wiki.tg.pipeline import DefaultPipeline
from tests.helpers.classifier_factory import make_classifier_result
from tests.unit.tg.conftest import FakeSender


def _make_idem() -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=True)
    idem.check_content = AsyncMock(return_value=("b" * 64, None))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


@pytest.fixture
async def jobs_session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(JobsBase.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield sm
    await engine.dispose()


@pytest.fixture
async def sessions_session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SessionsBase.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield sm
    await engine.dispose()


def _rec(hhmm: str = "09:00") -> Recurrence:
    return Recurrence(kind="daily", time_hhmm=hhmm, tz="Europe/Moscow")


async def _insert(
    sm, *, kind: str, status: str, payload: dict, owner: int = 1, scheduled_at_utc=None
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


def _pipe(sender, jobs_sm, sessions_sm, *, intent: Intent, action: str, **slots) -> DefaultPipeline:
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=make_classifier_result(intent, action=action, confidence=0.95, **slots)
    )
    scheduler = MagicMock()
    return DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=ConfirmationService(sender, sessions_sm),
        classifier=classifier,
        runner=MagicMock(),
        output=MagicMock(),
        jobs_session_maker=jobs_sm,
        scheduler=scheduler,
    ), scheduler


async def test_job_list_renders_owner_jobs(jobs_session_maker, sessions_session_maker) -> None:
    await _insert(
        jobs_session_maker,
        kind="recurring_reminder",
        status="scheduled",
        payload={
            "kind": "recurring_reminder",
            "message": "Принять таблетки",
            "recurrence": _rec().model_dump(mode="json"),
        },
    )
    sender = FakeSender()
    pipe, _ = _pipe(
        sender, jobs_session_maker, sessions_session_maker, intent=Intent.JOB, action="list"
    )

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="какие у меня напоминания")

    assert "Принять таблетки" in sender.sends[0]["text"]


async def test_job_list_empty(jobs_session_maker, sessions_session_maker) -> None:
    sender = FakeSender()
    pipe, _ = _pipe(
        sender, jobs_session_maker, sessions_session_maker, intent=Intent.JOB, action="list"
    )

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="какие у меня напоминания")

    assert "нет" in sender.sends[0]["text"].lower()


async def test_job_cancel_single_match_builds_destructive_confirm(
    jobs_session_maker, sessions_session_maker
) -> None:
    await _insert(
        jobs_session_maker,
        kind="recurring_reminder",
        status="scheduled",
        payload={
            "kind": "recurring_reminder",
            "message": "Принять таблетки от давления",
            "recurrence": _rec().model_dump(mode="json"),
        },
    )
    sender = FakeSender()
    pipe, scheduler = _pipe(
        sender,
        jobs_session_maker,
        sessions_session_maker,
        intent=Intent.JOB,
        action="cancel",
        needle="про таблетки",
    )

    await pipe.on_text(
        telegram_id=1, chat_id=10, update_id=2, text="убери напоминание про таблетки"
    )

    assert "отменить" in sender.sends[0]["text"].lower()
    scheduler.remove_job.assert_not_called()  # not mutated yet — confirm pending


async def test_job_cancel_zero_matches_says_not_found(
    jobs_session_maker, sessions_session_maker
) -> None:
    await _insert(
        jobs_session_maker,
        kind="recurring_reminder",
        status="scheduled",
        payload={
            "kind": "recurring_reminder",
            "message": "Принять таблетки",
            "recurrence": _rec().model_dump(mode="json"),
        },
    )
    sender = FakeSender()
    pipe, _ = _pipe(
        sender,
        jobs_session_maker,
        sessions_session_maker,
        intent=Intent.JOB,
        action="cancel",
        needle="покормить хомяка",
    )

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="отмени покормить хомяка")

    assert "не наш" in sender.sends[0]["text"].lower()


async def test_job_cancel_multiple_matches_builds_job_pick(
    jobs_session_maker, sessions_session_maker
) -> None:
    await _insert(
        jobs_session_maker,
        kind="recurring_reminder",
        status="scheduled",
        payload={
            "kind": "recurring_reminder",
            "message": "Принять таблетки утром",
            "recurrence": _rec("08:00").model_dump(mode="json"),
        },
    )
    await _insert(
        jobs_session_maker,
        kind="recurring_reminder",
        status="scheduled",
        payload={
            "kind": "recurring_reminder",
            "message": "Принять таблетки вечером",
            "recurrence": _rec("21:00").model_dump(mode="json"),
        },
    )
    sender = FakeSender()
    pipe, _ = _pipe(
        sender,
        jobs_session_maker,
        sessions_session_maker,
        intent=Intent.JOB,
        action="cancel",
        needle="принять таблетки",
    )

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="убери принять таблетки")

    assert sender.sends[0]["reply_markup"] is not None
    assert len(sender.sends[0]["reply_markup"].inline_keyboard) == 2


async def test_job_cancel_confirm_flow_cancels_via_manage(
    jobs_session_maker, sessions_session_maker
) -> None:
    job_id = await _insert(
        jobs_session_maker,
        kind="recurring_reminder",
        status="scheduled",
        payload={
            "kind": "recurring_reminder",
            "message": "Принять таблетки",
            "recurrence": _rec().model_dump(mode="json"),
        },
    )
    sender = FakeSender()
    pipe, scheduler = _pipe(
        sender,
        jobs_session_maker,
        sessions_session_maker,
        intent=Intent.JOB,
        action="cancel",
        needle="таблетки",
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="убери таблетки")
    pending_id = sender.last_reply_markup_pending_id()  # test helper — see conftest note below

    await pipe.on_confirm_callback(
        telegram_id=1, chat_id=10, pending_id=pending_id, action="confirm"
    )

    scheduler.remove_job.assert_called_once_with(f"recurring:{job_id}")
    assert "отменил" in sender.sends[-1]["text"].lower()


async def test_job_cancel_confirm_cancel_action_does_not_mutate(
    jobs_session_maker, sessions_session_maker
) -> None:
    await _insert(
        jobs_session_maker,
        kind="recurring_reminder",
        status="scheduled",
        payload={
            "kind": "recurring_reminder",
            "message": "Принять таблетки",
            "recurrence": _rec().model_dump(mode="json"),
        },
    )
    sender = FakeSender()
    pipe, scheduler = _pipe(
        sender,
        jobs_session_maker,
        sessions_session_maker,
        intent=Intent.JOB,
        action="cancel",
        needle="таблетки",
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="убери таблетки")
    pending_id = sender.last_reply_markup_pending_id()

    await pipe.on_confirm_callback(
        telegram_id=1, chat_id=10, pending_id=pending_id, action="cancel"
    )

    scheduler.remove_job.assert_not_called()
    assert (
        "не буду" in sender.sends[-1]["text"].lower()
        or "хорошо" in sender.sends[-1]["text"].lower()
    )


async def test_job_reschedule_once_shaped(jobs_session_maker, sessions_session_maker) -> None:
    job_id = await _insert(
        jobs_session_maker,
        kind="reminder_job",
        status="pending",
        payload={
            "kind": "reminder_job",
            "message": "забрать костюм",
            "lead_time_min": 0,
            "category": "generic",
        },
        scheduled_at_utc=datetime(2026, 8, 1, 6, 0),
    )
    sender = FakeSender()
    time_parser = MagicMock()
    from ai_steward_wiki.classifier.schema import TimeParseResult

    time_parser.parse_time = AsyncMock(
        return_value=TimeParseResult(
            when_utc=datetime(2026, 8, 2, 7, 0, tzinfo=UTC),
            source="dateparser",
            escalate=False,
            raw="завтра в 10",
            user_tz="Europe/Moscow",
        )
    )
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=make_classifier_result(
            Intent.JOB, action="reschedule", needle="костюм", time_expr="завтра в 10"
        )
    )
    scheduler = MagicMock()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=ConfirmationService(sender, sessions_session_maker),
        classifier=classifier,
        runner=MagicMock(),
        output=MagicMock(),
        jobs_session_maker=jobs_session_maker,
        scheduler=scheduler,
        time_parser=time_parser,
    )
    await pipe.on_text(
        telegram_id=1, chat_id=10, update_id=2, text="перенеси костюм на завтра в 10"
    )
    pending_id = sender.last_reply_markup_pending_id()

    await pipe.on_confirm_callback(
        telegram_id=1, chat_id=10, pending_id=pending_id, action="confirm"
    )

    scheduler.reschedule_job.assert_called_once()
    assert scheduler.reschedule_job.call_args[0][0] == f"reminder:{job_id}"


async def test_job_reschedule_recurring_shaped_closes_digest_defect(
    jobs_session_maker, sessions_session_maker
) -> None:
    """Closes the measured #35/#91/#99 digest-control defect cluster."""
    job_id = await _insert(
        jobs_session_maker,
        kind="digest_job",
        status="scheduled",
        payload={
            "kind": "digest",
            "wiki_scope": "all",
            "recurrence": _rec("08:00").model_dump(mode="json"),
            "window_hours": 24,
        },
    )
    sender = FakeSender()
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=make_classifier_result(
            # aisw-xi8 (Phase-C.2 execution deviation, low-risk/mechanical): the
            # plan's fixture used needle="сводку" (accusative), which does not
            # casefold-token-overlap with the rendered "сводка …" (nominative) —
            # match_jobs_by_needle (Phase-B, DEC-9) is exact whole-token overlap
            # BY DESIGN (no Russian morphological stemming). Using the nominative
            # form here preserves the test's single-match-reschedule intent
            # without touching Phase-B's already-verified matching contract.
            Intent.JOB,
            action="reschedule",
            needle="сводка",
            schedule_expr="на 8:30",
        )
    )
    scheduler = MagicMock()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=ConfirmationService(sender, sessions_session_maker),
        classifier=classifier,
        runner=MagicMock(),
        output=MagicMock(),
        jobs_session_maker=jobs_session_maker,
        scheduler=scheduler,
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="перенеси сводку на 8:30")
    pending_id = sender.last_reply_markup_pending_id()

    await pipe.on_confirm_callback(
        telegram_id=1, chat_id=10, pending_id=pending_id, action="confirm"
    )

    scheduler.reschedule_job.assert_called_once()
    assert scheduler.reschedule_job.call_args[0][0] == f"digest:{job_id}"


async def test_jobpick_callback_executes_selected_job(
    jobs_session_maker, sessions_session_maker
) -> None:
    await _insert(
        jobs_session_maker,
        kind="recurring_reminder",
        status="scheduled",
        payload={
            "kind": "recurring_reminder",
            "message": "Принять таблетки утром",
            "recurrence": _rec("08:00").model_dump(mode="json"),
        },
    )
    id_b = await _insert(
        jobs_session_maker,
        kind="recurring_reminder",
        status="scheduled",
        payload={
            "kind": "recurring_reminder",
            "message": "Принять таблетки вечером",
            "recurrence": _rec("21:00").model_dump(mode="json"),
        },
    )
    sender = FakeSender()
    pipe, scheduler = _pipe(
        sender,
        jobs_session_maker,
        sessions_session_maker,
        intent=Intent.JOB,
        action="cancel",
        needle="принять таблетки",
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="убери принять таблетки")
    pending_id = sender.last_reply_markup_pending_id()

    await pipe.on_jobpick_callback(telegram_id=1, chat_id=10, pending_id=pending_id, job_index=1)

    scheduler.remove_job.assert_called_once_with(f"recurring:{id_b}")
