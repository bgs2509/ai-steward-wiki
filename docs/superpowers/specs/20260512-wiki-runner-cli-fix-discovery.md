---
feature: wiki-runner-cli-fix
bd_id: aisw-d3i
date: 2026-05-12
status: draft
module_ids: [M-WIKI-RUNNER, M-CLASSIFIER-STAGE0, M-CLAUDE-CLI-COMMON]
functional_requirements:
  - id: FR-1
    text: Stage-1 wiki runner MUST replace the default Claude Code system prompt with the assembled wiki prompt (base + overlay), not append to it, so the model adopts WIKI persona and not the default Claude Code assistant persona.
  - id: FR-2
    text: Stage-1 wiki runner MUST pass the prompt via a real CLI flag form accepted by the installed `claude` binary (concretely `--system-prompt-file <path>`); the legacy `--append-system-prompt @<path>` form MUST be removed because it is the documented cause of `exit_code=1, n_events=0` observed in production logs.
  - id: FR-3
    text: Stage-1 wiki runner MUST spawn `claude` with a neutral cwd (the read-only `claude_config_dir`) so that Claude Code's automatic CLAUDE.md walk-up does not inject the project's CLAUDE.md or global ~/.claude/CLAUDE.md into the model context; the wiki tree MUST be exposed solely via the explicit `--add-dir <wiki_path>` argument.
  - id: FR-4
    text: On non-zero CLI exit, the Stage-1 wiki runner MUST capture and log stderr (truncated, e.g. first 512 bytes) on `wiki.run.finish` (or a dedicated `wiki.run.error`) and propagate a `WikiRunnerError` upward instead of silently returning `WikiRunResult` with `exit_code != 0` and empty events — current behaviour masks failures as the user-facing fallback `"Принято."`.
  - id: FR-5
    text: Shared Claude CLI invocation primitives MUST live in a single module (`M-CLAUDE-CLI-COMMON`, e.g. `src/ai_steward_wiki/claude_cli/`) and be reused by both Stage-0 (`classifier/backend.py`) and Stage-1 (`wiki/runner.py`). Primitives in scope (strong DRY) - binary resolution (`shutil.which`), env builder (`PATH=/usr/bin:/bin` + `CLAUDE_CONFIG_DIR`), neutral-cwd resolution, the `--system-prompt-file <path>` + `--permission-mode dontAsk` flag fragment, and stderr-truncation helper for error logging. Stage-specific concerns (JSON envelope unwrap, stream-json parsing, allowed/disallowed tools, max-turns, output-format) remain in their respective backends.
non_functional_requirements:
  - id: NFR-1
    text: Unit test for Stage-1 runner MUST assert (a) `--system-prompt-file` flag with the assembled path, (b) cwd equals `claude_config_dir`, (c) on non-zero exit the runner raises and stderr appears in the log call. Regression-pin against the same bug class fixed for Stage-0 in 4ebb5a0 + f08c912.
  - id: NFR-2
    text: No change to `WikiRunResult` schema, `StreamEvent` schema, `aggregate_text`, lock acquirer, transcript persistence, or kill-sequence behaviour. Surface-level refactor only.
  - id: NFR-3
    text: New `M-CLAUDE-CLI-COMMON` module MUST carry a MODULE_CONTRACT and be reflected in `docs/knowledge-graph.xml`; both Stage-0 and Stage-1 contracts MUST add it to `DEPENDS`.
  - id: NFR-4
    text: Behaviour preserved at Stage-0 — refactor does not alter the JSON envelope unwrap, the test double `FakeClaudeRunner`, or any public Stage-0 API (`ClassifierBackend.call`).
risks:
  - id: R-1
    text: Extracting common primitives can over-generalise (premature abstraction). Mitigation — limit shared API to functions already duplicated verbatim (binary resolve, env, cwd, system-prompt-file fragment, stderr truncation). No "ClaudeInvocationBuilder" class.
  - id: R-2
    text: Stage-1 currently uses different spawn semantics than Stage-0 (asyncio.create_subprocess_exec with `stdin=DEVNULL` + stream parse; Stage-0 uses `communicate(stdin=text)`). Shared module MUST NOT try to unify spawn — each stage keeps its own Spawner Protocol; only argv/env/cwd helpers are shared.
  - id: R-3
    text: Production logs show wiki run latency 2 s with exit 1 — confirms `--append-system-prompt @path` parse-failure hypothesis, but stderr is currently not read, so verification of root cause needs a one-off invocation (or test) that captures stderr before the fix is committed.
  - id: R-4
    text: Per-WIKI CLAUDE.md inside `wiki_path` (D-007) is *expected* to be discovered. Switching cwd to `claude_config_dir` and exposing wiki via `--add-dir` MUST preserve that — verify Claude Code still walks the `--add-dir` tree for CLAUDE.md (per `f08c912` commit message, auto-discovery walks *from cwd*, not from `--add-dir`; if so, per-WIKI overlay must be assembled into the prompt explicitly via `assemble_prompt`). If verification fails, fallback is to keep cwd=wiki_path but explicitly disable parent CLAUDE.md walk (no known flag) — escalate as ADR if hit.
