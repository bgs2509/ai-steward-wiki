# FILE: src/ai_steward_wiki/wiki/runner.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Stage-1a/1b Sonnet runner orchestrator — assemble prompt, acquire
#            locks, spawn `claude` CLI, stream events, persist transcript
#            atomically. Subprocess is behind a Spawner Protocol seam for tests.
#   SCOPE: run_wiki_session(...); Spawner Protocol; AsyncioSpawner default;
#          assemble_prompt helper; transcript persistence; SIGTERM→SIGKILL on
#          timeout via scheduler.core.kill_with_sequence.
#   DEPENDS: asyncio, contextlib, hashlib, json, os, pathlib, time, structlog,
#            ai_steward_wiki.wiki.{acquire,streaming},
#            ai_steward_wiki.scheduler.core (kill_with_sequence)
#   LINKS: M-WIKI-RUNNER, D-007, D-011, D-012, D-021
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   WikiRunnerError - base exception
#   WikiRunnerTimeoutError - hard timeout after kill-sequence
#   Spawner - Protocol; spawn(argv, env, cwd) -> SpawnedProcess
#   SpawnedProcess - Protocol; pid + stdout reader + wait/terminate/kill
#   AsyncioSpawner - default Spawner using asyncio.create_subprocess_exec
#   assemble_prompt - concat base+overlay → atomic tmp+os.replace into runtime_dir
#   WikiRunResult - dataclass result of one run (run_id, exit_code, events, …)
#   run_wiki_session - public entrypoint orchestrating one Stage-1a/1b run
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 7: initial M-WIKI-RUNNER orchestrator
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

import structlog

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


def assemble_prompt(*, base_path: Path, overlay_path: Path, runtime_dir: Path, run_id: str) -> Path:
    """Concatenate base+overlay prompts and atomically write to runtime_dir.

    Both pieces must carry a `semver: X.Y.Z` line. Returns the assembled path.
    """
    base = base_path.read_text(encoding="utf-8")
    overlay = overlay_path.read_text(encoding="utf-8")
    _check_semver(base, base_path.name)
    _check_semver(overlay, overlay_path.name)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    target = runtime_dir / f"{run_id}.system.md"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(base + "\n\n---\n\n" + overlay, encoding="utf-8")
    os.replace(tmp, target)
    return target


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
        "--model",
        model,
        "--add-dir",
        str(wiki_path),
        "--append-system-prompt",
        f"@{prompt_path}",
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


def _resolve_binary(binary: str) -> str:
    if "/" in binary:
        return binary
    resolved = shutil.which(binary)
    return resolved if resolved is not None else binary


@dataclass
class WikiRunResult:
    run_id: str
    exit_code: int
    events: list[StreamEvent]
    transcript_path: Path
    latency_ms: int


@dataclass
class _RunConfig:
    """Internal grouping to satisfy mypy + ruff arg counts."""

    binary: str = "claude"
    model: str = "claude-sonnet-4-5"
    timeout_s: float = 300.0
    term_grace_s: float = 10.0
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] = field(default_factory=lambda: ["WebFetch"])
    claude_config_dir: Path | None = None


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
    cfg = config or _RunConfig()
    started = time.monotonic()
    prompt_path = assemble_prompt(
        base_path=base_prompt_path,
        overlay_path=overlay_prompt_path,
        runtime_dir=runtime_dir,
        run_id=run_id,
    )
    transcript_path = wiki_path / "runs" / run_id / "transcript.jsonl"

    env = {"PATH": "/usr/bin:/bin"}
    if cfg.claude_config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = str(cfg.claude_config_dir)

    argv = _build_argv(
        binary=_resolve_binary(cfg.binary),
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
        proc = await spawner.spawn(argv, env=env, cwd=wiki_path)
        if proc.stdout is None:
            raise WikiRunnerError("spawned process has no stdout pipe")

        async def _drain() -> int:
            assert proc.stdout is not None
            last_type: str | None = None
            async for ev in parse_stream_json(proc.stdout):
                events.append(ev)
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

    _persist_transcript(events, transcript_path)
    latency_ms = int((time.monotonic() - started) * 1000)
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
