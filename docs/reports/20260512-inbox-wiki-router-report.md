---
feature: inbox-wiki-router
bd_id: aisw-dsg
epic: aisw-t2r
date: 2026-05-12
type: feature
status: complete
commits: 1307998, 8a77edb, 2a55be0, edf777e, 03e5e13, 17c6665, 330cbbc
adr: ADR-003
---

# Completion report — Inbox-WIKI Phase-A: stage raw content + Router-Claude invocation

## Goal

First slice of the documented smart-inbox-routing layer (epic `aisw-t2r`). Before: the pipeline classified Stage-0 intent then ran Claude in a flat per-user dir and replied — no Inbox-WIKI, no routing. Phase-A: get content *into* the user's `Inbox-WIKI/` and run the Stage-1a Router agent there, producing a structured `RouterDecision` that Phases B–E consume. Domain-WIKI move, Stage-1b ingest, confirm loop, cron bridge, hint fast-path are explicitly out of scope (`aisw-zd9/e45/kcz/12t`).

## What changed

1. **`M-INBOX-ROUTER` — new module `src/ai_steward_wiki/inbox/router.py`** — `RouterIntent` (`route | create_wiki | clarify | reject`), frozen `RouterDecision(intent, target_wiki, notes, raw, parsed_ok)`, `RouterError`, `parse_router_reply(text)`: extracts the **last** ```` ```router ```` fenced block, line-scans `key: value` (`notes` multi-line), normalises cross-field constraints (CLARIFY/REJECT → `target_wiki=None`; ROUTE/CREATE_WIKI without a target → demoted to CLARIFY), falls back to `RouterDecision(intent=CLARIFY, parsed_ok=False, …)` on a missing/malformed block.
2. **`prompts/inbox.md` → v1.1.0** — replaced the loose response list with a strict single fenced ```` ```router ```` block contract (`target_wiki` / `intent` / `notes`, ru `notes`).
3. **`M-TG-PIPELINE-CLASSIFIER`** — `DefaultPipeline` gains an optional `router: Router`. After Stage-0 classify, intents in `_ROUTABLE_INTENTS = {WIKI_INGEST, WIKI_QUERY, UNKNOWN}` with a wired router go through `router.route(...)`; the bot replies with `RouterDecision.notes`; `RouterError → ACK_RUNNER_ERR_RU`. Other intents and `router is None` keep the legacy flat run. New `Router` Protocol; new log anchors `tg.pipeline.router.dispatched|delivered|error`.
4. **`M-RUNTIME-WIRING`** — new `_RouterAdapter`: `ensure_inbox_wiki(telegram_id)` → `_render_raw_sidecar` writes `Inbox-WIKI/raw/<utc-ts>_<source>.md` (plain body for text; YAML front-matter + `staged_path` sidecar for media — the binary stays in `media_staging_root`) → `run_wiki_session(wiki_id=f"{tid}/Inbox-WIKI", wiki_path=inbox_dir, overlay=prompts/inbox.md, …)` → `parse_router_reply`; `WikiRunnerError → RouterError`. Injected into `DefaultPipeline` as `router=`. New log anchors `inbox.router.staged_raw|run.begin|run.done|parsed|parse_error`.
5. **Tests** — `tests/unit/inbox/test_router.py` (13: parser happy paths per intent, multi-line notes, null/empty target, last-block-wins, missing block fallback, unknown-intent fallback, demotion, frozen+forbid, RouterError); `tests/unit/tg/test_pipeline_router.py` (~13: routable→router, non-routable→legacy, no-router→legacy fallthrough, RouterError→ack, router log markers, clarify notes); `tests/unit/test_router_adapter.py` (10: `_render_raw_sidecar` text/voice/document; `_RouterAdapter.route` materialises Inbox-WIKI + writes sidecar + runs in Inbox-WIKI cwd + parses; `WikiRunnerError→RouterError`; log anchors; parse-error log). `tests/integration/test_e2e_pipeline.py` scenario 5 (+`wiki_root_e2e` / `real_router_adapter` / `pipeline_with_router` fixtures, gated `RUN_INTEGRATION=1` + claude binary): routable text → Claude runs inside `<wiki>/<tid>/Inbox-WIKI/`, bot replies with notes, legacy runner untouched, transcript lands under `Inbox-WIKI/runs/`.
6. **GRACE** — `M-INBOX-ROUTER` node + `V-M-INBOX-ROUTER` + dependency/CrossLink updates; Phase-A entry + module in `development-plan.xml`; version bumps (KG 0.0.6, VP 0.0.6, DP 0.0.5). ADR-003.

## Design decisions (via /questions-answers, see ADR-003)

Replace (no feature-flag — bot not deployed) · strict fenced `router` block (vs free-text or JSON) · dedicated `inbox/router.py` (vs folding into `inbox/parser.py`) · dedicated `_RouterAdapter` + narrow `Router` Protocol (vs overloading `_WikiRunnerAdapter`) · media sidecar only in Phase-A (binary move → Phase-E).

## Verification

`make total-test` — ALL STEPS PASSED: ruff-check 0, ruff-format 176/176, mypy 67 files 0 errors, grace-lint 67 governed + 3 XML 0 errors/warnings, inv-lint 14/14, test-cov **474 passed / 0 failed / coverage 91.35%**. `tests/integration` collects 10 scenarios (all skipped here — no Claude CLI in this session; nightly + cutover gate). One pre-existing RuntimeWarning (`_amain` never awaited) unrelated to this change.

## Follow-ups / notes

1. **Do not deploy `master` with Phase-A until `aisw-zd9` (Phase-B) closes** — between the two, routable messages get a Router decision echoed as text but nothing is moved/ingested into a domain WIKI. Recorded on epic `aisw-t2r`.
2. Phase-B (`aisw-zd9`) consumes `RouterDecision`: lookup/create the target `<Domain>-WIKI` (`wiki/lifecycle.py`), move the staged payload, Stage-1b librarian ingest there.
3. Retention sweep for `Inbox-WIKI/raw/` — later ops task.
4. `_WikiRunnerAdapter` still carries a dead `overlay_prompt_path=prompts/inbox.md` ctor arg (its `.run` writes its own scratch overlay) — pre-existing, not touched here; cleanup is a separate chore.
5. The `ACK_RUNNER_ERR_RU` reuse vs a dedicated `ACK_INBOX_ROUTER_ERR_RU` was left as reuse (no new user-facing string); revisit if UX wants a distinct message.
