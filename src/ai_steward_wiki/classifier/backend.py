# FILE: src/ai_steward_wiki/classifier/backend.py
# VERSION: 0.0.4
# START_MODULE_CONTRACT
#   PURPOSE: Backend abstraction for Stage-0 classifier — Claude CLI default + optional API + Fake.
#   SCOPE: ClassifierBackend Protocol; ClaudeCliBackend (subprocess); AnthropicApiBackend stub;
#          FakeClaudeRunner test double; Spawner Protocol seam for chunk 16 systemd-run wrap.
#   DEPENDS: asyncio, json, ai_steward_wiki.classifier.schema,
#            ai_steward_wiki.claude_cli.common (M-CLAUDE-CLI-COMMON)
#   LINKS: M-CLASSIFIER-STAGE0, M-CLAUDE-CLI-COMMON, D-009, D-013, INV-6, aisw-d3i
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ClassifierBackend - Protocol; async call(text, prompt_path, correlation_id) -> dict
#   Spawner - Protocol; subprocess spawn primitive (chunk 16 injects systemd-run prefix)
#   AsyncioSpawner - default Spawner using asyncio.create_subprocess_exec
#   ClaudeCliBackend - default backend invoking `claude` CLI in JSON mode (resolves binary)
#   AnthropicApiBackend - optional backend; activated only when STAGE0_BACKEND=anthropic_api
#   FakeClaudeRunner - deterministic test double, records calls
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.4 - aisw-d3i: import shared invocation primitives from
#                         M-CLAUDE-CLI-COMMON (resolve_binary, build_env,
#                         neutral_cwd, system_prompt_argv, truncate_stderr).
#                         No behaviour change; eliminates duplication with
#                         Stage-1 wiki runner.
#   PREVIOUS:    v0.0.3 - replace default system prompt via --system-prompt-file
#                         and run CLI in neutral cwd (fix aisw-p5b).
#   PREVIOUS:    v0.0.2 - unwrap Claude CLI envelope; switch flag form.
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ai_steward_wiki.classifier.schema import (
    ClassifierError,
    ClassifierSchemaError,
    ClassifierTimeoutError,
)
from ai_steward_wiki.claude_cli.common import (
    build_env,
    neutral_cwd,
    resolve_binary,
    system_prompt_argv,
    truncate_stderr,
)

__all__ = [
    "AnthropicApiBackend",
    "AsyncioSpawner",
    "ClassifierBackend",
    "ClaudeCliBackend",
    "FakeClaudeRunner",
    "Spawner",
]


@runtime_checkable
class ClassifierBackend(Protocol):
    name: str
    model: str

    async def call(
        self, *, text: str, prompt_path: Path, correlation_id: str
    ) -> dict[str, Any]: ...


class Spawner(Protocol):
    async def spawn(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        stdin: bytes,
        timeout_s: float,
        cwd: str | None = None,
    ) -> tuple[int, bytes, bytes]: ...


class AsyncioSpawner:
    """Default Spawner using asyncio.create_subprocess_exec.

    Chunk 16 wraps argv with `systemd-run --scope --uid=aisw-stage0 --` prefix without
    touching this module — inject a different Spawner via ClaudeCliBackend(spawner=...).
    """

    async def spawn(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        stdin: bytes,
        timeout_s: float,
        cwd: str | None = None,
    ) -> tuple[int, bytes, bytes]:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout_s)
        except TimeoutError as e:
            proc.kill()
            await proc.wait()
            raise ClassifierTimeoutError(f"claude CLI exceeded timeout {timeout_s}s") from e
        return proc.returncode or 0, stdout, stderr


