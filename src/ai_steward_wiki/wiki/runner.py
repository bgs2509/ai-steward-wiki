# FILE: src/ai_steward_wiki/wiki/runner.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Stage-1a/1b runner orchestrator — assemble prompt, hold one WIKI lock
#            across Claude and safe Codex fallback, stream provider-neutral events,
#            and persist the transcript atomically.
#   SCOPE: run_wiki_session(...); Spawner Protocol; AsyncioSpawner default;
#          assemble_prompt helper; typed Claude limit detection; replay evidence;
#          Codex JSONL normalization; transcript persistence; SIGTERM→SIGKILL.
#   DEPENDS: asyncio, contextlib, os, pathlib, time, structlog,
#            ai_steward_wiki.claude_cli.common (M-CLAUDE-CLI-COMMON),
#            ai_steward_wiki.llm.{failover,codex},
#            ai_steward_wiki.wiki.{acquire,streaming},
#            ai_steward_wiki.scheduler.core (kill_with_sequence)
#   LINKS: M-WIKI-RUNNER, M-CLAUDE-CLI-COMMON, M-LLM-FAILOVER, M-LLM-CODEX,
#          D-007, D-011, D-012, D-021, aisw-d3i, aisw-0mg, aisw-w83, aisw-8gw
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
#   WRITE_TOOLS - tool names a writing run must allow (Write/Edit/MultiEdit) under dontAsk (aisw-t6w)
#   WEB_SEARCH_TOOLS - sole tool a web_task run allows (WebSearch) — read-only, no WIKI add-dir (aisw-dqz)
#   WikiRunResult - dataclass result of one run (run_id, exit_code, events, permission_denials, …)
#   extract_permission_denials - pull permission_denials from the CLI result event (aisw-t6w)
#   AttemptEvidenceTracker - monotonic fail-closed Claude side-effect evidence
#   run_wiki_session - public entrypoint orchestrating one Stage-1a/1b run; extra_add_dirs adds read-only --add-dir targets (digest multi-WIKI, aisw-oqq)
#   aggregate_text - extract assistant text from WikiRunResult.events
#   final_turn_text - assistant text after the last tool_use (drops inter-tool narration; aisw-2n2)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - aisw-8gw: hold one WIKI lock across Claude and safe
#                Codex fallback, block unsafe replay, and persist one transcript.
#   PREVIOUS:    v0.0.15 - aisw-8gw: contract-only plan for locked safe Codex fallback.
#   PREVIOUS:    v0.0.14 - aisw-9io: deny Bash on every wiki run-kind. _RunConfig.
#                disallowed_tools default is now ["Bash", "WebFetch"] — no run needs a
#                shell and the sandbox forbids it, so the agent stopped wasting turns on
#                Bash permission_denied (17x on day-25). Paired with dropping the Bash
#                affordance from prompts/wiki.md so advertised policy == sandbox policy.
#   PREVIOUS:    v0.0.13 - aisw-dqz (Path B, HUMAN-approved 2026-06-26): web_task WebSearch
#                carve-out. (1) WEB_SEARCH_TOOLS=["WebSearch"] allow-list. (2) _RunConfig.
#                web_search flag: when set, _build_argv OMITS --add-dir on the WIKI tree and
#                run_wiki_session uses a neutral empty cwd (no WIKI read access) — prompt-
#                injection mitigation M-1. WebFetch stays in disallowed_tools (M-2). (3)
#                wiki.run.web_search.enabled log anchor. WebSearch is enabled ONLY via this
#                intent-scoped config (wired in __main__), never globally (M-5).
#   PREVIOUS:    v0.0.12 - aisw-t6w: fix ingest silent-data-loss. (1) WRITE_TOOLS
#                allow-list so writing runs (ingest/wiki/digest/librarian) can use
#                Write/Edit under --permission-mode dontAsk (Read/Bash unchanged;
#                router/classifier stay read-only via allowed_tools=None). (2)
#                extract_permission_denials + WikiRunResult.permission_denials +
#                WARNING wiki.run.permission_denied + permission_denied_count on the
#                finish log — rc==0 with blocked Write/Edit no longer reads as ok.
#   PREVIOUS:    v0.0.11 - aisw-22o: run the CLI with cwd = wiki_path (was neutral
#                claude_config_dir). The base/domain prompts use relative paths
#                (raw/, metrics/, log.md) assuming cwd == WIKI; the neutral cwd broke
#                that and the model asked the user where to write. wiki_path already
#                carries the per-user <telegram_id> segment; WIKIs are outside the dev
#                repo so no project CLAUDE.md is auto-discovered. Reverses old FR-3.
#   PREVIOUS:    v0.0.10 - aisw-nrt (chunk 2): claude_cli.spawn at boundary +
#                claude_cli.exit/error adjacent to wiki.run.finish/error. stdout_bytes
#                is 0 in the wiki path because stdout is streamed into events, not
#                retained as a byte buffer (intentional asymmetry vs classifier path).
#   PREVIOUS:    v0.0.9 - aisw-oqq: run_wiki_session/_build_argv accept extra_add_dirs
#                — additional read-only --add-dir targets, placed before media_dirs
#                (digest job reads several Domain-WIKIs in one run).
#   PREVIOUS:    v0.0.8 - aisw-t0n: run_wiki_session accepts an optional
#                timeout_s overriding config.timeout_s for this call (D-022:
#                ~30s photo vision vs ~300s text turn).
#   PREVIOUS:    v0.0.7 - aisw-m2m (media chunk 2): run_wiki_session accepts
#                media_paths; their parent dirs are appended to `--add-dir` so
#                Claude's Read tool can open attached images (D-022). claude CLI
#                2.1.139 has no `--image`; this is the only viable mechanism.
#                v0.0.6 - aisw-kpb: add `--verbose` to argv. claude CLI rejects
#                         `--print` (-p) + `--output-format stream-json` without
#                         `--verbose` (rc=1 "When using --print,
#                         --output-format=stream-json requires --verbose").
#                         Regression from v0.0.4 which added -p but not --verbose.
#   PREVIOUS:    v0.0.5 - aisw-w83: pipe user_input to claude stdin. `-p` (added
#                         in v0.0.4) requires user prompt via stdin or argv; runner
#                         previously used stdin=DEVNULL with user text smuggled
#                         into the system-prompt overlay, causing rc=1 "Input must
#                         be provided either through stdin or as a prompt argument
#                         when using --print". Spawner Protocol extended with
#                         stdin_data; AsyncioSpawner uses PIPE when provided.
#   PREVIOUS:    v0.0.4 - aisw-0mg: add -p, --setting-sources "",
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
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import structlog

