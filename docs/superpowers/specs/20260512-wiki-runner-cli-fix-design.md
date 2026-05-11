---
feature: wiki-runner-cli-fix
bd_id: aisw-d3i
date: 2026-05-12
status: draft
discovery: docs/superpowers/specs/20260512-wiki-runner-cli-fix-discovery.md
module_ids: [M-WIKI-RUNNER, M-CLASSIFIER-STAGE0, M-CLAUDE-CLI-COMMON]
chosen_approach: shared-primitives-single-file
stack:
  - python: "3.11+"
  - stdlib: shutil, pathlib, asyncio
  - test: pytest
new_modules:
  - id: M-CLAUDE-CLI-COMMON
    path: src/ai_steward_wiki/claude_cli/common.py
    purpose: Pure-function primitives shared by Stage-0 (classifier) and Stage-1 (wiki) Claude CLI backends.
    exports:
      - resolve_binary
      - build_env
      - neutral_cwd
      - system_prompt_argv
      - truncate_stderr
changed_modules:
  - id: M-CLASSIFIER-STAGE0
    file: src/ai_steward_wiki/classifier/backend.py
    change: Refactor to import shared primitives. No behaviour change. Deletes 5 inline duplicates.
  - id: M-WIKI-RUNNER
    file: src/ai_steward_wiki/wiki/runner.py
    change: (a) replace --append-system-prompt @path with --system-prompt-file <path>; (b) cwd=neutral_cwd; (c) read+truncate stderr; (d) raise WikiRunnerError on rc!=0; (e) require claude_config_dir; (f) assemble_prompt also folds in per-WIKI CLAUDE.md if present.
verification:
  - id: V-1
    text: New unit test pins argv for Stage-1 — asserts --system-prompt-file path equals assembled prompt and --append-system-prompt is absent.
  - id: V-2
    text: New unit test pins cwd for Stage-1 — equals claude_config_dir, not wiki_path.
  - id: V-3
    text: New unit test — fake Spawner returns proc with exit_code=1 + stderr=b"bad flag"; run_wiki_session raises WikiRunnerError and logs wiki.run.error with stderr truncated.
  - id: V-4
    text: Existing Stage-0 envelope unwrap test stays green after refactor (no behaviour change).
  - id: V-5
    text: New unit test for assemble_prompt — if wiki_path/CLAUDE.md exists, its contents appear after the overlay in the assembled file.
---

# Design — Stage-1 wiki runner Claude CLI invocation fix

## Goal

Make the Stage-1 wiki runner actually run. Fix the three defects from Discovery (invalid flag form, cwd leakage, silent failure masking) and extract the now-twice-duplicated invocation primitives into one module reused by both Stage-0 and Stage-1.

## Single-track approach: shared-primitives-single-file

No competing alternatives proposed — the scope is one bug-class with one obvious fix. Three sub-decisions were made in Brainstorming:

1. **Shape of shared module → single file `claude_cli/common.py`** with five pure functions. KISS. No class wrapper. Split only when > 200 LOC.
2. **`Spawner` Protocol stays duplicated.** Stage-0 returns `(rc, bytes, bytes)`; Stage-1 returns a streaming process. Union return is worse than two narrow Protocols.
3. **Per-WIKI CLAUDE.md → fold into `assemble_prompt` explicitly.** Does not rely on undocumented Claude Code auto-discovery behaviour through `--add-dir`. Survives the cwd change.

## Shared module — `src/ai_steward_wiki/claude_cli/common.py`

```python
def resolve_binary(binary: str) -> str:
    """Absolute path if found on outer PATH, else binary as-is (/-paths pass through)."""

def build_env(claude_config_dir: Path) -> dict[str, str]:
    """Restricted env for CLI subprocess: CLAUDE_CONFIG_DIR + minimal PATH."""

def neutral_cwd(claude_config_dir: Path) -> Path:
    """Working directory that does NOT auto-discover project CLAUDE.md."""

def system_prompt_argv(prompt_path: Path) -> list[str]:
    """Flag fragment replacing default Claude Code system prompt with the given file."""

def truncate_stderr(stderr: bytes, limit: int = 512) -> str:
    """UTF-8 decode (replace errors) + length cap, for error log lines."""
```

All five are pure (`build_env` returns a fresh dict; no globals). Each has a 1-line docstring. No protocols, no classes.

## Stage-0 refactor (`classifier/backend.py`)

1. Delete inline `_resolve_binary`.
2. Replace `env = {...}` literal with `build_env(self.claude_config_dir)`.
3. Replace `cwd=str(self.claude_config_dir)` with `cwd=str(neutral_cwd(self.claude_config_dir))`.
4. Replace `["--system-prompt-file", str(prompt_path)]` literal in `_argv` with `system_prompt_argv(prompt_path)` spread.
5. On `rc != 0`: keep current `ClassifierError(...)` but build message via `truncate_stderr(stderr)`.
6. Bump MODULE_CONTRACT CHANGE_SUMMARY to v0.0.4 (refactor: import shared primitives, no behaviour change). DEPENDS adds M-CLAUDE-CLI-COMMON.

