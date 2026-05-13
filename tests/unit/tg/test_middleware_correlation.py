from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs

from ai_steward_wiki.logging_setup import get_correlation_id
from ai_steward_wiki.tg.middleware_correlation import CorrelationMiddleware


def _fake_update(*, update_id: int, user_id: int | None, chat_id: int | None) -> Any:
    msg = None
    if user_id is not None or chat_id is not None:
        msg = SimpleNamespace(
            from_user=SimpleNamespace(id=user_id) if user_id is not None else None,
            chat=SimpleNamespace(id=chat_id) if chat_id is not None else None,
        )
    return SimpleNamespace(update_id=update_id, message=msg)


@pytest.mark.asyncio
async def test_correlation_middleware_binds_fields_and_emits_received() -> None:
    mw = CorrelationMiddleware()
    seen_ctx: dict[str, object] = {}
    seen_legacy: list[str | None] = []

    async def handler(event: Any, data: dict[str, Any]) -> str:
        seen_ctx.update(structlog.contextvars.get_contextvars())
        seen_legacy.append(get_correlation_id())
        return "ok"

    update = _fake_update(update_id=42, user_id=7, chat_id=99)

    with capture_logs() as records:
        result = await mw(handler, update, {})

    assert result == "ok"
    # tg.update.received emitted exactly once
    events = [r["event"] for r in records]
    assert "tg.update.received" in events
    # bound_contextvars carried correlation_id + identity into handler
    cid = seen_ctx.get("correlation_id")
    assert isinstance(cid, str)
    assert len(cid) > 0
    assert seen_ctx.get("update_id") == 42
    assert seen_ctx.get("telegram_id") == 7
    assert seen_ctx.get("chat_id") == 99
    # legacy ContextVar agrees (R-1 dual-binding mitigation)
    assert seen_legacy == [cid]
    # both binding paths cleared on exit
    assert get_correlation_id() is None
    assert "correlation_id" not in structlog.contextvars.get_contextvars()


@pytest.mark.asyncio
async def test_correlation_middleware_clears_on_handler_exception() -> None:
    mw = CorrelationMiddleware()

    async def handler(event: Any, data: dict[str, Any]) -> str:
        raise RuntimeError("boom")

    update = _fake_update(update_id=1, user_id=None, chat_id=None)

    with pytest.raises(RuntimeError):
        await mw(handler, update, {})

    assert get_correlation_id() is None
    assert "correlation_id" not in structlog.contextvars.get_contextvars()


@pytest.mark.asyncio
async def test_correlation_middleware_handles_missing_message() -> None:
    """Update without message (e.g. callback_query, edited_message, etc.) — telegram_id/chat_id None."""
    mw = CorrelationMiddleware()
    seen_ctx: dict[str, object] = {}

    async def handler(event: Any, data: dict[str, Any]) -> None:
        seen_ctx.update(structlog.contextvars.get_contextvars())

    update = _fake_update(update_id=5, user_id=None, chat_id=None)
    await mw(handler, update, {})

    assert seen_ctx.get("update_id") == 5
    assert seen_ctx.get("telegram_id") is None
    assert seen_ctx.get("chat_id") is None
