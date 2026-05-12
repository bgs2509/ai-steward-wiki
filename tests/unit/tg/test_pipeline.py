"""Unit tests for tg.pipeline.DefaultPipeline (M-TG-HANDLERS-WIRING)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.tg.pipeline import (
    ACK_PHOTO_RU,
    ACK_TEXT_RU,
    ACK_VOICE_RU,
    ACK_VOICE_UNAVAILABLE_RU,
    DefaultPipeline,
)
from ai_steward_wiki.tg.voice import VoiceUnavailableError
from tests.unit.tg.conftest import FakeSender


@dataclass
class _FakeRef:
    sha256: str = "deadbeef" * 8
    ext: str = "jpg"
    staging_path: Path = Path("/tmp/_staging/run_dead.jpg")


@dataclass
class _FakeTranscript:
    text: str = "привет мир"
    lang: str = "ru"
    duration_s: float = 1.0
    model: str = "fake"
    rtf: float = 0.1


def _make_idem(new: bool = True, *, content_match: object | None = None) -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=new)
    idem.check_content = AsyncMock(return_value=("ab" * 32, content_match))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


def _make_runner(text: str = "распознал чек") -> MagicMock:
    from ai_steward_wiki.tg.pipeline import WikiRunOutcome

    r = MagicMock()
    r.run = AsyncMock(return_value=WikiRunOutcome(run_id="run-x", text=text, latency_ms=10))
    return r


def _make_classifier() -> MagicMock:
    from ai_steward_wiki.classifier.schema import ClassifierResult, Intent

    cls = MagicMock()
    cls.classify = AsyncMock(
        return_value=ClassifierResult(
            intent=Intent.WIKI_QUERY,
            confidence=0.9,
            distilled_payload={},
            backend="fake",
            model="fake-m",
            prompt_semver="1.0.0",
            prompt_sha256="a" * 64,
            latency_ms=5,
        )
    )
    return cls


def _make_output() -> MagicMock:
    out = MagicMock()
    out.deliver = AsyncMock(return_value=None)
    return out


def _make_confirm(status: str | None = "confirmed") -> MagicMock:
    svc = MagicMock()
    svc.resolve = AsyncMock(return_value=status)
    # Phase-C (aisw-e45): on_confirm_callback reads the pending row first to
    # decide route_ingest vs legacy dispatch; default → legacy (None row).
    svc.get_pending = AsyncMock(return_value=None)
    return svc


@pytest.mark.asyncio
async def test_on_text_sends_ack_when_new() -> None:
    sender = FakeSender()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(new=True),
        confirmation=_make_confirm(),
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=100, text="hi")
    assert len(sender.sends) == 1
    assert sender.sends[0]["text"] == ACK_TEXT_RU
    assert sender.sends[0]["chat_id"] == 10


@pytest.mark.asyncio
async def test_on_text_skips_on_l1_duplicate() -> None:
    sender = FakeSender()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(new=False),
        confirmation=_make_confirm(),
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=100, text="hi")
    assert sender.sends == []


@pytest.mark.asyncio
async def test_on_voice_with_handler_returns_transcript() -> None:
    sender = FakeSender()
    voice = MagicMock()
    voice.handle = AsyncMock(return_value=(_FakeRef(), _FakeTranscript(text="hello")))
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
        voice=voice,
    )
    await pipe.on_voice(telegram_id=1, chat_id=10, update_id=100, audio_bytes=b"\x00")
    assert len(sender.sends) == 1
    assert sender.sends[0]["text"].startswith(ACK_VOICE_RU)
    assert "hello" in sender.sends[0]["text"]


@pytest.mark.asyncio
async def test_on_voice_without_handler_falls_back() -> None:
    sender = FakeSender()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
    )
    await pipe.on_voice(telegram_id=1, chat_id=10, update_id=100, audio_bytes=b"\x00")
    assert sender.sends[0]["text"] == ACK_TEXT_RU


@pytest.mark.asyncio
async def test_on_voice_empty_transcript_falls_back_to_default_ack() -> None:
    sender = FakeSender()
    voice = MagicMock()
    voice.handle = AsyncMock(return_value=(_FakeRef(), _FakeTranscript(text="")))
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
        voice=voice,
    )
    await pipe.on_voice(telegram_id=1, chat_id=10, update_id=100, audio_bytes=b"\x00")
    assert sender.sends[0]["text"] == ACK_TEXT_RU


@pytest.mark.asyncio
async def test_on_voice_forwards_ext_and_mime_to_handler() -> None:
    sender = FakeSender()
    voice = MagicMock()
    voice.handle = AsyncMock(return_value=(_FakeRef(ext="mp4"), _FakeTranscript(text="hi")))
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
        voice=voice,
    )
    await pipe.on_voice(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        audio_bytes=b"\x00",
        ext="mp4",
        mime="video/mp4",
    )
    voice.handle.assert_awaited_once()
    assert voice.handle.await_args.kwargs["ext"] == "mp4"
    assert voice.handle.await_args.kwargs["mime"] == "video/mp4"


@pytest.mark.asyncio
async def test_on_voice_with_caption_prepends_caption_to_text() -> None:
    sender = FakeSender()
    voice = MagicMock()
    voice.handle = AsyncMock(return_value=(_FakeRef(), _FakeTranscript(text="купил молоко")))
    runner = _make_runner()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
        voice=voice,
        classifier=_make_classifier(),
        runner=runner,
        output=_make_output(),
    )
    await pipe.on_voice(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        audio_bytes=b"\x00",
        caption="запиши в budget",
    )
    runner.run.assert_awaited_once()
    sent_text = runner.run.await_args.kwargs["text"]
    assert "запиши в budget" in sent_text
    assert "купил молоко" in sent_text


@pytest.mark.asyncio
async def test_on_voice_stt_unavailable_sends_specific_ack() -> None:
    sender = FakeSender()
    voice = MagicMock()
    voice.handle = AsyncMock(side_effect=VoiceUnavailableError("faster-whisper not installed"))
    classifier = MagicMock()
    classifier.classify = AsyncMock()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
        voice=voice,
        classifier=classifier,
    )
    await pipe.on_voice(telegram_id=1, chat_id=10, update_id=100, audio_bytes=b"\x00")
    assert sender.sends[0]["text"] == ACK_VOICE_UNAVAILABLE_RU
    classifier.classify.assert_not_called()


@pytest.mark.asyncio
async def test_on_photo_with_ingestor_stages_and_acks() -> None:
    sender = FakeSender()
    photo = MagicMock()
    photo.handle = MagicMock(return_value=_FakeRef(ext="jpg"))
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
        photo=photo,
    )
    await pipe.on_photo(
        telegram_id=1, chat_id=10, update_id=100, photo_bytes=b"\xff", mime="image/jpeg"
    )
    photo.handle.assert_called_once()
    assert sender.sends[0]["text"] == ACK_PHOTO_RU


@pytest.mark.asyncio
async def test_on_photo_without_ingestor_falls_back() -> None:
    sender = FakeSender()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
    )
    await pipe.on_photo(
        telegram_id=1, chat_id=10, update_id=100, photo_bytes=b"\xff", mime="image/jpeg"
    )
    assert sender.sends[0]["text"] == ACK_PHOTO_RU


@pytest.mark.asyncio
async def test_on_photo_full_pipeline_runs_runner_with_media() -> None:
    sender = FakeSender()
    photo = MagicMock()
    photo.handle = MagicMock(return_value=_FakeRef(ext="jpg"))
    runner = _make_runner(text="на фото — кассовый чек")
    output = _make_output()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
        photo=photo,
        classifier=_make_classifier(),
        runner=runner,
        output=output,
    )
    await pipe.on_photo(
        telegram_id=1, chat_id=10, update_id=100, photo_bytes=b"\xff\xd8", mime="image/jpeg"
    )
    runner.run.assert_awaited_once()
    media = runner.run.await_args.kwargs["media_paths"]
    assert media is not None
    assert len(media) == 1
    output.deliver.assert_awaited_once()
    assert output.deliver.await_args.kwargs["text"] == "на фото — кассовый чек"


@pytest.mark.asyncio
async def test_on_photo_uses_photo_vision_timeout() -> None:
    sender = FakeSender()
    photo = MagicMock()
    photo.handle = MagicMock(return_value=_FakeRef(ext="jpg"))
    runner = _make_runner()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
        photo=photo,
        classifier=_make_classifier(),
        runner=runner,
        output=_make_output(),
        photo_vision_timeout_s=30.0,
    )
    await pipe.on_photo(
        telegram_id=1, chat_id=10, update_id=100, photo_bytes=b"\xff\xd8", mime="image/jpeg"
    )
    runner.run.assert_awaited_once()
    assert runner.run.await_args.kwargs["timeout_s"] == 30.0


@pytest.mark.asyncio
async def test_on_photo_without_vision_timeout_passes_none() -> None:
    sender = FakeSender()
    photo = MagicMock()
    photo.handle = MagicMock(return_value=_FakeRef(ext="jpg"))
    runner = _make_runner()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
        photo=photo,
        classifier=_make_classifier(),
        runner=runner,
        output=_make_output(),
    )
    await pipe.on_photo(
        telegram_id=1, chat_id=10, update_id=100, photo_bytes=b"\xff\xd8", mime="image/jpeg"
    )
    assert runner.run.await_args.kwargs["timeout_s"] is None


@pytest.mark.asyncio
async def test_on_photo_with_caption_passes_caption_in_prompt() -> None:
    sender = FakeSender()
    photo = MagicMock()
    photo.handle = MagicMock(return_value=_FakeRef(ext="jpg"))
    runner = _make_runner()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
        photo=photo,
        classifier=_make_classifier(),
        runner=runner,
        output=_make_output(),
    )
    await pipe.on_photo(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        photo_bytes=b"\xff\xd8",
        mime="image/jpeg",
        caption="занеси расходы в budget",
    )
    runner.run.assert_awaited_once()
    assert "занеси расходы в budget" in runner.run.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_on_photo_l2_dedup_hit_sends_dedup_ack() -> None:
    from ai_steward_wiki.tg.pipeline import ACK_DEDUP_RU

    sender = FakeSender()
    photo = MagicMock()
    photo.handle = MagicMock(return_value=_FakeRef(ext="jpg"))
    runner = _make_runner()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(content_match=object()),
        confirmation=_make_confirm(),
        photo=photo,
        classifier=_make_classifier(),
        runner=runner,
        output=_make_output(),
    )
    await pipe.on_photo(
        telegram_id=1, chat_id=10, update_id=100, photo_bytes=b"\xff\xd8", mime="image/jpeg"
    )
    assert sender.sends[-1]["text"] == ACK_DEDUP_RU
    runner.run.assert_not_called()


@pytest.mark.asyncio
async def test_on_document_skips_on_l1_duplicate() -> None:
    """L1 dup short-circuits before any L2/mime processing.

    Broader on_document coverage (mime routing, L2 dedup, PII hashing) lives in
    tests/unit/tg/test_pipeline_document.py — see chunk 22 M-TG-DOCUMENT-FULL.
    """
    sender = FakeSender()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(new=False),
        confirmation=_make_confirm(),
    )
    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=b"x",
        mime="application/octet-stream",
        filename="x.bin",
    )
    assert sender.sends == []


@pytest.mark.asyncio
async def test_on_confirm_callback_delegates_to_service() -> None:
    sender = FakeSender()
    confirm = _make_confirm(status="confirmed")
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=confirm,
    )
    await pipe.on_confirm_callback(telegram_id=42, chat_id=10, pending_id=7, action="confirm")
    confirm.resolve.assert_awaited_once_with(42, 7, "confirm")
