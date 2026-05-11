# Chunk 21 — M-TG-PIPELINE-STREAMING Completion Report

- **Date:** 2026-05-11
- **bd_id:** aisw-a3z
- **Phase:** 21 (TG pipeline streaming)
- **SSoT:** `docs/superpowers/plans/20260511-ai-steward-wiki-launch/breakdown.xml` chunk-21

## Outcome

Long-running Stage-1a/1b runs now stream into Telegram in-place via D-026
StreamEditor. Fast runs (≤5 s) keep the original single-deliver UX with zero
overhead.

## Architecture

`DefaultStreamingDelivery` (in `tg/pipeline.py`) implements a 5 s wall-clock
race against `runner.run`:

- **Fast path** (runner finishes ≤5 s): no placeholder; single `output.deliver`.
- **Slow path** (timeout fires first): send `"⏳ Думаю…"` placeholder, build
  StreamEditor over it, replay buffered chunks, then live-feed subsequent
  `assistant_chunk` events; on completion `editor.finalize()` + final
  `output.deliver` with `aggregate_text(events)` as truth.
- **Runner exception during slow path:** `editor.finalize()` best-effort, then
  re-raise so the existing pipeline boundary maps to `ACK_RUNNER_ERR_RU`.

`run_wiki_session` gains an additive `on_event` callback (kw-only, default
`None`); callback exceptions are logged via `wiki.run.on_event_error` and
swallowed so the runner cannot be killed by streaming bugs.

`DefaultPipeline` accepts an optional `streaming: StreamingDelivery | None`
ctor param. When `None`, behaviour is identical to chunk 20 (proven by
back-compat tests).

## DEC-L2 amendment

Breakdown.xml referenced `WikiRunner.estimated_duration_s > 5` as the trigger.
That metadata does not exist. Captured DEC-TPS-1: trigger is wall-clock 5 s
race, equivalent UX without speculative estimation.

## Log markers

`tg.pipeline.stream.{begin,chunk,final,error}` plus runner-level
`wiki.run.on_event_error`.

## Verification

- Unit: 7 new tests in `tests/unit/tg/test_pipeline_streaming.py` (fast, slow,
  exception, back-compat, aggregate-as-truth, outcome.text fallback, empty
  fallback).
- Unit: 3 new tests in `tests/unit/wiki/test_runner_on_event.py`.
- Existing chunk-20 wiring tests pass unchanged (back-compat verified).
- Full unit suite: 385/385 green.
- `make lint`: clean (ruff + ruff format + mypy --strict).
- `grace lint --failOn errors`: 0 issues.

## Files touched

- `src/ai_steward_wiki/wiki/runner.py` — `on_event` callback param + exception
  swallow.
- `src/ai_steward_wiki/tg/pipeline.py` — v0.2.0; `StreamingDelivery` Protocol,
  `DefaultStreamingDelivery` class, ctor `streaming` param, conditional branch
  in `_run_text_pipeline`.
- `src/ai_steward_wiki/__main__.py` — wire `DefaultStreamingDelivery`,
  forward `on_event` through `_WikiRunnerAdapter.run`.
- `tests/unit/tg/test_pipeline_streaming.py` (new, 7 tests).
- `tests/unit/wiki/test_runner_on_event.py` (new, 3 tests).
- `docs/superpowers/specs/20260511-tg-pipeline-streaming-{discovery,design}.md`
  (new).
- `docs/superpowers/plans/20260511-tg-pipeline-streaming-plan.md` (new).
- `docs/verification-plan.xml` — `V-M-TG-PIPELINE-STREAMING` entry.
- `docs/development-plan.xml` — Phase-21 → done.
- `docs/superpowers/plans/20260511-ai-steward-wiki-launch/breakdown.xml` —
  chunk-21 bd_id.

## Deviations

DEC-L2 reinterpreted as race-based trigger (DEC-TPS-1) due to missing
`estimated_duration_s` metadata in `WikiRunner`. UX-equivalent.

## Out of scope

Voice streaming, mid-run cancel UI, MessageReactionUpdated indicators — none
block MVP launch.
