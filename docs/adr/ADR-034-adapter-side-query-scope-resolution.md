# ADR-034: Adapter-side scope resolution for wiki_query (no WikiRunner Protocol change)

**Status:** accepted
**Date:** 2026-07-02
**bd:** aisw-o6m
**Related:** ADR-032 (per-intent run config precedent), ADR-033 (nutrition layout), D-016 (Inbox hints), aisw-50z (query-gap — scoping deferred), aisw-5sd (hint fast-path)

## Context

wiki_query and digest run cross-WIKI at the user root with no domain context. The
aisw-50z fix that made queries answer-in-chat deferred domain scoping with the note
that it "would need a WikiRunner-Protocol change". Incident 2026-07-02
(run-586827219f79): a calorie query ran blind and missed `diet/food_log.csv`.

User approved phased Variant 2 via /best-approach: Phase A — inject WIKI layouts into
cross runs; Phase B — adaptive scoping for confident single-domain queries.

## Alternatives

1. **Pipeline-side scoping** (thread a resolved target through StreamingDelivery /
   WikiRunner Protocol) — the aisw-50z assumption. ❌ Breaks two Protocols and every
   fake in tests for information the adapter can derive itself.
2. **Adapter-side scoping** — `_WikiRunnerAdapter.run()` already receives `text`,
   `intent`, `owner_telegram_id` and alone chooses `wiki_path`. Resolve scope there;
   pipeline and Protocols untouched. ⭐
3. **Prompt-only guidance** (tell the model to read CLAUDE.md files first). ❌ The
   incident transcript shows the model prefers blind grep; non-deterministic.

## Decision

Alternative 2, with logic extracted into a new stdlib-only module
`wiki/scope.py` (M-WIKI-SCOPE):

1. `resolve_query_scope(text, catalog, wikis)` reuses the prod-proven ingest fast-path
   machinery (`inbox/hint_match.score_catalog` + `is_confident`, thresholds
   MIN_SCORE=2.0 / MIN_MARGIN=1.0, length gate MAX_FASTPATH_CHARS=600).
2. Confident → the matched WIKI dir becomes `wiki_path`; `run_wiki_session` then gives
   the run that cwd, `--add-dir`, and (via `assemble_prompt`, runner.py:271-274) the
   WIKI's CLAUDE.md — identical mechanics to ingest runs.
3. Not confident → user-root run as today, plus a bounded «Карта WIKI пользователя»
   block (managed-zone excerpts of all domain WIKIs) injected into the per-run scratch
   overlay. Digest gets the same block for non-primary WIKIs via planner_context.
4. Scope decisions are logged (`wiki.run.scope`); any resolution error degrades to the
   cross-root run (a scope decision can never fail a run).

## Consequences

1. tg/pipeline.py, WikiRunner / StreamingDelivery Protocols, wiki/runner.py and the
   scheduler stay untouched — the change is confined to `wiki/scope.py` + two adapters
   in `__main__.py`.
2. web_task keeps its ADR-032 isolation (neutral cwd, no WIKI add-dir) — the scope
   resolver only runs for WIKI_QUERY.
3. False-scoping risk is bounded by the same conservative thresholds that have run in
   prod for ingest since aisw-5sd; ambiguous queries keep full cross-WIKI reads.
4. Scoped query runs still carry WRITE_TOOLS (today's config) — tightening answer runs
   to read-only is deliberately out of scope (follow-up candidate).
5. A future unified ScopeResolver for all wiki intents (Variant 3) can lift
   `wiki/scope.py` unchanged — the module boundary anticipates it.
