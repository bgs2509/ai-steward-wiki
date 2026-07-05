# FILE: src/ai_steward_wiki/llm/codex.py
# VERSION: 0.1.1
# START_MODULE_CONTRACT
#   PURPOSE: Run Codex CLI through ChatGPT subscription authentication under explicit least-privilege profiles.
#   SCOPE: Restricted environment and argv builders; structured, agent, and text execution;
#          JSON Schema output; JSONL normalization; readiness checks without model invocation.
#   DEPENDS: asyncio, dataclasses, json, os, pathlib, shlex, shutil, tempfile,
#            typing, ai_steward_wiki.llm.failover
#   LINKS: M-LLM-CODEX, M-LLM-FAILOVER, ADR-035, aisw-8gw, FR-5, FR-6, FR-11, FR-12, FR-13, FR-15
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   CodexRunKind - structured, text, read, write, and web capability profiles
#   CodexEvent - provider-neutral agent event without WIKI-layer dependency
#   CodexRequest - validated invocation request without credentials
#   ProcessResult - sanitized subprocess bytes and exit code
#   CodexReadiness - non-model binary, version, auth, and CLI capability status
#   CodexError - base adapter error
#   CodexUnavailableError - process, timeout, binary, version, or auth failure
#   CodexOutputError - malformed structured or JSONL output
#   CodexSpawner - injectable subprocess boundary
#   AsyncioCodexSpawner - timeout and cancellation-safe asyncio implementation
#   CodexCliAdapter - restricted structured, text, agent, and readiness entry points
#   normalize_codex_event - map one Codex JSONL object to CodexEvent and evidence
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.1 - aisw-8gw: keep normalized events provider-local to
#                prevent an M-LLM-CODEX to M-WIKI-RUNNER dependency cycle.
#   PREVIOUS:    v0.1.0 - aisw-8gw: implement restricted subscription-backed
#                Codex invocation, output parsing, event normalization, and readiness.
#   PREVIOUS:    v0.0.0 - aisw-8gw: contract-only planning stub.
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

import structlog

from ai_steward_wiki.llm.failover import AttemptEvidence, EvidenceKind

__all__ = [
    "AsyncioCodexSpawner",
    "CodexCliAdapter",
    "CodexError",
    "CodexEvent",
    "CodexOutputError",
    "CodexReadiness",
    "CodexRequest",
    "CodexRunKind",
    "CodexSpawner",
    "CodexUnavailableError",
    "ProcessResult",
    "normalize_codex_event",
]

_log = structlog.get_logger("llm.codex")

ReasoningEffort = Literal["low", "medium", "high"]
CodexEventType = Literal["assistant_chunk", "tool_use", "final", "raw"]
_AGENT_KINDS = {
    "agent_read",
    "agent_write",
    "web",
}
_READ_ONLY_COMMANDS = {
    "cat",
    "du",
    "file",
    "find",
    "grep",
    "head",
    "ls",
    "pwd",
    "rg",
    "stat",
    "tail",
    "wc",
}
_MUTATING_COMMANDS = {
    "chmod",
    "chown",
    "cp",
    "dd",
    "install",
    "ln",
    "mkdir",
    "mv",
    "rm",
    "rmdir",
    "tee",
    "touch",
    "truncate",
}
_SHELL_MUTATION_TOKENS = (";", "|", "&", ">", "<", "\n", "\r")
_READINESS_FLAGS = {
    "--cd",
    "--ephemeral",
    "--ignore-rules",
    "--ignore-user-config",
    "--json",
    "--model",
    "--output-schema",
    "--sandbox",
    "--strict-config",
}


class CodexRunKind(StrEnum):
    STRUCTURED = "structured"
    TEXT = "text"
    AGENT_READ = "agent_read"
    AGENT_WRITE = "agent_write"
    WEB = "web"


