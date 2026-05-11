"""Integration test for chunk-20 M-TG-PIPELINE-CLASSIFIER end-to-end wiring.

Uses the real Claude CLI classifier backend with a fake runner+output to verify
the DefaultPipeline composition reaches the runner with a valid Intent within
the agreed latency budget. Gated by RUN_CLAUDE_CLI_INTEGRATION=1 because real
`claude` CLI invocation is environment-sensitive.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier import (
    ClaudeCliBackend,
    PromptCache,
)
from ai_steward_wiki.classifier.schema import ClassifierResult, Intent
from ai_steward_wiki.tg.pipeline import DefaultPipeline, WikiRunOutcome


class FakeSender:
    def __init__(self) -> None:
        self.sends: list[dict[str, object]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: object) -> object:
        self.sends.append({"chat_id": chat_id, "text": text, **kwargs})

        class _M:
            message_id = 1001

        return _M()

    async def edit_message_text(self, *args: object, **kwargs: object) -> None:
        return None

    async def send_document(self, *args: object, **kwargs: object) -> None:
        return None


REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT = REPO_ROOT / "prompts" / "classifier.md"

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_CLAUDE_CLI_INTEGRATION") != "1",
        reason="set RUN_CLAUDE_CLI_INTEGRATION=1 to enable",
    ),
    pytest.mark.skipif(shutil.which("claude") is None, reason="`claude` binary not on PATH"),
]


class _RealClassifierAdapter:
    def __init__(self) -> None:
        cfg_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
        self._backend = ClaudeCliBackend(claude_config_dir=cfg_dir, timeout_s=60.0)
        self._cache = PromptCache()

    async def classify(self, text: str, *, correlation_id: str) -> ClassifierResult:
        from ai_steward_wiki.classifier import classify

        return await classify(
            text,
            correlation_id=correlation_id,
            backend=self._backend,
            prompt_path=PROMPT,
            cache=self._cache,
        )


@pytest.mark.asyncio
async def test_pipeline_runs_real_classifier_then_fake_runner() -> None:
    sender = FakeSender()

    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=True)
    idem.check_content = AsyncMock(return_value=("a" * 64, None))
    idem.record_dedup_choice = AsyncMock(return_value=None)

    confirm = MagicMock()
    confirm.resolve = AsyncMock(return_value="confirmed")

    runner = MagicMock()
    runner.run = AsyncMock(return_value=WikiRunOutcome(run_id="r1", text="ответ", latency_ms=10))

    output = MagicMock()
    output.deliver = AsyncMock(return_value=None)

    pipe = DefaultPipeline(
        sender=sender,
        idempotency=idem,
        confirmation=confirm,
        classifier=_RealClassifierAdapter(),
        runner=runner,
        output=output,
    )

    await pipe.on_text(
        telegram_id=42,
        chat_id=10,
        update_id=1,
        text="напомни мне завтра в 9 утра позвонить маме",
    )

    runner.run.assert_awaited_once()
    assert isinstance(runner.run.await_args.kwargs["intent"], Intent)
    output.deliver.assert_awaited_once()
