# Chunk 20 — M-TG-PIPELINE-CLASSIFIER Completion Report

- **Date:** 2026-05-11
- **bd_id:** aisw-96y
- **Phase:** 20 (TG pipeline classifier wiring)
- **SSoT:** `docs/superpowers/plans/20260511-ai-steward-wiki-launch/breakdown.xml` chunk-20

## Outcome

Closed the core MVP-launch gap: `DefaultPipeline.on_text` and `on_voice` now compose
Classifier (Stage-0 Haiku) → Inbox L2 dedup → WikiRunner (Stage-1a/1b Sonnet) →
OutputDelivery instead of the ack-only stub.

## Architecture

Three new `Protocol`s in `tg/pipeline.py` (`Classifier`, `WikiRunner`,
`OutputDelivery`) make the pipeline framework-thin and unit-testable. Concrete
adapters live in `__main__.py` and bridge the wide library APIs (Stage-0
`classify` / `run_wiki_session` / `deliver_output`) to the narrow Protocols
(Hexagonal / Ports & Adapters).

Helper `aggregate_text(events)` in `wiki/runner.py` extracts assistant text from
three stream-json payload shapes (`message.content[*].text`, `delta.text`, flat
`text`). Empty runner output falls back to `ACK_TEXT_RU` via `deliver`.

Failure modes are mapped to safe ru-only acks at the pipeline boundary:
- `ClassifierError` → `ACK_CLASSIFY_ERR_RU`
- `WikiRunnerError` → `ACK_RUNNER_ERR_RU`
- L2 dedup hit → `ACK_DEDUP_RU` + `record_dedup_choice("auto_skip")`

Correlation id format: `tg-{update_id}-{telegram_id}`.

## Log markers

`tg.pipeline.classify.{begin,done,error}`,
`tg.pipeline.inbox.l2_dedup_hit`,
`tg.pipeline.runner.{dispatched,completed,error}`,
`tg.pipeline.deliver.sent`.

## Verification

- Unit: 11 tests in `tests/unit/tg/test_pipeline_classifier_wiring.py` (happy
  text, L1 dup, L2 dedup, classifier error, runner error, empty output, voice
  happy/empty, back-compat None injection, log markers via capsys).
- Unit: 6 tests in `tests/unit/wiki/test_runner_aggregate.py` covering all
  payload shapes.
- Integration: gated smoke test `tests/integration/test_pipeline_classifier_e2e.py`
  (RUN_CLAUDE_CLI_INTEGRATION=1) exercises real classifier + fake runner.
- Full unit suite: 387/387 passing.
- `make lint`: clean (ruff + ruff format + mypy --strict).
- `grace lint --failOn errors`: 0 issues.

## Files touched

- `src/ai_steward_wiki/tg/pipeline.py` — v0.1.0 rewrite (Protocols, adapters glue,
  `_run_text_pipeline`).
- `src/ai_steward_wiki/wiki/runner.py` — added `aggregate_text`.
- `src/ai_steward_wiki/__main__.py` — v0.1.0 contract; `_build_classifier_backend`
  factory, `_ClassifierAdapter`, `_WikiRunnerAdapter`, `_OutputDeliveryAdapter`,
  wiring block.
- `tests/unit/tg/test_pipeline_classifier_wiring.py` (new).
- `tests/unit/wiki/test_runner_aggregate.py` (new).
- `tests/integration/test_pipeline_classifier_e2e.py` (new, gated).
- `docs/superpowers/specs/20260511-tg-pipeline-classifier-{discovery,design}.md`
  (new).
- `docs/superpowers/plans/20260511-tg-pipeline-classifier-plan.md` (new).
- `docs/verification-plan.xml` — new V-M-TG-PIPELINE-CLASSIFIER entry.
- `docs/development-plan.xml` — Phase-20 STATUS=done.
- `docs/superpowers/plans/20260511-ai-steward-wiki-launch/breakdown.xml` —
  chunk-20 bd_id.

## Deviations

None vs the approved Writing-Plans implementation plan.
