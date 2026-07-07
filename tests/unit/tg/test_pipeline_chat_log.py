"""Unit tests for the D-033 chat_log wiring in DefaultPipeline (aisw-kml)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier.schema import ClassifierResult, Intent
from ai_steward_wiki.inbox.router import RouterDecision, RouterIntent
from ai_steward_wiki.storage.audit.chat_log import ChatTurn
from ai_steward_wiki.tg.pipeline import DefaultPipeline, WikiRunOutcome
from tests.helpers.classifier_factory import make_classifier_result
from tests.unit.tg.conftest import FakeSender


def _classifier_result(intent: Intent) -> ClassifierResult:
    action = "query" if intent is Intent.WIKI else None
    return make_classifier_result(intent, action=action, confidence=0.9)


def _make_idem() -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=True)
    idem.check_content = AsyncMock(return_value=("b" * 64, None))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


def _make_classifier(intent: Intent) -> MagicMock:
    cls = MagicMock()
    cls.classify = AsyncMock(return_value=_classifier_result(intent))
    return cls


def _make_runner(text: str = "ответ") -> MagicMock:
    r = MagicMock()
    r.run = AsyncMock(return_value=WikiRunOutcome(run_id="run-x", text=text, latency_ms=1))
    return r


def _make_output() -> MagicMock:
    out = MagicMock()
    out.deliver = AsyncMock(return_value=None)
    return out


def _make_chat_log(window: list[ChatTurn] | None = None) -> MagicMock:
    cl = MagicMock()
    cl.write_in = AsyncMock(return_value=None)
    cl.write_out = AsyncMock(return_value=None)
    cl.read_recent_window = AsyncMock(return_value=window or [])
    return cl


def _make_router(decision: RouterDecision) -> MagicMock:
    rt = MagicMock()
    rt.route = AsyncMock(return_value=decision)
    return rt


@pytest.mark.asyncio
async def test_writes_in_turn_on_inbound() -> None:
    sender = FakeSender()
    chat_log = _make_chat_log()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=MagicMock(),
        classifier=_make_classifier(Intent.WIKI),
        runner=_make_runner(),
        output=_make_output(),
        chat_log=chat_log,
    )

    await pipe.on_text(telegram_id=7, chat_id=70, update_id=5, text="какое давление?")

    chat_log.write_in.assert_awaited_once()
    kw = chat_log.write_in.await_args.kwargs
    assert kw["telegram_id"] == 7
    assert kw["chat_id"] == 70
    assert kw["text"] == "какое давление?"


@pytest.mark.asyncio
async def test_writes_out_turn_on_final_reply() -> None:
    sender = FakeSender()
    chat_log = _make_chat_log()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=MagicMock(),
        classifier=_make_classifier(Intent.WIKI),
        runner=_make_runner(text="120 на 80"),
        output=_make_output(),
        chat_log=chat_log,
    )

    await pipe.on_text(telegram_id=7, chat_id=70, update_id=5, text="давление?")

    chat_log.write_out.assert_awaited_once()
    kw = chat_log.write_out.await_args.kwargs
    assert kw["text"] == "120 на 80"


@pytest.mark.asyncio
async def test_recent_window_fed_to_router() -> None:
    sender = FakeSender()
    window = [
        ChatTurn(direction="in", text="давление?", created_at_utc=datetime(2026, 6, 23, 12, 0)),
        ChatTurn(direction="out", text="120 на 80", created_at_utc=datetime(2026, 6, 23, 12, 1)),
    ]
    chat_log = _make_chat_log(window)
    router = _make_router(
        RouterDecision(
            intent=RouterIntent.CLARIFY,
            target_wiki=None,
            notes="хм",
            raw="",
            parsed_ok=True,
        )
    )
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=MagicMock(),
        classifier=_make_classifier(Intent.UNKNOWN),
        runner=_make_runner(),
        output=_make_output(),
        router=router,
        chat_log=chat_log,
    )

    await pipe.on_text(telegram_id=7, chat_id=70, update_id=5, text="повтори")

    chat_log.read_recent_window.assert_awaited_once_with(7)
    kw = router.route.await_args.kwargs
    assert kw["recent_window"] == window


@pytest.mark.asyncio
async def test_no_chat_log_keeps_pipeline_working() -> None:
    """chat_log=None: no writes/reads, pipeline still delivers (back-compat)."""
    sender = FakeSender()
    output = _make_output()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=MagicMock(),
        classifier=_make_classifier(Intent.WIKI),
        runner=_make_runner(text="ok"),
        output=output,
        chat_log=None,
    )

    await pipe.on_text(telegram_id=7, chat_id=70, update_id=5, text="hi")

    output.deliver.assert_awaited_once()
