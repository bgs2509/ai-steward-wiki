from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier.schema import ClassifierResult, Intent
from ai_steward_wiki.llm.failover import ProvidersUnavailableError
from ai_steward_wiki.tg.pipeline import DefaultPipeline, WikiRunOutcome
from tests.unit.tg.conftest import FakeSender

_PROVIDERS_UNAVAILABLE_RU = (
    "Claude и Codex сейчас недоступны. " "Исходное сообщение сохранено — повторите позже."
)


def _idempotency() -> MagicMock:
    service = MagicMock()
    service.check_update_id = AsyncMock(return_value=True)
    service.check_content = AsyncMock(return_value=("ab" * 32, None))
    return service


def _classifier() -> MagicMock:
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=ClassifierResult(
            intent=Intent.WIKI_QUERY,
            confidence=0.9,
            distilled_payload={},
            backend="fake",
            model="fake",
            prompt_semver="1.0.0",
            prompt_sha256="a" * 64,
            latency_ms=1,
        )
    )
    return classifier


def _runner() -> MagicMock:
    runner = MagicMock()
    runner.run = AsyncMock(return_value=WikiRunOutcome(run_id="run", text="ok", latency_ms=1))
    return runner


def _output() -> MagicMock:
    output = MagicMock()
    output.deliver = AsyncMock(return_value=None)
    return output


@pytest.mark.parametrize("failure_at", ["classifier", "runner"])
async def test_dual_provider_failure_sends_one_recoverable_message(
    failure_at: str,
) -> None:
    sender = FakeSender()
    classifier = _classifier()
    runner = _runner()
    error = ProvidersUnavailableError(
        RuntimeError("claude limit"),
        RuntimeError("codex unavailable"),
    )
    if failure_at == "classifier":
        classifier.classify.side_effect = error
    else:
        runner.run.side_effect = error
    pipeline = DefaultPipeline(
        sender=sender,
        idempotency=_idempotency(),
        confirmation=MagicMock(),
        classifier=classifier,
        runner=runner,
        output=_output(),
    )

    await pipeline.on_text(
        telegram_id=1,
        chat_id=2,
        update_id=3,
        text="source remains in Telegram",
    )

    assert [item["text"] for item in sender.sends] == [_PROVIDERS_UNAVAILABLE_RU]
