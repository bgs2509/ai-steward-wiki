"""Integration test for ClaudeCliBackend against the real `claude` binary.

Gated by:
  - RUN_INTEGRATION=1 environment variable (unified gate after chunk-23)
  - presence of the `claude` binary on PATH
  - presence of CLAUDE_CONFIG_DIR with a usable subscription session

Nightly-only per chunk 5 acceptance.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from ai_steward_wiki.classifier import ClaudeCliBackend, PromptCache, classify

REPO_ROOT = Path(__file__).resolve().parents[3]
PROMPT = REPO_ROOT / "prompts" / "classifier.md"

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION") != "1",
        reason="set RUN_INTEGRATION=1 to enable",
    ),
    pytest.mark.skipif(shutil.which("claude") is None, reason="`claude` binary not on PATH"),
    pytest.mark.skipif(
        os.environ.get("CLAUDECODE") == "1",
        reason="recursive claude invocation (CLAUDECODE=1) — run outside Claude Code",
    ),
]


async def test_real_cli_returns_valid_result() -> None:
    cfg_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    backend = ClaudeCliBackend(claude_config_dir=cfg_dir, timeout_s=60.0)
    res = await classify(
        "напомни мне завтра в 9 утра позвонить маме",
        correlation_id="integ-1",
        backend=backend,
        prompt_path=PROMPT,
        cache=PromptCache(),
    )
    assert res.intent.value in {"reminder", "unknown"}
    assert 0.0 <= res.confidence <= 1.0
    assert res.backend == "claude_cli"
