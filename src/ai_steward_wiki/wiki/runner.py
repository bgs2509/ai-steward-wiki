# FILE: src/ai_steward_wiki/wiki/runner.py
# VERSION: 0.0.4
# START_MODULE_CONTRACT
#   PURPOSE: Stage-1a/1b Sonnet runner orchestrator — assemble prompt, acquire
#            locks, spawn `claude` CLI, stream events, persist transcript
#            atomically. Subprocess is behind a Spawner Protocol seam for tests.
#   SCOPE: run_wiki_session(...); Spawner Protocol; AsyncioSpawner default;
#          assemble_prompt helper; transcript persistence; SIGTERM→SIGKILL on
#          timeout via scheduler.core.kill_with_sequence.
#   DEPENDS: asyncio, contextlib, os, pathlib, time, structlog,
#            ai_steward_wiki.claude_cli.common (M-CLAUDE-CLI-COMMON),
#            ai_steward_wiki.wiki.{acquire,streaming},
#            ai_steward_wiki.scheduler.core (kill_with_sequence)
#   LINKS: M-WIKI-RUNNER, M-CLAUDE-CLI-COMMON, D-007, D-011, D-012, D-021, aisw-d3i, aisw-0mg
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   WikiRunnerError - base exception
#   WikiRunnerTimeoutError - hard timeout after kill-sequence
#   Spawner - Protocol; spawn(argv, env, cwd) -> SpawnedProcess
#   SpawnedProcess - Protocol; pid + stdout/stderr readers + wait/terminate/kill
#   AsyncioSpawner - default Spawner using asyncio.create_subprocess_exec
#   assemble_prompt - concat base+overlay (+ per-WIKI CLAUDE.md if present) → atomic write
#   WikiRunResult - dataclass result of one run (run_id, exit_code, events, …)
#   run_wiki_session - public entrypoint orchestrating one Stage-1a/1b run
#   aggregate_text - extract assistant text from WikiRunResult.events
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.4 - aisw-0mg: add -p, --setting-sources "",
#                         --disable-slash-commands to argv. Under subscription
#                         OAuth, default Claude Code system prompt + skills +
#                         user/project settings are loaded regardless of
#                         --system-prompt; isolation flags drop them. Verified
#                         2026-05-12 (claude 2.1.139): cache_creation_input_tokens
#                         goes from ~10k to 0. --tools is NOT zeroed for Stage-1
#                         (wiki edits require Read/Write/Edit).
#   PREVIOUS:    v0.0.3 - aisw-adj: inherit inline `--system-prompt` via
#                         system_prompt_argv. `--system-prompt-file` does NOT
#                         replace the default Claude Code system prompt under
#                         subscription auth (verified 2026-05-12, claude 2.1.139);
#                         wiki prompt is now passed as inline content. No local
#                         code change — fix is in M-CLAUDE-CLI-COMMON.
#   PREVIOUS:    v0.0.2 - aisw-d3i: fix Claude CLI invocation. Replace
#                         --append-system-prompt @path with --system-prompt-file
#                         <path>; run CLI in neutral cwd (claude_config_dir) so
#                         project CLAUDE.md is not auto-discovered; drain stderr
#                         and raise WikiRunnerError with truncated stderr on
#                         non-zero exit; fold per-WIKI CLAUDE.md into the
#                         assembled prompt explicitly; require claude_config_dir.
#                         Shared primitives extracted to M-CLAUDE-CLI-COMMON.
#   PREVIOUS:    v0.0.1 - chunk 7: initial M-WIKI-RUNNER orchestrator
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

import structlog

from ai_steward_wiki.claude_cli.common import (
    build_env,
    neutral_cwd,
    resolve_binary,
    system_prompt_argv,
    truncate_stderr,
)
from ai_steward_wiki.scheduler.core import kill_with_sequence
from ai_steward_wiki.wiki.acquire import LockAcquirer
from ai_steward_wiki.wiki.streaming import StreamEvent, parse_stream_json

__all__ = [
    "AsyncioSpawner",
    "SpawnedProcess",
    "Spawner",
    "WikiRunResult",
    "WikiRunnerError",
    "WikiRunnerTimeoutError",
    "aggregate_text",
    "assemble_prompt",
    "run_wiki_session",
]

_log = structlog.get_logger("wiki.runner")
_SEMVER_RE = re.compile(r"^semver:\s*(\d+\.\d+\.\d+)\s*$", re.MULTILINE)


class WikiRunnerError(Exception):
    """Base exception for the Stage-1a/1b runner."""


class WikiRunnerTimeoutError(WikiRunnerError):
    """Hard timeout — kill-sequence applied (D-021)."""


@runtime_checkable
class SpawnedProcess(Protocol):
    pid: int
    stdout: asyncio.StreamReader | None
    stderr: asyncio.StreamReader | None

    async def wait(self) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


class Spawner(Protocol):
    async def spawn(self, argv: list[str], *, env: dict[str, str], cwd: Path) -> SpawnedProcess: ...


