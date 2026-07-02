---
feature: adaptive-scope
bd_id: aisw-o6m
module_id: M-WIKI-SCOPE
status: stable
date: 2026-07-02
risk: medium
evidence: strong
open_questions: []
fr:
  - FR-1 (Phase B, query scoping) — When Stage-0 intent is wiki_query, the runner adapter MUST resolve scope BEFORE spawning the CLI. Confident single hint-catalog match (existing inbox/hint_match score_catalog + is_confident, thresholds MIN_SCORE=2.0 / MIN_MARGIN=1.0, text length ≤ MAX_FASTPATH_CHARS=600) → run scoped to that WIKI (wiki_path = the domain dir, so cwd, --add-dir and the per-WIKI CLAUDE.md appended by assemble_prompt all point at it, exactly like ingest runs).
  - FR-2 (Phase A, sighted cross) — A wiki_query run that is NOT confidently scoped MUST still run at the user root (cross-WIKI read preserved) but with a layouts block (managed zones of all the user's domain WIKIs) injected into the per-run scratch overlay, so the model knows every WIKI's Data layout paths without blind grepping.
  - FR-3 (Phase A, digest) — The digest run MUST receive the same layouts block for its non-primary WIKIs (the primary WIKI CLAUDE.md is already appended by assemble_prompt via wiki_path; the rest are only --add-dir today) appended to planner_context.
  - FR-4 — web_task behaviour MUST NOT change (neutral cwd, no WIKI add-dir, WebSearch-only — ADR-032 security carveout).
  - FR-5 — Scope decisions MUST be observable via a structured log anchor wiki.run.scope with fields correlation_id, telegram_id, intent, scope (scoped | cross), target_wiki (or null), top_score, margin.
  - FR-6 — Scope resolution failures (catalog resolver error, empty catalog, missing CLAUDE.md) MUST degrade gracefully to today's behaviour (cross-WIKI root run), never fail the run.
  - FR-7 — WikiRunner Protocol and tg/pipeline.py MUST NOT change (verified feasible — the adapter already receives text, intent and owner_telegram_id; pipeline.py:1495-1502 passes them today).
nfr:
  - NFR-1 — TDD (RED before GREEN per task); make total-test green; coverage ≥80% core; mypy --strict clean.
  - NFR-2 — No new settings/env knobs (thresholds stay module constants per hint_match.py:86-88 YAGNI precedent).
  - NFR-3 — Layouts block bounded — per-WIKI managed-zone excerpt capped (WIKIs capped at 20/user by wiki_max_per_user) so the injected block cannot blow the prompt.
  - NFR-4 — Ru-only user-visible strings (D-032); layouts block header in Russian.
  - NFR-5 — grace lint 0 issues; MODULE_CONTRACT + MODULE_MAP for every touched module; knowledge-graph refreshed.
constraints:
  - Dispatch layer stays intact — pipeline passes only text/intent/ids (pipeline.py:1455-1502); ALL scoping lives in the __main__.py adapters (aisw-50z Protocol-change concern is void with adapter-side resolution).
  - assemble_prompt (runner.py:250-280) already appends wiki_path/CLAUDE.md when present — scoped runs get their layout for free; do not duplicate it in the overlay for scoped runs.
  - Generic answer runs currently write a per-run scratch overlay (semver line + "# User turn", __main__.py:438-440) — the injection point for the cross-run layouts block; overlay must keep a semver line (assemble_prompt _check_semver).
  - Hint catalog and owner-WIKI enumeration already exist as factories (make_hint_catalog_resolver __main__.py:589-613, _resolve_owner_wikis_factory __main__.py:568-586) — reuse, do not re-implement.
  - Managed zone markers and parsing live in wiki/migration.py (MANAGED_START / MANAGED_END) — reuse for layout extraction.
  - Digest cwd/lock = first WIKI, rest are extra_add_dirs (firing.py:813-814); planner_context is the digest user_input (firing.py:815-821) — inject there, scheduler code path untouched otherwise.
risks:
  - R-1 (MEDIUM) — false-confident scoping sends a genuinely cross-WIKI question into one WIKI. Mitigation — same conservative thresholds that run in prod for the ingest fast-path (score ≥2, margin ≥1, length ≤600) plus graceful degradation (FR-6); worst case equals a today-style answer from one WIKI's view.
  - R-2 (LOW) — layouts block grows with WIKI count. Mitigation — NFR-3 cap; only managed zones, not full CLAUDE.md user zones.
  - R-3 (LOW) — scoped query runs keep WRITE_TOOLS (same config as today) — a query run could still write. Unchanged from current behaviour, explicitly out of scope (see scope_later).
  - R-4 (LOW) — deploy requires service restart on vpn-2 (Python change) and non-interactive sudo is unavailable — user runs the restart command (known constraint, memory project_vps_deploy).
scope_in:
  - src/ai_steward_wiki/wiki/scope.py — NEW module M-WIKI-SCOPE (ScopeDecision, resolve_query_scope, collect_layouts).
  - src/ai_steward_wiki/__main__.py — _WikiRunnerAdapter (scope resolution for WIKI_QUERY + overlay injection), _DigestRunnerAdapter (planner_context injection), wiring of resolvers.
  - tests/unit/wiki/test_scope.py — NEW; tests/unit/test_main_runner_adapter.py or extension of existing adapter tests; digest adapter test extension.
scope_out:
  - tg/pipeline.py, WikiRunner Protocol, StreamingDelivery — untouched (FR-7).
  - web_task run config (ADR-032) — untouched.
  - Scheduler/firing.py digest orchestration — untouched (injection happens in the adapter).
  - Sticky active-WIKI pointer as an extra scope signal — deferred (YAGNI at current scale).
scope_later:
  - Read-only tool config for scoped query runs (drop WRITE_TOOLS from answer runs).
  - Unified ScopeResolver stage for all wiki intents (Variant 3 of /best-approach, revisit at 5+ WIKIs).
  - distilled_payload domain hints from Stage-0 as a secondary scope signal.
---

# Discovery — Adaptive scope resolution for group-2 intents (wiki_query / digest)

## Problem

Group-2 intents (wiki_query, digest) always execute cross-WIKI at the user root with
zero domain context — no Data layout, no per-WIKI CLAUDE.md (except digest's primary).
Incident 2026-07-02 (run-586827219f79) — «сколько калорий я ем» ran blind at the root,
grepped Russian «ккал» against an English `kcal` CSV header, globbed only `*.md`, and
missed `diet/food_log.csv` entirely while wiki_ingest has a full scoping mechanism
(Stage-1a router + hint fast-path).

## Key discovery (changes the design radically vs the earlier /best-approach sketch)

The WikiRunner Protocol does NOT need to change. The adapter `_WikiRunnerAdapter.run()`
already receives `text`, `intent`, `owner_telegram_id` (__main__.py:419-429) and alone
decides `wiki_path` (user root today, __main__.py:430-432). Resolving scope inside the
adapter — not in the pipeline — keeps the dispatch layer intact. Bonus — `assemble_prompt`
(runner.py:271-274) already appends `wiki_path/CLAUDE.md` when it exists, so a scoped run
inherits the target WIKI's full CLAUDE.md (layout included) with zero extra code.

## Research trail

1. Explore agent map 2026-07-02 — all file:line citations in frontmatter constraints.
2. /best-approach ×2 this session (narrow query fix → systemic group-2 fix), user chose
   phased Variant 2 (78%).
3. WebSearch — adaptive routing (confident → narrow scope, else broad) is the 2026 SOTA
   pattern for multi-KB agent systems (arXiv 2501.07813, 2505.22095).
4. Prod evidence — hint_match thresholds (score ≥2, margin ≥1) run in prod for the ingest
   fast-path without false-scoping complaints (tests test_pipeline_hint_fastpath.py).
