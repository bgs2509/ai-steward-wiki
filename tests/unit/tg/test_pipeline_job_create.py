# FILE: tests/unit/tg/test_pipeline_job_create.py
"""RED-first coverage for job/create kind=once|digest (regression, byte-identical
downstream) and kind=recurring|check_in (new confirm flows) — aisw-xi8, DEC-11."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.classifier.recurrence import Recurrence, RecurrenceParseResult
from ai_steward_wiki.classifier.schema import Intent, TimeParseResult
from ai_steward_wiki.scheduler import cron_user as cron_user_mod
from ai_steward_wiki.scheduler.queue import PriorityJobQueue
from ai_steward_wiki.storage.jobs.engine import Base as JobsBase
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


@pytest.fixture(autouse=True)
def _reset_cron_user_ctx():
    cron_user_mod._ctx = None
    yield
    cron_user_mod._ctx = None


async def test_job_create_once_reuses_reminder_confirm_draft(
    jobs_session_maker, sessions_session_maker
) -> None:
    """FR-4/FR-7 regression: only the ENTRY point moved from regex to slots —
    downstream (category='reminder', recap format) is byte-identical."""
    sender = FakeSender()
    time_parser = MagicMock()
    time_parser.parse_time = AsyncMock(
        return_value=TimeParseResult(
            # 06:30 UTC == 09:30 Europe/Moscow (UTC+3) — DefaultPipeline's default
            # user_tz — so the localized recap renders "9:30"/"09:30" as asserted.
            when_utc=datetime(2099, 1, 1, 6, 30, tzinfo=UTC),
            source="dateparser",
            escalate=False,
            raw="завтра в 9:30",
            user_tz="Europe/Moscow",
        )
    )
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=make_classifier_result(
            Intent.JOB,
            action="create",
            kind="once",
            time_expr="завтра в 9:30",
            text="отправить отчёт",
        )
    )
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=ConfirmationService(sender, sessions_session_maker),
        classifier=classifier,
        runner=MagicMock(),
        output=MagicMock(),
        jobs_session_maker=jobs_session_maker,
        scheduler=MagicMock(),
        time_parser=time_parser,
    )
    await pipe.on_text(
        telegram_id=1, chat_id=10, update_id=2, text="напомни завтра в 9:30 отправить отчёт"
    )
    assert "9:30" in sender.sends[0]["text"] or "09:30" in sender.sends[0]["text"]
    assert "подтвержда" in sender.sends[0]["text"].lower()


async def test_job_create_once_without_time_parser_never_falls_to_runner() -> None:
    """DEC-2: unlike v1's REMINDER path, job/create/once must NEVER reach the
    generic runner when misconfigured."""
    sender = FakeSender()
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=make_classifier_result(
            Intent.JOB, action="create", kind="once", time_expr="завтра"
        )
    )
    runner = MagicMock()
    runner.run = AsyncMock(side_effect=AssertionError("must never be called"))
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=MagicMock(),
        classifier=classifier,
        runner=runner,
        output=MagicMock(),
        time_parser=None,
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="напомни завтра")
    runner.run.assert_not_awaited()


async def test_job_create_digest_reuses_digest_confirm_draft(
    jobs_session_maker, sessions_session_maker
) -> None:
    sender = FakeSender()
    recurrence_parser = MagicMock(
        return_value=RecurrenceParseResult(
            recurrence=Recurrence(kind="daily", time_hhmm="08:00", tz="Europe/Moscow")
        )
    )
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=make_classifier_result(
            Intent.JOB, action="create", kind="digest", schedule_expr="каждый день в 8"
        )
    )
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=ConfirmationService(sender, sessions_session_maker),
        classifier=classifier,
        runner=MagicMock(),
        output=MagicMock(),
        jobs_session_maker=jobs_session_maker,
        scheduler=MagicMock(),
        recurrence_parser=recurrence_parser,
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="делай сводку каждый день в 8")
    assert "сводк" in sender.sends[0]["text"].lower()


async def test_job_create_recurring_confirm_then_create_recurring_job(
    jobs_session_maker, sessions_session_maker
) -> None:
    sender = FakeSender()
    recurrence_parser = MagicMock(
        return_value=RecurrenceParseResult(
            recurrence=Recurrence(kind="daily", time_hhmm="08:00", tz="Europe/Moscow")
        )
    )
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=make_classifier_result(
            Intent.JOB,
            action="create",
            kind="recurring",
            schedule_expr="каждый день в 8",
            text="принимать таблетки",
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
        recurrence_parser=recurrence_parser,
    )
    await pipe.on_text(
        telegram_id=1,
        chat_id=10,
        update_id=2,
        text="напоминай принимать таблетки каждый день в 8",
    )
    pending_id = sender.last_reply_markup_pending_id()

    await pipe.on_confirm_callback(
        telegram_id=1, chat_id=10, pending_id=pending_id, action="confirm"
    )

    scheduler.add_job.assert_called_once()
    args, kwargs = scheduler.add_job.call_args
    from ai_steward_wiki.scheduler.firing import fire_recurring_job

    assert args[0] is fire_recurring_job
    assert "готово" in sender.sends[-1]["text"].lower()


async def test_job_create_check_in_confirm_then_create_check_in_job(
    jobs_session_maker, sessions_session_maker
) -> None:
    sender = FakeSender()
    recurrence_parser = MagicMock(
        return_value=RecurrenceParseResult(
            recurrence=Recurrence(kind="daily", time_hhmm="21:00", tz="Europe/Moscow")
        )
    )
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=make_classifier_result(
            Intent.JOB,
            action="create",
            kind="check_in",
            schedule_expr="каждый вечер в 21:00",
            text="как прошёл день",
        )
    )
    scheduler = cron_scheduler = MagicMock()
    cron_user_mod.set_cron_user_context(cron_scheduler, PriorityJobQueue(), jobs_session_maker)
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=ConfirmationService(sender, sessions_session_maker),
        classifier=classifier,
        runner=MagicMock(),
        output=MagicMock(),
        jobs_session_maker=jobs_session_maker,
        scheduler=scheduler,
        recurrence_parser=recurrence_parser,
    )
    await pipe.on_text(
        telegram_id=1,
        chat_id=10,
        update_id=2,
        text="спрашивай меня каждый вечер в 21:00, как прошёл день",
    )
    pending_id = sender.last_reply_markup_pending_id()

    await pipe.on_confirm_callback(
        telegram_id=1, chat_id=10, pending_id=pending_id, action="confirm"
    )

    cron_scheduler.add_job.assert_called_once()
    args, kwargs = cron_scheduler.add_job.call_args
    from ai_steward_wiki.scheduler.cron_user import fire_check_in_job

    assert args[0] is fire_check_in_job
    assert "готово" in sender.sends[-1]["text"].lower()


async def test_job_create_check_in_confirm_when_cron_user_unwired_degrades_gracefully(
    jobs_session_maker, sessions_session_maker
) -> None:
    """aisw-xi8 Step-12 review: _execute_job_create_check_in lacks the wiring
    guard its 'recurring' sibling has (_execute_job_create_recurring checks
    self._jobs_session_maker/self._scheduler before calling create_recurring_job).
    create_check_in_job instead reads its context from cron_user's OWN module-
    level registry (set_cron_user_context, installed once at __main__ startup);
    if that was never called (or __main__ wiring changes/breaks), it raises
    CronUserContextNotInitialisedError, which — before this fix — propagated
    unhandled out of on_confirm_callback instead of a graceful user-facing
    error, same as every other runner-unavailable path in this pipeline."""
    sender = FakeSender()
    recurrence_parser = MagicMock(
        return_value=RecurrenceParseResult(
            recurrence=Recurrence(kind="daily", time_hhmm="21:00", tz="Europe/Moscow")
        )
    )
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=make_classifier_result(
            Intent.JOB,
            action="create",
            kind="check_in",
            schedule_expr="каждый вечер в 21:00",
            text="как прошёл день",
        )
    )
    scheduler = MagicMock()
    # aisw-xi8 (Step-12 review fix) deliberately NOT calling
    # cron_user_mod.set_cron_user_context(...) here — this simulates the
    # unwired-context scenario the fix must degrade gracefully from.
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=ConfirmationService(sender, sessions_session_maker),
        classifier=classifier,
        runner=MagicMock(),
        output=MagicMock(),
        jobs_session_maker=jobs_session_maker,
        scheduler=scheduler,
        recurrence_parser=recurrence_parser,
    )
    await pipe.on_text(
        telegram_id=1,
        chat_id=10,
        update_id=2,
        text="спрашивай меня каждый вечер в 21:00, как прошёл день",
    )
    pending_id = sender.last_reply_markup_pending_id()

    await pipe.on_confirm_callback(
        telegram_id=1, chat_id=10, pending_id=pending_id, action="confirm"
    )  # must not raise

    assert (
        "занял" in sender.sends[-1]["text"].lower() or "позже" in sender.sends[-1]["text"].lower()
    )
