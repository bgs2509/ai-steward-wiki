# Wiki Runner CLI Fix — Completion Report

**Date:** 2026-05-12
**Epic:** `aisw-d3i`
**Status:** Done
**Commits:** `558e8e8` (fix), `a8e7158` (GRACE refresh)

## Problem

Stage-1 wiki runner called Claude CLI with `--append-system-prompt @path`, which the CLI rejects (`exit_code=1`, `n_events=0`). The pipeline silently fell back to `ACK_TEXT_RU`, so the bot replied "Принято." to every message. Same class of bug as Stage-0 fixes in `4ebb5a0` + `f08c912`.

## Root Causes

1. Invalid flag form `--append-system-prompt @path` (CLI accepts `--system-prompt-file <path>` only).
2. CLI cwd was the wiki path → Claude Code auto-discovered the project's `CLAUDE.md` and overrode the prompt.
3. Non-zero exit drained no stderr → failure was invisible in logs.

## Changes

1. **New module `M-CLAUDE-CLI-COMMON`** (`src/ai_steward_wiki/claude_cli/common.py`) — 5 pure helpers shared by Stage-0 and Stage-1:
   - `resolve_binary`, `build_env`, `neutral_cwd`, `system_prompt_argv`, `truncate_stderr`.
2. **Stage-1 runner** (`src/ai_steward_wiki/wiki/runner.py` v0.0.2):
   - `--system-prompt-file <path>` (replaces, does not append).
   - `cwd = neutral_cwd(claude_config_dir)` — no project `CLAUDE.md` auto-discovery.
   - `assemble_prompt(..., wiki_path=...)` folds per-WIKI `CLAUDE.md` into the assembled prompt.
   - On `rc != 0`: drains stderr (4 KiB / 1 s bounded), logs `wiki.run.error`, raises `WikiRunnerError`.
3. **Stage-0 classifier** (`backend.py` v0.0.4) — refactored to import shared primitives (no behaviour change).
4. **`__main__.py`** — fail-fast guard: `claude_config_dir` must be set before constructing `_WikiRunnerAdapter`.

## Verification

1. 415 unit tests pass.
2. `ruff` + `ruff-format` + `mypy --strict` + `gitleaks` clean (pre-commit).
3. `grace lint --profile standard --failOn errors` exit 0.
4. New tests:
   - `test_run_wiki_session_nonzero_exit_raises_with_stderr` — stderr captured + raised on `rc != 0`.
   - `test_assemble_prompt_folds_per_wiki_claude_md` / `..._without_per_wiki_file`.
   - 10 unit tests for `claude_cli.common`.

## GRACE Artifacts

1. `docs/knowledge-graph.xml` — added `<M-CLAUDE-CLI-COMMON>` block.
2. `docs/development-plan.xml` — added `<M-CLAUDE-CLI-COMMON bd_id="aisw-d3i">` entry.
3. `MODULE_CONTRACT` headers updated in `common.py`, `runner.py`, `backend.py`.

## Follow-ups

1. `M-CLASSIFIER-STAGE0` and `M-WIKI-RUNNER` already existed as `MODULE_CONTRACT` headers but are still absent from `knowledge-graph.xml`. Deferred — out of targeted scope for this feature; needs a separate `grace-refresh --full` session.
