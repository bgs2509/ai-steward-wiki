from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

from ai_steward_wiki.tg.middleware_correlation import CorrelationMiddleware


def _update(update_id: int = 7) -> Any:
    return SimpleNamespace(
        update_id=update_id,
        message=SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            chat=SimpleNamespace(id=1),
        ),
    )


@pytest.mark.asyncio
async def test_handled_event_on_success() -> None:
    mw = CorrelationMiddleware(handler_slow_threshold_ms=10_000)

    async def handler(_e: Any, _d: dict[str, Any]) -> str:
        return "ok"

    with capture_logs() as records:
        await mw(handler, _update(), {})

    handled = [r for r in records if r["event"] == "tg.update.handled"]
    assert len(handled) == 1
    assert handled[0]["failed"] is False
    assert "duration_ms" in handled[0]
    # update_id is bound via structlog contextvars (visible in real JSON logs via
    # merge_contextvars); capture_logs does not run that processor, so it is not
    # asserted here — the dedicated correlation test covers contextvar binding.
    # fast handler → no slow warning
    assert [r for r in records if r["event"] == "tg.update.handler_slow"] == []


@pytest.mark.asyncio
async def test_handled_event_on_exception_then_reraises() -> None:
    mw = CorrelationMiddleware(handler_slow_threshold_ms=10_000)

    async def handler(_e: Any, _d: dict[str, Any]) -> None:
        raise RuntimeError("boom")

    with capture_logs() as records, pytest.raises(RuntimeError):
        await mw(handler, _update(), {})

    handled = [r for r in records if r["event"] == "tg.update.handled"]
    assert len(handled) == 1
    assert handled[0]["failed"] is True


@pytest.mark.asyncio
async def test_slow_handler_warns() -> None:
    mw = CorrelationMiddleware(handler_slow_threshold_ms=0)

    async def handler(_e: Any, _d: dict[str, Any]) -> None:
        await asyncio.sleep(0.005)

    with capture_logs() as records:
        await mw(handler, _update(), {})

    slow = [r for r in records if r["event"] == "tg.update.handler_slow"]
    assert len(slow) == 1
    assert slow[0]["log_level"] == "warning"
