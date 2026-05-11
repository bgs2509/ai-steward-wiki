---
feature: system-prompt-replace
bd_id: aisw-adj
date: 2026-05-12
type: bugfix
status: complete
---

# Completion report — `--system-prompt-file` replaced with inline `--system-prompt`

## Problem

Stage-0 classifier and Stage-1 wiki runner both invoked `claude --system-prompt-file <path>` under subscription auth (`CLAUDE_CONFIG_DIR` set, no `ANTHROPIC_API_KEY`). The file form does NOT replace the default Claude Code system prompt — model ignored `prompts/classifier.md` / `prompts/wiki.md` and responded as a generic Claude Code assistant. Visible in runtime logs as `ClassifierSchemaError: claude CLI inner JSON parse failed: 'Привет! 👋 …'` on every user message.

## Verification of root cause

Reproduced with the bot's exact env on 2026-05-12 (claude 2.1.139):

```bash
env -i PATH=/usr/bin:/bin \
  CLAUDE_CONFIG_DIR=/home/bgs/.local/share/ai-steward-wiki/claude-code \
  claude --model claude-haiku-4-5 --output-format json --max-turns 1 \
  --system-prompt-file prompts/classifier.md … <<< "привет"
# → result: "Привет! 👋 Это Claude Code. Чем я могу тебе помочь?"

# same command with inline form:
  --system-prompt "$(cat prompts/classifier.md)" …
# → result: {"intent":"unknown","confidence":0.15,"distilled_payload":{…}}
```

`--bare` mode would make `--system-prompt-file` replace properly but requires `ANTHROPIC_API_KEY` and disables subscription auth — not viable.

## Fix

Single change in `src/ai_steward_wiki/claude_cli/common.py::system_prompt_argv`:

```python
# before
return ["--system-prompt-file", str(prompt_path)]
# after
return ["--system-prompt", prompt_path.read_text(encoding="utf-8")]
```

Signature unchanged → no code change in `M-CLASSIFIER-STAGE0` (`classifier/backend.py`) or `M-WIKI-RUNNER` (`wiki/runner.py`). Both inherit the fix via the shared helper.

## Affected artifacts

- `src/ai_steward_wiki/claude_cli/common.py` — body + header (v0.0.1 → v0.0.2)
- `src/ai_steward_wiki/classifier/backend.py` — CHANGE_SUMMARY only (v0.0.4 → v0.0.5)
- `src/ai_steward_wiki/wiki/runner.py` — CHANGE_SUMMARY only (v0.0.2 → v0.0.3)
- `tests/unit/claude_cli/test_common.py` — assert new argv shape, use tmp prompt file
- `tests/unit/classifier/test_cli_envelope.py` — `prompt_file` fixture; rename test
- `tests/unit/wiki/test_runner.py` — assert `--system-prompt` (not `--system-prompt-file`)
- `docs/superpowers/specs/20260512-system-prompt-replace-{discovery,design}.md` — new
- `docs/reports/20260512-system-prompt-replace-report.md` — this file

No changes to `docs/knowledge-graph.xml` or `docs/verification-plan.xml` (no public surface or test-id drift).

## Verification

1. **Unit tests:** 415 / 415 pass (`uv run pytest tests/unit`).
2. **Lint:** `make lint` clean (ruff check + format + mypy --strict).
3. **GRACE integrity:** `grace lint --profile standard --failOn errors` → 0 issues.
4. **Manual smoke (bot env replica):** `env -i CLAUDE_CONFIG_DIR=… claude … --system-prompt "$(cat prompts/classifier.md)" <<< "привет"` → proper classifier JSON returned.
5. **Pre-commit hooks:** all passed on the fix commit (ruff, ruff-format, mypy --strict, secret scan).

## Risks / follow-ups

- **ARG_MAX:** prompts currently single-digit KB; ARG_MAX on Linux is ~128 KB. No guard added; documented in helper docstring. If prompts grow large, switch to stdin or migrate to Anthropic SDK.
- **Future CLI behaviour:** if a later `claude` version makes `--system-prompt-file` replace under subscription auth, the inline form still works — no urgency to revert. The fix is forward-compatible.

## Trail of prior fix attempts (history)

- `9d157f7` — switched from `--append-system-prompt @path` → `--system-prompt-file` (incorrect hypothesis: file form replaces)
- `4ebb5a0`, `f08c912` — added neutral `cwd` to avoid CLAUDE.md auto-discovery (correct, kept)
- `558e8e8` — same for wiki runner (correct, kept)
- `e274c81` — **this fix**: inline `--system-prompt` content

The neutral-cwd work was necessary but not sufficient. The actual blocker was the file-vs-inline form.
