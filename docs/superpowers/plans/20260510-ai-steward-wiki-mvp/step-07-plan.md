# step-07-plan.md — Chunk 7 / M-WIKI-RUNNER

**bd_id:** aisw-x30
**Module:** M-WIKI-RUNNER
**Window estimate:** 0.55

## Goal
Implement Stage-1a/1b Sonnet runner (CLI subprocess + streaming + acquire-order
locks + atomic transcript persistence) per discovery+design 2026-05-10.

## Steps (TDD)

1. **Settings extension** — add `wiki_runner_model`, `wiki_runner_timeout_s`,
   `wiki_runner_term_grace_s` to `Settings` (frozen). No new validators.
2. **Streaming (RED → GREEN)** — `tests/unit/wiki/test_streaming.py` first:
   parse fixture (3 lines), partial-line buffering, malformed line skipped.
   Then `src/ai_steward_wiki/wiki/streaming.py`:
   `StreamEvent` Pydantic v2 frozen; `parse_stream_json(reader)` async iterator.
3. **Acquire (RED → GREEN)** — `tests/unit/wiki/test_acquire.py`: order
   serialisation (same wiki); stale-PID recovery; concurrency on different
   wikis. Then `src/ai_steward_wiki/wiki/acquire.py`:
   `LockAcquirer` Protocol + `WikiLockAdapter(WikiLockManager)`.
4. **Runner (RED → GREEN)** — `tests/unit/wiki/test_runner.py`: happy path with
   `FakeSpawner`; transcript atomic write; prompt assembly; timeout →
   kill-sequence; correlation_id structlog flow. Then
   `src/ai_steward_wiki/wiki/runner.py`: `Spawner` Protocol +
   `AsyncioSpawner` + `run_wiki_session(...)` orchestrator.
5. **Prompts** — `prompts/wiki.md` (base), `prompts/inbox.md` (Stage-1a overlay),
   `prompts/domain-health.md`, `prompts/domain-finance.md`,
   `prompts/domain-default.md`. Each starts with `semver: 1.0.0` line, RU prose,
   minimal but sane content.
6. **Barrel** — `src/ai_steward_wiki/wiki/__init__.py` with MODULE_CONTRACT +
   MODULE_MAP (BARREL role).
7. **Quality gate** — must pass before commit:
   - `uv run pytest tests/unit/wiki -q`
   - `uv run ruff check src/ai_steward_wiki/wiki tests/unit/wiki`
   - `uv run ruff format --check src/ai_steward_wiki/wiki tests/unit/wiki`
   - `uv run mypy src/ai_steward_wiki/wiki`
   - `make lint`
   - `make total-test`
8. **Commit** — `feat(M-WIKI-RUNNER): Stage-1a/1b Sonnet runner with streaming and acquire-order locks`
   with `bd_id: aisw-x30` trailer.
9. **Post-commit** — `grace-refresh` (new module added) + update breakdown.xml
   RunState (CurrentChunk=8, ClosedChunks adds 7, append note) + `bd close`.

## Verification

```bash
uv run pytest tests/unit/wiki -q
make lint
make total-test
```

## Out of scope

- systemd-run wrapping (chunk 16).
- Full domain prompt set (chunk 15).
- Real Claude CLI integration (nightly only).
- Lifecycle / pre-flight / NL naming (chunk 8).
