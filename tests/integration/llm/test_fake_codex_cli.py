from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ai_steward_wiki.llm.codex import (
    CodexCliAdapter,
    CodexRequest,
    CodexRunKind,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="set RUN_INTEGRATION=1 for fake CLI subprocess tests",
)

_FAKE_CODEX = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
trace_path = Path(sys.argv[0]).with_suffix(".trace")
with trace_path.open("a", encoding="utf-8") as trace:
    trace.write(json.dumps({
        "argv": args,
        "env_keys": sorted(os.environ),
        "stdin": sys.stdin.read(),
    }) + "\n")

if args == ["--version"]:
    print("codex-cli 0.142.5")
elif args == ["login", "status"]:
    print("Logged in using ChatGPT")
elif args == ["exec", "--help"]:
    print("--ephemeral --ignore-user-config --ignore-rules --strict-config --json "
          "--output-schema --sandbox --model --cd --add-dir")
elif "--json" in args:
    print(json.dumps({
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "agent answer"},
    }))
    print(json.dumps({
        "type": "turn.completed",
        "usage": {"input_tokens": 2, "output_tokens": 3},
    }))
elif "--output-schema" in args:
    schema_path = Path(args[args.index("--output-schema") + 1])
    json.loads(schema_path.read_text(encoding="utf-8"))
    print(json.dumps({"intent": "WIKI_QUERY", "confidence": 1.0}))
else:
    print("text answer")
"""


def _make_adapter(tmp_path: Path) -> tuple[CodexCliAdapter, Path]:
    binary = tmp_path / "codex"
    binary.write_text(_FAKE_CODEX, encoding="utf-8")
    binary.chmod(0o755)
    codex_home = tmp_path / "codex-home"
    runtime = tmp_path / "runtime"
    codex_home.mkdir()
    runtime.mkdir()
    return (
        CodexCliAdapter(
            binary=str(binary),
            expected_version="0.142.5",
            codex_home=codex_home,
            neutral_cwd=runtime,
            light_model="gpt-5.4-mini",
            light_reasoning="low",
            complex_model="gpt-5.5",
            complex_reasoning="medium",
        ),
        binary.with_suffix(".trace"),
    )


async def test_fake_cli_readiness_and_all_output_modes(tmp_path: Path) -> None:
    adapter, trace_path = _make_adapter(tmp_path)

    readiness = await adapter.check_readiness()
    structured = await adapter.run_structured(
        CodexRequest(
            prompt="classify",
            model=adapter.light_model,
            reasoning=adapter.light_reasoning,
            run_kind=CodexRunKind.STRUCTURED,
            correlation_id="integration-structured",
            timeout_s=5.0,
            cwd=adapter.neutral_cwd,
            output_schema={"type": "object"},
        )
    )
    text = await adapter.run_text(
        CodexRequest(
            prompt="summarize",
            model=adapter.complex_model,
            reasoning=adapter.complex_reasoning,
            run_kind=CodexRunKind.TEXT,
            correlation_id="integration-text",
            timeout_s=5.0,
            cwd=adapter.neutral_cwd,
        )
    )
    selected_wiki = tmp_path / "selected-WIKI"
    selected_wiki.mkdir()
    other_wiki = tmp_path / "other-WIKI"
    other_wiki.mkdir()
    events = await adapter.run_agent(
        CodexRequest(
            prompt=f"WORKSPACE_ROOT={selected_wiki}\nREAD_ROOT={other_wiki}",
            model=adapter.complex_model,
            reasoning=adapter.complex_reasoning,
            run_kind=CodexRunKind.AGENT_WRITE,
            correlation_id="integration-agent",
            timeout_s=5.0,
            cwd=selected_wiki,
            writable_wiki=selected_wiki,
            readable_paths=(other_wiki,),
        )
    )

    assert readiness.ready is True
    assert structured == {"intent": "WIKI_QUERY", "confidence": 1.0}
    assert text == "text answer"
    assert [event.type for event in events] == ["assistant_chunk", "final"]

    traces = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    invocation = traces[-1]
    argv = invocation["argv"]
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert argv[argv.index("--cd") + 1] == str(selected_wiki)
    assert "--add-dir" not in argv
    assert str(other_wiki) not in argv
    assert "OPENAI_API_KEY" not in invocation["env_keys"]
    assert "CODEX_HOME" in invocation["env_keys"]
