# Implementation Plan — Inbox-WIKI Phase-B: RouterDecision → domain WIKI select/materialise + move + Stage-1b ingest

**bd:** `aisw-zd9` · **epic:** `aisw-t2r` · **date:** 2026-05-12
**Discovery:** `docs/superpowers/specs/20260512-inbox-wiki-route-ingest-discovery.md`
**Design:** `docs/superpowers/specs/20260512-inbox-wiki-route-ingest-design.md`

TDD throughout: RED → GREEN → REFACTOR. Commit after each validated step. Scope = GRACE MODULE_ID.

---

## Step 1 — `M-INBOX-ROUTE`: new pure module `src/ai_steward_wiki/inbox/route.py`

**Files:** new `src/ai_steward_wiki/inbox/route.py`; new `tests/unit/inbox/test_route.py`.

1. RED — `tests/unit/inbox/test_route.py`:
   - `resolve_target_wiki`: build a real `WikiLifecycleManager(tmp_path, max_per_user=2)`. `RouterDecision(intent=CREATE_WIKI, target_wiki="Travel-WIKI", …)` → `RouteTarget`, `created=True`, `wiki_dir == tmp_path/<owner>/Travel-WIKI`. Second call same name → `created=False`. `RouterDecision(intent=ROUTE, target_wiki="Travel-WIKI")` after it exists → `created=False`. `RouterDecision(intent=ROUTE, target_wiki="Garden-WIKI")` when absent → `created=True` + `on_route_missing` callback called once. Cap: create 2 wikis then a 3rd `CREATE_WIKI` → `RouteRejection(reason="cap")`. Bad name: `target_wiki="!!!"` (or whatever `normalize_wiki_name` rejects) → `RouteRejection(reason="bad_name")`.
   - `render_target_raw` / `stage_raw_into_wiki`: `stage_raw_into_wiki(wiki_dir, source="text", user_text="hi", media_paths=None)` → `StagedRaw` with `sidecar_rel == "raw/<ts>_text.md"`, file exists under `wiki_dir`, content == `"hi\n"`, `media_rel == []`. `source="voice"` with a real staged file (write a tmp `.ogg`) → sidecar `.md` with `---\nsource: voice\n…` front-matter; `media_rel == ["raw/media/<ISO>_<sha8>.ogg"]`; `media_abs[0].exists()`, is absolute, the original staged file is gone (moved).
   - `pick_domain_overlay(prompts_dir, "health")` with a tmp prompts dir containing `domain-health.md` + `domain-default.md` → returns the health one; `pick_domain_overlay(prompts_dir, "garden")` → `domain-default.md`.
   - `build_ingest_prompt("вот билет", staged)` → str contains `staged.sidecar_rel`, the media rel paths (if any), and `"вот билет"`.
   Run `uv run pytest tests/unit/inbox/test_route.py` → fails (module missing).
