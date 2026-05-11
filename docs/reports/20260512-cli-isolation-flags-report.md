---
feature: cli-isolation-flags
bd_id: aisw-0mg
supersedes: aisw-adj
date: 2026-05-12
type: bugfix
status: complete
---

# Completion report — `-p` + isolation flags suppress default Claude Code persona

## Problem

Stage-0 classifier and Stage-1 wiki runner emitted Claude Code-persona responses
(«Привет! 👋 Это Claude Code…», «Вот мой основной функционал в Claude Code…»)
instead of the strict JSON / wiki-edit output dictated by `prompts/classifier.md`
and `prompts/wiki.md`. Every TG message produced
`ClassifierSchemaError: claude CLI inner JSON parse failed`.

`aisw-adj` previously claimed the cause was `--system-prompt-file` vs inline
`--system-prompt` and shipped the inline-content fix (`e274c81`). The fix
changed argv shape but **did not** alter model behaviour in production —
verified by a second runtime log dump on the same error class.

## Real root cause

Under **subscription OAuth** (no `ANTHROPIC_API_KEY`), the `claude` CLI loads
the default Claude Code system prompt, all enabled skills, and user/project
settings **regardless of `--system-prompt`**. The custom prompt is *appended*
to the agentic context rather than replacing it. `--bare` would replace it,
but requires API-key auth and is therefore off-limits.

Evidence (2026-05-12, claude 2.1.139, env-replica smoke):

| argv | `cache_creation_input_tokens` | result on "что ты умеешь" |
| --- | --- | --- |
| baseline (inline `--system-prompt`) | ~10 700 | Claude Code persona prose |
| baseline + `-p` + `--setting-sources ""` + `--disable-slash-commands` + `--tools ""` | **0** | valid classifier JSON |

`cache_creation` dropping to 0 is direct evidence that the default Claude Code
prompt is no longer being injected. The CLI `--help` also confirms `-p` is
mandatory for `--output-format json` / `stream-json` — it was missing from
both backends, an independent defect that compounded the persona leak.

## Fix

Two argv changes via existing module boundaries (no contract drift):

```python
# Stage-0 — src/ai_steward_wiki/classifier/backend.py::ClaudeCliBackend._argv
[binary, "-p", "--model", model, "--output-format", "json", "--max-turns", "1",
 *system_prompt_argv(prompt_path),
 "--setting-sources", "",
 "--disable-slash-commands",
 "--tools", "",                 # Stage-0 needs no tools at all
 "--permission-mode", "dontAsk"]

# Stage-1 — src/ai_steward_wiki/wiki/runner.py::_build_argv
[binary, "-p", "--model", model, "--add-dir", str(wiki_path),
 *system_prompt_argv(prompt_path),
 "--setting-sources", "",
 "--disable-slash-commands",     # NOT --tools "" — wiki edits need Read/Write/Edit
 "--output-format", "stream-json",
 "--permission-mode", "dontAsk"]
```

Stage-0 also retires the long `--disallowedTools Bash Read Write Edit Glob Grep WebFetch`
list in favour of `--tools ""`, which is the documented single-switch way to
disable the entire built-in tool set.

## Affected artifacts

- `src/ai_steward_wiki/classifier/backend.py` — argv + header bump (v0.0.5 → v0.0.6)
- `src/ai_steward_wiki/wiki/runner.py` — argv + header bump (v0.0.3 → v0.0.4)
- `tests/unit/classifier/test_cli_envelope.py` — assert new argv shape (`-p`,
  `--setting-sources ""`, `--disable-slash-commands`, `--tools ""`, no
  `--disallowedTools`)
- `tests/unit/wiki/test_runner.py` — assert `-p` + isolation flags
- `docs/reports/20260512-cli-isolation-flags-report.md` — this file

No change to `docs/knowledge-graph.xml` or `docs/verification-plan.xml`:
module boundaries and test IDs are unchanged.

## Verification

1. **Unit tests:** 415 / 415 pass (`uv run pytest tests/unit`).
2. **Lint:** `make lint` clean (ruff + ruff-format + mypy --strict).
3. **GRACE integrity:** `grace lint --profile standard --failOn errors` → 0 issues.
4. **Pre-commit hooks:** all green on commit `e63139a`.
5. **Production-realistic smoke (subscription OAuth, env-replica):**

   ```bash
   env -i PATH=… CLAUDE_CONFIG_DIR=/home/bgs/.local/share/ai-steward-wiki/claude-code \
     claude -p --model claude-haiku-4-5 --output-format json --max-turns 1 \
     --system-prompt "$(cat prompts/classifier.md)" \
     --setting-sources "" --disable-slash-commands --tools "" \
     --permission-mode dontAsk <<< "запиши: завтра в 10 утра встреча с врачом"
   # cache_creation_input_tokens: 0
   # result: {"intent":"reminder","confidence":0.98,
   #          "distilled_payload":{"event_description":"встреча с врачом", …}}
   ```

   Compared to baseline before fix on the same input: `cache_create ≈ 10 733`,
   `result` was Claude Code agentic prose.

## Risks / follow-ups

- **`--setting-sources ""` is a hard isolation.** If we later want per-tenant
  settings, they'll need to be passed explicitly via `--settings <json>`
  rather than relying on auto-discovery.
- **`--tools ""` on Stage-0 is intentional** — classifier should never invoke
  tools. Removing it would re-open the persona leak via tool descriptions.
- **No integration smoke in CI.** Unit tests assert argv shape, not model
  behaviour. The previous aisw-adj fix passed unit tests but failed in prod —
  follow-up: add a nightly integration test that hits a real CLI and asserts
  `cache_creation_input_tokens == 0` and `json.loads(result["result"])` returns
  a valid classifier object on a fixed corpus of Russian inputs.

## Trail of prior fix attempts

- `9d157f7` — switched `--append-system-prompt @path` → `--system-prompt-file`
  (incorrect hypothesis: file form replaces).
- `4ebb5a0`, `f08c912`, `558e8e8` — neutral cwd to avoid CLAUDE.md
  auto-discovery (correct, kept).
- `e274c81` — `--system-prompt-file` → inline `--system-prompt` content
  (incorrect: argv-shape change, no behavioural effect; shipped on
  insufficient smoke verification).
- `b08f481`, `c8c6158` — docs/specs/report for the incorrect aisw-adj diagnosis.
- `e63139a` — **this fix**: `-p` + `--setting-sources ""` +
  `--disable-slash-commands` + (Stage-0) `--tools ""`.

## Lesson

Argv-shape unit tests are insufficient verification for any change that
hypothesises about model behaviour under subscription OAuth. The aisw-adj
smoke ("привет" → valid JSON) was the smallest possible input the model
could handle even with the agentic persona; it falsely confirmed the
hypothesis. Going forward, smoke verification for CLI-flag changes must use
inputs ≥ 10 chars in the production domain (reminders / wiki queries) **and**
inspect `cache_creation_input_tokens` to confirm the default prompt is
actually absent.
