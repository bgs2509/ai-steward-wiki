from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
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