Tests (`tests/unit/classifier/test_cli_envelope.py`) unchanged in intent; minor edits where they previously asserted via the now-deleted inline helpers.

## Stage-1 fix (`wiki/runner.py`)

### `_build_argv`

```python
def _build_argv(*, binary, model, wiki_path, prompt_path, allowed_tools, disallowed_tools):
    argv = [
        binary,
        "--model", model,
        "--add-dir", str(wiki_path),
        *system_prompt_argv(prompt_path),       # <-- replaces --append-system-prompt @path
        "--output-format", "stream-json",
        "--permission-mode", "dontAsk",
    ]
    if allowed_tools:    argv.extend(["--allowedTools", *allowed_tools])
    if disallowed_tools: argv.extend(["--disallowedTools", *disallowed_tools])
    return argv
```

### `_RunConfig`

`claude_config_dir: Path` becomes **required** (no `| None`, no default). Production already always sets it (`__main__.py:376`); tests update to pass an explicit path (or a tmp_path fixture).

### `run_wiki_session`

Two changes around `spawner.spawn`:

```python
env = build_env(cfg.claude_config_dir)
cwd = neutral_cwd(cfg.claude_config_dir)
proc = await spawner.spawn(argv, env=env, cwd=cwd)   # was cwd=wiki_path
```

After the stream-drain loop, before returning success and inside the timeout branch:

```python
stderr_bytes = b""
if proc.stderr is not None:
    try:
        stderr_bytes = await asyncio.wait_for(proc.stderr.read(4096), timeout=1.0)
    except (TimeoutError, asyncio.IncompleteReadError):
        pass

if exit_code != 0:
    _log.error(
        "wiki.run.error",
        correlation_id=correlation_id, wiki_id=wiki_id, run_id=run_id,
        exit_code=exit_code, n_events=len(events),
        stderr=truncate_stderr(stderr_bytes),
    )
    _persist_transcript(events, transcript_path)
    raise WikiRunnerError(
        f"claude CLI exited rc={exit_code}; stderr={truncate_stderr(stderr_bytes)}"
    )
```

`SpawnedProcess` Protocol gains an optional `stderr: asyncio.StreamReader | None` attribute (asyncio's `Process` already exposes it — only the Protocol changes).

### `assemble_prompt`

```python
def assemble_prompt(*, base_path, overlay_path, runtime_dir, run_id, wiki_path=None):
    base = base_path.read_text(...)
    overlay = overlay_path.read_text(...)
    _check_semver(base, ...); _check_semver(overlay, ...)
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
```

`wiki_path` is **optional** to keep the function pure-by-default for existing tests. `run_wiki_session` passes it.

`_check_semver` is NOT applied to per-WIKI CLAUDE.md — it is operator-authored, not framework-versioned.

### MODULE_CONTRACT bump

Bump `wiki/runner.py` to v0.0.2: change summary mentions FR-1..4 + DEPENDS adds M-CLAUDE-CLI-COMMON.

## Pipeline-side effect of raising

`WikiRunnerError` propagates up to `tg/pipeline.py`. Existing handler path (look for `ACK_RUNNER_ERR_RU = "Задача заняла слишком много времени, попробуйте позже."`) — verify in Execution that pipeline catches `WikiRunnerError` and surfaces a real error message rather than the silent `"Принято."` fallback. If pipeline currently only catches `WikiRunnerTimeoutError`, broaden to `WikiRunnerError`. This is an in-scope side effect of FR-4.

## Testing strategy

Five unit tests (see verification block). All exercise `run_wiki_session` with a fake Spawner that returns a process with scripted `stdout` (an `asyncio.StreamReader` pre-loaded with bytes) and `stderr`. No integration test required — pure subprocess argv plumbing.

## Out of scope (carry-over from Discovery)

1. Prompt content changes.
2. AnthropicApiBackend wiring.
3. systemd-run wrapper (chunk 16).
4. Pipeline fallback string redesign (only the catch clause is touched).
5. Any change to `aggregate_text`, `parse_stream_json`, lock acquirer, kill-sequence, transcript persistence.

## Risk register update

R-3 (production stderr unknown) — superseded: the fix itself adds the stderr-reading code, so the next run will confirm the hypothesis in logs. No throwaway reproducer needed.

R-4 (per-WIKI CLAUDE.md auto-discovery) — resolved by explicit fold-in in `assemble_prompt`. No dependency on Claude Code internals.
