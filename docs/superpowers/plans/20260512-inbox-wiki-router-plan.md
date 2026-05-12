# Implementation Plan — Inbox-WIKI Phase-A: stage raw content + Router-Claude invocation

**bd:** `aisw-dsg` · **epic:** `aisw-t2r` · **date:** 2026-05-12
**Discovery:** `docs/superpowers/specs/20260512-inbox-wiki-router-discovery.md`
**Design:** `docs/superpowers/specs/20260512-inbox-wiki-router-design.md`

TDD throughout: RED (failing test) → GREEN (minimal code) → REFACTOR. Commit after each validated step. Commit scope = GRACE MODULE_ID.

---

## Step 1 — `M-INBOX-ROUTER`: `RouterIntent` + `RouterDecision` + `parse_router_reply`

**Files:** new `src/ai_steward_wiki/inbox/router.py`; new `tests/unit/inbox/test_router.py`.

1. RED — write `tests/unit/inbox/test_router.py`:
   - happy path for each `RouterIntent` (`route` with `target_wiki`, `create_wiki` with proposed name, `clarify` with `null`, `reject` with `null`); assert `parsed_ok is True`.
   - multi-line `notes:` (text spans several lines until the closing fence).
   - `target_wiki: null` and `target_wiki:` (empty) → `None`.
   - preamble text + a quoted format example *before* the real ```` ```router ```` block → last block wins.
   - no `router` block at all → `RouterDecision(intent=CLARIFY, target_wiki=None, parsed_ok=False)`, `notes` = trimmed first ≤500 chars (or generic ru prompt if empty).
   - unknown `intent:` value → fallback (`parsed_ok=False`, `CLARIFY`).
   - cross-field demotion: `intent: route` but `target_wiki: null` → demoted to `CLARIFY` with generic ru `notes`, `parsed_ok=False`.
   - `RouterDecision` is frozen + `extra="forbid"` (constructing with an extra kwarg raises).
   Run `uv run pytest tests/unit/inbox/test_router.py` → fails (module missing).
2. GREEN — create `src/ai_steward_wiki/inbox/router.py` with the full GRACE header (see §below), `RouterIntent(str, Enum)` (`ROUTE/CREATE_WIKI/CLARIFY/REJECT`), `RouterDecision(BaseModel, frozen, extra=forbid)` with fields `intent, target_wiki: str|None, notes: str, raw: str, parsed_ok: bool`, and `parse_router_reply(text: str) -> RouterDecision` implementing the parser rules from design §2 (regex `r"```router\s*\n(.*?)\n```"` with `re.DOTALL`, take the *last* match; line-scan `^(target_wiki|intent|notes):` with `notes` greedy-to-end; literal `null`/empty → `None`; cross-field normalisation; fallback). `# START_BLOCK_EXTRACT_FENCED` / `# START_BLOCK_NORMALISE` markers around the two logical parts.
   Run pytest → passes. `make lint` → clean.
3. REFACTOR if needed. Commit: `feat(M-INBOX-ROUTER): RouterDecision model + fenced-block reply parser`.

GRACE header for `router.py`:
```
# FILE: src/ai_steward_wiki/inbox/router.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Parse the Stage-1a Inbox-WIKI Router reply (fenced ```router block) into a RouterDecision.
#   SCOPE: RouterIntent enum, RouterDecision model, parse_router_reply (tolerant, fallback to CLARIFY).
#   DEPENDS: pydantic, re, enum
#   LINKS: D-004, D-016, prompts/inbox.md (>=1.1.0), M-INBOX-ROUTER, aisw-dsg
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
# START_MODULE_MAP
#   RouterIntent - closed enum: route | create_wiki | clarify | reject
#   RouterDecision - frozen Pydantic model (intent, target_wiki, notes, raw, parsed_ok)
#   parse_router_reply - extract the last ```router block, parse key:value, normalise, fallback
# END_MODULE_MAP
```
With `# START_CONTRACT: parse_router_reply` / `# END_CONTRACT: parse_router_reply` around the function (INPUTS `{ text: str }`, OUTPUTS `{ RouterDecision }`, SIDE_EFFECTS none).

## Step 2 — `prompts/inbox.md` → 1.1.0 (strict response format)

