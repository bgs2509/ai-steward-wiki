"""Chunk-21 tests for DefaultStreamingDelivery race + StreamEditor wiring."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier.schema import (
    ClassifierResult,
    Intent,
)
from ai_steward_wiki.tg.pipeline import (
    ACK_RUNNER_ERR_RU,
    ACK_TEXT_RU,
    DefaultPipeline,
    DefaultStreamingDelivery,
    WikiRunOutcome,
)
from ai_steward_wiki.wiki.runner import WikiRunnerError
from ai_steward_wiki.wiki.streaming import StreamEvent
from tests.unit.tg.conftest import FakeSender


def _cls_result() -> ClassifierResult:
    return ClassifierResult(
        intent=Intent.WIKI_QUERY,
        confidence=0.9,
        distilled_payload={"q": "x"},
        backend="fake",
        model="fake-m",
        prompt_semver="1.0.0",
        prompt_sha256="a" * 64,
        latency_ms=5,
    )


def _idem(*, l2_match: object = None) -> MagicMock:
    s = MagicMock()
    s.check_update_id = AsyncMock(return_value=True)
    s.check_content = AsyncMock(return_value=("b" * 64, l2_match))
    s.record_dedup_choice = AsyncMock(return_value=None)
    return s


def _confirm() -> MagicMock:
    s = MagicMock()
    s.resolve = AsyncMock(return_value="confirmed")
    return s


def _classifier() -> MagicMock:
    s = MagicMock()
    s.classify = AsyncMock(return_value=_cls_result())
    return s


def _output() -> MagicMock:
    s = MagicMock()
    s.deliver = AsyncMock(return_value=None)
    return s


@dataclass
class _FakeEditor:
    chat_id: int
    message_id: int
    feeds: list[str]
    finalized: int
    raise_on_finalize: bool = False

    async def feed(self, text: str) -> None:
        self.feeds.append(text)

    async def finalize(self) -> None:
        self.finalized += 1
        if self.raise_on_finalize:
            raise RuntimeError("boom-finalize")


def _editor_factory(captured: list[_FakeEditor]):
    def _make(*, sender, chat_id, first_message_id):
        ed = _FakeEditor(chat_id=chat_id, message_id=first_message_id, feeds=[], finalized=0)
        captured.append(ed)
        return ed

    return _make


def _runner_factory(
    *,
    delay: float,
    emit_events: list[StreamEvent] | None = None,
    raise_after: bool = False,
    outcome_text: str = "финальный текст",
):
    async def run(*, text, owner_telegram_id, correlation_id, intent, on_event=None):
        if emit_events:
            for ev in emit_events:
                if on_event is not None:
                    await on_event(ev)
                await asyncio.sleep(0)
        await asyncio.sleep(delay)
        if raise_after:
            raise WikiRunnerError("slow-explode")
        return WikiRunOutcome(run_id="run-1", text=outcome_text, latency_ms=int(delay * 1000))

    r = MagicMock()
    r.run = AsyncMock(side_effect=run)
    return r


@pytest.mark.asyncio
async def test_fast_path_no_placeholder_single_deliver() -> None:
    sender = FakeSender()
    editors: list[_FakeEditor] = []
    streaming = DefaultStreamingDelivery(
        sender=sender, timeout_s=1.0, stream_editor_factory=_editor_factory(editors)
    )
    out = _output()
    runner = _runner_factory(delay=0.01, outcome_text="готово")
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=_confirm(),
        classifier=_classifier(),
        runner=runner,
        output=out,
        streaming=streaming,
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="hi")
    assert sender.sends == []  # no placeholder
    assert editors == []  # no editor created
    out.deliver.assert_awaited_once()
    assert out.deliver.await_args.kwargs["text"] == "готово"
    # Fast path: deliver actually sends to TG (default tg_send=True).
    assert out.deliver.await_args.kwargs.get("tg_send", True) is True


@pytest.mark.asyncio
async def test_slow_path_sends_placeholder_then_streams() -> None:
    sender = FakeSender()
    editors: list[_FakeEditor] = []
    streaming = DefaultStreamingDelivery(
        sender=sender, timeout_s=0.05, stream_editor_factory=_editor_factory(editors)
    )
    out = _output()
    events = [
        StreamEvent(type="assistant_chunk", payload={"text": "часть-1 "}),
        StreamEvent(type="assistant_chunk", payload={"text": "часть-2"}),
    ]
    runner = _runner_factory(delay=0.15, emit_events=events)
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=_confirm(),
        classifier=_classifier(),
        runner=runner,
        output=out,
        streaming=streaming,
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="hi")
    # placeholder + 1 editor
    assert len(sender.sends) == 1
    assert "Думаю" in sender.sends[0]["text"]
    assert len(editors) == 1
    assert editors[0].feeds == ["часть-1 ", "часть-2"]
    assert editors[0].finalized == 1
    out.deliver.assert_awaited_once()
    assert out.deliver.await_args.kwargs["text"] == "часть-1 часть-2"
    # Slow path: reply already streamed via the editor — deliver() persists only.
    assert out.deliver.await_args.kwargs["tg_send"] is False


@pytest.mark.asyncio
async def test_runner_exception_during_streaming_finalizes_and_acks() -> None:
    sender = FakeSender()
    editors: list[_FakeEditor] = []
    streaming = DefaultStreamingDelivery(
        sender=sender, timeout_s=0.05, stream_editor_factory=_editor_factory(editors)
    )
    out = _output()
    events = [StreamEvent(type="assistant_chunk", payload={"text": "частичный"})]
    runner = _runner_factory(delay=0.15, emit_events=events, raise_after=True)
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=_confirm(),
        classifier=_classifier(),
        runner=runner,
        output=out,
        streaming=streaming,
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="hi")
    # placeholder sent + editor finalized + safe ack via sender (pipeline boundary)
    assert "Думаю" in sender.sends[0]["text"]
    assert editors[0].finalized == 1
    # The pipeline-level WikiRunnerError handler sends the safe ack via sender.
    assert any(s["text"] == ACK_RUNNER_ERR_RU for s in sender.sends)
    out.deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_back_compat_no_streaming_injection_uses_chunk20_path() -> None:
    sender = FakeSender()
    out = _output()
    runner = _runner_factory(delay=0.01, outcome_text="ответ")
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=_confirm(),
        classifier=_classifier(),
        runner=runner,
        output=out,
        # streaming=None — back-compat
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="hi")
    assert sender.sends == []  # no placeholder
    out.deliver.assert_awaited_once()
    assert out.deliver.await_args.kwargs["text"] == "ответ"


@pytest.mark.asyncio
async def test_aggregate_text_drives_final_deliver_text() -> None:
    sender = FakeSender()
    editors: list[_FakeEditor] = []
    streaming = DefaultStreamingDelivery(
        sender=sender, timeout_s=0.05, stream_editor_factory=_editor_factory(editors)
    )
    out = _output()
    events = [
        StreamEvent(type="assistant_chunk", payload={"text": "a"}),
        StreamEvent(type="assistant_chunk", payload={"text": "b"}),
        StreamEvent(type="assistant_chunk", payload={"text": "c"}),
    ]
    runner = _runner_factory(delay=0.15, emit_events=events, outcome_text="ignored")
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=_confirm(),
        classifier=_classifier(),
        runner=runner,
        output=out,
        streaming=streaming,
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="hi")
    assert out.deliver.await_args.kwargs["text"] == "abc"


@pytest.mark.asyncio
async def test_streaming_uses_outcome_text_when_no_assistant_events() -> None:
    sender = FakeSender()
    editors: list[_FakeEditor] = []
    streaming = DefaultStreamingDelivery(
        sender=sender, timeout_s=0.05, stream_editor_factory=_editor_factory(editors)
    )
    out = _output()
    runner = _runner_factory(delay=0.15, outcome_text="fallback-ok")
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=_confirm(),
        classifier=_classifier(),
        runner=runner,
        output=out,
        streaming=streaming,
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="hi")
    assert out.deliver.await_args.kwargs["text"] == "fallback-ok"


@pytest.mark.asyncio
async def test_streaming_empty_outcome_falls_back_to_ack_text() -> None:
    sender = FakeSender()
    editors: list[_FakeEditor] = []
    streaming = DefaultStreamingDelivery(
        sender=sender, timeout_s=0.05, stream_editor_factory=_editor_factory(editors)
    )
    out = _output()
    runner = _runner_factory(delay=0.15, outcome_text="")
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=_confirm(),
        classifier=_classifier(),
        runner=runner,
        output=out,
        streaming=streaming,
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="hi")
    assert out.deliver.await_args.kwargs["text"] == ACK_TEXT_RU
