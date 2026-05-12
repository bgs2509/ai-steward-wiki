---
feature: inbox-wiki-route-ingest
bd_id: aisw-zd9
epic: aisw-t2r
date: 2026-05-12
type: feature
status: complete
commits: 554b0d9, b0972be, ecc1493, 5d4d35c, 744834c, 84a3aae
adr: ADR-004
follows: aisw-dsg
---

# Completion report — Inbox-WIKI Phase-B: RouterDecision → domain WIKI select/materialise + move + Stage-1b ingest

## Goal

Close the loop opened by Phase-A (epic `aisw-t2r`). Before: the bot ran the Stage-1a Router in `Inbox-WIKI/` and just echoed `RouterDecision.notes`. Phase-B: a `ROUTE`/`CREATE_WIKI` decision now resolves/creates the target `<Domain>-WIKI`, moves the raw payload into it, runs a Stage-1b "librarian" session there, and replies with the ingest result. Confirm loop, cron bridge, hint fast-path remain out of scope (`aisw-e45/kcz/12t`).

## What changed

1. **`M-INBOX-ROUTE` — new pure module `src/ai_steward_wiki/inbox/route.py`** — `RouteTarget` / `RouteRejection` / `StagedRaw`; `resolve_target_wiki(decision, *, lifecycle, owner, wiki_root, default_template_id="_default", on_route_missing=None)` (ROUTE → lookup; ROUTE-missing → `create_wiki` + callback + warning; CREATE_WIKI → `create_wiki`; `AntiSpamCapError` → `RouteRejection("cap")`; `WikiNameError` → `RouteRejection("bad_name")`; `created` computed from a pre-`create` lookup); `render_target_raw` + `stage_raw_into_wiki` (write `wiki_dir/raw/<utc-ts>_<source>.md` — plain body for text, YAML front-matter for media — and `promote_path_to_raw` each media binary into `wiki_dir/raw/media/`, skipping already-gone files); `pick_domain_overlay` (`prompts/domain-<slug>.md` if present else `domain-default.md`); `build_ingest_prompt` (ru Stage-1b instruction referencing the raw path(s) + the user text).
2. **`M-TG-PIPELINE-CLASSIFIER`** — `DefaultPipeline` gains an optional `librarian: Librarian` + the `IngestOutcome` dataclass. In the routable branch, after `router.route()`, a `ROUTE`/`CREATE_WIKI` decision (with a wired librarian + output) is executed via `librarian.ingest(...)`; the reply (`notes + "\n\n" + summary` | `notes + hint`) is delivered via `OutputDelivery.deliver` (`status="ok"`) or `send_message` (`rejected` / `run_failed`). `CLARIFY`/`REJECT` and the `librarian is None` case keep Phase-A's notes-echo. Phase-A's `tg.pipeline.router.delivered` log renamed → `tg.pipeline.router.decided`; new anchors `tg.pipeline.route.ingest_dispatched|delivered`.
3. **`M-RUNTIME-WIRING`** — new `_LibrarianAdapter`: `resolve_target_wiki` → `stage_raw_into_wiki` → `run_wiki_session(wiki_id=f"{tid}/{primary}", wiki_path=<Domain>-WIKI dir, base=prompts/wiki.md, overlay=pick_domain_overlay(...), user_input=build_ingest_prompt(...), media_paths=staged.media_abs or None, timeout_s=None)` → `IngestOutcome("ok", notes + summary, …)`; `RouteRejection` → `IngestOutcome("rejected", notes + hint, …)`; `WikiRunnerError` → `IngestOutcome("run_failed", notes + retry-hint, …)` (the moved raw is kept). Injected into `DefaultPipeline` as `librarian=`. New log anchors `inbox.route.target_resolved|route_target_was_missing|cap_reached|bad_name|raw_moved|ingest.begin|ingest.done|ingest_failed`.
4. **Tests** — `tests/unit/inbox/test_route.py` (13: `resolve_target_wiki` create/idempotent/route-existing/route-missing+callback/cap/bad-name; `stage_raw_into_wiki` text/voice+promote/missing-media-skipped; `pick_domain_overlay` known/fallback; `build_ingest_prompt` references). `tests/unit/tg/test_pipeline_route_ingest.py` (7: routable→librarian.ingest+deliver-on-ok; rejected→send_message; run_failed→send_message; CLARIFY→no librarian; librarian=None→notes-echo; log markers). `tests/unit/test_librarian_adapter.py` (6: CREATE_WIKI happy path incl. `run_wiki_session` call args; `WikiRunnerError`→run_failed keeps raw; cap→rejected; ROUTE-missing→create+warning-log; log anchors; media promoted into the target WIKI). `tests/integration/test_e2e_pipeline.py` scenario 6 (+`real_librarian_adapter` / `pipeline_full_routing` fixtures, gated `RUN_INTEGRATION=1` + claude binary): routable text → real Stage-1a router → (if ROUTE/CREATE_WIKI) real Stage-1b ingest into `<wiki>/<tid>/<Name>-WIKI/`; tolerant of CLARIFY/REJECT.
5. **GRACE** — `M-INBOX-ROUTE` node + `V-M-INBOX-ROUTE` + dependency/CrossLink updates + new log markers + `Librarian`/`IngestOutcome` annotations; `Phase-B2` entry + module in `development-plan.xml`; version bumps (KG 0.0.7, VP 0.0.7, DP 0.0.6). ADR-004.

## Design decisions (via /questions-answers, see ADR-004)

Auto-create on a missing `ROUTE` target (+warning) · move-raw-into-target-before-Stage-1b (forward-only retry, no rollback) · new `inbox/route.py` pure helpers + dedicated `_LibrarianAdapter` + narrow `Librarian` Protocol · reply = `notes + "\n\n" + summary` via `deliver_output` · `template_id="_default"` for now · text-timeout (~300s) for Stage-1b regardless of source.

## Verification

`make total-test` — ALL STEPS PASSED: ruff-check 0, ruff-format 180/180, mypy 68 files 0 errors, grace-lint 68 governed + 3 XML 0 errors/warnings, inv-lint 14/14, test-cov **500 passed / 0 failed / coverage 91.68%**. `tests/integration` collects 11 scenarios (all skipped here — no Claude CLI in this session; nightly + cutover gate). One pre-existing RuntimeWarning (`_amain` never awaited) unrelated to this change.

## Follow-ups / notes

1. **The Phase-A "do not deploy `master`" caveat is lifted** — with Phase-A + Phase-B the route+ingest loop is closed. Recorded on epic `aisw-t2r`. (Production cutover still gated by `cutover-checklist.md` + the nightly integration suite.)
2. Phase-C (`aisw-e45`) wraps `resolve+move+ingest` behind an inline-button confirm gate at the `router.route() → librarian.ingest()` seam in `_run_text_pipeline`.
3. Phase-D (`aisw-kcz`) builds on the same orchestration point for the reminder/aggregator → `jobs.db` bridge.
4. Pre-existing gaps not touched: `WikiLifecycleManager.create_wiki` writes a minimal frontmatter `CLAUDE.md` (no template body); domain→`template_id` mapping; per-WIKI git auto-commit on ingest; retry/sweep of un-ingested files left in `<Domain>-WIKI/raw/`; restore-from-trash on `ROUTE` to a soft-deleted name.
5. The `_WikiRunnerAdapter`'s dead `overlay_prompt_path=prompts/inbox.md` ctor arg (noted in the Phase-A report) is still there — separate cleanup chore.
