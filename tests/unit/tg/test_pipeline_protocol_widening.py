"""RED-first coverage for the WikiRunner/StreamingDelivery `action` widening
(aisw-xi8, DEC-5, ADR-035)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier.schema import Intent
from ai_steward_wiki.tg.pipeline import DefaultStreamingDelivery, WikiRunOutcome


class _RunnerDouble:
    """A WikiRunner double constructed WITHOUT threading `action` — proves the
    widening is backward-compatible (action defaults to None)."""

    def __init__(self) -> None:
        self.received_action: object = "not-called"

    async def run(
        self,
        *,
        text,
        owner_telegram_id,
        correlation_id,
        intent,
        on_event=None,
        media_paths=None,
        timeout_s=None,
        action=None,
    ) -> WikiRunOutcome:
        self.received_action = action
        return WikiRunOutcome(run_id="r1", text="ok", latency_ms=1)


@pytest.mark.asyncio
async def test_runner_double_without_action_kwarg_still_satisfies_protocol() -> None:
    """A caller that never passes action= (legacy shape) leaves it at the default None."""
    runner = _RunnerDouble()
    outcome = await runner.run(
        text="x", owner_telegram_id=1, correlation_id="c", intent=Intent.WIKI
    )
    assert runner.received_action is None  # default applied, not the ctor sentinel
    assert outcome.text == "ok"


@pytest.mark.asyncio
async def test_runner_double_receives_explicit_action() -> None:
    runner = _RunnerDouble()
    await runner.run(
        text="x", owner_telegram_id=1, correlation_id="c", intent=Intent.WIKI, action="query"
    )
    assert runner.received_action == "query"


@pytest.mark.asyncio
async def test_default_streaming_delivery_threads_action_to_runner() -> None:
    runner = MagicMock()
    runner.run = AsyncMock(return_value=WikiRunOutcome(run_id="r1", text="ok", latency_ms=1))
    output = MagicMock()
    output.deliver = AsyncMock(return_value=None)
    sender = MagicMock()
    sender.send_message = AsyncMock(return_value=MagicMock(message_id=1))

    delivery = DefaultStreamingDelivery(sender=sender, timeout_s=5.0)
    await delivery.run_and_deliver(
        runner=runner,
        output=output,
        chat_id=1,
        telegram_id=1,
        text="x",
        intent=Intent.WIKI,
        correlation_id="c",
        action="query",
    )
    runner.run.assert_awaited_once()
    assert runner.run.await_args.kwargs["action"] == "query"


@pytest.mark.asyncio
async def test_default_streaming_delivery_action_defaults_to_none() -> None:
    """Back-compat: a caller that omits action= (e.g. the pre-aisw-xi8 test suite
    shape) still works — the runner receives action=None."""
    runner = MagicMock()
    runner.run = AsyncMock(return_value=WikiRunOutcome(run_id="r1", text="ok", latency_ms=1))
    output = MagicMock()
    output.deliver = AsyncMock(return_value=None)
    sender = MagicMock()

    delivery = DefaultStreamingDelivery(sender=sender, timeout_s=5.0)
    await delivery.run_and_deliver(
        runner=runner,
        output=output,
        chat_id=1,
        telegram_id=1,
        text="x",
        intent=Intent.WEB,
        correlation_id="c",
    )
    assert runner.run.await_args.kwargs["action"] is None