scope_in:
  - src/ai_steward_wiki/wiki/runner.py (_build_argv, run_wiki_session — flag + cwd + stderr logging)
  - src/ai_steward_wiki/classifier/backend.py (refactor to use shared primitives, no behaviour change)
  - src/ai_steward_wiki/claude_cli/ (new module M-CLAUDE-CLI-COMMON)
  - tests/unit/wiki/test_runner.py (regression tests for the 3 invariants)
  - tests/unit/classifier/test_cli_envelope.py (adapt assertions to shared helpers; behaviour unchanged)
  - docs/knowledge-graph.xml (new module + DEPENDS updates)
scope_out:
  - aggregate_text logic, StreamEvent parsing, lock acquisition, kill-sequence, transcript persistence
  - prompt content changes (prompts/wiki.md, prompts/classifier.md)
  - AnthropicApiBackend, systemd-run wrapper (chunk 16)
  - Pipeline-level fallback strings (ACK_TEXT_RU stays as-is)
  - Per-WIKI CLAUDE.md overlay semantics — out of scope unless R-4 verification forces escalation
---

# Discovery — Stage-1 wiki runner Claude CLI invocation fix

## Symptom

Production logs (`runtime.text_pipeline` end-to-end trace):
```
wiki.run.start  model=claude-sonnet-4-5
wiki.lock.acquired
wiki.run.finish  exit_code=1  n_events=0  latency_ms≈2100
tg.output.delivered  size=8  (== "Принято.")
```

Bot returns the same 8-byte fallback `ACK_TEXT_RU = "Принято."` (`src/ai_steward_wiki/tg/pipeline.py:117`, used at `:447`/`:894`) for every user message because `outcome.text` is empty whenever the wiki run produced zero assistant events.

## Root causes (high-confidence hypotheses, see R-3)

1. **Invalid system-prompt flag form.** `wiki/runner.py:200-201` passes `--append-system-prompt @{prompt_path}`. The Stage-0 commit `4ebb5a0` documents that this `@path` form is **not** accepted by the installed Claude CLI ("*append variant does not exist / behaves wrong*"). Stage-0 was switched to `--system-prompt-file <path>`. Stage-1 retains the broken form → CLI exits 1 during argument parsing, producing zero stream-json events.

2. **Wrong persona via auto-discovered CLAUDE.md.** Even if the flag were valid, `wiki/runner.py:310` sets `cwd=wiki_path`. Claude Code walks parent dirs from cwd to auto-discover CLAUDE.md; the wiki tree's parents include `/home/bgs/ai-steward-wiki/...` and ultimately `~/.claude/CLAUDE.md`. The model ends up adopting the "Claude Code assistant" persona, exactly the failure mode fixed in Stage-0 commit `f08c912`. The mitigation in Stage-0 — running CLI in `claude_config_dir` (neutral) — applies here verbatim.

3. **Silent failure masking.** `run_wiki_session` returns `WikiRunResult(exit_code=1, events=[])` without inspecting stderr. The pipeline layer cannot distinguish "CLI crashed" from "CLI ran fine but produced no text" — both collapse to `ACK_TEXT_RU`. This is the defect-amplifier that turned a one-flag bug into "bot replies the same word to every message".

## DRY constraint (user-directed)

User explicitly required strong DRY. Stage-0 currently owns 5 invocation primitives that Stage-1 needs verbatim:

1. Binary resolve via `shutil.which` with `/` short-circuit (Stage-0 `:147-156`).
2. Restricted env: `{"CLAUDE_CONFIG_DIR": ..., "PATH": "/usr/bin:/bin"}` (Stage-0 `:159`).
3. Neutral cwd = `claude_config_dir` (Stage-0 `:170`).
4. System-prompt flag fragment: `["--system-prompt-file", str(p)]` (Stage-0 `:133-134`).
5. Stderr truncation on rc != 0 error message (Stage-0 `:173-175`).

These will be extracted into a new minimal module `M-CLAUDE-CLI-COMMON` (`src/ai_steward_wiki/claude_cli/`) — pure functions only, no class abstractions. Stage-specific argv assembly stays in each backend.

## What stays the same

1. Stage-1 spawn semantics (`asyncio.create_subprocess_exec`, `stdin=DEVNULL`, stream-json parse) — different from Stage-0 and stays different.
2. `Spawner` Protocol in `wiki/runner.py` (chunk 16 systemd-run wrap depends on it).
3. `--add-dir <wiki_path>` for read access to the wiki tree.
4. `assemble_prompt` (base + overlay → atomic write).
5. Lock acquirer, kill-sequence, transcript persistence.

## Preflight notes

1. Pre-commit infra alive: `core.hooksPath=.beads/hooks` + `.pre-commit-config.yaml` present + `pre-commit` binary on PATH.
2. Lint baseline clean for `src/ai_steward_wiki/wiki/` and `src/ai_steward_wiki/classifier/` (`ruff check` exit 0).
3. Sentrux not onboarded (`.sentrux/rules.toml` absent) — preflight & postflight skipped per workflow rule.

## Open questions deferred to Brainstorming

1. Module name and shape: `claude_cli/argv.py` + `claude_cli/env.py`, or a single `claude_cli/common.py`? (KISS — one file unless > 200 lines.)
2. Do we keep `Spawner` Protocol duplicated between Stage-0 and Stage-1, or hoist it too? (Probably keep duplicated — signatures differ: Stage-0 spawn waits-and-returns bytes; Stage-1 spawn returns a process for streaming.)
3. Verification of R-4 — does `--add-dir` cause Claude Code to walk CLAUDE.md inside that dir? Decide before contract finalisation.