@dataclass
class AsyncioSpawner:
    """Default Spawner using asyncio.create_subprocess_exec.

    Chunk 16 swaps this for a systemd-run wrapper without touching the runner.
    """

    async def spawn(self, argv: list[str], *, env: dict[str, str], cwd: Path) -> SpawnedProcess:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(cwd),
        )
        return proc  # asyncio.subprocess.Process satisfies SpawnedProcess


def _check_semver(text: str, label: str) -> None:
    if _SEMVER_RE.search(text) is None:
        raise WikiRunnerError(f"prompt {label} missing required `semver: X.Y.Z` frontmatter")


def assemble_prompt(
    *,
    base_path: Path,
    overlay_path: Path,
    runtime_dir: Path,
    run_id: str,
    wiki_path: Path | None = None,
) -> Path:
    """Concatenate base + overlay (+ per-WIKI CLAUDE.md if present) and atomically write.

    Base and overlay must carry a `semver: X.Y.Z` line. The optional per-WIKI
    CLAUDE.md (at `wiki_path / "CLAUDE.md"`) is appended verbatim if present —
    operator-authored, not framework-versioned, so no semver check. Folding it
    in here eliminates dependence on Claude Code's CLAUDE.md auto-discovery,
    which is disabled by the neutral cwd in `run_wiki_session`.
    """
    base = base_path.read_text(encoding="utf-8")
    overlay = overlay_path.read_text(encoding="utf-8")
    _check_semver(base, base_path.name)
    _check_semver(overlay, overlay_path.name)
    pieces = [base, "---", overlay]
    if wiki_path is not None:
        per_wiki = wiki_path / "CLAUDE.md"
        if per_wiki.exists():
            pieces += ["---", per_wiki.read_text(encoding="utf-8")]
    runtime_dir.mkdir(parents=True, exist_ok=True)
    target = runtime_dir / f"{run_id}.system.md"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text("\n\n".join(pieces), encoding="utf-8")
    os.replace(tmp, target)
    return target


# START_CONTRACT: aggregate_text
#   PURPOSE: Concatenate assistant text content from stream-json events.
#   INPUTS: { events: list[StreamEvent] - parsed events from one run }
#   OUTPUTS: { str - concatenated assistant text; "" when no assistant content }
#   SIDE_EFFECTS: none (pure)
#   LINKS: M-TG-PIPELINE-CLASSIFIER (chunk 20, DEC-TPC-2)
# END_CONTRACT: aggregate_text
def aggregate_text(events: list[StreamEvent]) -> str:
    """Extract assistant text from an event list (stream-json shape).

    Tolerant: returns "" if no assistant_chunk events carry recognisable text.
    Recognised payload shapes:
      - payload["message"]["content"] = [{"type": "text", "text": "..."}, ...]
      - payload["delta"]["text"] = "..."
      - payload["text"] = "..."
    """
    parts: list[str] = []
    for ev in events:
        if ev.type != "assistant_chunk":
            continue
        payload = ev.payload
        message = payload.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text = item.get("text")
                        if isinstance(text, str):
                            parts.append(text)
                continue
        delta = payload.get("delta")
        if isinstance(delta, dict):
            text = delta.get("text")
            if isinstance(text, str):
                parts.append(text)
                continue
        text = payload.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _persist_transcript(events: list[StreamEvent], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(ev.model_dump_json() + "\n")
    os.replace(tmp, target)


def _build_argv(
    *,
    binary: str,
    model: str,
    wiki_path: Path,
    prompt_path: Path,
    allowed_tools: list[str] | None,
    disallowed_tools: list[str] | None,
) -> list[str]:
    argv: list[str] = [
        binary,
        "-p",
        "--model",
        model,
        "--add-dir",
        str(wiki_path),
        *system_prompt_argv(prompt_path),
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--output-format",
        "stream-json",
        "--permission-mode",
        "dontAsk",
    ]
    if allowed_tools:
        argv.extend(["--allowedTools", *allowed_tools])
    if disallowed_tools:
        argv.extend(["--disallowedTools", *disallowed_tools])
    return argv


@dataclass
class WikiRunResult:
    run_id: str
    exit_code: int
    events: list[StreamEvent]
    transcript_path: Path
    latency_ms: int


@dataclass
class _RunConfig:
    """Internal grouping to satisfy mypy + ruff arg counts.

    `claude_config_dir` is required (post aisw-d3i): the runner uses it to
    build both the CLI env and the neutral cwd. Tests construct it with a
    tmp_path fixture; production wires from Settings.claude_config_dir.
    """

    claude_config_dir: Path
    binary: str = "claude"
    model: str = "claude-sonnet-4-5"
    timeout_s: float = 300.0
    term_grace_s: float = 10.0
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] = field(default_factory=lambda: ["WebFetch"])


