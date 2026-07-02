---
feature: adaptive-scope
bd_id: aisw-o6m
module_id: M-WIKI-SCOPE
status: stable
date: 2026-07-02
risk: medium
evidence: strong
open_questions: []
stack:
  - Python 3.11 stdlib only for the new module (re, dataclasses, pathlib) — mirrors inbox/hint_match.py which is deliberately stdlib-only.
  - Reused in-project machinery — inbox/hint_match (score_catalog, is_confident, MIN_SCORE, MIN_MARGIN, MAX_FASTPATH_CHARS), wiki/migration (MANAGED_START/MANAGED_END markers), __main__.py factories (make_hint_catalog_resolver, _resolve_owner_wikis_factory), wiki/runner assemble_prompt CLAUDE.md append.
  - structlog for the wiki.run.scope anchor (existing logging_setup pipeline).
  - No new external dependencies; no Context7 triggers (stdlib + in-repo APIs only).
decisions:
  - DEC-1 Scope resolution lives in the runner adapter (__main__.py), NOT in tg/pipeline.py — the adapter already receives text, intent, owner_telegram_id; WikiRunner Protocol and dispatch stay untouched (kills the aisw-50z Protocol-change blocker). ADR-034.
  - DEC-2 New module src/ai_steward_wiki/wiki/scope.py (M-WIKI-SCOPE) holds the pure logic — ScopeDecision frozen dataclass, resolve_query_scope(text, catalog), collect_layouts(wikis) — so __main__.py keeps only thin wiring and the logic is unit-testable without adapters.
  - DEC-3 Scoped run = wiki_path swap only. When confident, the adapter passes the matched WIKI dir as wiki_path to run_wiki_session — cwd, --add-dir and CLAUDE.md-append all follow automatically (runner.py:552-556, 271-274). No changes inside wiki/runner.py.
  - DEC-4 Cross run injection point = the existing per-run scratch overlay (__main__.py:438-440). Overlay becomes semver line + "# User turn" + "# Карта WIKI пользователя" block with managed-zone excerpts of every domain WIKI.
  - DEC-5 Digest injection point = planner_context (adapter-level, _DigestRunnerAdapter.__call__), appending layouts of non-primary WIKIs only — primary CLAUDE.md already arrives via assemble_prompt.
  - DEC-6 Same confidence gates as prod ingest fast-path — score_catalog + is_confident + len(text) <= MAX_FASTPATH_CHARS. No new thresholds, no settings knobs (hint_match.py YAGNI precedent).
  - DEC-7 Graceful degradation — any resolver/IO error in scope resolution logs and falls back to today's cross-root behaviour; a scope decision can never fail a run.
---

# Design — Adaptive scope for wiki_query / digest (Variant 2, phased)

## Module map

```
wiki/scope.py (NEW, M-WIKI-SCOPE)
├── ScopeDecision            # frozen: kind ("scoped"|"cross"), target_stem, target_path, top_score, margin
├── resolve_query_scope()    # (text, catalog, wikis) -> ScopeDecision; hint_match under the hood
└── collect_layouts()        # (wikis) -> str; managed-zone excerpts, per-WIKI cap, ru header

__main__.py
├── _WikiRunnerAdapter.run() # intent==WIKI_QUERY: resolve scope
│     scoped -> wiki_path=target_path (CLAUDE.md auto-appended by assemble_prompt)
│     cross  -> wiki_path=user root, scratch overlay += collect_layouts(...)
│     web_task / other intents: unchanged
└── _DigestRunnerAdapter     # planner_context += collect_layouts(non-primary wikis)
```

## Data flow (query)

1. Pipeline (unchanged) → `runner.run(text, intent=WIKI_QUERY, owner_telegram_id, …)`.
2. Adapter: `catalog = await hint_catalog_resolver(telegram_id)`;
   `wikis = await owner_wikis_resolver(telegram_id)`;
   `decision = resolve_query_scope(text, catalog, wikis)`.
3. `decision.kind == "scoped"` → `wiki_path = decision.target_path`;
   log `wiki.run.scope` (scope=scoped, target_wiki, top_score, margin).
4. `decision.kind == "cross"` → `wiki_path = user_root`;
   scratch overlay = semver + user-turn header + `collect_layouts(wikis)`;
   log `wiki.run.scope` (scope=cross, target_wiki=None).
5. `run_wiki_session` as today.

## collect_layouts format (ru, D-032)

```
# Карта WIKI пользователя

## Medical-WIKI
<managed zone excerpt: Data layout + File resolution…>

## Cooking-WIKI
<…>
```

Managed zone extracted by the MANAGED_START/MANAGED_END markers from wiki/migration.py;
file missing or markers absent → the WIKI is listed with «(схема не описана)»; excerpt
capped per WIKI (защита промпта, wiki_max_per_user=20).

## Error handling

Resolver exceptions → `log.warning("wiki.run.scope.degraded", error=…)` → cross-root run
(FR-6). Empty catalog → cross. Text > 600 chars → cross (long analytical questions are
usually multi-domain anyway).

## Test plan sketch

1. `tests/unit/wiki/test_scope.py` — resolve_query_scope confident/ambiguous/empty/long;
   collect_layouts happy/missing-file/no-markers/cap.
2. Adapter tests — WIKI_QUERY scoped (wiki_path == domain dir), cross (overlay contains
   Карта WIKI), degraded resolver, WEB_TASK untouched, non-query intents untouched.
3. Digest adapter test — planner_context contains non-primary layouts.