2. GREEN — create `src/ai_steward_wiki/inbox/route.py` with the full GRACE header (PURPOSE: resolve/create target Domain-WIKI from a RouterDecision, stage the raw payload into it, build the Stage-1b ingest prompt; DEPENDS: ai_steward_wiki.wiki.{lifecycle,name}, ai_steward_wiki.inbox.{staging,router}; ROLE: RUNTIME; MAP_MODE: EXPORTS), `MODULE_MAP`, `START_CHANGE_SUMMARY`. Implement per design §2:
   - frozen `slots` dataclasses `RouteTarget(wiki_name: WikiName, wiki_dir: Path, created: bool)`, `RouteRejection(reason: Literal["cap","bad_name"], hint: str)`, `StagedRaw(sidecar_rel: str, media_rel: list[str], media_abs: list[Path])`; `RouteOutcome = RouteTarget | RouteRejection`.
   - `_RU_CAP_HINT = "Достигнут лимит вики — удали ненужную и попробуй снова."`, `_RU_BAD_NAME_HINT = "Не смог разобрать имя вики — переформулируй."`
   - `resolve_target_wiki(decision, *, lifecycle, owner, wiki_root, default_template_id="_default", on_route_missing=None) -> RouteOutcome`: for `ROUTE` first `lifecycle.lookup(owner, decision.target_wiki)`; if found → `RouteTarget(found, wiki_root/str(owner)/found.primary, created=False)`; if not found and intent is `ROUTE` → call `on_route_missing()` if given, then fall through to create. For `CREATE_WIKI` (or ROUTE-missing): `try: name = lifecycle.create_wiki(owner, decision.target_wiki, default_template_id) except AntiSpamCapError: return RouteRejection("cap", _RU_CAP_HINT) except WikiNameError: return RouteRejection("bad_name", _RU_BAD_NAME_HINT)`. Determine `created` by comparing pre/post `lifecycle.lookup` existence — simpler: `existed = lifecycle.lookup(owner, decision.target_wiki) is not None; name = create_wiki(...); return RouteTarget(name, wiki_root/str(owner)/name.primary, created=not existed)`. (`create_wiki` is idempotent — returns existing on duplicate/near-dup, so `created` is reliable iff we checked before.) **Note:** `normalize_wiki_name` raises `WikiNameError` (a `ValueError`); `create_wiki` calls it internally — so the `except WikiNameError` around `create_wiki` covers it. `# START_BLOCK_RESOLVE_TARGET` marker.
   - `render_target_raw(*, source, user_text, media_rel) -> tuple[str,str]`: `ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")`; text → `(f"{ts}_text.md", user_text if user_text.endswith("\n") else user_text+"\n")`; non-text → `.md` with `---\nsource: {source}\nreceived_utc: {ts}\nraw_media:\n  - {p}\n…\n---\n\n## Содержимое\n\n{user_text.rstrip()}\n`.
   - `stage_raw_into_wiki(wiki_dir, *, source, user_text, media_paths) -> StagedRaw`: promote each media path via `promote_path_to_raw(p, wiki_root=wiki_dir)` (absolute targets) → compute `media_rel = [str(p.relative_to(wiki_dir)) for p in promoted]`, `media_abs = promoted`; then `filename, content = render_target_raw(source=source, user_text=user_text, media_rel=media_rel)`; atomic write (`tmp` + `os.replace`) to `wiki_dir/"raw"/filename` (mkdir parents); return `StagedRaw(f"raw/{filename}", media_rel, media_abs)`. Log `inbox.route.raw_moved` per artifact (sidecar + each media). `# START_BLOCK_STAGE_RAW` marker.
   - `pick_domain_overlay(prompts_dir, slug) -> Path`: `cand = prompts_dir / f"domain-{slug}.md"; return cand if cand.exists() else prompts_dir / "domain-default.md"`.
   - `build_ingest_prompt(user_text, staged) -> str`: a ru instruction — "Пользователь прислал материал для занесения в эту WIKI.\nТекст обращения: {user_text}\nФайлы в этой WIKI: {sidecar_rel}{; медиа: …media_rel… if any}\nВыполни ingest: распарси содержимое, занеси информацию в подходящие страницы (создай при необходимости), проставь бэклинки между страницами, запиши действие в log.md. Кратко ответь, что записал и на какие страницы." + (if media: "Изображения/аудио открой инструментом Read.").
   Run pytest → passes. `make lint` → clean.
3. Add `# START_CONTRACT` blocks for `resolve_target_wiki` and `stage_raw_into_wiki`. REFACTOR if needed. Commit: `feat(M-INBOX-ROUTE): target-WIKI resolution + raw staging + ingest-prompt helpers`.

## Step 2 — `M-TG-PIPELINE-CLASSIFIER`: `Librarian` Protocol + `IngestOutcome` + ROUTE/CREATE_WIKI dispatch

**Files:** `src/ai_steward_wiki/tg/pipeline.py`; new `tests/unit/tg/test_pipeline_route_ingest.py`.

1. RED — `tests/unit/tg/test_pipeline_route_ingest.py` with a fake `Librarian` (`ingest(...)` returns a canned `IngestOutcome`), fake router (returns a `RouterDecision`), fake sender/output/runner:
   - router(`ROUTE`) + librarian wired → `librarian.ingest` called once with `decision`, `telegram_id`, `user_text==text`, `source`, `correlation_id`; `IngestOutcome(status="ok", reply="R\n\nS", run_id="ing-1", target_wiki="T-WIKI", created=False)` → `output.deliver(chat_id, telegram_id, run_id="ing-1", text="R\n\nS")` called; `sender.send_message` NOT for the notes; `runner.run` NOT called.
   - router(`CREATE_WIKI`) → same as ROUTE (librarian called).
   - `IngestOutcome(status="rejected", reply="R\n\nlimit", run_id=None, …)` → `sender.send_message(chat_id, "R\n\nlimit")`; `output.deliver` NOT called.
   - `IngestOutcome(status="run_failed", reply="R\n\nlater", run_id="ing-2", …)` → `sender.send_message(chat_id, "R\n\nlater")`.
   - router(`CLARIFY`) → `librarian.ingest` NOT called; `sender.send_message(chat_id, decision.notes)` (Phase-A behaviour).
   - router(`ROUTE`) + `librarian is None` → `librarian` not called; `sender.send_message(chat_id, decision.notes)` (graceful fall-through).
   - log markers `tg.pipeline.route.ingest_dispatched` + `tg.pipeline.route.delivered` via capsys.
   Also: existing `tests/unit/tg/test_pipeline_router.py` still passes (the ROUTE/CLARIFY decisions there now hit the new branch — the `_make_router` default returns `RouterIntent.ROUTE` with `target_wiki="Travel-WIKI"`; those tests pass `router` but NOT `librarian` → fall-through to `send_message(notes)` → still asserts `sends[0]["text"] == "Положу в Travel-WIKI."` ✅). Verify by running it.
   Run pytest → fails (no `librarian` param / branch).
