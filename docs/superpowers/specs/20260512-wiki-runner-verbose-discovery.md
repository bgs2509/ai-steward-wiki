---
feature: wiki-runner-verbose
bd_id: aisw-kpb
status: approved
date: 2026-05-12
functional_requirements:
  - id: FR-1
    text: "M-WIKI-RUNNER MUST pass --verbose to the claude CLI whenever it uses --print (-p) together with --output-format stream-json, so the CLI does not exit rc=1."
  - id: FR-2
    text: "A unit test MUST enforce the invariant: if 'stream-json' is present in the runner argv, then '--verbose' is also present."
non_functional_requirements:
  - id: NFR-1
    text: "No change to Spawner protocol, stdin piping, or stream-json event parsing — fix is confined to argv construction."
  - id: NFR-2
    text: "Existing happy-path test (test_run_wiki_session_happy_path) and stream parser tests must still pass."
risks:
  - "Risk: --verbose may inject extra diagnostic/system-init events into the stream. Mitigation: existing _concat_assistant_text filters by event type; FakeSpawner lines in tests already model only assistant/final events — verify happy-path still green."
scope:
  in:
    - "Add '--verbose' to _build_argv in src/ai_steward_wiki/wiki/runner.py."
    - "Add argv invariant assertion to tests/unit/wiki/test_runner.py."
    - "Bump CHANGE_SUMMARY in runner.py header."
  out:
    - "Switching wiki runner to --output-format json (non-streaming) — separate architectural change."
    - "Centralising CLI flags into claude_cli_common — separate refactor."
    - "Pre-flight CLI flag smoke-check at startup — separate hardening task."
  later:
    - "Optional: --include-partial-messages for token-level streaming (not required for MVP)."
---

# Discovery — M-WIKI-RUNNER `--verbose` for stream-json headless invocation

## Problem

Live run (2026-05-12, `proc-a78d0031`): the wiki runner spawned `claude` and it exited `rc=1`:

```
Error: When using --print, --output-format=stream-json requires --verbose
```

`wiki.run.error` with `n_events: 0`, `WikiRunnerError` propagated to the Telegram user.

## Root cause

`_build_argv` (`src/ai_steward_wiki/wiki/runner.py:264-279`) emits `-p` **and** `--output-format stream-json` but **not** `--verbose`. Claude Code CLI requires `--verbose` for stream-json under `--print` (documented in the official headless docs; same error reproduced in GitHub issues). This is a regression from `aisw-0mg` (`e63139a`), which added `-p` to the wiki runner to suppress the default Claude Code persona under subscription OAuth — `-p` made `--verbose` mandatory for the stream-json output format, but it was not added. The classifier backend was unaffected because it uses `--output-format json` (non-streaming), which does not require `--verbose`.

## Best practices (from research)

1. Canonical headless recipe: `claude -p --output-format stream-json --verbose` (Anthropic headless docs).
2. Enforce CLI-flag invariants in unit tests — `_build_argv` has changed 3+ times in recent commits (aisw-0mg, aisw-w83, aisw-adj); regressions in this function are a recurring class.

## Preflight notes

- Pre-commit infra alive: `core.hooksPath=.beads/hooks` bootstrap delegates to `pre-commit run` (`.pre-commit-config.yaml` present, `pre-commit 4.0.1` available).
- Lint baseline clean: `uv run ruff check src/ tests/` → "All checks passed!".
- Sentrux: `.sentrux/rules.toml` absent — skipped (project not onboarded).