@dataclass
class ClaudeCliBackend:
    """Spawns `claude --model claude-haiku-4-5` in JSON mode for Stage-0 classification."""

    claude_config_dir: Path
    timeout_s: float = 30.0
    binary: str = "claude"
    model: str = "claude-haiku-4-5"
    name: str = "claude_cli"
    spawner: Spawner = field(default_factory=AsyncioSpawner)

    def _argv(self, prompt_path: Path) -> list[str]:
        return [
            self.binary,
            "--model",
            self.model,
            "--output-format",
            "json",
            "--max-turns",
            "1",
            *system_prompt_argv(prompt_path),
            "--disallowedTools",
            "Bash",
            "Read",
            "Write",
            "Edit",
            "Glob",
            "Grep",
            "WebFetch",
            "--permission-mode",
            "dontAsk",
        ]

    async def call(self, *, text: str, prompt_path: Path, correlation_id: str) -> dict[str, Any]:
        env = build_env(self.claude_config_dir)
        argv = self._argv(prompt_path)
        argv[0] = resolve_binary(self.binary)
        rc, stdout, stderr = await self.spawner.spawn(
            argv,
            env=env,
            stdin=text.encode("utf-8"),
            timeout_s=self.timeout_s,
            cwd=str(neutral_cwd(self.claude_config_dir)),
        )
        if rc != 0:
            raise ClassifierError(
                f"claude CLI exited with rc={rc}; stderr={truncate_stderr(stderr)}"
            )
        try:
            envelope = json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ClassifierSchemaError(f"claude CLI returned non-JSON: {stdout[:512]!r}") from e
        if not isinstance(envelope, dict):
            raise ClassifierSchemaError(
                f"claude CLI JSON is not an object: {type(envelope).__name__}"
            )
        return _unwrap_cli_envelope(envelope)


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def _unwrap_cli_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Extract the inner classifier JSON from a Claude CLI result envelope.

    The CLI envelope looks like {type:"result", subtype:"success", result:"<text>", ...}.
    The model is instructed (prompts/classifier.md) to put a strict JSON object into
    `result`. Defensive: strip code fences if the model adds them anyway.
    """
    if envelope.get("is_error") is True or envelope.get("subtype") != "success":
        raise ClassifierSchemaError(
            f"claude CLI envelope not success: subtype={envelope.get('subtype')!r} "
            f"api_error_status={envelope.get('api_error_status')!r}"
        )
    result_text = envelope.get("result")
    if not isinstance(result_text, str):
        raise ClassifierSchemaError(
            f"claude CLI envelope missing string 'result': got {type(result_text).__name__}"
        )
    candidate = result_text.strip()
    fence_match = _FENCE_RE.match(candidate)
    if fence_match is not None:
        candidate = fence_match.group(1).strip()
    try:
        inner = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ClassifierSchemaError(
            f"claude CLI inner JSON parse failed: {result_text[:256]!r}"
        ) from e
    if not isinstance(inner, dict):
        raise ClassifierSchemaError(
            f"claude CLI inner JSON is not an object: {type(inner).__name__}"
        )
    return inner


@dataclass
class AnthropicApiBackend:
    """Optional API backend. Activated iff Settings.stage0_backend == 'anthropic_api'.

    INV-6 enforced upstream by Settings model_validator: separate credential, never reuses
    Claude Code OAuth. The actual SDK call is implemented in chunk 16 (deployment wiring);
    chunk 5 ships the type seam so the orchestrator can compile against either backend.
    """

    credential_path: Path
    model: str = "claude-haiku-4-5"
    name: str = "anthropic_api"

    async def call(self, *, text: str, prompt_path: Path, correlation_id: str) -> dict[str, Any]:
        raise NotImplementedError(
            "AnthropicApiBackend.call is wired in chunk 16 — set STAGE0_BACKEND=claude_cli for now"
        )


@dataclass
class FakeClaudeRunner:
    """Deterministic test double. Pure Python, no subprocess.

    `responses` is either a list (popped left-to-right) or a callable (text -> dict).
    Every invocation is appended to `self.calls` for assertion.
    """

    responses: list[dict[str, Any]] | Callable[[str], dict[str, Any]] = field(default_factory=list)
    model: str = "fake-haiku"
    name: str = "fake"
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def call(self, *, text: str, prompt_path: Path, correlation_id: str) -> dict[str, Any]:
        self.calls.append(
            {"text": text, "prompt_path": str(prompt_path), "correlation_id": correlation_id}
        )
        if callable(self.responses):
            return self.responses(text)
        if not self.responses:
            raise ClassifierError("FakeClaudeRunner: no scripted response left")
        return self.responses.pop(0)
