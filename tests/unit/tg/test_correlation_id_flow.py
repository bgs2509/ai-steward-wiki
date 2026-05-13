"""End-to-end test: correlation_id flows through CorrelationMiddleware into a
downstream @traced function. No real TG, no real Claude — pure middleware +
decorator wiring."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import structlog

from ai_steward_wiki.logging_setup import traced
from ai_steward_wiki.tg.middleware_correlation import CorrelationMiddleware


@traced(event_prefix="test.downstream")
async def downstream(captured: list[dict[str, Any]]) -> None:
    captured.append(dict(structlog.contextvars.get_contextvars()))


@pytest.mark.asyncio
async def test_correlation_id_flows_through_middleware_into_traced() -> None:
    mw = CorrelationMiddleware()
    captured: list[dict[str, Any]] = []

    async def handler(event: Any, data: dict[str, Any]) -> str:
        await downstream(captured)
        return "ok"

    update = SimpleNamespace(
        update_id=1,
        message=SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            chat=SimpleNamespace(id=42),
        ),
    )

    await mw(handler, update, {})

    assert len(captured) == 1
    ctx = captured[0]
    assert isinstance(ctx.get("correlation_id"), str)
    assert ctx["update_id"] == 1
    assert ctx["telegram_id"] == 42
    assert ctx["chat_id"] == 42