**Files:** `prompts/inbox.md`; touch any test that pins the prompt semver/sha (search `prompts/inbox` in tests first).

1. RED — if a test asserts the prompt semver/content, update its expectation to `1.1.0` first (so it fails until the file changes). If none — skip RED, this is a content artifact.
2. GREEN — bump first line to `semver: 1.1.0`; replace the "Формат ответа" section with the strict fenced-`router`-block spec from design §3 (keep "Задачи" section). Ensure the file still passes `check-yaml`-style hooks (it's Markdown, fine) and `run_wiki_session`'s semver validation (`semver: X.Y.Z` on line 1 — already the convention).
3. Run any prompt-loading unit test → passes. Commit: `feat(M-INBOX-ROUTER): tighten prompts/inbox.md router reply to a fenced block (v1.1.0)`.

## Step 3 — `M-TG-PIPELINE-CLASSIFIER`: routable-intent branch → `Router.route` → deliver notes

**Files:** `src/ai_steward_wiki/tg/pipeline.py`; new `tests/unit/tg/test_pipeline_router.py` (or extend existing pipeline test module).

1. RED — `tests/unit/tg/test_pipeline_router.py` with a fake `Router` (`route(...)` returns a canned `RouterDecision`), fake classifier (intent overridable), fake sender, fake runner:
   - intent `WIKI_INGEST` + router wired → `router.route` called once with `text/telegram_id/correlation_id/source/media_paths/timeout_s`; `sender.send_message(chat_id, decision.notes)` called; `runner.run` / `streaming.run_and_deliver` NOT called.
   - same for `WIKI_QUERY` and `UNKNOWN`.
   - intent `REMINDER` (non-routable) → legacy path (`runner`/`streaming`) used; `router.route` NOT called.
   - routable intent + `router is None` → legacy path used (graceful fallthrough).
   - `router.route` raises `RouterError` → `sender.send_message(chat_id, ACK_RUNNER_ERR_RU)`; no exception escapes.
   - `_routable_wired` property True only when classifier+router+output all set.
   Run pytest → fails (no `router` param / branch).
2. GREEN — in `pipeline.py`: import `RouterDecision`, `RouterError` (`RouterError` defined in `__main__.py`? No — define it in `inbox/router.py` so the pipeline can import it without importing `__main__`); add module constant `_ROUTABLE_INTENTS = frozenset({Intent.WIKI_INGEST, Intent.WIKI_QUERY, Intent.UNKNOWN})`; add `Router` Protocol (`async def route(*, text, telegram_id, correlation_id, source, media_paths=None, timeout_s=None) -> RouterDecision`); add ctor param `router: Router | None = None` + `self._router`; add `_routable_wired` property; in `_run_text_pipeline` after `classify`, before the legacy run: `if result.intent in _ROUTABLE_INTENTS and self._router is not None: <log tg.pipeline.router.dispatched>; try: decision = await self._router.route(...) except RouterError: <log tg.pipeline.router.error>; send ACK_RUNNER_ERR_RU; return; send_message(chat_id, decision.notes); return`. Update the `M-TG-PIPELINE-CLASSIFIER` MODULE_MAP/CONTRACT header (add `Router`, `_ROUTABLE_INTENTS`; +DEPENDS `M-INBOX-ROUTER`) and `START_CHANGE_SUMMARY`.
   - **Decision pending Step 9 self-review:** reuse `ACK_RUNNER_ERR_RU` vs a new inbox-specific ru string. Default = reuse (no new user-facing string). If reviewer prefers a dedicated message, add `ACK_INBOX_ROUTER_ERR_RU` near the other ack constants.
   Run pytest → passes. `make lint` → clean.
3. Commit: `feat(M-TG-PIPELINE-CLASSIFIER): route WIKI_INGEST/WIKI_QUERY/UNKNOWN through the Inbox-WIKI Router`.

> `RouterError` lives in `inbox/router.py` (so both `pipeline.py` and `__main__.py` import it from there). Adjust Step 1 header SCOPE accordingly: add `RouterError - raised by the runtime adapter when the Router CLI run fails unrecoverably`.

## Step 4 — `M-RUNTIME-WIRING`: `_RouterAdapter` + wiring into the pipeline

**Files:** `src/ai_steward_wiki/__main__.py`; new/extended `tests/unit/...` for the adapter (mirror the existing `_WikiRunnerAdapter` test style; if `__main__` has no unit test module yet, add `tests/unit/test_main_router_adapter.py` with fakes for `acquirer`/`spawner`/`run_wiki_session`).

1. RED — test `_RouterAdapter.route`:
   - calls `ensure_inbox_wiki(telegram_id, wiki_root=..., template_path=<templates/inbox-wiki/CLAUDE.md>)` → uses returned `inbox_dir`.
   - writes a raw sidecar under `inbox_dir/raw/`: for `source="text"` → `<ts>_text.md` with the message body; for `source="voice"` → `<ts>_voice.md` with YAML front-matter (`source: voice`, `staged_path: <path>`, `received_utc: ...`) + a `transcript:` block (= `text`); for `source="document"` → `<ts>_document.md` with `filename:` + `mime:` if available (pass-through from the handler) + `staged_path:`.
   - invokes `run_wiki_session` with `wiki_id == f"{telegram_id}/Inbox-WIKI"`, `wiki_path == inbox_dir`, `overlay_prompt_path == <prompts/inbox.md>`, `user_input == text`, `media_paths` forwarded, `timeout_s` forwarded.
   - parses the result via `parse_router_reply`; returns the `RouterDecision`.
   - `WikiRunnerError` from `run_wiki_session` → re-raised as `RouterError`.
   - emits `inbox.router.staged_raw`, `inbox.router.run.begin/done`, `inbox.router.parsed` (assert via a caplog/structlog capture helper already used elsewhere).
   Run pytest → fails.
2. GREEN — add `_RouterAdapter` (ctor: `wiki_root, inbox_template_path, base_prompt_path, inbox_overlay_path, runtime_dir, acquirer, spawner, run_config`) with `async def route(...)` per design §5, plus a private `_stage_raw(inbox_dir, *, source, text, media_paths, mime=None, filename=None, correlation_id) -> Path` helper (UTC timestamp `datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")`, atomic write via tmp+`os.replace` like `materialize._materialise_sync`). Resolve `inbox_overlay_path` from the prompts dir Settings exposes (same place `base_prompt_path` comes from); `inbox_template_path` from the templates dir. In `_amain` (or wherever adapters are built): construct `router_adapter = _RouterAdapter(...)`; pass `router=router_adapter` into the pipeline/dispatcher construction (`build_dispatcher(... , router=...)` → `MessagePipeline(... , router=...)` / `DefaultPipeline(... , router=...)`). Update `__main__.py` MODULE_CONTRACT `DEPENDS` (+`M-INBOX-ROUTER`) and `START_CHANGE_SUMMARY`; update knowledge-graph CrossLinks (`M-RUNTIME-WIRING → M-INBOX-ROUTER`, `M-TG-PIPELINE-CLASSIFIER → M-INBOX-ROUTER`).
   Run pytest → passes. `make lint` → clean.
3. Commit: `feat(M-RUNTIME-WIRING): wire _RouterAdapter (Inbox-WIKI Stage-1a) into the pipeline`.

## Step 5 — Integration test (gated)

**Files:** extend `tests/integration/test_e2e_pipeline.py`.

1. Add a scenario: a text message classified to a routable intent (use the real Stage-0 backend or force the intent via a small seam) → assert the bot replies with the Router `notes`, and that a transcript file exists under `<wiki_root>/<tid>/Inbox-WIKI/runs/<run_id>/transcript.jsonl` (proving `cwd`/`wiki_path` was the Inbox-WIKI dir). Keep it under the existing `RUN_INTEGRATION=1` gate. Update `make integration` doc if needed (likely already picks the file up).
2. Run `RUN_INTEGRATION=1 uv run pytest tests/integration/test_e2e_pipeline.py -q` locally (best-effort — requires the real Claude CLI; if unavailable in this session, mark the assertion and note it for the reviewer / CI nightly).
3. Commit: `test(M-INBOX-ROUTER): e2e — routable text runs the Router in Inbox-WIKI/`.

## Step 6 — GRACE refresh + knowledge-graph / verification-plan

1. Update `docs/knowledge-graph.xml`: add `M-INBOX-ROUTER` node (TYPE=RUNTIME, STATUS=done, path `src/ai_steward_wiki/inbox/router.py`, depends `-` (only stdlib+pydantic), verification-ref `V-M-INBOX-ROUTER`, annotations for the three exports + `RouterError`); add `M-INBOX-ROUTER` to `<depends>` of `M-TG-PIPELINE-CLASSIFIER` and `M-RUNTIME-WIRING`; add CrossLinks. (Run `grace-refresh` to let it derive what it can, then hand-fix the rest.)
2. Update `docs/verification-plan.xml`: add `V-M-INBOX-ROUTER` (unit `tests/unit/inbox/test_router.py`), extend `V-M-TG-PIPELINE-CLASSIFIER` (`tests/unit/tg/test_pipeline_router.py` + new log anchors `tg.pipeline.router.dispatched|error`), extend `V-M-RUNTIME-WIRING` (`tests/unit/test_main_router_adapter.py` + anchors `inbox.router.staged_raw|run.begin|run.done|parsed|parse_error`), extend the e2e suite entry. Run `grace-refresh --verify`.
3. `grace lint --failOn errors` → 0 issues.
4. Commit: `chore(knowledge-graph): add M-INBOX-ROUTER + refresh after Inbox-WIKI Phase-A`.

## Step 7 — Finish

1. `make total-test` (lint + grace + inv-lint + coverage ≥80% + integration if env) → green (integration best-effort).
2. ADR (Step 8 of feature-workflow): write `docs/adr/ADR-NNN-inbox-wiki-router-invocation.md` — decision: Stage-1a Router runs in `Inbox-WIKI/` via a dedicated adapter; reply is a fenced `router` block parsed into `RouterDecision`; replaces the flat run for routable intents; alternatives considered (feature-flag, JSON reply, shared adapter) — this sets the pattern Phases B–E build on. (NNN = next free number under `docs/adr/`.)
3. Update `docs/superpowers/specs/20260512-inbox-wiki-router-discovery.md` / `-design.md` status frontmatter → `done`.
4. `_report` — completion report `docs/reports/20260512-inbox-wiki-router-report.md` (this is a meaningful feature, not a trivial fix).
5. Changelog: append to `docs/20260408_changelog.md` if that file is the project changelog (verify it exists first).
6. `bd update aisw-dsg --notes="Phase-A done — see report"`; `bd close aisw-dsg --reason="Inbox-WIKI Phase-A complete; aisw-zd9 (Phase-B) now unblocked"`; `bd close --suggest-next`.
7. **Do NOT `git push`** (user must request). **Do NOT deploy `master`** until `aisw-zd9` closes — add this note to the epic `aisw-t2r`.

---

## Self-review checklist (Step 9 of feature-workflow)

- [x] Every changed/new MODULE_CONTRACT has task(s): `M-INBOX-ROUTER` (Step 1–2), `M-TG-PIPELINE-CLASSIFIER` (Step 3), `M-RUNTIME-WIRING` (Step 4).
- [x] Every FR covered: FR-1 (Step 4 ensure_inbox_wiki), FR-2 (Step 4 _stage_raw + sidecar), FR-3 (Step 4 run_wiki_session in Inbox-WIKI), FR-4 (Step 1 parse_router_reply + fallback), FR-5 (Step 3 deliver notes), FR-6 (Step 3 _ROUTABLE_INTENTS gate).
- [x] Every NFR has a verification step: NFR-1 log anchors (Steps 3,4 + verification-plan Step 6), NFR-2 reuse run_wiki_session + lock id (Step 4 test), NFR-3 default timeout (Step 4 — no new knob), NFR-4 mypy/ruff/grace (every step `make lint`; Step 7 `make total-test`), NFR-5 no new dep (no `pyproject.toml` change in any step).
- [x] Verification plan updated (Step 6).
- [x] Log anchors from design §6 included (Steps 3, 4).
- [x] ADR decisions implemented (Step 7.2 records them post-hoc; the design already follows them).
- [x] Task order respects DEPENDS: router.py → prompt → pipeline → __main__ → integration → graph → finish.
- [x] No placeholders — every step has concrete files, tests, commands.
- [ ] One open micro-decision deferred to execution/reviewer: reuse `ACK_RUNNER_ERR_RU` vs new `ACK_INBOX_ROUTER_ERR_RU` (Step 3) — default reuse.
