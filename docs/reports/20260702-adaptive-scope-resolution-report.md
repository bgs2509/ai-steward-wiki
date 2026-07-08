# Completion Report — Adaptive scope resolution for wiki_query/digest

- **bd_id:** aisw-o6m
- **module:** M-WIKI-SCOPE (NEW), M-RUNTIME-WIRING
- **date:** 2026-07-02
- **decision origin:** ADR-034 — Variant 2 (phased: layout injection + adaptive scoping) approved via `/best-approach`, after incident 2026-07-02 (`run-586827219f79`): a calorie query ran cross-WIKI and missed `diet/food_log.csv` because `wiki_query` had no domain scoping

## What changed

`wiki_query` and `digest` runs previously always ran cross-WIKI at the user root with no domain context, so answers could miss data that lived in a specific `<Domain>-WIKI/`. Added adapter-side scope resolution in a new `wiki/scope.py` module: `_WikiRunnerAdapter.run()` now resolves scope itself from `text`/`intent`/`owner_telegram_id` it already receives — no `WikiRunner`/`StreamingDelivery` Protocol change, matching ADR-034's rejection of the pipeline-side-scoping alternative (`aisw-50z`'s original assumption) as unnecessarily invasive.

Two-phase behavior:
- **Confident single-domain match** (reusing the prod-proven ingest fast-path: `inbox/hint_match.score_catalog` + `is_confident`, thresholds `MIN_SCORE=2.0`/`MIN_MARGIN=1.0`, `MAX_FASTPATH_CHARS=600`) → the run gets that WIKI's `cwd`, `--add-dir`, and CLAUDE.md, identical to ingest runs.
- **Not confident** → cross-root run as before, but now injected with a bounded «Карта WIKI пользователя» block (managed-zone excerpts of every domain WIKI) so the model at least sees what data exists elsewhere. `digest` gets the same block for non-primary WIKIs via `planner_context`.

Scope decisions are logged (`wiki.run.scope`, degraded case `wiki.run.scope.degraded`); any resolution error degrades to the cross-root run — a scope decision can never fail a run outright.

## Files

- `src/ai_steward_wiki/wiki/scope.py` (NEW, 135 lines) — `resolve_query_scope`, confidence gate, layout-block builder.
- `src/ai_steward_wiki/__main__.py` (+97/-12) — `_WikiRunnerAdapter`/digest adapter wiring, scope resolution call sites.
- `tests/unit/wiki/test_scope.py` (NEW), `tests/unit/test_main_runner_adapter.py`, `tests/unit/test_main_digest_adapter.py` — scope resolution, confidence thresholds, degraded-path coverage.
- `docs/adr/ADR-034-adapter-side-query-scope-resolution.md` — alternatives considered and decision record.

## Verification (evidence, per bd close reason)

- All tests green, `grace lint` clean, merged to master.
- Gate 10 auto-approved: risk=medium, evidence=strong (self-review checklist pass, stdlib + in-repo dependencies only, no Context7 triggers).

## Known limitations / deferred

- Scoped query runs still carry `WRITE_TOOLS` (existing config) — tightening answer runs to read-only was deliberately deferred (ADR-034 consequence 4).
- False-scoping risk is bounded by the same conservative thresholds already running in prod for ingest since `aisw-5sd`; ambiguous queries keep the safer full cross-WIKI read.
- A future unified `ScopeResolver` covering all WIKI intents (ADR-034 "Variant 3") can reuse `wiki/scope.py` unchanged — the module boundary anticipates it, but is not built.
