# Plan — web-gap (aisw-dqz) — web_task mode + WebSearch policy (Path B approved)

bd_id: aisw-dqz · module_id: M-TG-PIPELINE / M-CLASSIFIER-STAGE0 / M-WIKI-RUNNER
Human approved Path B at the security gate (2026-06-26): risk=high, enable WebSearch
for web_task only, intent-scoped, read-only, no WIKI add-dir, WebFetch stays denied.

## Locked constraints (do NOT deviate)
1. `Intent.WEB_TASK = "web_task"`; classifier.md enum + semantics; semver 1.1.0 → 1.2.0.
2. web_task NOT in `_ROUTABLE_INTENTS`, not reminder/digest/admin → generic answer runner.
3. web_task `_RunConfig`: `allowed_tools=["WebSearch"]`, NO WRITE_TOOLS, WebFetch in
   disallowed_tools, no `--add-dir` on WIKI tree, neutral cwd. WebSearch never global.
4. ADR amending D-038: WebSearch carve-out + M-1..M-5 + human approval.
5. `wiki.run.web_search.*` log anchor (cheap).

## Steps (TDD: RED → GREEN)

1. **RED-runner** (`tests/unit/wiki/test_runner.py`): `test_web_task_run_config_allows_websearch_neutral_cwd`
   — config with `web_search=True, allowed_tools=WEB_SEARCH_TOOLS` ⇒ argv has
   `--allowedTools WebSearch`, NO `--add-dir <wiki>`, cwd != wiki_path (neutral),
   `--disallowedTools WebFetch` present. FAILS (no web_search field / always adds wiki dir).
2. **RED-pipeline** (`tests/unit/tg/test_pipeline_router.py`): add `Intent.WEB_TASK` to the
   `test_non_routable_intent_uses_legacy_path` parametrize ⇒ web_task with router wired must
   reach `runner.run`, not `router.route`. FAILS (web_task not an enum member yet).
3. **RED-schema** (`tests/unit/classifier/test_schema.py`): add "web_task" to
   `test_intent_enum_closed` expected set. FAILS until enum updated.
4. **GREEN schema**: add `WEB_TASK = "web_task"` to `Intent` (classifier/schema.py); bump VERSION.
5. **GREEN runner**: `WEB_SEARCH_TOOLS = ["WebSearch"]` + export; `_RunConfig.web_search: bool`;
   `_build_argv(add_wiki_dir: bool)`; neutral cwd + skip wiki add-dir when `web_search`;
   `wiki.run.web_search.enabled` log anchor; header bump.
6. **GREEN __main__**: `_WikiRunnerAdapter` gets optional `web_run_config`; `run()` selects it
   when `intent is Intent.WEB_TASK`; wire `web_run_config=_RunConfig(... allowed_tools=
   WEB_SEARCH_TOOLS, web_search=True)`. file:line recorded in report.
7. **GREEN prompt**: classifier.md — add `"web_task"` to output enum + semantics bullet 8;
   semver → 1.2.0.
8. **ADR**: `docs/adr/ADR-002-websearch-web-task-carveout.md` amending D-038.
9. `mkdir -p data/`; `make total-test` fully green; fix ALL failures.
10. grace-refresh; smart-commit (Conventional Commits + MODULE_ID scope); `bd close`.

## Out of scope
Global WebSearch; WebFetch re-enable; per-user web quota; single-theme cwd-scoping.
