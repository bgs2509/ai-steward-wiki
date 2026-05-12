# Implementation Plan — M-WIKI-RUNNER `--verbose` (bd aisw-kpb)

SSoT for execution. Approach: add `--verbose` to `_build_argv` + enforce invariant in unit test.

## Task 1 — RED: add failing argv assertions

File: `tests/unit/wiki/test_runner.py`, in `test_run_wiki_session_happy_path`, after `assert "stream-json" in argv`:

```python
# aisw-kpb: claude CLI rejects --print + stream-json without --verbose.
assert "--verbose" in argv
assert ("stream-json" not in argv) or ("--verbose" in argv)
```

Run: `uv run pytest tests/unit/wiki/test_runner.py -q` → expect FAIL on `assert "--verbose" in argv`.

## Task 2 — GREEN: add `--verbose` to `_build_argv`

File: `src/ai_steward_wiki/wiki/runner.py`, in `_build_argv` argv list, change:

```python
        "--output-format",
        "stream-json",
```
to:
```python
        "--verbose",
        "--output-format",
        "stream-json",
```

Run: `uv run pytest tests/unit/wiki/test_runner.py -q` → expect PASS.

## Task 3 — Bump CHANGE_SUMMARY header

File: `src/ai_steward_wiki/wiki/runner.py` — add a new `LAST_CHANGE` line in `START_CHANGE_SUMMARY` for `aisw-kpb` (add `--verbose`; required by claude CLI for `--print` + `stream-json`), demote current entry to `PREVIOUS`.

## Task 4 — Full gate

- `uv run pytest tests/unit -q`
- `make lint`

## Verification

- FR-1: Task 2 — `--verbose` present in argv. FR-2: Task 1 — invariant assertion. NFR-2: Task 4 — full unit suite green.
