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
        self.calls: list[list[str]] = []

    async def spawn(self, argv, *, env, stdin, timeout_s):
        self.calls.append(list(argv))
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


async def test_unwraps_inner_classifier_json() -> None:
    inner = {"intent": "reminder", "confidence": 0.92, "distilled_payload": {"foo": "bar"}}
    spawner = _StubSpawner(stdout=_envelope(json.dumps(inner)))
    backend = _make_backend(spawner)
    out = await backend.call(text="напомни завтра", prompt_path=Path("p"), correlation_id="c")
    assert out == inner


async def test_strips_code_fences() -> None:
    inner = {"intent": "wiki_query", "confidence": 0.7, "distilled_payload": {}}
    fenced = f"```json\n{json.dumps(inner)}\n```"
    spawner = _StubSpawner(stdout=_envelope(fenced))
    backend = _make_backend(spawner)
    out = await backend.call(text="t", prompt_path=Path("p"), correlation_id="c")
    assert out == inner


async def test_conversational_text_raises_schema_error() -> None:
    """Real failure shape captured 2026-05-11 — model defaulted to assistant persona."""
    spawner = _StubSpawner(stdout=_envelope("Привет! 👋 Я могу помочь тебе с проектом."))
    backend = _make_backend(spawner)
    with pytest.raises(ClassifierSchemaError, match="inner JSON parse failed"):
        await backend.call(text="Привет", prompt_path=Path("p"), correlation_id="c")


async def test_error_envelope_raises() -> None:
    spawner = _StubSpawner(
        stdout=json.dumps(
            {"type": "result", "subtype": "error", "is_error": True, "api_error_status": 500}
        ).encode("utf-8")
    )
    backend = _make_backend(spawner)
    with pytest.raises(ClassifierSchemaError, match="not success"):
        await backend.call(text="t", prompt_path=Path("p"), correlation_id="c")


async def test_uses_append_system_prompt_file_flag() -> None:
    inner = {"intent": "unknown", "confidence": 0.5, "distilled_payload": {}}
    spawner = _StubSpawner(stdout=_envelope(json.dumps(inner)))
    backend = _make_backend(spawner)
    prompt_path = Path("/tmp/classifier.md")
    await backend.call(text="t", prompt_path=prompt_path, correlation_id="c")
    argv = spawner.calls[0]
    assert "--append-system-prompt-file" in argv
    idx = argv.index("--append-system-prompt-file")
    assert argv[idx + 1] == str(prompt_path)
    assert "--append-system-prompt" not in argv
    assert f"@{prompt_path}" not in argv
