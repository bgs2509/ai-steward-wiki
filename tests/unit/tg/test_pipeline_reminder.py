"""Unit tests for the Phase-D.a reminder fast-path in DefaultPipeline (aisw-kcz)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_steward_wiki.classifier.schema import (
    ClassifierResult,
    ClassifierSchemaError,
    ClassifierTimeoutError,
    Intent,
    TimeParseResult,
)
from ai_steward_wiki.scheduler.queue import Lane
from ai_steward_wiki.storage.jobs.engine import Base
from ai_steward_wiki.storage.jobs.models import Job
from ai_steward_wiki.storage.jobs.payloads import ReminderPayload, parse_job_payload
from ai_steward_wiki.tg.confirm import build_route_confirm_keyboard
from ai_steward_wiki.tg.pipeline import (
    REMINDER_ACK_LEAD_RU,
    REMINDER_CONFIRM_CANCELLED_RU,
    REMINDER_CONFIRM_STALE_RU,
    REMINDER_PAST_RU,
    REMINDER_RECURRING_RU,
    REMINDER_UNPARSEABLE_RU,
    DefaultPipeline,
    _extract_lead_minutes,
)
from tests.unit.tg.conftest import FakeSender

NOW = datetime(2026, 5, 12, 18, 0, tzinfo=UTC)  # 21:00 Europe/Moscow
FUTURE = datetime(2026, 5, 13, 3, 0, tzinfo=UTC)  # tomorrow 06:00 MSK
PAST = datetime(2026, 5, 1, 6, 0, tzinfo=UTC)


def _classifier(
    *,
    intent: Intent = Intent.REMINDER,
    confidence: float = 0.93,
    distilled: dict[str, Any] | None = None,
) -> MagicMock:
    cls = MagicMock()
    cls.classify = AsyncMock(
        return_value=ClassifierResult(
            intent=intent,
            confidence=confidence,
            distilled_payload=distilled if distilled is not None else {},
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


def _confirm() -> MagicMock:
    c = MagicMock()
    rec = MagicMock()
    rec.pending_id = 7
    c.request_explicit = AsyncMock(return_value=rec)
    return c


class _FakeTimeParser:
    def __init__(self, result: TimeParseResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def parse_time(
        self, text: str, *, user_tz, now_utc, prefer_future=False, correlation_id=""
    ) -> TimeParseResult:
        self.calls.append(
            {"text": text, "user_tz": user_tz, "now_utc": now_utc, "prefer_future": prefer_future}
        )
        return self.result


def _tpr(*, when_utc, escalate=False, source="dateparser") -> TimeParseResult:
    return TimeParseResult(
        when_utc=when_utc,
        source="escalate" if escalate else source,
        escalate=escalate,
        raw="raw",
        user_tz="Europe/Moscow",
    )


class _FakeScheduler:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def add_job(self, func, *, trigger=None, args=None, id=None, misfire_grace_time=None, **kw):
        self.calls.append({"func": func, "args": args, "id": id, "misfire": misfire_grace_time})


@pytest.fixture
async def jobs_maker(tmp_path: Any):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _runner() -> MagicMock:
    from ai_steward_wiki.tg.pipeline import WikiRunOutcome

    r = MagicMock()
    r.run = AsyncMock(return_value=WikiRunOutcome(run_id="run-x", text="legacy", latency_ms=1))
    return r


def _output() -> MagicMock:
    out = MagicMock()
    out.deliver = AsyncMock(return_value=None)
    return out


def _pipe(
    *,
    sender: FakeSender,
    classifier: MagicMock,
    confirmation: MagicMock,
    time_parser: Any = None,
    jobs_session_maker: Any = None,
    scheduler: Any = None,
) -> DefaultPipeline:
    return DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=confirmation,
        classifier=classifier,
        runner=_runner(),
        output=_output(),
        time_parser=time_parser,
        jobs_session_maker=jobs_session_maker,
        scheduler=scheduler,
        clock=lambda: NOW,
    )


# --- detection -------------------------------------------------------------


async def test_future_time_requests_confirm() -> None:
    sender = FakeSender()
    confirm = _confirm()
    tp = _FakeTimeParser(_tpr(when_utc=FUTURE))
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(distilled={"reminder_text": "позвонить врачу"}),
        confirmation=confirm,
        time_parser=tp,
    )
    await pipe.on_text(
        telegram_id=42, chat_id=42, update_id=1, text="напомни завтра в 6 позвонить врачу"
    )

    confirm.request_explicit.assert_awaited_once()
    draft_obj = confirm.request_explicit.call_args.args[0]
    assert draft_obj.category == "reminder"
    assert draft_obj.draft["when_utc"] == "2026-05-13T03:00:00+00:00"
    assert draft_obj.draft["message"] == "позвонить врачу"
    assert "13.05 06:00" in draft_obj.recap_text
    assert "Europe/Moscow" in draft_obj.recap_text
    assert (
        confirm.request_explicit.call_args.kwargs["keyboard_factory"]
        is build_route_confirm_keyboard
    )
    assert tp.calls[0]["prefer_future"] is True
    assert sender.sends == []  # no message until confirm


async def test_message_falls_back_to_raw_text() -> None:
    sender = FakeSender()
    confirm = _confirm()
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(distilled={}),
        confirmation=confirm,
        time_parser=_FakeTimeParser(_tpr(when_utc=FUTURE)),
    )
    await pipe.on_text(telegram_id=42, chat_id=42, update_id=1, text="напомни в 6")
    assert confirm.request_explicit.call_args.args[0].draft["message"] == "напомни в 6"

    confirm2 = _confirm()
    pipe2 = _pipe(
        sender=FakeSender(),
        classifier=_classifier(distilled={"reminder_text": "   "}),
        confirmation=confirm2,
        time_parser=_FakeTimeParser(_tpr(when_utc=FUTURE)),
    )
    await pipe2.on_text(telegram_id=42, chat_id=42, update_id=2, text="напомни в 6")
    assert confirm2.request_explicit.call_args.args[0].draft["message"] == "напомни в 6"


async def test_escalate_replies_clarification() -> None:
    sender = FakeSender()
    confirm = _confirm()
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(),
        confirmation=confirm,
        time_parser=_FakeTimeParser(_tpr(when_utc=None, escalate=True)),
    )
    await pipe.on_text(telegram_id=42, chat_id=42, update_id=1, text="напомни когда-нибудь")
    assert sender.sends[-1]["text"] == REMINDER_UNPARSEABLE_RU
    confirm.request_explicit.assert_not_awaited()


async def test_explicitly_past_date_rejected() -> None:
    sender = FakeSender()
    confirm = _confirm()
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(),
        confirmation=confirm,
        time_parser=_FakeTimeParser(_tpr(when_utc=PAST)),
    )
    await pipe.on_text(telegram_id=42, chat_id=42, update_id=1, text="напомни 1 мая")
    assert sender.sends[-1]["text"] == REMINDER_PAST_RU
    confirm.request_explicit.assert_not_awaited()


async def test_recurring_phrasing_not_yet() -> None:
    sender = FakeSender()
    confirm = _confirm()
    tp = _FakeTimeParser(_tpr(when_utc=FUTURE))
    pipe = _pipe(sender=sender, classifier=_classifier(), confirmation=confirm, time_parser=tp)
    await pipe.on_text(telegram_id=42, chat_id=42, update_id=1, text="каждый день в 9 сводка")
    assert sender.sends[-1]["text"] == REMINDER_RECURRING_RU
    assert tp.calls == []
    confirm.request_explicit.assert_not_awaited()


async def test_low_confidence_not_handled_here() -> None:
    sender = FakeSender()
    confirm = _confirm()
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(confidence=0.5),
        confirmation=confirm,
        time_parser=_FakeTimeParser(_tpr(when_utc=FUTURE)),
    )
    await pipe.on_text(telegram_id=42, chat_id=42, update_id=1, text="напомни в 6")
    confirm.request_explicit.assert_not_awaited()
    assert all(
        s["text"] not in {REMINDER_UNPARSEABLE_RU, REMINDER_PAST_RU, REMINDER_RECURRING_RU}
        for s in sender.sends
    )


async def test_no_time_parser_not_handled_here() -> None:
    sender = FakeSender()
    confirm = _confirm()
    pipe = _pipe(sender=sender, classifier=_classifier(), confirmation=confirm, time_parser=None)
    await pipe.on_text(telegram_id=42, chat_id=42, update_id=1, text="напомни в 6")
    confirm.request_explicit.assert_not_awaited()


# --- time_expr distillation (aisw-2mg, RC-2) -------------------------------


async def test_reminder_intent_passes_distilled_time_expr_to_parser() -> None:
    """When Stage-0 distils time_expr, parser receives the clean expression."""
    sender = FakeSender()
    confirm = _confirm()
    tp = _FakeTimeParser(_tpr(when_utc=FUTURE))
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(
            distilled={"time_expr": "через 5 минут", "reminder_text": "пойти гулять"}
        ),
        confirmation=confirm,
        time_parser=tp,
    )
    await pipe.on_text(
        telegram_id=42,
        chat_id=42,
        update_id=1,
        text="напомни мне пойти гулять через 5 минут",
    )
    # Parser receives the distilled clean expression, NOT the raw sentence.
    assert tp.calls
    assert tp.calls[0]["text"] == "через 5 минут"


async def test_reminder_intent_falls_back_to_raw_text_when_time_expr_missing() -> None:
    """NFR-2: missing time_expr → fallback to raw text, no crash."""
    sender = FakeSender()
    confirm = _confirm()
    tp = _FakeTimeParser(_tpr(when_utc=FUTURE))
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(distilled={"reminder_text": "позвонить"}),
        confirmation=confirm,
        time_parser=tp,
    )
    await pipe.on_text(telegram_id=42, chat_id=42, update_id=1, text="напомни завтра в 6 позвонить")
    assert tp.calls
    assert tp.calls[0]["text"] == "напомни завтра в 6 позвонить"


async def test_reminder_intent_blank_time_expr_falls_back_to_raw_text() -> None:
    """Whitespace-only time_expr is treated as missing (defensive)."""
    sender = FakeSender()
    confirm = _confirm()
    tp = _FakeTimeParser(_tpr(when_utc=FUTURE))
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(distilled={"time_expr": "   "}),
        confirmation=confirm,
        time_parser=tp,
    )
    await pipe.on_text(telegram_id=42, chat_id=42, update_id=1, text="напомни в 6")
    assert tp.calls
    assert tp.calls[0]["text"] == "напомни в 6"


# --- parser error guard (aisw-4dr, RC-3) -----------------------------------


class _RaisingTimeParser:
    """TimeParser whose parse_time always raises a given exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def parse_time(
        self, text: str, *, user_tz, now_utc, prefer_future=False, correlation_id=""
    ) -> TimeParseResult:
        self.calls.append({"text": text, "correlation_id": correlation_id})
        raise self._exc


