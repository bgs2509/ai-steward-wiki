# Design — M-WIKI-RUNNER

**Discovery:** `20260510-wiki-runner-discovery.md` (stable).
**Status:** stable.
**Date:** 2026-05-10.

## Module layout

```
src/ai_steward_wiki/wiki/
├── __init__.py     # MODULE_CONTRACT (BARREL), re-exports
├── acquire.py      # LockAcquirer Protocol + WikiLockAdapter (uses scheduler.locks)
├── streaming.py    # StreamEvent (Pydantic v2 frozen) + parse_stream_json async iter
└── runner.py       # run_wiki_session orchestrator, Spawner Protocol seam
```

`prompts/wiki.md`, `prompts/inbox.md`, `prompts/domain-{health,finance,default}.md`
— each starts with `semver: 1.0.0` frontmatter, RU prose.

## Type seams

```python
class Spawner(Protocol):
    async def spawn(
        self, argv: list[str], *, env: dict[str, str], cwd: Path,
    ) -> "SpawnedProcess": ...

class SpawnedProcess(Protocol):
    pid: int
    stdout: asyncio.StreamReader
    async def wait(self) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...

class LockAcquirer(Protocol):
    def acquire(self, wiki_id: str, wiki_path: Path) -> AbstractAsyncContextManager[None]: ...
```

`AsyncioSpawner` wraps `asyncio.create_subprocess_exec`. `WikiLockAdapter`
delegates to `scheduler.locks.WikiLockManager` (already merged in chunk 4).
Tests inject `FakeSpawner` (lines + exit_code, optional sleep / SIGTERM
behaviour) and `FakeLockAcquirer` for fast assertions.

## CLI argv (FR-1)

```
claude
  --model <settings.wiki_runner_model>            # default claude-sonnet-4-5
  --add-dir <wiki_path>
  --append-system-prompt @<assembled_prompt_path>
  --output-format stream-json
  --permission-mode dontAsk
  [--allowedTools …]                              # caller may pass extra
  [--disallowedTools WebFetch …]
```

`CLAUDE_CONFIG_DIR` env points at subscription auth dir.
`PATH=/usr/bin:/bin` to keep the spawned env minimal (mirrors classifier).

## Prompt assembly (FR-2)

```python
def assemble_prompt(
    *, base: Path, overlay: Path, runtime_dir: Path, run_id: str,
) -> Path:
    text = base.read_text("utf-8") + "\n\n---\n\n" + overlay.read_text("utf-8")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    target = runtime_dir / f"{run_id}.system.md"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)
    return target
```

Frontmatter `semver: X.Y.Z` is enforced by an explicit regex check before
assembly (reuses the regex shape from `classifier.stage0`). If either piece is
missing the line, raise `WikiRunnerError`.

## Acquire order (FR-3)

`acquire.py` exports `WikiLockAdapter(manager: WikiLockManager)` whose
`acquire(wiki_id, wiki_path)` returns the `WikiLockManager.acquire` async
context manager — this **already** enforces semaphore → memlock → flock with
stale-PID recovery (see `scheduler/locks.py`). The adapter exists so the
runner can be tested with a `FakeLockAcquirer` and so chunk-7 owns the
namespace `wiki.acquire` even though the implementation lives in scheduler.

`runner.py` builds an `AsyncExitStack`:
1. `await stack.enter_async_context(acquirer.acquire(wiki_id, wiki_path))`
2. `await stack.enter_async_context(_spawn_subprocess(...))`
The reverse-order release falls out for free.

## Streaming (FR-4)

```python
class StreamEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["assistant_chunk", "tool_use", "final", "raw"]
    payload: dict[str, Any]
```

`async def parse_stream_json(reader: asyncio.StreamReader) -> AsyncIterator[StreamEvent]`
reads line-by-line via `reader.readline()`. Empty lines skipped. Malformed JSON
→ structlog warn + skip (do not raise — Claude CLI may emit progress on stderr,
keep the stream tolerant). Mapping rules:
- `{"type": "assistant", ...}` → `assistant_chunk`
- `{"type": "tool_use", ...}` → `tool_use`
- `{"type": "result", ...}` or `{"stop_reason": ...}` → `final`
- otherwise → `raw` (preserved verbatim in payload)

## Transcript persistence (FR-5)

```python
def persist_transcript(events: list[StreamEvent], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(ev.model_dump_json() + "\n")
    os.replace(tmp, target)
```

`target = wiki_path / "runs" / run_id / "transcript.jsonl"`.

## Timeout / kill (FR-6)

`run_wiki_session(..., timeout_s: float = 300.0)`:
- `asyncio.wait_for(_drain_stream_and_wait(proc), timeout_s)`
- on `TimeoutError` → call `kill_with_sequence(proc, grace_seconds=10.0)`
  imported from `scheduler.core` and re-raise `WikiRunnerTimeoutError`.

## structlog events (FR-7)

| event | when | extra fields |
|-------|------|--------------|
| `wiki.run.start` | before spawn | `correlation_id, wiki_id, run_id, model, mode` |
| `wiki.lock.acquired` | inside acquire | `wiki_id, run_id, latency_ms` |
| `wiki.lock.stale_recovered` | only on stale | `wiki_id, dead_pid` |
| `wiki.run.event` | per StreamEvent (sampled at type-change) | `event_type, run_id` |
| `wiki.run.finish` | after wait | `correlation_id, wiki_id, run_id, exit_code, n_events, latency_ms` |

## Tests

Unit (`tests/unit/wiki/`):
1. `test_acquire.py` — order-serialisation; stale-PID recovery; reentrancy on
   different `wiki_id` runs concurrently.
2. `test_streaming.py` — fixture stream parses to N events; partial line
   buffering; malformed line skipped; `final` detection.
3. `test_runner.py` — happy path with `FakeSpawner` returning 3 lines + rc=0;
   transcript file exists, atomic (tmp gone, target present); prompt assembly
   correctness (file content concatenation); timeout → kill sequence invoked
   exactly once + `WikiRunnerTimeoutError` raised; correlation_id flowed into
   structlog (capture via `structlog.testing`).

Integration nightly skeleton: `tests/integration/wiki/test_runner_real.py`
with `pytest.skip("RUN_INTEGRATION not set")` guard — no real CLI in unit run.

## Settings extension

```python
# settings.py additions:
wiki_runner_model: str = "claude-sonnet-4-5"
wiki_runner_timeout_s: float = 300.0
wiki_runner_term_grace_s: float = 10.0
```

## Out of scope (chunks 8/15/16)

- Lifecycle / NL naming / pre-flight: chunk 8.
- Full domain prompt catalogue: chunk 15.
- `systemd-run --scope` wrapping of `AsyncioSpawner`: chunk 16.

## Risk register

1. `claude` stream-json schema may evolve. Mitigation — `raw` fallback type +
   tolerant parse + structlog emit for unknown types.
2. `os.replace` is atomic only within the same filesystem. Mitigation — tmp
   sibling in the same directory.
3. AsyncExitStack reverse-release semantics rely on the
   `WikiLockManager.acquire` already doing reverse-order release (it does —
   verified in `scheduler/locks.py`).
