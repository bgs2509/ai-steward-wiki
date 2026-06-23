---
feature: wiki-runner
bd_id: aisw-x30
module_id: M-WIKI-RUNNER
status: stable
date: 2026-05-10
---

# Discovery — M-WIKI-RUNNER (Stage-1a/1b Sonnet runner)

**Feature:** chunk 7 of 20260510-ai-steward-wiki-mvp epic.
**bd_id:** aisw-x30 (claimed, in_progress).
**Date:** 2026-05-10.
**Status:** stable.

## Problem

After Stage-0 classification (chunk 5) and Inbox materialisation (chunk 6), the
service must invoke Claude **Sonnet** in headless CLI mode against either the
user's `Inbox-WIKI/` (Stage-1a) or a resolved `<Domain>-WIKI/` (Stage-1b). The
runner needs to:

1. Spawn `claude` with the correct flags for streaming side-effectful work.
2. Hold strict acquire-order locks (semaphore → memlock → flock) for the duration
   of a session to prevent concurrent writes to the same WIKI.
3. Recover from stale on-disk locks left by crashed processes.
4. Persist the streamed transcript atomically.

Inputs: source spec — `docs/Spec-WIKI/research/tech-spec-draft.md` §6, §7;
existing modules — `M-SCHEDULER` (`scheduler.locks.WikiLockManager`,
`scheduler.core.kill_with_sequence`), `M-CLASSIFIER-STAGE0`
(`classifier.backend.Spawner` Protocol pattern).

## Functional requirements

1. **FR-1 Subprocess invocation.** Build argv:
   - `claude --model <sonnet> --add-dir <wiki_path> --append-system-prompt @<assembled_prompt_file> --output-format stream-json --permission-mode dontAsk`
   - Allow/Disallow tool flags configurable; sane MVP defaults: disallow `WebFetch`, `Bash` outside the wiki sandbox is governed by `--add-dir`.
2. **FR-2 Prompt assembly.** Concatenate `prompts/wiki.md` (base) with one
   overlay (`prompts/inbox.md` for Stage-1a, `prompts/domain-<domain>.md` for
   Stage-1b). Write to a tmp file under `settings.workspace_root/runtime/prompts/`,
   pass via `@file`. Each piece must carry `semver: X.Y.Z` frontmatter.
3. **FR-3 Lock acquisition.** Strict order:
   1. Scheduler-level semaphore (capacity = `MAX_CONCURRENT_CLI`).
   2. In-memory `asyncio.Lock` per `wiki_id`.
   3. On-disk `fcntl.flock` on `<wiki>/.wiki.lock`, with **PID written to file**.
   Release in **reverse** order. Stale-lock recovery: if PID in file is not
   alive (`os.kill(pid, 0) → ProcessLookupError`), warn + reclaim.
4. **FR-4 Streaming.** Parse `--output-format stream-json` (line-delimited JSON).
   Yield typed events (`assistant_chunk`, `tool_use`, `final`) over an async
   iterator. Tolerate partial / malformed lines (skip + log).
5. **FR-5 Atomic transcript persistence.** Collect all events; on completion,
   write `<wiki>/runs/<run_id>/transcript.jsonl` via `tmp + os.replace`.
6. **FR-6 Timeout & cancellation.** On hard timeout / external cancel:
   `SIGTERM → 10s grace → SIGKILL` (delegate to
   `scheduler.core.kill_with_sequence`).
7. **FR-7 Observability.** structlog events `wiki.run.start`, `wiki.run.event`,
   `wiki.run.finish`, `wiki.lock.acquired`, `wiki.lock.stale_recovered` with
   `correlation_id, wiki_id, run_id, latency_ms`.

## Non-functional requirements

1. **NFR-1 Concurrency-safety.** Two concurrent runs against the same `wiki_id`
   must serialise — the lock contention test enforces this.
2. **NFR-2 Crash-resilience.** Stale `.wiki.lock` left by crashed PID must not
   block forever; the runner reclaims on next acquire.
3. **NFR-3 Testability.** All subprocess + scheduler integration MUST be behind
   Protocol seams (`Spawner`, `LockAcquirer`, `KillSequence`) so unit tests run
   without a real `claude` binary or systemd.
4. **NFR-4 mypy --strict clean** for `src/ai_steward_wiki/wiki/`.
5. **NFR-5 Coverage ≥ 80%** for `wiki/` module.

## Scope

**In:**
- `src/ai_steward_wiki/wiki/{__init__.py, runner.py, streaming.py, acquire.py}`
- `prompts/{wiki.md, inbox.md, domain-health.md, domain-finance.md, domain-default.md}`
- `tests/unit/wiki/{conftest.py, test_acquire.py, test_streaming.py, test_runner.py}`

**Out (deferred):**
- `systemd-run --scope` wrapping → chunk 16 (M-DEPLOY).
- Full domain prompt set → chunk 15 (M-TEMPLATES).
- Real Claude CLI integration test → nightly only (skeleton with skip marker).
- NL pre-flight, soft-delete, lifecycle, anti-spam → chunk 8 (M-WIKI-LIFECYCLE).

## Spec references

- §6 Decision-making (CLI invocation): `tech-spec-draft.md:380-440`.
- §7 Concurrency & Locking: `tech-spec-draft.md:444-468`.
- D-007 `--add-dir` scope; D-011 semaphore + memlock; D-012 flock + atomic
  write + stale-PID recovery; D-021 SIGTERM→SIGKILL.

## Acceptance

1. `pytest tests/unit/wiki -q` ≥ 12 tests, all green.
2. `make lint` exits 0.
3. `make total-test` exits 0 (full repo).
4. Lock contention test: two concurrent `run_wiki_session` calls for the same
   `wiki_id` serialise; events ordered.
5. Stale-lock recovery test: pre-write dead PID into `.wiki.lock`, expect
   acquire ≤ 2s + `wiki.lock.stale_recovered` event.