async def test_reminder_intent_classifier_schema_error_emits_unparseable_ru(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sender = FakeSender()
    confirm = _confirm()
    parser = _RaisingTimeParser(
        ClassifierSchemaError("claude CLI inner JSON parse failed: 'prose…'")
    )
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(),
        confirmation=confirm,
        time_parser=parser,
    )
    # Must NOT raise — handler swallows parser exceptions per FR-1.
    await pipe.on_text(telegram_id=42, chat_id=42, update_id=1, text="напомни через 5 минут")

    assert parser.calls, "parse_time must have been invoked"
    assert sender.sends
    assert sender.sends[-1]["text"] == REMINDER_UNPARSEABLE_RU
    confirm.request_explicit.assert_not_awaited()
    # New NFR-3 log anchor for observability — structlog → stdout.
    out = capsys.readouterr().out
    assert "tg.pipeline.reminder.parser_failed" in out
    assert "ClassifierSchemaError" in out


async def test_reminder_intent_timeout_error_emits_unparseable_ru() -> None:
    sender = FakeSender()
    confirm = _confirm()
    parser = _RaisingTimeParser(ClassifierTimeoutError("haiku CLI timeout after 30s"))
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(),
        confirmation=confirm,
        time_parser=parser,
    )
    await pipe.on_text(telegram_id=42, chat_id=42, update_id=1, text="напомни через 5 минут")
    assert sender.sends
    assert sender.sends[-1]["text"] == REMINDER_UNPARSEABLE_RU
    confirm.request_explicit.assert_not_awaited()


