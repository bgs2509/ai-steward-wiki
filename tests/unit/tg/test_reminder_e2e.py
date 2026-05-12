"""End-to-end reminder flow as a slow unit (aisw-kcz): real ConfirmationService +
real jobs DB + fake scheduler — on_text → confirm callback → jobs.Job → fire_job."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.classifier.schema import ClassifierResult, Intent, TimeParseResult
from ai_steward_wiki.scheduler import firing
from ai_steward_wiki.scheduler.firing import fire_job, set_firing_context
from ai_steward_wiki.storage.jobs.engine import Base as JobsBase
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import ReminderPayload, parse_job_payload
from ai_steward_wiki.storage.sessions.models import PendingConfirm
from ai_steward_wiki.tg.confirm import ConfirmationService
from ai_steward_wiki.tg.pipeline import DefaultPipeline
from tests.unit.tg.conftest import FakeSender

REPO_ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 5, 12, 18, 0, tzinfo=UTC)
FUTURE = datetime(2026, 5, 13, 6, 0, tzinfo=UTC)


@pytest.fixture
async def sessions_maker(tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "sessions.db"
    monkeypatch.setenv("AISW_SESSIONS_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "sessions" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "sessions"))
    command.upgrade(cfg, "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture
async def jobs_maker(tmp_path: Any):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(JobsBase.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_ctx():
    firing._ctx = None
    yield
    firing._ctx = None


class _FakeScheduler:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def add_job(self, func, *, trigger=None, args=None, id=None, misfire_grace_time=None, **kw):
        self.calls.append({"func": func, "args": args, "id": id, "misfire": misfire_grace_time})


class _TP:
    async def parse_time(self, text, *, user_tz, now_utc, prefer_future=False, correlation_id=""):
        return TimeParseResult(
            when_utc=FUTURE, source="dateparser", escalate=False, raw=text, user_tz=str(user_tz)
        )


def _classifier() -> MagicMock:
    c = MagicMock()
    c.classify = AsyncMock(
        return_value=ClassifierResult(
            intent=Intent.REMINDER,
            confidence=0.95,
            distilled_payload={"reminder_text": "позвонить врачу"},
            backend="fake",
            model="m",
            prompt_semver="1.0.0",
            prompt_sha256="a" * 64,
            latency_ms=1,
        )
    )
    return c


def _idem() -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=True)
    idem.check_content = AsyncMock(return_value=("b" * 64, None))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


async def test_reminder_end_to_end(sessions_maker, jobs_maker) -> None:
    bot = FakeSender()
    sched = _FakeScheduler()
    confirmation = ConfirmationService(bot, sessions_maker)
    pipe = DefaultPipeline(
        sender=bot,
        idempotency=_idem(),
        confirmation=confirmation,
        classifier=_classifier(),
        runner=MagicMock(),
        output=MagicMock(),
        time_parser=_TP(),
        jobs_session_maker=jobs_maker,
        scheduler=sched,
        clock=lambda: NOW,
    )

    # 1) NL message → a reminder pending_confirms row, no message yet
    await pipe.on_text(
        telegram_id=42, chat_id=42, update_id=1, text="напомни завтра в 6 позвонить врачу"
    )
    async with sessions_maker() as s:
        rows = (await s.execute(select(PendingConfirm))).scalars().all()
    assert len(rows) == 1
    pending = rows[0]
    assert pending.category == "reminder"
    assert bot.sends
    assert "Подтверждаешь" in bot.sends[-1]["text"]
    sends_before = len(bot.sends)

    # 2) confirm → jobs.Job + DateTrigger + ack
    await pipe.on_confirm_callback(
        telegram_id=42, chat_id=42, pending_id=pending.id, action="confirm"
    )
    async with jobs_maker() as s:
        jobs = (await s.execute(select(Job))).scalars().all()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.kind == "reminder_job"
    assert job.status == "pending"
    assert job.scheduled_at_utc == FUTURE.replace(tzinfo=None)
    payload = parse_job_payload(job.payload)
    assert isinstance(payload, ReminderPayload)
    assert payload.message == "позвонить врачу"
    assert len(sched.calls) == 1
    assert sched.calls[0]["id"] == f"reminder:{job.id}"
    assert sched.calls[0]["misfire"] is None
    assert len(bot.sends) == sends_before + 1
    assert bot.sends[-1]["text"].startswith("Готово")

    # 3) APScheduler fires → plain TG message + row done
    deliver_bot = FakeSender()
    set_firing_context(sender=deliver_bot, jobs_session_maker=jobs_maker)
    await fire_job(job.id)
    assert deliver_bot.sends == [
        {
            "chat_id": 42,
            "text": "\U0001f514 Напоминание: позвонить врачу",
            "parse_mode": "HTML",
            "reply_markup": None,
            "message_id": 1001,
        }
    ]
    async with jobs_maker() as s:
        row = await s.get(Job, job.id)
        assert row is not None
        assert row.status == "done"
        assert row.started_at_utc is not None
        assert row.finished_at_utc is not None
