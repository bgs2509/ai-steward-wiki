# Completion Report — Retry Stage-0 ClassifierError once before falling back to intent=unknown

- **bd_id:** aisw-l3h
- **module:** M-CLASSIFIER-STAGE0
- **date:** 2026-07-03
- **decision origin:** two prod incidents on vpn-2 (2026-07-02T19:03 — `.claude.json` write race; 2026-07-02T13:13/13:18 — config-path drift) where a bare `ClassifierError` on Claude CLI `rc!=0` dropped the user's message with only an error ack, unlike `ClassifierTimeoutError` which already degraded gracefully

## What changed

`Stage0Backend.call()` raised a bare `ClassifierError` on a non-zero Claude CLI exit code with no graceful degradation path, while the sibling `ClassifierTimeoutError` already fell back to `intent=unknown`. Added one retry (0.5s pause) before degrading to that same `intent=unknown` fallback shape (`aisw-32p`'s `TIMEOUT_FALLBACK` block), so a single transient CLI failure (file write race, momentary config drift) no longer drops the message outright.

`type(e) is ClassifierError` (exact type check, not `isinstance`) is used deliberately to exclude the `ClassifierSchemaError` and `ClassifierTimeoutError` subclasses from the retry path — a schema error is a permanent fault (retrying won't fix malformed output) and a timeout already has its own fallback.

Chosen via `/best-approach` (Variant 2) over: retry-only with no fallback (half-fix, still drops on persistent failure), a configurable backoff helper (YAGNI — only one call site), and adding a `tenacity` dependency (unjustified for 8 lines of retry logic).

## Files

- `src/ai_steward_wiki/classifier/stage0.py` (+93/-38 net within a 131-line diff) — retry-once-then-fallback logic in `call()`.
- `tests/unit/classifier/test_stage0.py` (+69) — retry-then-succeed, double-failure-degrades-to-unknown, and schema-error-never-retried cases.

## Verification (evidence, per bd close reason and acceptance criteria)

- `ClassifierError` on the first attempt retries once and succeeds if the second call succeeds.
- Two consecutive `ClassifierError` failures degrade to `intent=unknown` with `distilled_payload={fallback: stage0_error}`.
- `ClassifierSchemaError` still propagates unretried — no retry attempt is made for it.
- `make lint` + `make grace-lint` + `pytest tests/unit` all green; merged to master via PR #1.

## Known limitations / deferred

- None recorded — this is a scoped single-call-site reliability fix with no deferred follow-up.