2. GREEN — in `pipeline.py`: import `RouterIntent` (already import `RouterDecision`); add `from dataclasses import dataclass` is already there → add frozen `slots` `IngestOutcome(status: Literal["ok","rejected","run_failed"], reply: str, run_id: str | None, target_wiki: str | None, created: bool)` near `WikiRunOutcome`; add `Librarian` Protocol (`async def ingest(self, decision: RouterDecision, *, telegram_id: int, user_text: str, source: Literal["text","voice","document","photo"], media_paths: list[Path] | None = None, correlation_id: str) -> IngestOutcome`); add ctor param `librarian: Librarian | None = None` + `self._librarian`; add `"IngestOutcome"`, `"Librarian"` to `__all__`. In `_run_text_pipeline`'s `# START_BLOCK_ROUTABLE_BRANCH`, after the `tg.pipeline.router.delivered`-equivalent... actually the current code does `send_message(decision.notes); return`. Replace that tail with:
   ```python
   if decision.intent in (RouterIntent.ROUTE, RouterIntent.CREATE_WIKI) and self._router_ingest_wired:
       _log.info("tg.pipeline.route.ingest_dispatched", correlation_id=..., telegram_id=..., intent=decision.intent.value, source=source)
       outcome = await self._librarian.ingest(decision, telegram_id=telegram_id, user_text=text, source=source, media_paths=media_paths, correlation_id=correlation_id)
       _log.info("tg.pipeline.route.delivered", correlation_id=..., telegram_id=..., status=outcome.status, target_wiki=outcome.target_wiki, created=outcome.created, run_id=outcome.run_id)
       if outcome.status == "ok":
           assert self._output is not None
           await self._output.deliver(chat_id=chat_id, telegram_id=telegram_id, run_id=outcome.run_id or "", text=outcome.reply)
       else:
           await self._sender.send_message(chat_id, outcome.reply)
       return
   await self._sender.send_message(chat_id, decision.notes)
   return
   ```
   where `_router_ingest_wired` = `self._librarian is not None and self._output is not None`. (Keep the existing `tg.pipeline.router.delivered` log from Phase-A above this — rename it if it collides? Phase-A used `tg.pipeline.router.delivered` for the notes-send. Now the notes-send only happens on CLARIFY/REJECT/no-librarian. Keep `tg.pipeline.router.delivered` where it is — it logs the parsed decision regardless; the new `tg.pipeline.route.delivered` logs the ingest outcome. Slightly close names but distinct (`router` vs `route`). To avoid confusion rename the Phase-A one to `tg.pipeline.router.decided` — small ripple in `test_pipeline_router.py` (one capsys assert). Decide at execution: prefer rename for clarity.) Update the `M-TG-PIPELINE-CLASSIFIER` header (MODULE_MAP +`Librarian`, `IngestOutcome`; +DEPENDS `M-INBOX-ROUTE`; `START_CHANGE_SUMMARY`; bump VERSION 0.4.0 → 0.5.0).
   Run both new and `test_pipeline_router.py` → pass. `make lint` → clean.
3. Commit: `feat(M-TG-PIPELINE-CLASSIFIER): dispatch ROUTE/CREATE_WIKI to the Librarian (Stage-1b ingest)`.

## Step 3 — `M-RUNTIME-WIRING`: `_LibrarianAdapter` + wiring into the pipeline

**Files:** `src/ai_steward_wiki/__main__.py`; new `tests/unit/test_librarian_adapter.py`.

