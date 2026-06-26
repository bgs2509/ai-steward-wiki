# Implementation Plan — query-gap (aisw-50z)

> SSoT for execution. Module: M-TG-PIPELINE. risk=medium, evidence=strong.
> Design: docs/superpowers/specs/20260626-query-gap-design.md

## Task 1 — RED: failing test proving wiki_query answers, not files

File: `tests/unit/tg/test_pipeline_router.py`

1. Change the parametrize on `test_routable_intent_goes_through_router_not_runner`
   from `[WIKI_INGEST, WIKI_QUERY, UNKNOWN]` to `[WIKI_INGEST, UNKNOWN]`
   (wiki_query is no longer a routable/filing intent).
2. Add `test_wiki_query_answers_via_runner_not_router`: build a pipe with a wired
   router + classifier(WIKI_QUERY); call `on_text`; assert
   `runner.run.assert_awaited_once()`, `router.route.assert_not_awaited()`,
   and the runner gets `intent == Intent.WIKI_QUERY`.
3. Run `uv run pytest tests/unit/tg/test_pipeline_router.py -q` — the new test MUST FAIL
   on current code (wiki_query currently goes to router.route).

## Task 2 — GREEN: remove WIKI_QUERY from the routable set

File: `src/ai_steward_wiki/tg/pipeline.py`

1. Line 585: `_ROUTABLE_INTENTS = frozenset({Intent.WIKI_INGEST, Intent.UNKNOWN})`.
2. Update the comment above it to state wiki_query is answered by the generic runner.
3. Bump the MODULE_CONTRACT header: VERSION 0.12.0 -> 0.13.0, move current LAST_CHANGE
   to PREVIOUS, write new LAST_CHANGE (aisw-50z) describing the wiki_query answer routing.
4. Run the test file — both tests GREEN.

## Task 3 — Full quality gate

1. `mkdir -p data/` (already done).
2. `make lint` — ruff + format + mypy + grace lint clean.
3. `make total-test` — fully green, coverage >=80%. Fix ALL failures.

## Self-review checklist

- [ ] FR-1..FR-5 covered (answer path reached; no file/confirm; ingest/unknown unchanged; log trace intact).
- [ ] NFR-1 (1 module), NFR-2 (lint/types), NFR-3 (ru-only, no new copy), NFR-4 (TDD RED first).
- [ ] MODULE_CONTRACT header updated.
- [ ] No placeholders; task order respects: RED before GREEN.
