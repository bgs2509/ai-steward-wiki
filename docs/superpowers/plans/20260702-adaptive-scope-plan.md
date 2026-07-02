# Plan — Adaptive scope for wiki_query / digest (aisw-o6m, Variant 2)

> SSoT for execution. Specs: `20260702-adaptive-scope-discovery.md`,
> `20260702-adaptive-scope-design.md`, ADR-034. TDD: RED → GREEN → REFACTOR per task.
> Verification: `make lint` + targeted pytest per task; `make total-test` at Step 12.

## Task 1 — M-WIKI-SCOPE core: `wiki/scope.py` + `tests/unit/wiki/test_scope.py`

1. RED: tests for `resolve_query_scope(text, catalog, wikis)`:
   - confident single match (catalog with distinct keywords) → `ScopeDecision(kind="scoped", target_stem, target_path, top_score, margin)`;
   - ambiguous (two close domains) → `kind="cross"`, `target_stem is None`;
   - empty catalog → cross; text longer than `MAX_FASTPATH_CHARS` → cross;
   - matched stem missing from `wikis` mapping → cross (defensive).
2. RED: tests for `collect_layouts(wikis)`:
   - happy path — output contains `# Карта WIKI пользователя`, each `## <Stem>-WIKI` header, managed-zone body between MANAGED markers;
   - CLAUDE.md missing → WIKI listed with «(схема не описана)»;
   - markers absent → same fallback line;
   - per-WIKI excerpt cap enforced (constant `MAX_LAYOUT_CHARS_PER_WIKI`).
3. GREEN: implement module (stdlib only) with MODULE_CONTRACT/MODULE_MAP headers, START_BLOCK markers, reusing `inbox.hint_match` (score_catalog, is_confident, MAX_FASTPATH_CHARS) and `wiki.migration` (MANAGED_START, MANAGED_END).
4. Commit `feat(M-WIKI-SCOPE): scope decision + layouts collection for group-2 intents`.

## Task 2 — `_WikiRunnerAdapter` scoping (FR-1, FR-2, FR-5, FR-6, FR-7)

1. RED (extend adapter tests; find existing suite via `grep -rl "_WikiRunnerAdapter" tests/`; if none — new `tests/unit/test_main_runner_adapter.py` with fake spawner/acquirer copied from `tests/unit/wiki/test_runner.py` fixtures):
   - WIKI_QUERY + confident catalog → `run_wiki_session` receives `wiki_path == <domain dir>`;
   - WIKI_QUERY + ambiguous → `wiki_path == user root` AND scratch overlay file content contains `Карта WIKI пользователя`;
   - WIKI_QUERY + resolver raises → cross-root run, no exception, `wiki.run.scope.degraded` logged;
   - WEB_TASK → behaviour unchanged (web config, no scope calls);
   - resolvers not wired (None) → today's behaviour byte-for-byte.
2. GREEN: `_WikiRunnerAdapter.__init__` gains optional `hint_catalog_resolver` + `owner_wikis_resolver` (default None); `run()` for `intent is Intent.WIKI_QUERY` resolves ScopeDecision, swaps `wiki_path` / augments scratch overlay, logs `wiki.run.scope` anchor (fields per FR-5).
3. Wire resolvers in `main()` (reuse `make_hint_catalog_resolver` + `_resolve_owner_wikis_factory` instances already built for pipeline/digest).
4. Commit `feat(M-WIKI-SCOPE): adaptive cwd-scoping for wiki_query in runner adapter`.

## Task 3 — `_DigestRunnerAdapter` layouts injection (FR-3)

1. RED (extend `tests/unit/test_main_digest_adapter.py`): full-digest call → `user_input` passed to `run_wiki_session` contains planner_context AND `Карта WIKI пользователя` block for non-primary WIKIs; expand call unchanged.
2. GREEN: adapter ctor gains optional `owner_wikis_resolver`; `__call__` (full digest branch only) appends `collect_layouts(non_primary)` to planner_context; degrade silently on error.
3. Commit `feat(M-WIKI-SCOPE): digest planner context carries non-primary WIKI layouts`.

## Task 4 — verification sweep

1. `make lint` (ruff + format + mypy --strict + grace lint) — clean.
2. `uv run pytest tests/unit` — all green, coverage ≥80% core.
3. `grace-refresh` targeted — knowledge-graph/verification-plan pick up M-WIKI-SCOPE.

## Task 5 — finish

1. Meta commit (ADR-034, specs, plan, graph XML).
2. Merge worktree → master, push (deploy flow per approved scope).
3. Deploy vpn-2: git pull + `~/.local/bin/uv sync` (py change) — restart requires TTY sudo → hand the user the command.
4. `bd close aisw-o6m`.

## Self-review checklist

- [x] Every FR covered: T2 (FR-1/2/5/6/7), T3 (FR-3), T2 web_task test (FR-4).
- [x] Every NFR: T1-T3 TDD (NFR-1), no settings knobs (NFR-2), cap in T1 (NFR-3), ru strings (NFR-4), T4 grace (NFR-5).
- [x] ADR-034 decisions DEC-1..7 all land in T1-T3.
- [x] No placeholders; task order respects dependencies (T1 → T2/T3 → T4 → T5).
- [x] Context-window budget: 3 code files + 3 test files, fits single window.