1. RED — test `_LibrarianAdapter.ingest` (mirror `test_router_adapter.py` style; patch `runtime.run_wiki_session` with an `AsyncMock` returning a fake result whose `.events = [StreamEvent(type="assistant_chunk", payload={"text": "Записал X на стр. y.md"})]`, `.latency_ms = 50`):
   - `RouterDecision(intent=CREATE_WIKI, target_wiki="Travel-WIKI", notes="Положу в Travel-WIKI.", …)` over a tmp `wiki_root` → the dir `<wiki_root>/<tid>/Travel-WIKI/` is created (via the real `WikiLifecycleManager` injected with `wiki_root=tmp`), a `raw/<ts>_text.md` appears under it, `run_wiki_session` called with `wiki_id == f"{tid}/Travel-WIKI"`, `wiki_path == <wiki_root>/<tid>/Travel-WIKI`, `overlay_prompt_path == <prompts>/domain-default.md`, `base_prompt_path == <prompts>/wiki.md`, `user_input` is the ingest prompt (contains `raw/<ts>_text.md` and the user text), `timeout_s is None`; return `IngestOutcome(status="ok", run_id startswith "ingest-", target_wiki="Travel-WIKI", created=True)`, `reply.startswith("Положу в Travel-WIKI.")` and contains `"Записал X"`.
   - `WikiRunnerError` from `run_wiki_session` → `IngestOutcome(status="run_failed", reply` contains `"Не удалось разложить"`, `run_id` set, `target_wiki="Travel-WIKI"`; the `raw/<ts>_text.md` still exists under the wiki dir).
   - cap: a `WikiLifecycleManager(tmp, max_per_user=1)` with one wiki already → `CREATE_WIKI` of a second → `IngestOutcome(status="rejected", reply` contains the cap hint, `run_id is None`).
   - ROUTE to a missing name → creates it, `created=True`, log `inbox.route.route_target_was_missing` via capsys.
   - log anchors `inbox.route.target_resolved`, `inbox.route.raw_moved`, `inbox.route.ingest.begin`, `inbox.route.ingest.done` via capsys.
   - media path: pass a tmp staged `.jpg`; assert it's promoted into `<wiki>/raw/media/...` and `run_wiki_session` got `media_paths` with that path.
   Run pytest → fails (no `_LibrarianAdapter`).
2. GREEN — add `_LibrarianAdapter` to `__main__.py` per design §4: ctor `(wiki_root, prompts_dir, lifecycle, runtime_dir, acquirer, spawner, run_config)`; `async def ingest(self, decision, *, telegram_id, user_text, source, media_paths=None, correlation_id) -> IngestOutcome` — `resolve_target_wiki(decision, lifecycle=self._lifecycle, owner=telegram_id, wiki_root=self._wiki_root, default_template_id="_default", on_route_missing=lambda: logger.warning("inbox.route.route_target_was_missing", correlation_id=correlation_id, telegram_id=telegram_id, target_wiki=decision.target_wiki))`; on `RouteRejection` → log `inbox.route.cap_reached`/`inbox.route.bad_name` + return `IngestOutcome("rejected", f"{decision.notes}\n\n{rej.hint}", None, None, False)`; on `RouteTarget` → log `inbox.route.target_resolved`; `staged = await asyncio.to_thread(stage_raw_into_wiki, target.wiki_dir, source=source, user_text=user_text, media_paths=media_paths)`; `overlay = pick_domain_overlay(self._prompts_dir, target.wiki_name.slug)`; `prompt = build_ingest_prompt(user_text, staged)`; `run_id = f"ingest-{uuid4().hex[:12]}"`; log `inbox.route.ingest.begin`; `try: result = await run_wiki_session(wiki_id=f"{telegram_id}/{target.wiki_name.primary}", wiki_path=target.wiki_dir, base_prompt_path=self._prompts_dir/"wiki.md", overlay_prompt_path=overlay, run_id=run_id, correlation_id=correlation_id, runtime_dir=self._runtime_dir, acquirer=self._acquirer, spawner=self._spawner, config=self._run_config, user_input=prompt, media_paths=staged.media_abs or None, timeout_s=None) except WikiRunnerError as e: log inbox.route.ingest_failed; return IngestOutcome("run_failed", f"{decision.notes}\n\nНе удалось разложить по полочкам — попробую позже.", run_id, target.wiki_name.primary, target.created)`; `summary = aggregate_text(result.events)`; log `inbox.route.ingest.done`; `return IngestOutcome("ok", f"{decision.notes}\n\n{summary or '(WIKI обновлена)'}", run_id, target.wiki_name.primary, target.created)`. Construct it in `_amain` next to `_RouterAdapter` (build a `WikiLifecycleManager(settings.wiki_root, max_per_user=settings.wiki_max_per_user, retention_days=settings.wiki_trash_retention_days)`); pass `librarian=librarian_adapter` into `DefaultPipeline(...)`. Update `__main__.py` header (SCOPE +`_LibrarianAdapter`; DEPENDS +`inbox.route`, +`M-INBOX-ROUTE` in LINKS; `START_CHANGE_SUMMARY`; bump VERSION 0.2.0 → 0.3.0). Imports: `from ai_steward_wiki.inbox.route import RouteRejection, RouteTarget, build_ingest_prompt, pick_domain_overlay, resolve_target_wiki, stage_raw_into_wiki`; `from ai_steward_wiki.tg.pipeline import IngestOutcome` (+ existing imports); `from ai_steward_wiki.wiki.lifecycle import WikiLifecycleManager`.
   Run pytest (new + `test_runtime_wiring.py`) → pass. `make lint` → clean. `uv run mypy src` → 0.
