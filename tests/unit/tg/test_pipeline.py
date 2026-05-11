"""Unit tests for tg.pipeline.DefaultPipeline (M-TG-HANDLERS-WIRING)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.tg.pipeline import (
    ACK_DOC_RU,
    ACK_PHOTO_RU,
    ACK_TEXT_RU,
    ACK_VOICE_RU,
    DefaultPipeline,
)
from tests.unit.tg.conftest import FakeSender


@dataclass
class _FakeRef:
    sha256: str = "deadbeef" * 8
    ext: str = "jpg"


@dataclass
class _FakeTranscript:
    text: str = "привет мир"
    lang: str = "ru"
    duration_s: float = 1.0
    model: str = "fake"
    rtf: float = 0.1


def _make_idem(new: bool = True) -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=new)
    return idem


def _make_confirm(status: str | None = "confirmed") -> MagicMock:
    svc = MagicMock()
    svc.resolve = AsyncMock(return_value=status)
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
async def test_on_document_logs_and_acks() -> None:
    sender = FakeSender()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
    )
    await pipe.on_document(
        telegram_id=1,
        chat_id=10,
        update_id=100,
        doc_bytes=b"hello",
        mime="text/plain",
        filename="x.txt",
    )
    assert sender.sends[0]["text"] == ACK_DOC_RU


@pytest.mark.asyncio
async def test_on_document_skips_on_l1_duplicate() -> None:
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
