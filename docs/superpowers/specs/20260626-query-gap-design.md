---
feature: query-gap
bd_id: aisw-50z
module_id: M-TG-PIPELINE
status: stable
date: 2026-06-26
risk: medium
evidence: strong
open_questions: []
stack:
  - library: structlog
    version: pinned (uv.lock)
    used_for: existing tg.pipeline.runner.dispatched / deliver.sent anchors (no new anchor needed)
  - library: pytest / pytest-asyncio
    version: pinned (uv.lock)
    used_for: RED->GREEN unit test on the routing predicate
decisions:
  - D-local-1: Remove Intent.WIKI_QUERY from _ROUTABLE_INTENTS (pipeline.py:585). This is the whole behavioural fix. _ROUTABLE_INTENTS becomes {WIKI_INGEST, UNKNOWN}. wiki_query then skips HINT_FASTPATH (1157) and ROUTABLE_BRANCH (1278) — both gated on the set — and, being neither REMINDER/DIGEST/ADMIN, falls through to the generic answer runner (1404+).
  - D-local-2: Theme-picking is DELEGATED to the Claude run, not done in the pipeline. The generic runner runs in the user ROOT (wiki_root/<telegram_id>) with --add-dir on root, so the run reads across all <Domain>-WIKI/ and answers from the relevant one. This is the e2e spec's "read across the user's WIKIs" branch and is always correct. score_catalog/is_confident stay reserved for the INGEST fast-path only.
  - D-local-3: NO cwd-scoping to a single matched theme WIKI in this fix. Scoping would require threading a target wiki_path through the WikiRunner Protocol + _WikiRunnerAdapter + StreamingDelivery + pipeline (>=4 modules => HIGH risk, breaks the medium-risk envelope). Deferred as a LATER optimization (focus/latency), not a correctness requirement.
  - D-local-4: NO new user-facing copy and NO new log anchor. The generic runner already logs tg.pipeline.runner.dispatched(intent=wiki_query) + tg.pipeline.deliver.sent and delivers assistant text (ACK_TEXT_RU only as empty-output fallback). The e2e log_watch scorer reads exactly this trace.
  - D-local-5: wiki_ingest and unknown are UNCHANGED — they stay in _ROUTABLE_INTENTS and keep router / hint-fast-path filing.
---

# Design — query-gap: wiki_query answers instead of files

## Chosen approach

Single-line routing fix: drop `Intent.WIKI_QUERY` from `_ROUTABLE_INTENTS`. wiki_query
falls through to the existing answer-capable generic runner, which already runs Claude
in the user root with cross-WIKI read access and delivers text to chat.

```
_ROUTABLE_INTENTS = frozenset({Intent.WIKI_INGEST, Intent.UNKNOWN})   # was: + WIKI_QUERY
```

## Control-flow after the change (wiki_query, router wired)

1. classify -> intent=wiki_query (logged at classify.done)
2. REMINDER fast-path — skip (not reminder)
3. DIGEST fast-path — skip (not digest)
4. HINT_FASTPATH (1157) — `result.intent in _ROUTABLE_INTENTS` is now False -> skip (no silent file)
5. ROUTABLE_BRANCH (1278) — same predicate False -> skip (no router, no confirm-to-file)
6. ADMIN (1395) — skip (not admin)
7. Generic runner (1404+): `self._streaming.run_and_deliver(... intent=wiki_query)` (text) or
   `self._runner.run(...)` + `self._output.deliver(...)` -> assistant answer delivered to chat.

## Why no theme-matching code is added

The generic runner's cwd is `wiki_root/<telegram_id>` (the parent of all theme WIKIs),
granted via `--add-dir`. The Claude run reads whichever theme WIKI is relevant and
answers — theme selection is the model's job at read time. Adding `score_catalog`-based
cwd-scoping is an optimization that buys focus/latency at the cost of a cross-module
protocol change; deferred (LATER).

## Verification (TDD)

- RED: a new `test_pipeline_router.py` test wires a router and classifies wiki_query;
  asserts `runner.run` awaited once and `router.route` NOT awaited. FAILS on current code
  (wiki_query -> router.route).
- Adjust `test_routable_intent_goes_through_router_not_runner` parametrize to
  `[WIKI_INGEST, UNKNOWN]` (wiki_query is no longer routable).
- GREEN after removing WIKI_QUERY from the set.
- `make total-test` fully green.

## Out of scope

web_task / WebSearch lifting (mode 8), wiki_lint (mode 4), single-WIKI cwd-scoping,
write-prevention hardening during query runs.