@dataclass(frozen=True, slots=True)
class CodexEvent:
    type: CodexEventType
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CodexRequest:
    prompt: str
    model: str
    reasoning: ReasoningEffort
    run_kind: CodexRunKind
    correlation_id: str
    timeout_s: float
    cwd: Path
    writable_wiki: Path | None = None
    readable_paths: tuple[Path, ...] = ()
    image_paths: tuple[Path, ...] = ()
    output_schema: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.prompt:
            raise ValueError("prompt must not be empty")
        if not self.model:
            raise ValueError("model must not be empty")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if self.run_kind is CodexRunKind.STRUCTURED and self.output_schema is None:
            raise ValueError("structured run requires output_schema")
        if self.run_kind is not CodexRunKind.STRUCTURED and self.output_schema is not None:
            raise ValueError("output_schema is allowed only for structured runs")
        if self.run_kind is CodexRunKind.AGENT_WRITE and self.writable_wiki is None:
            raise ValueError("agent_write run requires writable_wiki")
        if (
            self.run_kind is CodexRunKind.AGENT_WRITE
            and self.writable_wiki is not None
            and self.cwd != self.writable_wiki
        ):
            raise ValueError("agent_write cwd must equal writable_wiki")
        if self.run_kind is not CodexRunKind.AGENT_WRITE and self.writable_wiki is not None:
            raise ValueError("writable_wiki is allowed only for agent_write runs")
        if self.run_kind is CodexRunKind.WEB and (self.readable_paths or self.image_paths):
            raise ValueError("web run cannot access WIKI or media paths")


@dataclass(frozen=True, slots=True)
class ProcessResult:
    exit_code: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True, slots=True)
class CodexReadiness:
    ready: bool
    reason: str | None
    binary: str | None
    version: str | None


class CodexError(RuntimeError):
    """Base error for the Codex CLI integration."""


class CodexUnavailableError(CodexError):
    """The Codex process or subscription is unavailable."""


class CodexOutputError(CodexError):
    """Codex returned output outside the approved contract."""


class CodexSpawner(Protocol):
    async def spawn(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        stdin: bytes,
        timeout_s: float,
        cwd: Path,
    ) -> ProcessResult: ...


@dataclass
class AsyncioCodexSpawner:
    async def spawn(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        stdin: bytes,
        timeout_s: float,
        cwd: Path,
    ) -> ProcessResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(cwd),
            )
        except OSError as exc:
            raise CodexUnavailableError("codex process could not start") from exc
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout_s)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise CodexUnavailableError("codex process timed out") from exc
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            raise
        return ProcessResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
        )


