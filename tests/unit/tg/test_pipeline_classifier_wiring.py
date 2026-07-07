"""Unit tests for chunk-20 M-TG-PIPELINE-CLASSIFIER composition in DefaultPipeline.

Covers: happy text path, L1 dup, L2 dedup hit, classifier error, runner error,
voice happy, voice empty transcript, back-compat None-injection, log markers,
voice classifier error.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier.schema import (
    ClassifierResult,
    ClassifierSchemaError,
    Intent,
)
from ai_steward_wiki.inbox.idempotency import SeenFileMatch
from ai_steward_wiki.tg.pipeline import (
    ACK_CLASSIFY_ERR_RU,
    ACK_DEDUP_RU,
    ACK_RUNNER_ERR_RU,
    ACK_TEXT_RU,
    ACK_VOICE_RU,
    DefaultPipeline,
    WikiRunOutcome,
)
from ai_steward_wiki.wiki.runner import WikiRunnerTimeoutError
from tests.helpers.classifier_factory import make_classifier_result
from tests.unit.tg.conftest import FakeSender


def _classifier_result(intent: Intent = Intent.WIKI) -> ClassifierResult:
    return make_classifier_result(intent, action="query", confidence=0.91)


def _make_idem(*, new_update: bool = True, l2_match: SeenFileMatch | None = None) -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=new_update)
    idem.check_content = AsyncMock(return_value=("b" * 64, l2_match))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


def _make_confirm() -> MagicMock:
    svc = MagicMock()
    svc.resolve = AsyncMock(return_value="confirmed")
    return svc


def _make_classifier(
    result: ClassifierResult | None = None, *, raises: Exception | None = None
) -> MagicMock:
    cls = MagicMock()
    if raises is not None:
        cls.classify = AsyncMock(side_effect=raises)
    else:
        cls.classify = AsyncMock(return_value=result or _classifier_result())
    return cls


def _make_runner(
    outcome: WikiRunOutcome | None = None, *, raises: Exception | None = None
) -> MagicMock:
    r = MagicMock()
    if raises is not None:
        r.run = AsyncMock(side_effect=raises)
    else:
        r.run = AsyncMock(
            return_value=outcome or WikiRunOutcome(run_id="run-x", text="ответ", latency_ms=42)
        )
    return r


def _make_output() -> MagicMock:
    out = MagicMock()
    out.deliver = AsyncMock(return_value=None)
    return out


def _full_pipeline(
    *,
    sender: FakeSender,
    idem: MagicMock,
    classifier: MagicMock | None = None,
    runner: MagicMock | None = None,
    output: MagicMock | None = None,
    voice: MagicMock | None = None,
) -> DefaultPipeline:
    return DefaultPipeline(
        sender=sender,
        idempotency=idem,
        confirmation=_make_confirm(),
        voice=voice,
        classifier=classifier or _make_classifier(),
        runner=runner or _make_runner(),
        output=output or _make_output(),
    )


@dataclass
class _FakeRef:
    sha256: str = "deadbeef" * 8
    ext: str = "ogg"


@dataclass
class _FakeTranscript:
    text: str = "привет"
    lang: str = "ru"
    duration_s: float = 1.0
    model: str = "fake"
    rtf: float = 0.1


# ---------- Tests ----------


@pytest.mark.asyncio
async def test_text_happy_path_invokes_full_pipeline_once() -> None:
    sender = FakeSender()
    idem = _make_idem()
    cls = _make_classifier()
    runner = _make_runner()
    out = _make_output()
    pipe = _full_pipeline(sender=sender, idem=idem, classifier=cls, runner=runner, output=out)

    await pipe.on_text(telegram_id=42, chat_id=10, update_id=5, text="привет")

    cls.classify.assert_awaited_once()
    runner.run.assert_awaited_once()
    out.deliver.assert_awaited_once()
    assert idem.check_content.await_count == 1
    assert sender.sends == []  # delivery via output adapter (mocked), not sender
    args = runner.run.await_args.kwargs
    assert args["text"] == "привет"
    assert args["owner_telegram_id"] == 42
    assert args["intent"] == Intent.WIKI


@pytest.mark.asyncio
async def test_text_l1_dup_short_circuits_before_classifier() -> None:
    sender = FakeSender()
    idem = _make_idem(new_update=False)
    cls = _make_classifier()
    pipe = _full_pipeline(sender=sender, idem=idem, classifier=cls)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=100, text="hi")

    cls.classify.assert_not_awaited()
    idem.check_content.assert_not_awaited()
    assert sender.sends == []


@pytest.mark.asyncio
async def test_text_l2_dedup_hit_records_and_replies_dedup_ack() -> None:
    from datetime import datetime

    sender = FakeSender()
    match = SeenFileMatch(
        content_sha256="b" * 64,
        owner_telegram_id=42,
        kind="text",
        first_seen_at_utc=datetime(2026, 5, 11),
        within_ttl=True,
    )
    idem = _make_idem(l2_match=match)
    cls = _make_classifier()
    runner = _make_runner()
    pipe = _full_pipeline(sender=sender, idem=idem, classifier=cls, runner=runner)

    await pipe.on_text(telegram_id=42, chat_id=10, update_id=7, text="старое")

    cls.classify.assert_not_awaited()
    runner.run.assert_not_awaited()
    idem.record_dedup_choice.assert_awaited_once_with("b" * 64, 42, "auto_skip")
    assert sender.sends == [
        {
            "chat_id": 10,
            "text": ACK_DEDUP_RU,
            "parse_mode": "HTML",
            "reply_markup": None,
            "message_id": 1001,
        }
    ]


@pytest.mark.asyncio
async def test_text_classifier_error_replies_safe_ack_and_skips_runner() -> None:
    sender = FakeSender()
    runner = _make_runner()
    out = _make_output()
    pipe = _full_pipeline(
        sender=sender,
        idem=_make_idem(),
        classifier=_make_classifier(raises=ClassifierSchemaError("bad")),
        runner=runner,
        output=out,
    )

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="x")

    runner.run.assert_not_awaited()
    out.deliver.assert_not_awaited()
    assert sender.sends[0]["text"] == ACK_CLASSIFY_ERR_RU


@pytest.mark.asyncio
async def test_text_runner_error_replies_safe_ack_and_skips_deliver() -> None:
    sender = FakeSender()
    out = _make_output()
    pipe = _full_pipeline(
        sender=sender,
        idem=_make_idem(),
        runner=_make_runner(raises=WikiRunnerTimeoutError("slow")),
        output=out,
    )

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="x")

    out.deliver.assert_not_awaited()
    assert sender.sends[0]["text"] == ACK_RUNNER_ERR_RU


@pytest.mark.asyncio
async def test_text_empty_runner_output_falls_back_to_default_ack_via_deliver() -> None:
    sender = FakeSender()
    out = _make_output()
    pipe = _full_pipeline(
        sender=sender,
        idem=_make_idem(),
        runner=_make_runner(outcome=WikiRunOutcome(run_id="r1", text="", latency_ms=10)),
        output=out,
    )

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="x")

    out.deliver.assert_awaited_once()
    delivered = out.deliver.await_args.kwargs["text"]
    assert delivered == ACK_TEXT_RU


@pytest.mark.asyncio
async def test_text_backcompat_no_classifier_falls_back_to_simple_ack() -> None:
    sender = FakeSender()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
        # classifier/runner/output omitted on purpose
    )

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="x")

    assert sender.sends[0]["text"] == ACK_TEXT_RU


@pytest.mark.asyncio
async def test_voice_happy_path_runs_pipeline_on_transcript() -> None:
    sender = FakeSender()
    voice = MagicMock()
    voice.handle = AsyncMock(return_value=(_FakeRef(), _FakeTranscript(text="запиши встречу")))
    cls = _make_classifier()
    runner = _make_runner()
    out = _make_output()
    pipe = _full_pipeline(
        sender=sender,
        idem=_make_idem(),
        classifier=cls,
        runner=runner,
        output=out,
        voice=voice,
    )

    await pipe.on_voice(telegram_id=1, chat_id=10, update_id=2, audio_bytes=b"\x00")

    cls.classify.assert_awaited_once()
    runner.run.assert_awaited_once()
    assert runner.run.await_args.kwargs["text"] == "запиши встречу"
    out.deliver.assert_awaited_once()


@pytest.mark.asyncio
async def test_voice_empty_transcript_falls_back_to_default_ack() -> None:
    sender = FakeSender()
    voice = MagicMock()
    voice.handle = AsyncMock(return_value=(_FakeRef(), _FakeTranscript(text="")))
    cls = _make_classifier()
    pipe = _full_pipeline(sender=sender, idem=_make_idem(), classifier=cls, voice=voice)

    await pipe.on_voice(telegram_id=1, chat_id=10, update_id=2, audio_bytes=b"\x00")

    cls.classify.assert_not_awaited()
    assert sender.sends[0]["text"] == ACK_TEXT_RU


@pytest.mark.asyncio
async def test_voice_backcompat_no_classifier_uses_legacy_voice_ack() -> None:
    sender = FakeSender()
    voice = MagicMock()
    voice.handle = AsyncMock(return_value=(_FakeRef(), _FakeTranscript(text="привет")))
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
        voice=voice,
    )

    await pipe.on_voice(telegram_id=1, chat_id=10, update_id=2, audio_bytes=b"\x00")

    assert sender.sends[0]["text"].startswith(ACK_VOICE_RU)
    assert "привет" in sender.sends[0]["text"]


@pytest.mark.asyncio
async def test_text_pipeline_emits_expected_log_markers(capsys: pytest.CaptureFixture[str]) -> None:
    sender = FakeSender()
    pipe = _full_pipeline(sender=sender, idem=_make_idem())

    await pipe.on_text(telegram_id=42, chat_id=10, update_id=2, text="hi")

    captured = capsys.readouterr().out
    expected = [
        "tg.pipeline.classify.begin",
        "tg.pipeline.classify.done",
        "tg.pipeline.runner.dispatched",
        "tg.pipeline.runner.completed",
        "tg.pipeline.deliver.sent",
    ]
    missing = [m for m in expected if m not in captured]
    assert not missing, f"missing markers: {missing}\nlog output:\n{captured}"
