---
feature: system-prompt-replace
bd_id: aisw-adj
status: stable
date: 2026-05-12
technology_stack:
  - python: "3.11+"
  - claude_cli: "2.1.139 (subscription auth)"
approach: inline-system-prompt
---

# Design — Inline `--system-prompt` with file content

## Decision

Change `system_prompt_argv(prompt_path)` in `src/ai_steward_wiki/claude_cli/common.py` to read the file and emit `["--system-prompt", <content>]` instead of `["--system-prompt-file", <path>]`.

## Why this over alternatives

1. **`--system-prompt-file`** (current): does NOT replace default system prompt under subscription auth (verified). Rejected.
2. **`--bare` + `--system-prompt-file`**: requires `ANTHROPIC_API_KEY`, breaks subscription auth. Rejected.
3. **Inline `--system-prompt` (chosen)**: verified to replace the default system prompt. Two prompt files in use (`classifier.md` ~2 KB, `wiki.md` similar) — far below ARG_MAX. Minimal change, single helper.
4. **Migrate to Anthropic Python SDK**: scope creep — would replace subprocess pipeline entirely. Deferred.

## Affected modules

- `M-CLAUDE-CLI-COMMON` — `system_prompt_argv` body changes; signature unchanged.
- `M-CLASSIFIER-STAGE0` — no code change; tests must accept new argv shape.
- `M-WIKI-RUNNER` — no code change; tests must accept new argv shape.

## Edge cases

- Empty / missing prompt file → `read_text` raises `FileNotFoundError`. Callers already validate `prompt_path.exists()` upstream where relevant; for the test surface this is acceptable fail-loud behaviour.
- Very large prompts (>~128 KB) → ARG_MAX risk. Not realistic for current prompts (single-digit KB). No guard added; document in module header.

## Verification

- Unit test: `system_prompt_argv` returns `["--system-prompt", <content>]` for a tmp file. Old test (if any) for `--system-prompt-file` shape updated.
- Integration test (existing, gated by `RUN_INTEGRATION=1`): re-validates end-to-end Stage-0 classification against real CLI.
- Manual smoke: run bot, send "привет" → expect classifier JSON (not generic greeting).
