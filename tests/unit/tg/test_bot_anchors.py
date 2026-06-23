from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest
from structlog.testing import capture_logs

from ai_steward_wiki.tg.bot import AiogramSender


class _FakeBot:
    def __init__(self, *, delay: float = 0.0, raises: bool = False) -> None:
        self._delay = delay
        self._raises = raises

    async def send_message(self, **_kw: Any) -> Any:
        await asyncio.sleep(self._delay)
        if self._raises:
            raise RuntimeError("tg down")
        return SimpleNamespace(message_id=1)


class _ParseFailBot:
    """Raises TelegramBadRequest with the given message on the first send only."""

    def __init__(self, message: str) -> None:
        self._message = message
        self.calls: list[dict[str, Any]] = []

    async def send_message(self, **kw: Any) -> Any:
        self.calls.append(kw)
        if len(self.calls) == 1:
            raise TelegramBadRequest(method=None, message=self._message)  # type: ignore[arg-type]
        return SimpleNamespace(message_id=2)


@pytest.mark.asyncio
async def test_send_message_parse_fallback_retries_plain() -> None:
    bot = _ParseFailBot("Bad Request: can't parse entities: Unsupported start tag")
    sender = AiogramSender(cast_bot(bot), io_slow_threshold_ms=10_000)
    with capture_logs() as records:
        await sender.send_message(1, "АД <120/80")  # returns despite first failure
    assert len(bot.calls) == 2
    assert bot.calls[0]["parse_mode"] == "HTML"
    assert bot.calls[1]["parse_mode"] is None  # retry as plain text
    assert bot.calls[1]["text"] == "АД <120/80"  # same text, just unparsed mode
    assert any(r["event"] == "tg.io.send_message.parse_fallback" for r in records)
    # not logged as a hard error — the reply WAS delivered
    assert [r for r in records if r["event"] == "tg.io.send_message.error"] == []


@pytest.mark.asyncio
async def test_send_message_other_badrequest_reraised_no_retry() -> None:
    bot = _ParseFailBot("Bad Request: chat not found")
    sender = AiogramSender(cast_bot(bot), io_slow_threshold_ms=10_000)
    with capture_logs() as records, pytest.raises(TelegramBadRequest):
        await sender.send_message(1, "hi")
    assert len(bot.calls) == 1  # no retry for non-parse errors
    assert [r for r in records if r["event"] == "tg.io.send_message.parse_fallback"] == []


@pytest.mark.asyncio
async def test_send_message_slow_logs_anchor() -> None:
    sender = AiogramSender(cast_bot(_FakeBot(delay=0.005)), io_slow_threshold_ms=0)
    with capture_logs() as records:
        await sender.send_message(1, "hi")
    slow = [r for r in records if r["event"] == "tg.io.send_message.slow"]
    assert len(slow) == 1
    assert "duration_ms" in slow[0]


@pytest.mark.asyncio
async def test_send_message_fast_is_silent() -> None:
    sender = AiogramSender(cast_bot(_FakeBot(delay=0.0)), io_slow_threshold_ms=10_000)
    with capture_logs() as records:
        await sender.send_message(1, "hi")
    assert [r for r in records if r["event"].startswith("tg.io.send_message")] == []


@pytest.mark.asyncio
async def test_send_message_error_logs_and_reraises() -> None:
    sender = AiogramSender(cast_bot(_FakeBot(raises=True)), io_slow_threshold_ms=0)
    with capture_logs() as records, pytest.raises(RuntimeError):
        await sender.send_message(1, "hi")
    err = [r for r in records if r["event"] == "tg.io.send_message.error"]
    assert len(err) == 1


def cast_bot(fake: _FakeBot) -> Any:
    """AiogramSender only ever calls the methods we stubbed; typing bridge for tests."""
    return fake