3. Commit: `feat(M-RUNTIME-WIRING): wire _LibrarianAdapter (Stage-1b ingest into the target WIKI)`.

## Step 4 — Integration scenario (gated)

**Files:** extend `tests/integration/test_e2e_pipeline.py` + conftest (`pipeline_with_router` → add a `librarian` so the routable path runs end-to-end, or a new `pipeline_full_routing` fixture wiring both `_RouterAdapter` and `_LibrarianAdapter`).

1. Add a `real_librarian_adapter` fixture (real `_LibrarianAdapter` over the tmp `wiki_root_e2e`, real `prompts/` + `WikiLifecycleManager`, real `AsyncioSpawner`/`WikiLockManager`, `CLAUDE_CONFIG_DIR`). Add `pipeline_full_routing` = the `pipeline_with_router` body + `librarian=real_librarian_adapter`. Scenario 6 `test_routable_text_routes_and_ingests`: a routable text message → real Stage-1a router → if it decided ROUTE/CREATE_WIKI → real Stage-1b ingest → assert the target `<wiki_root>/<tid>/<Name>-WIKI/` dir exists and contains at least one `.md` page besides `CLAUDE.md` (or a non-empty `log.md`), and the bot reply contains the router notes. Tolerate the router deciding CLARIFY/REJECT (then only `Inbox-WIKI/` exists and no ingest happened — assert that branch too). Keep under `RUN_INTEGRATION=1` + claude-binary gate. Bump conftest + test-file headers.
2. `RUN_INTEGRATION=1 uv run pytest tests/integration/test_e2e_pipeline.py -q` — best-effort (needs the real Claude CLI; if unavailable here, the test collects-but-skips — note for the reviewer / nightly).
3. Commit: `test(M-INBOX-ROUTE): e2e — routable text routes to a Domain-WIKI and ingests via Stage-1b`.

## Step 5 — GRACE refresh + knowledge-graph / verification-plan / dev-plan

1. `docs/knowledge-graph.xml`: add `M-INBOX-ROUTE` node (TYPE=RUNTIME, STATUS=done, path `src/ai_steward_wiki/inbox/route.py`, depends `M-WIKI-LIFECYCLE, M-INBOX, M-INBOX-ROUTER`, verification-ref `V-M-INBOX-ROUTE`, annotations for the exports); add `M-INBOX-ROUTE` to `<depends>` of `M-TG-PIPELINE-CLASSIFIER` and `M-RUNTIME-WIRING`; add CrossLinks (`M-TG-PIPELINE-CLASSIFIER → M-INBOX-ROUTE`, `M-RUNTIME-WIRING → M-INBOX-ROUTE`, `M-INBOX-ROUTE → M-WIKI-LIFECYCLE`, `M-INBOX-ROUTE → M-INBOX`, `M-INBOX-ROUTE → M-INBOX-ROUTER`); update the `M-TG-PIPELINE-CLASSIFIER` annotations (+`Librarian`, `IngestOutcome`); bump `Project VERSION` 0.0.6 → 0.0.7.
2. `docs/verification-plan.xml`: add `V-M-INBOX-ROUTE` (unit `tests/unit/inbox/test_route.py`); extend `V-M-TG-PIPELINE-CLASSIFIER` (`tests/unit/tg/test_pipeline_route_ingest.py` + markers `tg.pipeline.route.ingest_dispatched|delivered`); extend `V-M-RUNTIME-WIRING` (`tests/unit/test_librarian_adapter.py` + markers `inbox.route.target_resolved|route_target_was_missing|cap_reached|bad_name|raw_moved|ingest.begin|ingest.done|ingest_failed`); extend the e2e suite entry. Bump `VerificationPlan VERSION` 0.0.6 → 0.0.7.
3. `docs/development-plan.xml`: add `M-INBOX-ROUTE bd_id="aisw-zd9"` to `<Modules>`; add a `Phase-B2` (or `Phase-B.b`) entry under `<Phases>` describing Phase-B; bump `DevelopmentPlan VERSION` 0.0.5 → 0.0.6.
4. `grace lint --failOn errors` → 0 issues.
5. Commit: `chore(knowledge-graph): add M-INBOX-ROUTE + refresh after Inbox-WIKI Phase-B (aisw-zd9)`.