async def test_reminder_intent_generic_exception_emits_unparseable_ru() -> None:
    """Any unexpected exception (e.g. RuntimeError) must also be caught — FR-1."""
    sender = FakeSender()
    confirm = _confirm()
    parser = _RaisingTimeParser(RuntimeError("boom"))
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(),
        confirmation=confirm,
        time_parser=parser,
    )
    await pipe.on_text(telegram_id=42, chat_id=42, update_id=1, text="напомни через 5 минут")
    assert sender.sends
    assert sender.sends[-1]["text"] == REMINDER_UNPARSEABLE_RU
    confirm.request_explicit.assert_not_awaited()


# --- confirm callback ------------------------------------------------------


def _pending_reminder(
    *,
    when_iso: str = "2026-05-13T03:00:00+00:00",
    message: str = "позвонить врачу",
    lead_time_min: int = 0,
) -> MagicMock:
    p = MagicMock()
    p.category = "reminder"
    p.draft_json = json.dumps(
        {
            "when_utc": when_iso,
            "message": message,
            "lead_time_min": lead_time_min,
            "user_tz": "Europe/Moscow",
            "correlation_id": "c",
        }
    )
    return p


async def test_confirm_creates_job_and_acks(jobs_maker) -> None:
    sender = FakeSender()
    confirm = MagicMock()
    confirm.get_pending = AsyncMock(return_value=_pending_reminder())
    confirm.resolve = AsyncMock(return_value="confirmed")
    sched = _FakeScheduler()
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(),
        confirmation=confirm,
        jobs_session_maker=jobs_maker,
        scheduler=sched,
    )
    await pipe.on_confirm_callback(telegram_id=42, chat_id=42, pending_id=7, action="confirm")

    async with jobs_maker() as s:
        rows = (await s.execute(Job.__table__.select())).all()
    assert len(rows) == 1
    row = rows[0]._mapping
    assert row["kind"] == "reminder_job"
    assert row["priority"] == int(Lane.USER_WRITE)
    assert row["scheduled_at_utc"] == FUTURE.replace(tzinfo=None)
    payload = parse_job_payload(row["payload"])
    assert isinstance(payload, ReminderPayload)
    assert payload.message == "позвонить врачу"
    assert len(sched.calls) == 1
    assert sched.calls[0]["id"] == f"reminder:{row['id']}"
    assert sched.calls[0]["misfire"] is None
    assert "13.05 06:00" in sender.sends[-1]["text"]
    assert sender.sends[-1]["text"].startswith("Готово")


