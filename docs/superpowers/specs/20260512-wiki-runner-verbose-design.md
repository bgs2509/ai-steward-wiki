---
feature: wiki-runner-verbose
bd_id: aisw-kpb
status: approved
date: 2026-05-12
approach: add-verbose-flag
technology_decisions:
  - id: TD-1
    text: "Add the literal flag '--verbose' to the argv list in _build_argv, adjacent to '--output-format stream-json'. No new dependency, no API change."
  - id: TD-2
    text: "Test strategy: extend the existing happy-path argv assertions in tests/unit/wiki/test_runner.py with `assert '--verbose' in argv` and a structural invariant check `('stream-json' in argv) implies ('--verbose' in argv)`."
  - id: TD-3
    text: "Reject alternative 'switch to --output-format json': the wiki runner and run_and_deliver are built around streaming delivery (per Spec-WIKI D-decisions); changing it is out of scope for a bugfix."
---

# Design — M-WIKI-RUNNER `--verbose` for stream-json

## Chosen approach: add `--verbose` flag (option A from `/best`, 70/100)

### Change

`src/ai_steward_wiki/wiki/runner.py` — in `_build_argv`, the argv list currently contains:

```python
"--output-format", "stream-json",
```

Add `"--verbose"` immediately before `"--output-format"` (or after `"stream-json"` — order is irrelevant to the CLI). Bump the `START_CHANGE_SUMMARY` block in the file header to a new patch version referencing `aisw-kpb`.

### Test

`tests/unit/wiki/test_runner.py::test_run_wiki_session_happy_path` — add after the existing `assert "stream-json" in argv`:

```python
# aisw-kpb: claude CLI rejects --print + stream-json without --verbose.
assert "--verbose" in argv
assert ("stream-json" not in argv) or ("--verbose" in argv)  # invariant
```

(The second assertion is the durable invariant; kept explicit so a future argv edit that drops one but not the other fails loudly.)

### Why not the alternatives

- **`--output-format json`** — drops streaming; large refactor of `run_and_deliver`; contradicts Spec-WIKI streaming-delivery decisions. Out of scope.
- **Centralise flags in `claude_cli_common`** — good follow-up DRY refactor, but not required to fix the bug; would widen the diff and the blast radius.
- **Startup CLI smoke-check** — hardening, not a fix; separate task.

### Code-quality check (17 principles)

- Strengthens **Fail Fast** (correct config up-front instead of rc=1 at first user message) and **Explicit > Implicit** (required flag stated explicitly, mirroring the classifier's deliberate non-streaming choice).
- Adds **Testability**: the invariant becomes an executable contract.
- No principle weakened. No red flags.
