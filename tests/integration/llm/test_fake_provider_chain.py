from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ai_steward_wiki.classifier.backend import ClaudeCliBackend, FailoverClassifierBackend
from ai_steward_wiki.llm.codex import CodexCliAdapter
from ai_steward_wiki.llm.failover import FailoverPolicy, ProviderState

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="set RUN_INTEGRATION=1 for fake provider subprocess tests",
)

_FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json
from pathlib import Path
import sys

trace_path = Path(sys.argv[0]).with_suffix(".trace")
with trace_path.open("a", encoding="utf-8") as trace:
    trace.write(json.dumps({"argv": sys.argv[1:]}) + "\n")

print(json.dumps({
    "type": "result",
    "subtype": "error",
    "is_error": True,
    "api_error_status": 429,
    "result": "subscription limit reached",
}))
raise SystemExit(1)
"""

_FAKE_CODEX = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
trace_path = Path(sys.argv[0]).with_suffix(".trace")
with trace_path.open("a", encoding="utf-8") as trace:
    trace.write(json.dumps({
        "argv": args,
        "env_keys": sorted(os.environ),
    }) + "\n")

if "--output-schema" not in args:
    raise SystemExit(2)
schema_path = Path(args[args.index("--output-schema") + 1])
json.loads(schema_path.read_text(encoding="utf-8"))
print(json.dumps({"intent": "WIKI_QUERY", "confidence": 1.0}))
"""


def _make_executable(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path


async def test_typed_claude_limit_runs_one_codex_fallback(tmp_path: Path) -> None:
    claude = _make_executable(tmp_path / "claude", _FAKE_CLAUDE)
    codex_binary = _make_executable(tmp_path / "codex", _FAKE_CODEX)
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    runtime_dir = tmp_path / "runtime"
    claude_home.mkdir()
    codex_home.mkdir()
    runtime_dir.mkdir()
    prompt_path = tmp_path / "classifier.md"
    prompt_path.write_text("Return one classification object.", encoding="utf-8")

    policy = FailoverPolicy(cooldown_s=900.0)
    codex = CodexCliAdapter(
        binary=str(codex_binary),
        expected_version="0.142.5",
        codex_home=codex_home,
        neutral_cwd=runtime_dir,
        light_model="gpt-5.4-mini",
        light_reasoning="low",
        complex_model="gpt-5.5",
        complex_reasoning="medium",
    )
    backend = FailoverClassifierBackend(
        primary=ClaudeCliBackend(
            claude_config_dir=claude_home,
            binary=str(claude),
        ),
        codex=codex,
        policy=policy,
        timeout_s=5.0,
    )

    result = await backend.call(
        text="synthetic query",
        prompt_path=prompt_path,
        correlation_id="fake-provider-chain",
    )

    assert result == {"intent": "WIKI_QUERY", "confidence": 1.0}
    assert policy.state is ProviderState.CODEX
    assert len(claude.with_suffix(".trace").read_text(encoding="utf-8").splitlines()) == 1
    codex_trace = codex_binary.with_suffix(".trace").read_text(encoding="utf-8").splitlines()
    assert len(codex_trace) == 1
    invocation = json.loads(codex_trace[0])
    assert invocation["argv"][invocation["argv"].index("--model") + 1] == "gpt-5.4-mini"
    assert "OPENAI_API_KEY" not in invocation["env_keys"]