async def test_confirm_cancel_no_job(jobs_maker) -> None:
    sender = FakeSender()
    confirm = MagicMock()
    confirm.get_pending = AsyncMock(return_value=_pending_reminder())
    confirm.resolve = AsyncMock(return_value="cancelled")
    sched = _FakeScheduler()
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(),
        confirmation=confirm,
        jobs_session_maker=jobs_maker,
        scheduler=sched,
    )
    await pipe.on_confirm_callback(telegram_id=42, chat_id=42, pending_id=7, action="cancel")
    assert sched.calls == []
    async with jobs_maker() as s:
        assert (await s.execute(Job.__table__.select())).all() == []
    assert sender.sends[-1]["text"] == REMINDER_CONFIRM_CANCELLED_RU


async def test_confirm_stale_notice(jobs_maker) -> None:
    sender = FakeSender()
    confirm = MagicMock()
    confirm.get_pending = AsyncMock(return_value=_pending_reminder())
    confirm.resolve = AsyncMock(return_value=None)
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(),
        confirmation=confirm,
        jobs_session_maker=jobs_maker,
        scheduler=_FakeScheduler(),
    )
    await pipe.on_confirm_callback(telegram_id=42, chat_id=42, pending_id=7, action="confirm")
    assert sender.sends[-1]["text"] == REMINDER_CONFIRM_STALE_RU
    async with jobs_maker() as s:
        assert (await s.execute(Job.__table__.select())).all() == []