from ai_steward_wiki.claude_cli.common import (
    build_env,
    parse_claude_subscription_limit,
    resolve_binary,
    system_prompt_argv,
    truncate_stderr,
)
from ai_steward_wiki.llm.codex import CodexCliAdapter, CodexRequest, CodexRunKind
from ai_steward_wiki.llm.failover import (
    AttemptEvidence,
    EvidenceKind,
    FailoverPolicy,
    ProviderLimitError,
)
from ai_steward_wiki.logging_events import (
    CLAUDE_CLI_ERROR,
    CLAUDE_CLI_EXIT,
    CLAUDE_CLI_SPAWN,
    WIKI_RUN,
)
from ai_steward_wiki.logging_setup import traced
from ai_steward_wiki.scheduler.core import kill_with_sequence
from ai_steward_wiki.wiki.acquire import LockAcquirer
from ai_steward_wiki.wiki.streaming import StreamEvent, parse_stream_json

__all__ = [
    "WEB_SEARCH_TOOLS",
    "WRITE_TOOLS",
    "AsyncioSpawner",
    "AttemptEvidenceTracker",
    "SpawnedProcess",
    "Spawner",
    "WikiRunResult",
    "WikiRunnerError",
    "WikiRunnerTimeoutError",
    "aggregate_text",
    "assemble_prompt",
    "extract_permission_denials",
    "final_turn_text",
    "run_wiki_session",
]

# aisw-t6w: tools a *writing* run (ingest / wiki edit / digest / librarian) must be
# allowed to use. Under `--permission-mode dontAsk` Read/Bash are allowed by default
# but Write/Edit are denied without an explicit allow-list, which silently aborted
# ingest (CSV/log.md never written while the run still reported success). Read-only
# runs (router / classifier) keep allowed_tools=None and never receive these.
WRITE_TOOLS: list[str] = ["Write", "Edit", "MultiEdit"]