async def run_wiki_session(
    *,
    wiki_id: str,
    wiki_path: Path,
    base_prompt_path: Path,
    overlay_prompt_path: Path,
    run_id: str,
    correlation_id: str,
    runtime_dir: Path,
    acquirer: LockAcquirer,
    spawner: Spawner,
    config: _RunConfig | None = None,
    on_event: Callable[[StreamEvent], Awaitable[None]] | None = None,
) -> WikiRunResult:
    """Run one Stage-1a/1b Sonnet session against `wiki_path`.

    Side-effects:
      - acquires (semaphore→memlock→flock) via `acquirer`,
      - spawns `claude` CLI via `spawner`,
      - streams stream-json events,
      - persists transcript atomically into `<wiki>/runs/<run_id>/transcript.jsonl`,
      - on timeout invokes scheduler.core.kill_with_sequence and raises
        WikiRunnerTimeoutError.
    """
    if config is None:
        raise WikiRunnerError(
            "run_wiki_session requires a _RunConfig (claude_config_dir is mandatory)"
        )
    cfg = config
    started = time.monotonic()
    prompt_path = assemble_prompt(
        base_path=base_prompt_path,
        overlay_path=overlay_prompt_path,
        runtime_dir=runtime_dir,
        run_id=run_id,
        wiki_path=wiki_path,
    )
    transcript_path = wiki_path / "runs" / run_id / "transcript.jsonl"

    env = build_env(cfg.claude_config_dir)
    cwd = neutral_cwd(cfg.claude_config_dir)

    argv = _build_argv(
        binary=resolve_binary(cfg.binary),
        model=cfg.model,
        wiki_path=wiki_path,
        prompt_path=prompt_path,
        allowed_tools=cfg.allowed_tools,
        disallowed_tools=cfg.disallowed_tools,
    )

    _log.info(
        "wiki.run.start",
        correlation_id=correlation_id,
        wiki_id=wiki_id,
        run_id=run_id,
        model=cfg.model,
    )

    events: list[StreamEvent] = []
    async with AsyncExitStack() as stack:
        lock_started = time.monotonic()
        await stack.enter_async_context(acquirer.acquire(wiki_id, wiki_path))
        _log.info(
            "wiki.lock.acquired",
            wiki_id=wiki_id,
            run_id=run_id,
            latency_ms=int((time.monotonic() - lock_started) * 1000),
        )

        wiki_path.mkdir(parents=True, exist_ok=True)
        proc = await spawner.spawn(argv, env=env, cwd=cwd)
        if proc.stdout is None:
            raise WikiRunnerError("spawned process has no stdout pipe")

        async def _drain() -> int:
            assert proc.stdout is not None
            last_type: str | None = None
            async for ev in parse_stream_json(proc.stdout):
                events.append(ev)
                if on_event is not None:
                    try:
                        await on_event(ev)
                    except Exception as exc:
                        _log.warning(
                            "wiki.run.on_event_error",
                            run_id=run_id,
                            correlation_id=correlation_id,
                            error=type(exc).__name__,
                        )
                if ev.type != last_type:
                    _log.info(
                        "wiki.run.event",
                        run_id=run_id,
                        correlation_id=correlation_id,
                        event_type=ev.type,
                    )
                    last_type = ev.type
            return await proc.wait()

        try:
            exit_code = await asyncio.wait_for(_drain(), timeout=cfg.timeout_s)
        except TimeoutError as e:
            with contextlib.suppress(ProcessLookupError):
                await kill_with_sequence(proc, grace_seconds=cfg.term_grace_s)
            _log.warning(
                "wiki.run.finish",
                correlation_id=correlation_id,
                wiki_id=wiki_id,
                run_id=run_id,
                exit_code=-1,
                n_events=len(events),
                latency_ms=int((time.monotonic() - started) * 1000),
                timeout=True,
            )
            _persist_transcript(events, transcript_path)
            raise WikiRunnerTimeoutError(f"wiki run exceeded timeout {cfg.timeout_s}s") from e

    stderr_text = await _drain_stderr(proc)
    _persist_transcript(events, transcript_path)
    latency_ms = int((time.monotonic() - started) * 1000)
    if exit_code != 0:
        _log.error(
            "wiki.run.error",
            correlation_id=correlation_id,
            wiki_id=wiki_id,
            run_id=run_id,
            exit_code=exit_code,
            n_events=len(events),
            latency_ms=latency_ms,
            stderr=stderr_text,
        )
        raise WikiRunnerError(f"claude CLI exited rc={exit_code}; stderr={stderr_text}")
    _log.info(
        "wiki.run.finish",
        correlation_id=correlation_id,
        wiki_id=wiki_id,
        run_id=run_id,
        exit_code=exit_code,
        n_events=len(events),
        latency_ms=latency_ms,
    )
    return WikiRunResult(
        run_id=run_id,
        exit_code=exit_code,
        events=events,
        transcript_path=transcript_path,
        latency_ms=latency_ms,
    )


async def _drain_stderr(proc: SpawnedProcess) -> str:
    """Read up to 4 KiB of stderr (best-effort, bounded). Returns truncated string."""
    if proc.stderr is None:
        return ""
    try:
        data = await asyncio.wait_for(proc.stderr.read(4096), timeout=1.0)
    except (TimeoutError, asyncio.IncompleteReadError):
        return ""
    return truncate_stderr(data)