async def test_double_confirm_idempotent(jobs_maker) -> None:
    sender = FakeSender()
    confirm = MagicMock()
    confirm.get_pending = AsyncMock(return_value=_pending_reminder())
    confirm.resolve = AsyncMock(side_effect=["confirmed", None])
    sched = _FakeScheduler()
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(),
        confirmation=confirm,
        jobs_session_maker=jobs_maker,
        scheduler=sched,
    )
    await pipe.on_confirm_callback(telegram_id=42, chat_id=42, pending_id=7, action="confirm")
    await pipe.on_confirm_callback(telegram_id=42, chat_id=42, pending_id=7, action="confirm")
    assert len(sched.calls) == 1  # only the first created a job
    async with jobs_maker() as s:
        assert len((await s.execute(Job.__table__.select())).all()) == 1
    assert sender.sends[-1]["text"] == REMINDER_CONFIRM_STALE_RU


async def test_non_reminder_category_uses_generic_resolve(jobs_maker) -> None:
    sender = FakeSender()
    confirm = MagicMock()
    other = MagicMock()
    other.category = "elevation"
    confirm.get_pending = AsyncMock(return_value=other)
    confirm.resolve = AsyncMock(return_value="confirmed")
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(),
        confirmation=confirm,
        jobs_session_maker=jobs_maker,
        scheduler=_FakeScheduler(),
    )
    await pipe.on_confirm_callback(telegram_id=42, chat_id=42, pending_id=9, action="confirm")
    confirm.resolve.assert_awaited_once_with(42, 9, "confirm")


# --- lead offset: «… а ещё за N до» (#3, aisw-5wr) -------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("напомни в субботу в 12, а ещё за час до", 60),
        ("за 30 минут до", 30),
        ("за полчаса до", 30),
        ("за 2 часа до", 120),
        ("за 2 дня до", 2880),
        ("напомни завтра в 9", 0),
        ("просто текст без оффсета", 0),
    ],
)
def test_extract_lead_minutes(text: str, expected: int) -> None:
    assert _extract_lead_minutes(text) == expected


async def test_confirm_with_lead_creates_two_jobs(jobs_maker) -> None:
    sender = FakeSender()
    confirm = MagicMock()
    confirm.get_pending = AsyncMock(return_value=_pending_reminder(lead_time_min=60))
    confirm.resolve = AsyncMock(return_value="confirmed")
    sched = _FakeScheduler()
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(),
        confirmation=confirm,
        jobs_session_maker=jobs_maker,
        scheduler=sched,
    )
    await pipe.on_confirm_callback(telegram_id=42, chat_id=42, pending_id=7, action="confirm")

    async with jobs_maker() as s:
        rows = (await s.execute(Job.__table__.select())).all()
    # main at FUTURE (03:00) + lead at FUTURE-60min (02:00)
    assert len(rows) == 2
    scheduled = sorted(r._mapping["scheduled_at_utc"] for r in rows)
    assert scheduled[0] == (FUTURE - timedelta(minutes=60)).replace(tzinfo=None)
    assert scheduled[1] == FUTURE.replace(tzinfo=None)
    assert len(sched.calls) == 2
    assert sender.sends[-1]["text"] == REMINDER_ACK_LEAD_RU.format(when_local="13.05 06:00")


async def test_confirm_lead_in_past_creates_only_main(jobs_maker) -> None:
    sender = FakeSender()
    confirm = MagicMock()
    # main only 30 min after NOW; a 60-min lead lands in the past → skip the early job.
    near = "2026-05-12T18:30:00+00:00"
    confirm.get_pending = AsyncMock(return_value=_pending_reminder(when_iso=near, lead_time_min=60))
    confirm.resolve = AsyncMock(return_value="confirmed")
    sched = _FakeScheduler()
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(),
        confirmation=confirm,
        jobs_session_maker=jobs_maker,
        scheduler=sched,
    )
    await pipe.on_confirm_callback(telegram_id=42, chat_id=42, pending_id=7, action="confirm")

    async with jobs_maker() as s:
        rows = (await s.execute(Job.__table__.select())).all()
    assert len(rows) == 1
    assert len(sched.calls) == 1
