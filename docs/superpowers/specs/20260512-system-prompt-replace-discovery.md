---
feature: system-prompt-replace
bd_id: aisw-adj
status: stable
date: 2026-05-12
functional_requirements:
  - FR-1: Stage-0 classifier CLI invocation MUST cause Claude to follow `prompts/classifier.md` as the sole system prompt (no default Claude Code persona).
  - FR-2: Stage-1 wiki runner CLI invocation MUST cause Claude to follow `prompts/wiki.md` (+ domain prompt) as the sole system prompt.
  - FR-3: Both invocations MUST continue to work under subscription auth (CLAUDE_CONFIG_DIR), not require ANTHROPIC_API_KEY.
non_functional_requirements:
  - NFR-1: No additional subprocess overhead (no extra file reads per call beyond what already happens).
  - NFR-2: "Backwards-compatible signature for `system_prompt_argv(prompt_path: Path)` — callers stay unchanged."
  - NFR-3: "Argv MUST NOT leak prompt text into process listings beyond what `--system-prompt` already does (acceptable: same as inline prompts elsewhere)."
risks:
  - R-1: "Very large prompts → command-line ARG_MAX. Mitigation: prompts/classifier.md ≈ 2 KB, prompts/wiki.md similar — well below ARG_MAX (typically 128 KB+). Document limit; fail loud if exceeded."
  - R-2: "Future CLI version may make `--system-prompt-file` replace properly. Mitigation: leave a `# WHY:` comment pointing at this bd_id so a future cleanup is intentional."
scope:
  in:
    - Edit `system_prompt_argv` in `src/ai_steward_wiki/claude_cli/common.py`.
    - Bump VERSION + CHANGE_SUMMARY in the affected module.
    - Tests covering the new argv shape.
  out:
    - Refactor of broader CLI invocation primitives.
    - Migration to Anthropic SDK / `--bare` mode.
    - Tests against the real Claude CLI (covered by integration suite, off by default).
---

# Discovery — Fix `--system-prompt-file` not replacing default Claude Code system prompt

## Problem (verified)

`claude --system-prompt-file <path>` under subscription auth (CLAUDE_CONFIG_DIR set, no ANTHROPIC_API_KEY) does NOT replace the default Claude Code system prompt. The classifier prompt is ignored; model responds as a generic Claude Code assistant. Reproduced 2026-05-12 with `claude` 2.1.139:

```
env -i PATH=/usr/bin:/bin CLAUDE_CONFIG_DIR=/home/bgs/.local/share/ai-steward-wiki/claude-code \
  claude --model claude-haiku-4-5 --output-format json --max-turns 1 \
  --system-prompt-file prompts/classifier.md … <<< "привет"
# result: "Привет! 👋 Это Claude Code. Чем я могу тебе помочь?"
```

With inline `--system-prompt "$(cat prompts/classifier.md)"` same env:

```
result: {"intent":"unknown","confidence":0.15,"distilled_payload":{…}}
```

`--bare` enables `--system-prompt-file` replacement but disables subscription auth → not viable.

## Root cause

`system_prompt_argv` in `src/ai_steward_wiki/claude_cli/common.py:73` emits `--system-prompt-file <path>`, which the CLI treats as additive context, not a replacement, under subscription auth.

## Fix shape

Replace argv builder to read prompt file at call time and pass content via `--system-prompt`:

```python
def system_prompt_argv(prompt_path: Path) -> list[str]:
    return ["--system-prompt", prompt_path.read_text(encoding="utf-8")]
```

This impacts both Stage-0 classifier and Stage-1 wiki runner (single helper).

## Open questions

None — fix shape is mechanical and verified.

## Preflight

- pre-commit: configured (`.pre-commit-config.yaml` present, beads hooks path active).
- lint baseline: `uv run ruff check src/` → clean.
- sentrux: not onboarded (no `.sentrux/rules.toml`) — skip.
