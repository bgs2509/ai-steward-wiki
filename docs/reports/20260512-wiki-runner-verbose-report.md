---
feature: wiki-runner-verbose
bd_id: aisw-kpb
follows: aisw-w83
date: 2026-05-12
type: bugfix
status: complete
commit: fd202bc
---

# Completion report — add `--verbose` for stream-json headless invocation in Stage-1 wiki runner

## Problem

Every TG text turn failed at the Stage-1 stage with exit_code=1 and zero
events emitted:

```
Error: When using --print, --output-format=stream-json requires --verbose
```

Reproduced live 2026-05-12T06:20:09Z (`correlation_id=tg-1708-763463467`,
`run_id=run-e40c4fe907f2`): `wiki.run.error` with `n_events=0`,
`WikiRunnerError` propagated through `run_wiki_session → run_and_deliver →
_run_text_pipeline` to the Telegram user.

## Root cause

`_build_argv` (`src/ai_steward_wiki/wiki/runner.py`) emitted `-p`
(= `--print`) **and** `--output-format stream-json` but **not**
`--verbose`. Claude Code CLI requires `--verbose` for the `stream-json`
output format under `--print` — without it there is nothing to stream
line-by-line, so the CLI exits during flag validation (before reading
stdin). This is a regression from `aisw-0mg` (`e63139a`), which added
`-p` to the wiki runner to suppress the default Claude Code persona under
subscription OAuth; `-p` made `--verbose` mandatory for `stream-json`, but
it was not added at the time.

The Stage-0 classifier was unaffected: it uses `--output-format json`
(non-streaming, `classifier/backend.py`), which does not require
`--verbose`.

## Fix

Minimum surface area — one literal flag:

1. `_build_argv` argv list now includes `"--verbose"` immediately before
   `"--output-format", "stream-json"`.
2. `START_CHANGE_SUMMARY` header bumped `v0.0.5 → v0.0.6` with the
   `aisw-kpb` rationale; prior entry demoted to `PREVIOUS`.

No public-API change in `M-WIKI-RUNNER`; no contract / knowledge-graph /
verification-plan drift (`grace lint` standard profile: 0 errors,
0 warnings).

## Tests

1. **Updated** — `tests/unit/wiki/test_runner.py::test_run_wiki_session_happy_path`
   now asserts `"--verbose" in argv` plus the durable invariant
   `("stream-json" not in argv) or ("--verbose" in argv)`, so a future
   argv edit that drops one but not the other fails loudly.

TDD: RED confirmed (assertion failed against pre-fix argv), then GREEN.

## Verification

```bash
uv run pytest tests/unit                 # 416 passed, 1 pre-existing warning
make lint                                # ruff + ruff-format + mypy --strict OK
grace lint --failOn errors               # 0 issues (66 governed files, 3 XML)
.git/hooks/pre-commit (via git commit)   # trailing-ws + ruff + mypy + gitleaks OK
```

## Files

```
src/ai_steward_wiki/wiki/runner.py   (+8 / −1, v0.0.5 → v0.0.6)
tests/unit/wiki/test_runner.py       (+3, argv invariant assertions)
docs/superpowers/specs/20260512-wiki-runner-verbose-discovery.md  (new)
docs/superpowers/specs/20260512-wiki-runner-verbose-design.md     (new)
docs/superpowers/plans/20260512-wiki-runner-verbose-plan.md       (new)
```

## Commits

```
fd202bc fix(M-WIKI-RUNNER): add --verbose for stream-json headless invocation (aisw-kpb)
9cbad4e docs(aisw-kpb): discovery + design + plan for wiki-runner --verbose fix
```

## Follow-ups (out of scope for aisw-kpb)

1. DRY: centralise the shared Claude-CLI flag set (`-p`,
   `--setting-sources ""`, `--disable-slash-commands`, `--permission-mode
   dontAsk`, and — when streaming — `--verbose`) into a parameterised
   factory in `claude_cli/common.py`, used by both the classifier and the
   wiki runner. The split argv builders are why this regression landed in
   one backend but not the other.
2. Startup / nightly-integration smoke-check that the assembled CLI flag
   set is accepted by the installed `claude` version, so future CLI
   breaking changes fail at deploy time rather than at the first user
   message.