# aisw-dqz (Path B, HUMAN-approved 2026-06-26): the ONLY tool a `web_task` run is allowed.
# WebSearch (Anthropic-mediated search) lets the model answer "найди в интернете …" from
# live web content. It is denied for every other run by the dontAsk allowlist; this carve-out
# is intent-scoped (wired in __main__ for Intent.WEB only, aisw-xi8 re-anchor from the
# deleted Intent.WEB_TASK v1 member — M-5). The run is read-only
# (no WRITE_TOOLS) and gets no --add-dir on the WIKI tree (see _RunConfig.web_search), so
# untrusted web content cannot be turned into WIKI writes/exfiltration (M-1). WebFetch (the
# arbitrary-URL / SSRF vector) stays in disallowed_tools (M-2).
WEB_SEARCH_TOOLS: list[str] = ["WebSearch"]

_log = structlog.get_logger("wiki.runner")
_SEMVER_RE = re.compile(r"^semver:\s*(\d+\.\d+\.\d+)\s*$", re.MULTILINE)


class WikiRunnerError(Exception):
    """Base exception for the Stage-1a/1b runner."""


class WikiRunnerTimeoutError(WikiRunnerError):
    """Hard timeout — kill-sequence applied (D-021)."""


@runtime_checkable
class SpawnedProcess(Protocol):
    pid: int
    stdin: asyncio.StreamWriter | None
    stdout: asyncio.StreamReader | None
    stderr: asyncio.StreamReader | None

    async def wait(self) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


class Spawner(Protocol):
    async def spawn(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        cwd: Path,
        stdin_data: bytes | None = None,
    ) -> SpawnedProcess: ...


@dataclass
class AsyncioSpawner:
    """Default Spawner using asyncio.create_subprocess_exec.

    Chunk 16 swaps this for a systemd-run wrapper without touching the runner.
    """

    async def spawn(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        cwd: Path,
        stdin_data: bytes | None = None,
    ) -> SpawnedProcess:
        _log.info(
            CLAUDE_CLI_SPAWN,
            argv_length=len(argv),
            env_keys_count=len(env),
            cwd=str(cwd),
        )
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=(
                asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL
            ),
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
def _assistant_text(ev: StreamEvent) -> str:
    """Extract concatenated assistant text from one stream event ("" if none).

    Recognised payload shapes (first match wins, matching the legacy aggregate):
      - payload["message"]["content"] = [{"type": "text", "text": "..."}, ...]
      - payload["delta"]["text"] = "..."
      - payload["text"] = "..."
    """
    if ev.type != "assistant_chunk":
        return ""
    payload = ev.payload
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            parts = [
                item["text"]
                for item in content
                if isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ]
            return "".join(parts)
    delta = payload.get("delta")
    if isinstance(delta, dict):
        text = delta.get("text")
        if isinstance(text, str):
            return text
    text = payload.get("text")
    if isinstance(text, str):
        return text
    return ""


def _has_tool_use(ev: StreamEvent) -> bool:
    """True if the event represents a tool invocation (boundary for final_turn_text)."""
    if ev.type == "tool_use":
        return True
    if ev.type != "assistant_chunk":
        return False
    message = ev.payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            return any(
                isinstance(item, dict) and item.get("type") == "tool_use" for item in content
            )
    return False


def aggregate_text(events: list[StreamEvent]) -> str:
    """Extract assistant text from an event list (stream-json shape).

    Tolerant: returns "" if no assistant_chunk events carry recognisable text.
    Recognised payload shapes:
      - payload["message"]["content"] = [{"type": "text", "text": "..."}, ...]
      - payload["delta"]["text"] = "..."
      - payload["text"] = "..."
    """
    return "".join(_assistant_text(ev) for ev in events)