@dataclass
class CodexCliAdapter:
    binary: str
    expected_version: str
    codex_home: Path
    neutral_cwd: Path
    light_model: str
    light_reasoning: ReasoningEffort
    complex_model: str
    complex_reasoning: ReasoningEffort
    spawner: CodexSpawner = field(default_factory=AsyncioCodexSpawner)
    readiness_timeout_s: float = 10.0

    def build_env(self) -> dict[str, str]:
        """Return an allowlisted environment without API billing credentials."""
        return {
            "CODEX_HOME": str(self.codex_home),
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
        }

    def build_argv(
        self,
        request: CodexRequest,
        *,
        output_schema_path: Path | None = None,
    ) -> list[str]:
        """Build one explicit, ephemeral, non-interactive Codex invocation."""
        # START_BLOCK_CODEX_INVOKE
        expected_cwd = (
            request.writable_wiki
            if request.run_kind is CodexRunKind.AGENT_WRITE
            else self.neutral_cwd
        )
        if request.cwd != expected_cwd:
            raise ValueError("Codex request cwd violates its capability profile")
        if request.run_kind is CodexRunKind.STRUCTURED and output_schema_path is None:
            raise ValueError("structured run requires output_schema_path")
        sandbox = "workspace-write" if request.run_kind is CodexRunKind.AGENT_WRITE else "read-only"
        argv = [self._resolved_binary()]
        if request.run_kind is CodexRunKind.WEB:
            argv.append("--search")
        argv.extend(
            [
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--strict-config",
                "--skip-git-repo-check",
                "--color",
                "never",
                "--model",
                request.model,
                "--sandbox",
                sandbox,
                "--cd",
                str(request.cwd),
                "--config",
                f'model_reasoning_effort="{request.reasoning}"',
                "--config",
                'approval_policy="never"',
                "--config",
                "project_doc_max_bytes=0",
                "--config",
                'shell_environment_policy.inherit="none"',
            ]
        )
        if request.run_kind in {
            CodexRunKind.AGENT_READ,
            CodexRunKind.AGENT_WRITE,
            CodexRunKind.WEB,
        }:
            argv.append("--json")
        if request.image_paths:
            argv.append("--image")
            argv.extend(str(path) for path in request.image_paths)
        if output_schema_path is not None:
            argv.extend(["--output-schema", str(output_schema_path)])
        argv.append("-")
        return argv
        # END_BLOCK_CODEX_INVOKE

    async def run_structured(self, request: CodexRequest) -> dict[str, Any]:
        if request.run_kind is not CodexRunKind.STRUCTURED or request.output_schema is None:
            raise ValueError("run_structured requires a structured request")
        schema_path = self._write_schema(request.output_schema)
        try:
            result = await self._execute(
                request,
                self.build_argv(request, output_schema_path=schema_path),
            )
        finally:
            schema_path.unlink(missing_ok=True)
        try:
            payload = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CodexOutputError("codex structured output is not JSON") from exc
        if not isinstance(payload, dict):
            raise CodexOutputError("codex structured output is not an object")
        return payload

    async def run_text(self, request: CodexRequest) -> str:
        if request.run_kind is not CodexRunKind.TEXT:
            raise ValueError("run_text requires a text request")
        result = await self._execute(request, self.build_argv(request))
        try:
            return result.stdout.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise CodexOutputError("codex text output is not UTF-8") from exc

    async def run_agent(self, request: CodexRequest) -> list[CodexEvent]:
        if request.run_kind.value not in _AGENT_KINDS:
            raise ValueError("run_agent requires an agent or web request")
        result = await self._execute(request, self.build_argv(request))
        events: list[CodexEvent] = []
        try:
            text = result.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CodexOutputError("codex JSONL output is not UTF-8") from exc
        # START_BLOCK_CODEX_NORMALIZE
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CodexOutputError("codex JSONL contains malformed JSON") from exc
            if not isinstance(raw, dict):
                raise CodexOutputError("codex JSONL event is not an object")
            event, _evidence = normalize_codex_event(raw)
            events.append(event)
        # END_BLOCK_CODEX_NORMALIZE
        if not any(event.type == "final" for event in events):
            raise CodexOutputError("codex JSONL has no completed turn")
        return events

    async def check_readiness(self) -> CodexReadiness:
        """Check binary, pinned version, subscription login, and CLI flags."""
        binary = self._find_binary()
        if binary is None:
            return CodexReadiness(False, "binary_unavailable", None, None)
        if not self.codex_home.is_dir() or not os.access(
            self.codex_home,
            os.R_OK | os.X_OK,
        ):
            return CodexReadiness(False, "codex_home_unavailable", binary, None)
        try:
            version_result = await self._readiness_call([binary, "--version"])
        except CodexUnavailableError:
            return CodexReadiness(False, "invocation_failed", binary, None)
        if version_result.exit_code != 0:
            return CodexReadiness(False, "invocation_failed", binary, None)
        version_line = version_result.stdout.decode("utf-8", "replace").strip()
        expected_line = f"codex-cli {self.expected_version}"
        if version_line != expected_line:
            return CodexReadiness(False, "version_mismatch", binary, version_line)
        try:
            login_result = await self._readiness_call([binary, "login", "status"])
        except CodexUnavailableError:
            return CodexReadiness(False, "invocation_failed", binary, self.expected_version)
        if login_result.exit_code != 0:
            return CodexReadiness(
                False,
                "authentication_unavailable",
                binary,
                self.expected_version,
            )
        login_text = login_result.stdout.decode("utf-8", "replace").strip().casefold()
        if "logged in using chatgpt" not in login_text:
            return CodexReadiness(
                False,
                "subscription_auth_required",
                binary,
                self.expected_version,
            )
        try:
            help_result = await self._readiness_call([binary, "exec", "--help"])
        except CodexUnavailableError:
            return CodexReadiness(False, "invocation_failed", binary, self.expected_version)
        help_text = help_result.stdout.decode("utf-8", "replace")
        if help_result.exit_code != 0 or not all(flag in help_text for flag in _READINESS_FLAGS):
            return CodexReadiness(
                False,
                "non_interactive_unsupported",
                binary,
                self.expected_version,
            )
        return CodexReadiness(True, None, binary, self.expected_version)

    async def _execute(self, request: CodexRequest, argv: list[str]) -> ProcessResult:
        started = time.perf_counter_ns()
        result = await self.spawner.spawn(
            argv,
            env=self.build_env(),
            stdin=request.prompt.encode("utf-8"),
            timeout_s=request.timeout_s,
            cwd=self.neutral_cwd,
        )
        duration_ms = int((time.perf_counter_ns() - started) / 1_000_000)
        if result.exit_code != 0:
            _log.warning(
                "llm.provider.failed",
                provider="codex",
                model=request.model,
                run_kind=request.run_kind.value,
                correlation_id=request.correlation_id,
                outcome="nonzero_exit",
                exit_code=result.exit_code,
                duration_ms=duration_ms,
                stdout_bytes=len(result.stdout),
                stderr_bytes=len(result.stderr),
            )
            raise CodexUnavailableError(f"codex exited with code {result.exit_code}")
        return result

    async def _readiness_call(self, argv: list[str]) -> ProcessResult:
        return await self.spawner.spawn(
            argv,
            env=self.build_env(),
            stdin=b"",
            timeout_s=self.readiness_timeout_s,
            cwd=self.neutral_cwd,
        )

    def _write_schema(self, schema: Mapping[str, object]) -> Path:
        self.neutral_cwd.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            prefix="codex-schema-",
            suffix=".json",
            dir=self.neutral_cwd,
            text=True,
        )
        path = Path(raw_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(schema, handle, ensure_ascii=False)
            path.chmod(0o600)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path

    def _find_binary(self) -> str | None:
        if "/" in self.binary:
            path = Path(self.binary)
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
            return None
        return shutil.which(self.binary)

    def _resolved_binary(self) -> str:
        return self._find_binary() or self.binary


def normalize_codex_event(
    raw: Mapping[str, Any],
) -> tuple[CodexEvent, AttemptEvidence]:
    """Map one Codex JSONL event into the existing runner event contract."""
    raw_type = raw.get("type")
    if raw_type == "item.completed":
        item = raw.get("item")
        if not isinstance(item, Mapping):
            return _raw_event(raw), AttemptEvidence(EvidenceKind.UNKNOWN, "missing item")
        item_type = item.get("type")
        payload = dict(item)
        if item_type == "agent_message":
            text = item.get("text")
            if not isinstance(text, str):
                raise CodexOutputError("codex agent message has no text")
            return (
                CodexEvent(type="assistant_chunk", payload={"text": text}),
                AttemptEvidence(EvidenceKind.READ_ONLY, "agent message"),
            )
        if item_type == "file_change":
            return (
                CodexEvent(type="tool_use", payload=payload),
                AttemptEvidence(EvidenceKind.MUTATION, "file change"),
            )
        if item_type == "web_search":
            return (
                CodexEvent(type="tool_use", payload=payload),
                AttemptEvidence(EvidenceKind.READ_ONLY, "web search"),
            )
        if item_type == "command_execution":
            return CodexEvent(type="tool_use", payload=payload), _command_evidence(
                item.get("command")
            )
        return _raw_event(raw), AttemptEvidence(EvidenceKind.UNKNOWN, "unknown item")
    if raw_type == "turn.completed":
        return (
            CodexEvent(type="final", payload=dict(raw)),
            AttemptEvidence(EvidenceKind.READ_ONLY, "turn completed"),
        )
    if raw_type in {"turn.failed", "error"}:
        raise CodexOutputError("codex agent turn failed")
    return _raw_event(raw), AttemptEvidence(EvidenceKind.UNKNOWN, "unknown event")


def _raw_event(raw: Mapping[str, Any]) -> CodexEvent:
    return CodexEvent(type="raw", payload=dict(raw))


def _command_evidence(command_value: object) -> AttemptEvidence:
    if isinstance(command_value, str):
        command = command_value
    elif isinstance(command_value, Sequence) and all(
        isinstance(part, str) for part in command_value
    ):
        command = " ".join(command_value)
    else:
        return AttemptEvidence(EvidenceKind.UNKNOWN, "missing command")
    if any(token in command for token in _SHELL_MUTATION_TOKENS):
        return AttemptEvidence(EvidenceKind.UNKNOWN, "compound shell command")
    try:
        parts = shlex.split(command)
    except ValueError:
        return AttemptEvidence(EvidenceKind.UNKNOWN, "invalid shell command")
    if not parts:
        return AttemptEvidence(EvidenceKind.UNKNOWN, "empty shell command")
    executable = Path(parts[0]).name
    if executable in _READ_ONLY_COMMANDS:
        return AttemptEvidence(EvidenceKind.READ_ONLY, executable)
    if executable in _MUTATING_COMMANDS:
        return AttemptEvidence(EvidenceKind.MUTATION, executable)
    return AttemptEvidence(EvidenceKind.UNKNOWN, executable)
