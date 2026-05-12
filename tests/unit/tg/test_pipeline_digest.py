"""Unit tests for the Phase-D.b.1 digest fast-path in DefaultPipeline (aisw-oqq)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.classifier.recurrence import Recurrence, RecurrenceParseResult
from ai_steward_wiki.classifier.schema import ClassifierResult, Intent
from ai_steward_wiki.storage.jobs.engine import Base
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import DigestPayload, parse_job_payload
from ai_steward_wiki.tg.confirm import build_route_confirm_keyboard
from ai_steward_wiki.tg.pipeline import (
    DIGEST_ACK_RU,
    DIGEST_CONFIRM_CANCELLED_RU,
    DIGEST_CONFIRM_STALE_RU,
    DIGEST_UNPARSEABLE_RU,
    REMINDER_RECURRING_RU,
    DefaultPipeline,
)
from tests.unit.tg.conftest import FakeSender

NOW = datetime(2026, 5, 12, 18, 0, tzinfo=UTC)


def _classifier() -> MagicMock:
    cls = MagicMock()
    cls.classify = AsyncMock(
        return_value=ClassifierResult(
            intent=Intent.REMINDER,
            confidence=0.95,
            distilled_payload={},
            backend="fake",
            model="m",
            prompt_semver="1.0.0",
            prompt_sha256="a" * 64,
            latency_ms=1,
        )
    )
    return cls


def _idem() -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=True)
    idem.check_content = AsyncMock(return_value=("b" * 64, None))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


def _runner() -> MagicMock:
    from ai_steward_wiki.tg.pipeline import WikiRunOutcome

    r = MagicMock()
    r.run = AsyncMock(return_value=WikiRunOutcome(run_id="run-x", text="legacy", latency_ms=1))
    return r


def _output() -> MagicMock:
    out = MagicMock()
    out.deliver = AsyncMock(return_value=None)
    return out


class _FakeRecurrenceParser:
    def __init__(self, result: RecurrenceParseResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, text: str, *, user_tz: str, correlation_id: str = ""
    ) -> RecurrenceParseResult:
        self.calls.append({"text": text, "user_tz": user_tz})
        return self.result


class _FakeScheduler:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def add_job(self, func, *, trigger=None, args=None, id=None, replace_existing=False, **kw):
        self.calls.append(
            {"func": func, "args": args, "id": id, "replace_existing": replace_existing}
        )


@pytest.fixture
async def jobs_maker(tmp_path: Any):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _confirm_request() -> MagicMock:
    c = MagicMock()
    rec = MagicMock()
    rec.pending_id = 9
    c.request_explicit = AsyncMock(return_value=rec)
    return c


def _pipe(
    *,
    sender: FakeSender,
    confirmation: MagicMock,
    recurrence_parser: Any = None,
    jobs_session_maker: Any = None,
    scheduler: Any = None,
) -> DefaultPipeline:
    return DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=confirmation,
        classifier=_classifier(),
        runner=_runner(),
        output=_output(),
        time_parser=MagicMock(),  # gate only; the recurring branch returns before parse_time
        recurrence_parser=recurrence_parser,
        jobs_session_maker=jobs_session_maker,
        scheduler=scheduler,
        clock=lambda: NOW,
    )


def _daily() -> Recurrence:
    return Recurrence(kind="daily", time_hhmm="09:00", tz="Europe/Moscow")


# --- detection -------------------------------------------------------------


async def test_recurring_phrasing_requests_digest_confirm() -> None:
    sender = FakeSender()
    confirm = _confirm_request()
    parser = _FakeRecurrenceParser(RecurrenceParseResult(recurrence=_daily()))
    pipe = _pipe(sender=sender, confirmation=confirm, recurrence_parser=parser)
    await pipe.on_text(
        telegram_id=42, chat_id=42, update_id=1, text="присылай сводку каждый день в 9"
    )

    confirm.request_explicit.assert_awaited_once()
    draft_obj = confirm.request_explicit.call_args.args[0]
    assert draft_obj.category == "digest"
    assert draft_obj.draft["recurrence"] == _daily().model_dump(mode="json")
    assert draft_obj.draft["wiki_scope"] == "all"
    assert draft_obj.draft["window_hours"] == 24
    assert "каждый день в 09:00" in draft_obj.recap_text
    assert (
        confirm.request_explicit.call_args.kwargs["keyboard_factory"]
        is build_route_confirm_keyboard
    )
    assert sender.sends == []  # nothing until confirm


async def test_unparseable_recurrence_clarifies() -> None:
    sender = FakeSender()
    confirm = _confirm_request()
    parser = _FakeRecurrenceParser(RecurrenceParseResult(escalate=True, reason="no_time"))
    pipe = _pipe(sender=sender, confirmation=confirm, recurrence_parser=parser)
    await pipe.on_text(telegram_id=42, chat_id=42, update_id=1, text="присылай сводку каждый день")
    assert sender.sends[-1]["text"] == DIGEST_UNPARSEABLE_RU
    confirm.request_explicit.assert_not_awaited()


async def test_no_recurrence_parser_falls_back_to_not_yet() -> None:
    sender = FakeSender()
    confirm = _confirm_request()
    pipe = _pipe(sender=sender, confirmation=confirm, recurrence_parser=None)
    await pipe.on_text(
        telegram_id=42, chat_id=42, update_id=1, text="присылай сводку каждый день в 9"
    )
    assert sender.sends[-1]["text"] == REMINDER_RECURRING_RU
    confirm.request_explicit.assert_not_awaited()


# --- confirm callback ------------------------------------------------------


class _FakeConfirmStore:
    """Minimal ConfirmationService stand-in for the confirm-callback path."""

    def __init__(self, *, category: str, draft: dict[str, Any], resolve_status: str | None) -> None:
        self._pending = MagicMock()
        self._pending.category = category
        self._pending.draft_json = json.dumps(draft, ensure_ascii=False)
        self._resolve_status = resolve_status
        self.resolve_calls: list[tuple[int, int, str]] = []

    async def get_pending(self, pending_id: int):
        return self._pending

    async def resolve(self, telegram_id: int, pending_id: int, action: str):
        self.resolve_calls.append((telegram_id, pending_id, action))
        return self._resolve_status

    # unused by these tests but referenced by __init__ type
    async def request_explicit(self, *a, **k): ...
    async def auto_ack(self, *a, **k): ...
    async def implicit_ack(self, *a, **k): ...


def _draft_dict() -> dict[str, Any]:
    return {
        "recurrence": _daily().model_dump(mode="json"),
        "wiki_scope": "all",
        "window_hours": 24,
        "user_tz": "Europe/Moscow",
        "correlation_id": "cid",
    }


async def test_confirm_creates_digest_job(jobs_maker) -> None:
    sender = FakeSender()
    sched = _FakeScheduler()
    store = _FakeConfirmStore(category="digest", draft=_draft_dict(), resolve_status="confirmed")
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=store,  # type: ignore[arg-type]
        classifier=_classifier(),
        runner=_runner(),
        output=_output(),
        jobs_session_maker=jobs_maker,
        scheduler=sched,
    )
    await pipe.on_confirm_callback(telegram_id=42, chat_id=42, pending_id=9, action="confirm")
    # one digest job row + one CronTrigger registration
    async with jobs_maker() as s:
        from sqlalchemy import select

        rows = (await s.execute(select(Job).where(Job.kind == "digest_job"))).scalars().all()
        assert len(rows) == 1
        parsed = parse_job_payload(rows[0].payload)
        assert isinstance(parsed, DigestPayload)
        assert parsed.recurrence == _daily()
    assert len(sched.calls) == 1
    assert sched.calls[0]["id"] == f"digest:{rows[0].id}"
    assert sched.calls[0]["replace_existing"] is True
    assert sender.sends[-1]["text"] == DIGEST_ACK_RU.format(schedule_human="каждый день в 09:00")


async def test_confirm_cancel_creates_no_job(jobs_maker) -> None:
    sender = FakeSender()
    sched = _FakeScheduler()
    store = _FakeConfirmStore(category="digest", draft=_draft_dict(), resolve_status="cancelled")
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=store,  # type: ignore[arg-type]
        classifier=_classifier(),
        runner=_runner(),
        output=_output(),
        jobs_session_maker=jobs_maker,
        scheduler=sched,
    )
    await pipe.on_confirm_callback(telegram_id=42, chat_id=42, pending_id=9, action="cancel")
    assert sender.sends[-1]["text"] == DIGEST_CONFIRM_CANCELLED_RU
    assert sched.calls == []
    async with jobs_maker() as s:
        from sqlalchemy import select

        assert (await s.execute(select(Job))).scalars().all() == []


async def test_confirm_stale_notice(jobs_maker) -> None:
    sender = FakeSender()
    sched = _FakeScheduler()
    store = _FakeConfirmStore(category="digest", draft=_draft_dict(), resolve_status=None)
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=store,  # type: ignore[arg-type]
        classifier=_classifier(),
        runner=_runner(),
        output=_output(),
        jobs_session_maker=jobs_maker,
        scheduler=sched,
    )
    await pipe.on_confirm_callback(telegram_id=42, chat_id=42, pending_id=9, action="confirm")
    assert sender.sends[-1]["text"] == DIGEST_CONFIRM_STALE_RU
    assert sched.calls == []