## Step 6 — Review + Finish

1. `make total-test` → ALL PASSED, coverage ≥80%.
2. ADR (Step 8 of feature-workflow): `docs/adr/ADR-004-inbox-wiki-route-ingest.md` — decisions: auto-create on a missing ROUTE target; move-raw-into-target-before-Stage-1b (forward-only retry, no rollback); dedicated `_LibrarianAdapter` + `Librarian` Protocol; `template_id="_default"` for now; text-timeout for Stage-1b. (NNN = next free under `docs/adr/` → 004.)
3. Update `docs/superpowers/specs/20260512-inbox-wiki-route-ingest-{discovery,design}.md` status → `done`.
4. `_report` — `docs/reports/20260512-inbox-wiki-route-ingest-report.md`.
5. `smart-commit` the remaining meta (ADR, report, status flips, graph) → `docs(adr): ADR-004 Inbox-WIKI route+ingest + Phase-B completion report (aisw-zd9)`.
6. `bd update aisw-zd9 --notes="Phase-B done — see report"`; `bd close aisw-zd9 --reason="Inbox-WIKI Phase-B complete; aisw-e45/aisw-kcz now unblocked"`; `bd close --suggest-next`.
7. **Update epic `aisw-t2r`:** Phase-A+B both done → the "do not deploy master" caveat from Phase-A is **lifted** (the route+ingest loop is closed). Note that on the epic.
8. **Do NOT `git push`** (user must request).

---

## Self-review checklist (Step 9 of feature-workflow)

- [x] Every changed/new MODULE_CONTRACT has task(s): `M-INBOX-ROUTE` (Step 1), `M-TG-PIPELINE-CLASSIFIER` (Step 2), `M-RUNTIME-WIRING` (Step 3).
- [x] Every FR covered: FR-1 (Step 1 `resolve_target_wiki` + Step 3 wiring), FR-2 (Step 1 `RouteRejection` + Step 3 cap/bad-name handling), FR-3 (Step 1 `stage_raw_into_wiki` move-before), FR-4 (Step 3 `run_wiki_session` + Step 1 `pick_domain_overlay`/`build_ingest_prompt`), FR-5 (Step 2 dispatch + Step 3 reply composition), FR-6 (log anchors in Steps 1–3 + verification-plan in Step 5).
- [x] Every NFR has a verification step: NFR-1 reuse (Step 1/3 — no reimplementation; `make lint`/`mypy` every step; `make total-test` Step 6), NFR-2 no new dep/table (no `pyproject.toml`/alembic change in any step), NFR-3 lock keyed on target WIKI (Step 3 test asserts `wiki_id`), NFR-4 ru-only / UTC / no bypass / no git wiring (Steps 1–3).
- [x] Verification plan updated (Step 5).
- [x] Log anchors from design §5 included (Steps 1–3).
- [x] ADR decisions implemented (Step 6.2 records them; the code already follows them).
- [x] Task order respects DEPENDS: route.py → pipeline → __main__ → integration → graph → finish.
- [x] No placeholders — concrete files, tests, commands per step.
- [ ] One micro-decision deferred to execution: rename Phase-A's `tg.pipeline.router.delivered` → `tg.pipeline.router.decided` for clarity vs the new `tg.pipeline.route.delivered` (small ripple in `test_pipeline_router.py` + `verification-plan.xml`) — default = rename.