# START_CONTRACT: final_turn_text
#   PURPOSE: Return only the trailing assistant answer, dropping inter-tool narration.
#   INPUTS: { events: list[StreamEvent] - parsed events from one run }
#   OUTPUTS: { str - text of assistant turn(s) after the last tool_use; falls back
#             to aggregate_text when there is no tool_use or no trailing text }
#   SIDE_EFFECTS: none (pure)
#   LINKS: aisw-2n2, M-TG-PIPELINE-CLASSIFIER, M-INBOX-LIBRARIAN
# END_CONTRACT: final_turn_text
def final_turn_text(events: list[StreamEvent]) -> str:
    """Concatenate assistant text emitted after the last tool invocation.

    In the agentic loop Claude narrates before each tool call ("Прочитаю сырьё…").
    The user-facing answer is the trailing text turn(s) after the last tool_use;
    this strips the narration leak. Never empty when aggregate_text is non-empty:
      - no tool_use at all  -> identical to aggregate_text (whole answer)
      - tool_use but no text after it -> fall back to aggregate_text
    """
    last_tool = -1
    for index, ev in enumerate(events):
        if _has_tool_use(ev):
            last_tool = index
    if last_tool == -1:
        return aggregate_text(events)
    tail = "".join(_assistant_text(ev) for ev in events[last_tool + 1 :])
    return tail if tail else aggregate_text(events)


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
    media_dirs: list[Path] | None = None,
    extra_add_dirs: list[Path] | None = None,
    add_wiki_dir: bool = True,
) -> list[str]:
    # claude CLI 2.1.139 has no --image flag; local media is exposed to the
    # Read tool by granting --add-dir on the staged file's directory (D-022).
    # extra_add_dirs: additional read-only --add-dir targets (digest multi-WIKI, aisw-oqq).
    # add_wiki_dir=False (aisw-dqz web_task): suppress --add-dir on the WIKI tree so the run
    # has no read access to the user's WIKIs (prompt-injection mitigation M-1).
    extra_dirs = [str(d) for d in (extra_add_dirs or [])] + [str(d) for d in (media_dirs or [])]
    wiki_add_dir = ["--add-dir", str(wiki_path)] if add_wiki_dir else []
    argv: list[str] = [
        binary,
        "-p",
        "--model",
        model,
        *wiki_add_dir,
        *extra_dirs,
        *system_prompt_argv(prompt_path),
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--verbose",
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
    # aisw-t6w: tool-permission denials reported by the CLI `result` event. Non-empty
    # means the model could not complete an action (e.g. Write/Edit blocked) even though
    # exit_code may be 0 — a silent-data-loss signal that callers MUST NOT treat as ok.
    permission_denials: list[dict[str, Any]] = field(default_factory=list)


def extract_permission_denials(events: list[StreamEvent]) -> list[dict[str, Any]]:
    """Pull `permission_denials` from the CLI result (`final`) event, if any.

    The claude `--output-format stream-json` result line carries a
    `permission_denials` array (each: tool_name, tool_use_id, tool_input) listing
    tool calls the permission layer refused. Returns the last final event's list,
    or [] when absent. Pure — no side effects.
    """
    denials: list[dict[str, Any]] = []
    for ev in events:
        if ev.type != "final":
            continue
        raw = ev.payload.get("permission_denials")
        if isinstance(raw, list):
            denials = [d for d in raw if isinstance(d, dict)]
    return denials


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
    # aisw-9io: Bash is denied on EVERY wiki run-kind. No run (router/query/ingest/
    # digest/librarian/web) needs a shell, and the systemd sandbox forbids it — without
    # an explicit deny the agent kept trying Bash and burning turns on permission_denied
    # (17x on day-25). Denying it here (plus dropping the Bash affordance from
    # prompts/wiki.md) keeps the advertised policy and the sandbox aligned. WebFetch stays
    # denied (SSRF guard M-2, aisw-dqz).
    disallowed_tools: list[str] = field(default_factory=lambda: ["Bash", "WebFetch"])
    # aisw-dqz (Path B): when True the run is a web_task — it gets NO --add-dir on the WIKI
    # tree and a neutral cwd (no WIKI read access). Pair with allowed_tools=WEB_SEARCH_TOOLS.
    web_search: bool = False
    failover_policy: FailoverPolicy | None = None
    codex_adapter: CodexCliAdapter | None = None


_EVIDENCE_PRIORITY = {
    EvidenceKind.READ_ONLY: 0,
    EvidenceKind.UNKNOWN: 1,
    EvidenceKind.DELIVERED: 2,
    EvidenceKind.MUTATION: 3,
}
_READ_ONLY_CLAUDE_TOOLS = {"Glob", "Grep", "Read", "WebSearch"}
_MUTATING_CLAUDE_TOOLS = {"Edit", "MultiEdit", "NotebookEdit", "Write"}


@dataclass
class AttemptEvidenceTracker:
    evidence: AttemptEvidence = field(
        default_factory=lambda: AttemptEvidence(EvidenceKind.READ_ONLY, "no side effect")
    )

    def observe(self, evidence: AttemptEvidence) -> None:
        if _EVIDENCE_PRIORITY[evidence.kind] > _EVIDENCE_PRIORITY[self.evidence.kind]:
            self.evidence = evidence


@dataclass(frozen=True, slots=True)
class _AttemptResult:
    exit_code: int
    events: list[StreamEvent]
    stderr_text: str = ""


def _claude_event_evidence(event: StreamEvent) -> AttemptEvidence:
    tool_names: list[str] = []
    if event.type == "tool_use":
        name = event.payload.get("name") or event.payload.get("tool_name")
        if isinstance(name, str):
            tool_names.append(name)
        else:
            return AttemptEvidence(EvidenceKind.UNKNOWN, "unnamed Claude tool")
    elif event.type == "assistant_chunk":
        message = event.payload.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                tool_names.extend(
                    str(item.get("name"))
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "tool_use"
                )
    if not tool_names:
        return AttemptEvidence(EvidenceKind.READ_ONLY, "no tool action")
    if any(name in _MUTATING_CLAUDE_TOOLS for name in tool_names):
        return AttemptEvidence(EvidenceKind.MUTATION, "Claude mutation tool")
    if all(name in _READ_ONLY_CLAUDE_TOOLS for name in tool_names):
        return AttemptEvidence(EvidenceKind.READ_ONLY, "Claude read-only tool")
    return AttemptEvidence(EvidenceKind.UNKNOWN, "unknown Claude tool")


def _codex_run_kind(config: _RunConfig) -> CodexRunKind:
    if config.web_search:
        return CodexRunKind.WEB
    if config.allowed_tools and any(tool in WRITE_TOOLS for tool in config.allowed_tools):
        return CodexRunKind.AGENT_WRITE
    return CodexRunKind.AGENT_READ


@traced(event_prefix=WIKI_RUN)
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
    user_input: str = "",
    media_paths: list[Path] | None = None,
    extra_add_dirs: list[Path] | None = None,
    timeout_s: float | None = None,
) -> WikiRunResult:
    """Run one Stage-1a/1b Sonnet session against `wiki_path`.

    `media_paths` (D-022): local image/audio files the user attached. Their
    parent directories are added to `--add-dir` so the CLI's Read tool can
    open them; the file paths themselves must be referenced from `user_input`.

    `timeout_s` (D-022): per-call override of ``config.timeout_s`` — e.g. 30s for
    photo vision vs the default 300s for a text wiki turn. None → use config.

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
    if config.failover_policy is not None and config.codex_adapter is not None:
        return await _run_wiki_session_with_failover(
            wiki_id=wiki_id,
            wiki_path=wiki_path,
            base_prompt_path=base_prompt_path,
            overlay_prompt_path=overlay_prompt_path,
            run_id=run_id,
            correlation_id=correlation_id,
            runtime_dir=runtime_dir,
            acquirer=acquirer,
            spawner=spawner,
            config=config,
            on_event=on_event,
            user_input=user_input,
            media_paths=media_paths,
            extra_add_dirs=extra_add_dirs,
            timeout_s=timeout_s,
        )
    cfg = config
    effective_timeout_s = timeout_s if timeout_s is not None else cfg.timeout_s
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
    # aisw-22o: run INSIDE the WIKI dir. The base/domain prompts use relative paths
    # (raw/, metrics/, log.md) and assume cwd == the WIKI; previously cwd was a neutral
    # dir (claude_config_dir), so the model could not resolve them and asked the user
    # for the path. wiki_path already includes the per-user <telegram_id> segment
    # (<wiki_root>/<telegram_id>/<WIKI>), so this is correct for every user. WIKIs live
    # outside the dev repo, so no project CLAUDE.md is auto-discovered up the tree.
    #
    # aisw-dqz (Path B): a web_task run must NOT read the user's WIKIs (M-1). Use a
    # dedicated EMPTY neutral cwd and suppress the WIKI --add-dir, so the only file the
    # Read tool can reach is this empty dir while WebSearch answers from the live web.
    if cfg.web_search:
        cwd = runtime_dir / "web_task_cwd"
        cwd.mkdir(parents=True, exist_ok=True)
    else:
        cwd = wiki_path

    media_dirs = sorted({p.parent for p in media_paths}) if media_paths else None
    argv = _build_argv(
        binary=resolve_binary(cfg.binary),
        model=cfg.model,
        wiki_path=wiki_path,
        prompt_path=prompt_path,
        allowed_tools=cfg.allowed_tools,
        disallowed_tools=cfg.disallowed_tools,
        media_dirs=media_dirs,
        extra_add_dirs=extra_add_dirs,
        add_wiki_dir=not cfg.web_search,
    )
    if cfg.web_search:
        _log.info(
            "wiki.run.web_search.enabled",
            correlation_id=correlation_id,
            wiki_id=wiki_id,
            run_id=run_id,
            allowed_tools=cfg.allowed_tools,
        )

    _log.info(
        "wiki.run.start",
        correlation_id=correlation_id,
        wiki_id=wiki_id,
        run_id=run_id,
        model=cfg.model,
        media_count=len(media_paths) if media_paths else 0,
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
        stdin_bytes = user_input.encode("utf-8") if user_input else None
        proc = await spawner.spawn(argv, env=env, cwd=cwd, stdin_data=stdin_bytes)
        if proc.stdout is None:
            raise WikiRunnerError("spawned process has no stdout pipe")
        if stdin_bytes is not None and proc.stdin is not None:
            proc.stdin.write(stdin_bytes)
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await proc.stdin.drain()
            proc.stdin.close()

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
            exit_code = await asyncio.wait_for(_drain(), timeout=effective_timeout_s)
        except TimeoutError as e:
            with contextlib.suppress(ProcessLookupError):
                await kill_with_sequence(proc, grace_seconds=cfg.term_grace_s)
            latency_ms = int((time.monotonic() - started) * 1000)
            _log.warning(
                "wiki.run.finish",
                correlation_id=correlation_id,
                wiki_id=wiki_id,
                run_id=run_id,
                exit_code=-1,
                n_events=len(events),
                latency_ms=latency_ms,
                timeout=True,
            )
            _log.error(
                CLAUDE_CLI_ERROR,
                exit_code=None,
                duration_ms=latency_ms,
                stdout_bytes=0,
                stderr_bytes=0,
                reason="timeout",
            )
            _persist_transcript(events, transcript_path)
            raise WikiRunnerTimeoutError(f"wiki run exceeded timeout {effective_timeout_s}s") from e

    stderr_text = await _drain_stderr(proc)
    _persist_transcript(events, transcript_path)
    latency_ms = int((time.monotonic() - started) * 1000)
    stderr_bytes = len(stderr_text.encode("utf-8"))
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
        _log.error(
            CLAUDE_CLI_ERROR,
            exit_code=exit_code,
            duration_ms=latency_ms,
            stdout_bytes=0,
            stderr_bytes=stderr_bytes,
            reason="nonzero_exit",
        )
        raise WikiRunnerError(f"claude CLI exited rc={exit_code}; stderr={stderr_text}")
    # aisw-t6w: rc==0 is NOT proof of success. A blocked Write/Edit surfaces as a
    # non-empty `permission_denials` while the CLI still exits 0 — the silent
    # data-loss that aborted ingest. Make it visible (WARNING + qualified finish log)
    # so callers never read this as a clean ok.
    permission_denials = extract_permission_denials(events)
    if permission_denials:
        _log.warning(
            "wiki.run.permission_denied",
            correlation_id=correlation_id,
            wiki_id=wiki_id,
            run_id=run_id,
            denied_tools=sorted({str(d.get("tool_name")) for d in permission_denials}),
            denied_count=len(permission_denials),
        )
    _log.info(
        "wiki.run.finish",
        correlation_id=correlation_id,
        wiki_id=wiki_id,
        run_id=run_id,
        exit_code=exit_code,
        n_events=len(events),
        latency_ms=latency_ms,
        permission_denied_count=len(permission_denials),
    )
    _log.info(
        CLAUDE_CLI_EXIT,
        exit_code=exit_code,
        duration_ms=latency_ms,
        stdout_bytes=0,
        stderr_bytes=stderr_bytes,
    )
    return WikiRunResult(
        run_id=run_id,
        exit_code=exit_code,
        events=events,
        transcript_path=transcript_path,
        latency_ms=latency_ms,
        permission_denials=permission_denials,
    )


async def _run_wiki_session_with_failover(
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
    config: _RunConfig,
    on_event: Callable[[StreamEvent], Awaitable[None]] | None,
    user_input: str,
    media_paths: list[Path] | None,
    extra_add_dirs: list[Path] | None,
    timeout_s: float | None,
) -> WikiRunResult:
    """Run Claude and a safe Codex fallback under one acquired WIKI lock."""
    policy = config.failover_policy
    codex = config.codex_adapter
    if policy is None or codex is None:
        raise WikiRunnerError("failover runner requires policy and Codex adapter")
    effective_timeout_s = timeout_s if timeout_s is not None else config.timeout_s
    started = time.monotonic()
    prompt_path = assemble_prompt(
        base_path=base_prompt_path,
        overlay_path=overlay_prompt_path,
        runtime_dir=runtime_dir,
        run_id=run_id,
        wiki_path=wiki_path,
    )
    transcript_path = wiki_path / "runs" / run_id / "transcript.jsonl"
    transcript_events: list[StreamEvent] = []
    evidence = AttemptEvidenceTracker()
    media_dirs = sorted({path.parent for path in media_paths}) if media_paths else None
    claude_env = build_env(config.claude_config_dir)
    if config.web_search:
        claude_cwd = runtime_dir / "web_task_cwd"
        claude_cwd.mkdir(parents=True, exist_ok=True)
    else:
        claude_cwd = wiki_path
    claude_argv = _build_argv(
        binary=resolve_binary(config.binary),
        model=config.model,
        wiki_path=wiki_path,
        prompt_path=prompt_path,
        allowed_tools=config.allowed_tools,
        disallowed_tools=config.disallowed_tools,
        media_dirs=media_dirs,
        extra_add_dirs=extra_add_dirs,
        add_wiki_dir=not config.web_search,
    )

    async def notify(event: StreamEvent, *, track_primary: bool) -> None:
        if track_primary:
            evidence.observe(_claude_event_evidence(event))
        if on_event is None:
            return
        try:
            await on_event(event)
        except Exception as exc:
            if track_primary:
                evidence.observe(AttemptEvidence(EvidenceKind.UNKNOWN, "callback failed"))
            _log.warning(
                "wiki.run.on_event_error",
                run_id=run_id,
                correlation_id=correlation_id,
                error=type(exc).__name__,
            )
            return
        if track_primary and event.type == "assistant_chunk" and _assistant_text(event):
            evidence.observe(AttemptEvidence(EvidenceKind.DELIVERED, "streamed output"))

    async def run_claude() -> _AttemptResult:
        attempt_events: list[StreamEvent] = []
        stdin_bytes = user_input.encode("utf-8") if user_input else None
        proc = await spawner.spawn(
            claude_argv,
            env=claude_env,
            cwd=claude_cwd,
            stdin_data=stdin_bytes,
        )
        if proc.stdout is None:
            raise WikiRunnerError("spawned process has no stdout pipe")
        if stdin_bytes is not None and proc.stdin is not None:
            proc.stdin.write(stdin_bytes)
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await proc.stdin.drain()
            proc.stdin.close()

        async def drain() -> int:
            assert proc.stdout is not None
            last_type: str | None = None
            async for event in parse_stream_json(proc.stdout):
                attempt_events.append(event)
                transcript_events.append(event)
                await notify(event, track_primary=True)
                if event.type != last_type:
                    _log.info(
                        "wiki.run.event",
                        run_id=run_id,
                        correlation_id=correlation_id,
                        event_type=event.type,
                    )
                    last_type = event.type
            return await proc.wait()

        try:
            exit_code = await asyncio.wait_for(drain(), timeout=effective_timeout_s)
        except TimeoutError as exc:
            with contextlib.suppress(ProcessLookupError):
                await kill_with_sequence(proc, grace_seconds=config.term_grace_s)
            latency_ms = int((time.monotonic() - started) * 1000)
            _log.error(
                CLAUDE_CLI_ERROR,
                exit_code=None,
                duration_ms=latency_ms,
                stdout_bytes=0,
                stderr_bytes=0,
                reason="timeout",
            )
            raise WikiRunnerTimeoutError(
                f"wiki run exceeded timeout {effective_timeout_s}s"
            ) from exc
        stderr_text = await _drain_stderr(proc)
        for event in reversed(attempt_events):
            if event.type != "final":
                continue
            limit = parse_claude_subscription_limit(event.payload)
            if limit is not None:
                raise ProviderLimitError(
                    provider="claude",
                    reset_at=limit.reset_at,
                    evidence=evidence.evidence,
                )
            break
        if exit_code != 0:
            latency_ms = int((time.monotonic() - started) * 1000)
            _log.error(
                "wiki.run.error",
                correlation_id=correlation_id,
                wiki_id=wiki_id,
                run_id=run_id,
                exit_code=exit_code,
                n_events=len(attempt_events),
                latency_ms=latency_ms,
                stderr=stderr_text,
            )
            raise WikiRunnerError(f"claude CLI exited rc={exit_code}; stderr={stderr_text}")
        _log.info(
            CLAUDE_CLI_EXIT,
            exit_code=exit_code,
            duration_ms=int((time.monotonic() - started) * 1000),
            stdout_bytes=0,
            stderr_bytes=len(stderr_text.encode("utf-8")),
        )
        return _AttemptResult(exit_code=exit_code, events=attempt_events, stderr_text=stderr_text)

    async def run_codex() -> _AttemptResult:
        run_kind = _codex_run_kind(config)
        if run_kind is CodexRunKind.AGENT_WRITE:
            request_cwd = wiki_path
            writable_wiki: Path | None = wiki_path
        else:
            codex.neutral_cwd.mkdir(parents=True, exist_ok=True)
            request_cwd = codex.neutral_cwd
            writable_wiki = None
        system_prompt = prompt_path.read_text(encoding="utf-8")
        prompt_parts = [system_prompt]
        readable_paths: tuple[Path, ...] = ()
        image_paths: tuple[Path, ...] = ()
        if run_kind is not CodexRunKind.WEB:
            additional_roots = tuple(extra_add_dirs or []) + tuple(media_dirs or [])
            readable_paths = (wiki_path, *additional_roots)
            image_paths = tuple(media_paths or [])
            prompt_parts.extend(
                [
                    f"WORKSPACE_ROOT={wiki_path}",
                    "ADDITIONAL_READ_ROOTS="
                    + json.dumps([str(path) for path in additional_roots], ensure_ascii=False),
                ]
            )
        prompt_parts.extend(["<user_input>", user_input, "</user_input>"])
        codex_events = await codex.run_agent(
            CodexRequest(
                prompt="\n\n".join(prompt_parts),
                model=codex.complex_model,
                reasoning=codex.complex_reasoning,
                run_kind=run_kind,
                correlation_id=correlation_id,
                timeout_s=effective_timeout_s,
                cwd=request_cwd,
                writable_wiki=writable_wiki,
                readable_paths=readable_paths,
                image_paths=image_paths,
            )
        )
        attempt_events = [
            StreamEvent(type=event.type, payload=event.payload) for event in codex_events
        ]
        transcript_events.extend(attempt_events)
        for event in attempt_events:
            await notify(event, track_primary=False)
        return _AttemptResult(exit_code=0, events=attempt_events)

    _log.info(
        "wiki.run.start",
        correlation_id=correlation_id,
        wiki_id=wiki_id,
        run_id=run_id,
        model=config.model,
        media_count=len(media_paths) if media_paths else 0,
    )
    lock_started = time.monotonic()
    async with acquirer.acquire(wiki_id, wiki_path):
        _log.info(
            "wiki.lock.acquired",
            wiki_id=wiki_id,
            run_id=run_id,
            latency_ms=int((time.monotonic() - lock_started) * 1000),
        )
        wiki_path.mkdir(parents=True, exist_ok=True)
        try:
            attempt = await policy.execute(
                run_kind=_codex_run_kind(config).value,
                correlation_id=correlation_id,
                claude=run_claude,
                codex=run_codex,
            )
        except BaseException:
            _persist_transcript(transcript_events, transcript_path)
            raise

    _persist_transcript(transcript_events, transcript_path)
    permission_denials = extract_permission_denials(attempt.events)
    latency_ms = int((time.monotonic() - started) * 1000)
    _log.info(
        "wiki.run.finish",
        correlation_id=correlation_id,
        wiki_id=wiki_id,
        run_id=run_id,
        exit_code=attempt.exit_code,
        n_events=len(attempt.events),
        latency_ms=latency_ms,
        permission_denied_count=len(permission_denials),
    )
    return WikiRunResult(
        run_id=run_id,
        exit_code=attempt.exit_code,
        events=attempt.events,
        transcript_path=transcript_path,
        latency_ms=latency_ms,
        permission_denials=permission_denials,
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
