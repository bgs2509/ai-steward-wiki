---
feature: wiki-runner-stdin-pipe
bd_id: aisw-w83
follows: aisw-0mg
date: 2026-05-12
type: bugfix
status: complete
commit: e37721f
---

# Completion report — pipe user_input to claude stdin in Stage-1 wiki runner

## Problem

After `aisw-0mg` (`e63139a`) added `-p` to the wiki-runner argv, every TG
text turn failed at the Stage-1 stage with exit_code=1:

```
Error: Input must be provided either through stdin or as a prompt argument
when using --print
```

Reproduced live 2026-05-11T22:10:24Z (`correlation_id=tg-1700-763463467`)
and 2026-05-11T22:10:43Z (`correlation_id=tg-1702-763463467`).

## Root cause

Two interacting defects:

1. **`AsyncioSpawner` used `stdin=DEVNULL`** for the Stage-1 subprocess
   (`src/ai_steward_wiki/wiki/runner.py:132` pre-fix). With `-p` (= `--print`)
   the CLI mandates a user prompt via stdin or as a positional argument.
2. **User text was smuggled into the system-prompt overlay**
   (`src/ai_steward_wiki/__main__.py:206` pre-fix wrote
   `header + text` into the per-run overlay scratch file). Even if claude
   had accepted empty stdin, this is semantically wrong: the Telegram
   message is a *user turn*, not part of the *system* persona.

The Stage-0 classifier was unaffected because its own `AsyncioSpawner`
already piped `text.encode("utf-8")` to stdin (`backend.py:171`).

## Fix

Aligned Stage-1 with the Stage-0 pattern, minimum surface area:

1. `Spawner` Protocol extended with `stdin_data: bytes | None = None`.
2. `AsyncioSpawner.spawn` switches to `stdin=PIPE` when `stdin_data` is
   provided, otherwise keeps `DEVNULL` (back-compat for tests / future
   no-input runs).
3. `SpawnedProcess` Protocol gained `stdin: asyncio.StreamWriter | None`.
4. `run_wiki_session(..., user_input: str = "")`: after spawn, if
   `user_input` non-empty, write bytes → drain → close stdin (suppressing
   `BrokenPipeError`/`ConnectionResetError`) before the stdout drain loop.
5. `WikiRunner.run` in `__main__.py` stops concatenating user text into
   the overlay; the overlay scratch now holds only the semver-valid
   `# User turn` header. The Telegram text is passed via `user_input`.

Module version bumped `0.0.4 → 0.0.5`. `LINKS` extended with `aisw-w83`.
No public-API drift in `M-WIKI-RUNNER` beyond the new optional kwarg.

## Tests

1. **New** — `tests/unit/wiki/test_runner.py::test_run_wiki_session_pipes_user_input_to_stdin`
   asserts that `FakeSpawner.calls[0]["stdin_data"]` equals
   `"что ты умеешь".encode()` AND that the assembled system-prompt file
   does **not** contain the user turn.
2. **Updated** — happy-path test now also asserts `stdin_data is None`
   when `user_input` is omitted (regression guard for the default).
3. `tests/unit/wiki/conftest.py::FakeSpawner.spawn` accepts and records
   `stdin_data`; `FakeProcess` exposes `stdin` for Protocol compliance.

## Verification

```bash
uv run pytest tests/unit                 # 416 passed
make lint                                # ruff + ruff-format + mypy --strict OK
.git/hooks/pre-commit (via git commit)   # gitleaks + ruff + mypy OK
```

## Files

```
src/ai_steward_wiki/wiki/runner.py   (+15 / −3, v0.0.4 → v0.0.5)
src/ai_steward_wiki/__main__.py      (+5 / −9)
tests/unit/wiki/runner.py            (new test, updated happy-path assert)
tests/unit/wiki/conftest.py          (FakeSpawner/FakeProcess plumbing)
```

## Commit

```
e37721f fix(M-WIKI-RUNNER): pipe user_input to claude stdin (aisw-w83)
```

## Follow-ups (out of scope for aisw-w83)

1. Proper Inbox staging (chunks 21+) — replace the per-run scratch overlay
   with a real Inbox artifact; the `user_input` plumbing established here
   stays.
2. Consider tightening `Spawner` Protocol: once all call sites pass
   `stdin_data` explicitly, drop the default and remove the DEVNULL branch.
