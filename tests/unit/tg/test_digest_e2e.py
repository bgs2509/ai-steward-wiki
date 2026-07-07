"""End-to-end digest flow as a slow unit (aisw-oqq): real ConfirmationService +
real jobs DB + fake scheduler + fake digest runner — on_text → confirm callback →
jobs.Job(kind='digest_job') + CronTrigger → fire_digest_job → delivery."""

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

from ai_steward_wiki.classifier.recurrence import Recurrence, RecurrenceParseResult
from ai_steward_wiki.classifier.schema import Intent
from ai_steward_wiki.scheduler import firing
from ai_steward_wiki.scheduler.firing import fire_digest_job, set_digest_context
from ai_steward_wiki.storage.jobs.engine import Base as JobsBase
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import DigestPayload, parse_job_payload
from ai_steward_wiki.storage.sessions.models import PendingConfirm
from ai_steward_wiki.tg.confirm import ConfirmationService
from ai_steward_wiki.tg.pipeline import DefaultPipeline
from tests.helpers.classifier_factory import make_classifier_result
from tests.unit.tg.conftest import FakeSender

REPO_ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 5, 12, 18, 0, tzinfo=UTC)


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


@pytest.fixture
async def audit_maker(tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "audit.db"
    monkeypatch.setenv("AISW_AUDIT_DB_URL_SYNC", f"sqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic" / "audit" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic" / "audit"))
    command.upgrade(cfg, "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_ctx():
    firing._digest_ctx = None
    yield
    firing._digest_ctx = None


class _FakeScheduler:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def add_job(self, func, *, trigger=None, args=None, id=None, replace_existing=False, **kw):
        self.calls.append(
            {
                "func": func,
                "args": args,
                "id": id,
                "trigger": trigger,
                "replace_existing": replace_existing,
            }
        )

    def remove_job(self, job_id: str) -> None: ...


def _daily() -> Recurrence:
    return Recurrence(kind="daily", time_hhmm="09:00", tz="Europe/Moscow")


class _RP:
    def __call__(self, text, *, user_tz, correlation_id=""):
        return RecurrenceParseResult(
            recurrence=Recurrence(kind="daily", time_hhmm="09:00", tz=user_tz)
        )


class _DigestRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, *, wiki_id, wiki_path, extra_add_dirs, planner_context, correlation_id
    ):
        self.calls.append({"wiki_id": wiki_id, "extra_add_dirs": extra_add_dirs})
        return "TL;DR: всё спокойно.\n📅 Сегодня: —"


def _classifier() -> MagicMock:
    # DEC-14: v1 Intent.REMINDER (digest sub-detection via regex) -> v2
    # Intent.JOB, action="create", kind="digest".
    c = MagicMock()
    c.classify = AsyncMock(
        return_value=make_classifier_result(
            Intent.JOB, action="create", kind="digest", confidence=0.95
        )
    )
    return c


def _idem() -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=True)
    idem.check_content = AsyncMock(return_value=("b" * 64, None))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


async def test_digest_end_to_end(sessions_maker, jobs_maker, audit_maker, tmp_path: Any) -> None:
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
        time_parser=MagicMock(),  # gate only
        recurrence_parser=_RP(),
        jobs_session_maker=jobs_maker,
        scheduler=sched,
        clock=lambda: NOW,
    )

    # 1) NL message → a digest pending_confirms row, no message yet
    await pipe.on_text(
        telegram_id=42, chat_id=42, update_id=1, text="присылай сводку каждый день в 9"
    )
    async with sessions_maker() as s:
        rows = (await s.execute(select(PendingConfirm))).scalars().all()
    assert len(rows) == 1
    pending = rows[0]
    assert pending.category == "digest"
    assert bot.sends
    assert "Подтверждаешь" in bot.sends[-1]["text"]
    sends_before = len(bot.sends)

    # 2) confirm → jobs.Job(kind='digest_job') + CronTrigger + ack
    await pipe.on_confirm_callback(
        telegram_id=42, chat_id=42, pending_id=pending.id, action="confirm"
    )
    async with jobs_maker() as s:
        jobs = (await s.execute(select(Job))).scalars().all()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.kind == "digest_job"
    assert job.status == "scheduled"
    payload = parse_job_payload(job.payload)
    assert isinstance(payload, DigestPayload)
    assert payload.recurrence == _daily()
    assert payload.wiki_scope == "all"
    assert len(sched.calls) == 1
    assert sched.calls[0]["id"] == f"digest:{job.id}"
    assert sched.calls[0]["replace_existing"] is True
    assert len(bot.sends) == sends_before + 1
    assert bot.sends[-1]["text"].startswith("Готово")

    # 3) APScheduler fires → digest runner + delivery + row stays 'scheduled'
    deliver_bot = FakeSender()
    runner = _DigestRunner()
    health_dir = tmp_path / "Health-WIKI"
    finance_dir = tmp_path / "Finance-WIKI"
    health_dir.mkdir()
    finance_dir.mkdir()

    async def _resolve(owner_id: int) -> list[tuple[str, Path]]:
        return [("Health-WIKI", health_dir), ("Finance-WIKI", finance_dir)]

    set_digest_context(
        scheduler=sched,
        runner=runner,
        resolve_owner_wikis=_resolve,
        jobs_session_maker=jobs_maker,
        audit_session_maker=audit_maker,
        sender=deliver_bot,
        sessions_session_maker=sessions_maker,
    )
    await fire_digest_job(job.id)
    assert len(deliver_bot.sends) == 1
    assert "TL;DR" in deliver_bot.sends[-1]["text"]
    assert runner.calls[0]["wiki_id"] == "Health-WIKI"
    assert runner.calls[0]["extra_add_dirs"] == [finance_dir]
    assert (health_dir / "data" / "runs").is_dir()
    async with jobs_maker() as s:
        row = await s.get(Job, job.id)
        assert row is not None
        assert row.status == "scheduled"
        assert row.retry_count == 0
        assert row.finished_at_utc is not None
