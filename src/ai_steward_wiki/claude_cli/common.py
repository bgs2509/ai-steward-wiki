# FILE: src/ai_steward_wiki/claude_cli/common.py
# VERSION: 0.0.2
# START_MODULE_CONTRACT
#   PURPOSE: Pure-function primitives shared by Stage-0 (classifier) and Stage-1 (wiki) Claude CLI backends.
#   SCOPE: resolve_binary, build_env, neutral_cwd, system_prompt_argv, truncate_stderr.
#          No subprocess spawning; system_prompt_argv reads the prompt file (small text I/O).
#   DEPENDS: shutil, pathlib
#   LINKS: M-CLAUDE-CLI-COMMON, M-CLASSIFIER-STAGE0, M-WIKI-RUNNER, aisw-d3i, aisw-adj
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   resolve_binary - shutil.which absolute-path resolver with /-path short-circuit
#   build_env - restricted env dict (CLAUDE_CONFIG_DIR + minimal PATH) for CLI subprocess
#   neutral_cwd - working directory that does NOT auto-discover project CLAUDE.md
#   system_prompt_argv - inlines prompt file content via --system-prompt (replaces default)
#   truncate_stderr - UTF-8 decode + length cap for error log lines
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.2 - aisw-adj: switch system_prompt_argv from --system-prompt-file
#                         to inline --system-prompt with file content. The file form does
#                         NOT replace the default Claude Code system prompt under
#                         subscription auth (verified 2026-05-12, claude 2.1.139).
#   PREVIOUS:    v0.0.1 - initial extraction of duplicated invocation primitives (aisw-d3i)
# END_CHANGE_SUMMARY

from __future__ import annotations

import shutil
from pathlib import Path

__all__ = [
    "build_env",
    "neutral_cwd",
    "resolve_binary",
    "system_prompt_argv",
    "truncate_stderr",
]


def resolve_binary(binary: str) -> str:
    """Return absolute path to `binary` via shutil.which, or the value as-is.

    `/`-containing values are returned unchanged (already an explicit path).
    Resolved against the outer PATH (caller's environment), not the restricted
    PATH passed to the subprocess.
    """
    if "/" in binary:
        return binary
    resolved = shutil.which(binary)
    return resolved if resolved is not None else binary


def build_env(claude_config_dir: Path) -> dict[str, str]:
    """Restricted environment dict for the Claude CLI subprocess.

    Sets CLAUDE_CONFIG_DIR (subscription auth scope) and a minimal PATH.
    Returns a fresh dict; safe to mutate by the caller.
    """
    return {
        "CLAUDE_CONFIG_DIR": str(claude_config_dir),
        "PATH": "/usr/bin:/bin",
    }


def neutral_cwd(claude_config_dir: Path) -> Path:
    """Working directory that prevents Claude Code's CLAUDE.md auto-discovery.

    Claude Code walks parent directories from cwd to find CLAUDE.md. Running
    inside the read-only config dir avoids picking up the project's CLAUDE.md.
    """
    return claude_config_dir


def system_prompt_argv(prompt_path: Path) -> list[str]:
    """Argv fragment that REPLACES the default Claude Code system prompt.

    Inlines the prompt file content via `--system-prompt <content>`. The
    `--system-prompt-file <path>` form does NOT replace the default Claude
    Code system prompt under subscription auth (verified 2026-05-12,
    claude 2.1.139, bd aisw-adj): model defaults to the generic Claude Code
    assistant persona, ignoring classifier/wiki prompts. `--bare` mode would
    fix the file form but requires ANTHROPIC_API_KEY and breaks subscription
    auth, so it is not used here.

    Prompt files in use are single-digit KB, well below ARG_MAX. If a future
    prompt approaches command-line size limits, revisit (consider stdin or
    SDK migration).
    """
    return ["--system-prompt", prompt_path.read_text(encoding="utf-8")]


def truncate_stderr(stderr: bytes, limit: int = 512) -> str:
    """UTF-8 decode (replace errors) and truncate stderr for error log lines."""
    text = stderr.decode("utf-8", "replace")
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"
