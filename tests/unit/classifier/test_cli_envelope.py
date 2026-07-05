"""Regression tests for Claude CLI envelope unwrapping in ClaudeCliBackend.

bd_id: aisw-p5b — backend was previously returning the raw CLI envelope as the
classifier payload, which produced 20 pydantic ValidationErrors on every message.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_steward_wiki.classifier.backend import ClaudeCliBackend, Spawner
from ai_steward_wiki.classifier.schema import ClassifierSchemaError


class _StubSpawner:
    def __init__(self, stdout: bytes, rc: int = 0, stderr: bytes = b"") -> None:
        self._rc = rc
        self._stdout = stdout
        self._stderr = stderr
        self.calls: list[dict] = []

    async def spawn(self, argv, *, env, stdin, timeout_s, cwd=None):
        self.calls.append({"argv": list(argv), "cwd": cwd, "env": dict(env)})
        return self._rc, self._stdout, self._stderr


def _make_backend(spawner: Spawner) -> ClaudeCliBackend:
    return ClaudeCliBackend(
        claude_config_dir=Path("/tmp/fake-claude-config"),
        binary="claude",
        spawner=spawner,
    )


def _envelope(result_text: str, *, subtype: str = "success", is_error: bool = False) -> bytes:
    return json.dumps(
        {
            "type": "result",
            "subtype": subtype,
            "is_error": is_error,
            "result": result_text,
            "session_id": "s-1",
            "usage": {"input_tokens": 10},
            "stop_reason": "end_turn",
        }
    ).encode("utf-8")


@pytest.fixture
def prompt_file(tmp_path: Path) -> Path:
    p = tmp_path / "classifier.md"
    p.write_text("CLASSIFIER PROMPT\n", encoding="utf-8")
    return p


async def test_unwraps_inner_classifier_json(prompt_file: Path) -> None:
    inner = {"intent": "reminder", "confidence": 0.92, "distilled_payload": {"foo": "bar"}}
    spawner = _StubSpawner(stdout=_envelope(json.dumps(inner)))
    backend = _make_backend(spawner)
    out = await backend.call(text="напомни завтра", prompt_path=prompt_file, correlation_id="c")
    assert out == inner


async def test_strips_code_fences(prompt_file: Path) -> None:
    inner = {"intent": "wiki_query", "confidence": 0.7, "distilled_payload": {}}
    fenced = f"```json\n{json.dumps(inner)}\n```"
    spawner = _StubSpawner(stdout=_envelope(fenced))
    backend = _make_backend(spawner)
    out = await backend.call(text="t", prompt_path=prompt_file, correlation_id="c")
    assert out == inner


async def test_conversational_text_raises_schema_error(prompt_file: Path) -> None:
    """Real failure shape captured 2026-05-11 — model defaulted to assistant persona."""
    spawner = _StubSpawner(stdout=_envelope("Привет! 👋 Я могу помочь тебе с проектом."))
    backend = _make_backend(spawner)
    with pytest.raises(ClassifierSchemaError, match="inner JSON parse failed"):
        await backend.call(text="Привет", prompt_path=prompt_file, correlation_id="c")


async def test_error_envelope_raises(prompt_file: Path) -> None:
    spawner = _StubSpawner(
        stdout=json.dumps(
            {"type": "result", "subtype": "error", "is_error": True, "api_error_status": 500}
        ).encode("utf-8")
    )
    backend = _make_backend(spawner)
    with pytest.raises(ClassifierSchemaError, match="not success"):
        await backend.call(text="t", prompt_path=prompt_file, correlation_id="c")


async def test_inlines_system_prompt_and_neutral_cwd(prompt_file: Path) -> None:
    """System prompt content must be inlined via `--system-prompt` (not `--system-prompt-file`
    — the file form does NOT replace the default Claude Code system prompt under subscription
    auth; verified 2026-05-12, bd aisw-adj). cwd must be neutral so project CLAUDE.md is not
    auto-discovered."""
    inner = {"intent": "unknown", "confidence": 0.5, "distilled_payload": {}}
    spawner = _StubSpawner(stdout=_envelope(json.dumps(inner)))
    backend = _make_backend(spawner)
    await backend.call(text="t", prompt_path=prompt_file, correlation_id="c")
    call = spawner.calls[0]
    argv = call["argv"]
    assert "--system-prompt" in argv
    idx = argv.index("--system-prompt")
    assert argv[idx + 1] == "CLASSIFIER PROMPT\n"
    assert "--system-prompt-file" not in argv
    assert "--append-system-prompt" not in argv
    assert "--append-system-prompt-file" not in argv
    assert f"@{prompt_file}" not in argv
    assert call["cwd"] == "/tmp/fake-claude-config"
    # aisw-0mg: subscription OAuth requires -p + isolation to suppress
    # default Claude Code persona (cache_creation_input_tokens → 0).
    assert "-p" in argv
    ss = argv.index("--setting-sources")
    assert argv[ss + 1] == ""
    assert "--disable-slash-commands" in argv
    t = argv.index("--tools")
    assert argv[t + 1] == ""
    # old long --disallowedTools list replaced by --tools "".
    assert "--disallowedTools" not in argv


# --- aisw-xi8: thinking-off env knob + fence-with-prose robustness ---------


async def test_fence_with_trailing_prose_unwraps(prompt_file: Path) -> None:
    """aisw-xi8: with thinking disabled the model sometimes appends explanatory
    prose AFTER the fenced JSON (observed live on «Через 20 минут»). The old
    anchored fence regex choked on it; unwrapping must SEARCH for the fence
    (same failure shape schema.unwrap_fenced_json already solved — aisw-7j3)."""
    inner = {"intent": "unknown", "confidence": 0.85, "distilled_payload": {}}
    reply = (
        f"```json\n{json.dumps(inner)}\n```\n\n"
        "The message is a bare time expression with no actionable intent attached."
    )
    spawner = _StubSpawner(stdout=_envelope(reply))
    backend = _make_backend(spawner)
    out = await backend.call(text="Через 20 минут", prompt_path=prompt_file, correlation_id="c")
    assert out == inner


async def test_max_thinking_tokens_env_when_set(prompt_file: Path) -> None:
    """aisw-xi8: Stage-0 classification is recognition, not computation — the
    backend must pass MAX_THINKING_TOKENS to the CLI when the field is set."""
    inner = {"intent": "chat", "confidence": 0.99, "distilled_payload": {}}
    spawner = _StubSpawner(stdout=_envelope(json.dumps(inner)))
    backend = ClaudeCliBackend(
        claude_config_dir=Path("/tmp/fake-claude-config"),
        binary="claude",
        spawner=spawner,
        max_thinking_tokens=0,
    )
    await backend.call(text="привет", prompt_path=prompt_file, correlation_id="c")
    assert spawner.calls[0]["env"]["MAX_THINKING_TOKENS"] == "0"


async def test_max_thinking_tokens_env_absent_by_default(prompt_file: Path) -> None:
    """Default None → env untouched (time-parse fallback NEEDS thinking for date
    arithmetic — verified live: «за месяц до 25 июня следующего года» degrades
    to a past-year date without it)."""
    inner = {"intent": "chat", "confidence": 0.99, "distilled_payload": {}}
    spawner = _StubSpawner(stdout=_envelope(json.dumps(inner)))
    backend = _make_backend(spawner)
    await backend.call(text="привет", prompt_path=prompt_file, correlation_id="c")
    assert "MAX_THINKING_TOKENS" not in spawner.calls[0]["env"]
