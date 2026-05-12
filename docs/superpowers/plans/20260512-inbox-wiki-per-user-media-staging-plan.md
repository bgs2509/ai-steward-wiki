# Inbox-WIKI Phase-E.a: per-user media `_staging` — Implementation Plan

> **For agentic workers:** RED → GREEN → REFACTOR per task; never write production code before a failing test. Steps use `- [ ]`.

**Goal:** Stage voice/photo bytes under `<wiki_root>/<telegram_id>/Inbox-WIKI/raw/media/_staging/` instead of the shared `settings.media_staging_root`; make the 24h sweep iterate every user's Inbox-WIKI; remove `media_staging_root`. `promote_path_to_raw` unchanged. (Subsumes `aisw-64c`. Bit 1 — `## Inbox hint` router-bypass — split to `aisw-5sd` / Phase-E.b.)

**bd:** aisw-12t (epic aisw-t2r; subsumes aisw-64c; blocks aisw-5sd). Spec: `docs/superpowers/specs/20260512-inbox-wiki-hint-fastpath-staging-{discovery,design}.md` (T-1..T-10).

**Conventions:** type hints + `mypy --strict` for `src/`; structlog with `telegram_id` where in TG context; ru-only user strings (none new here); all DB/sweep datetimes UTC; `# noqa: RUF001` on Cyrillic-homoglyph literals if flagged. After bulk edits run the FULL gate (`make lint` + `make total-test` semantics), not just the check you targeted.

---

## Task 1: `inbox_wiki_path` helper in `inbox/materialize.py`

**Files:** Modify `src/ai_steward_wiki/inbox/materialize.py` (add pure helper; `__all__`, MODULE_MAP, SCOPE, CHANGE_SUMMARY; bump VERSION). Test: `tests/unit/inbox/test_materialize.py`.

- [ ] RED: `test_inbox_wiki_path_arithmetic` — `inbox_wiki_path(123, wiki_root=Path("/w"))` == `Path("/w/123/Inbox-WIKI")`; no IO (works on a non-existent root).
- [ ] GREEN: `def inbox_wiki_path(telegram_id: int, *, wiki_root: Path) -> Path: return wiki_root / str(telegram_id) / INBOX_WIKI_DIRNAME` with a `START_CONTRACT: inbox_wiki_path` block (PURPOSE/INPUTS/OUTPUTS/SIDE_EFFECTS=none/LINKS D-004,D-016,D-022). Add to `__all__` + MODULE_MAP; SCOPE += helper; CHANGE_SUMMARY += `v0.1.0 - aisw-12t: + inbox_wiki_path (per-user media staging root)`.
- [ ] Verify: `uv run pytest tests/unit/inbox/test_materialize.py`.

## Task 2: `sweep_all_user_staging` in `inbox/staging.py`

**Files:** Modify `src/ai_steward_wiki/inbox/staging.py` (add wrapper; `__all__`, MODULE_MAP, SCOPE, CHANGE_SUMMARY, VERSION; import `INBOX_WIKI_DIRNAME` from `materialize` — watch for an import cycle: `materialize` does not import `staging`, so `staging` importing `INBOX_WIKI_DIRNAME` from `materialize` is fine; if a cycle appears, inline the `"Inbox-WIKI"` literal with a comment). Test: `tests/unit/inbox/test_staging.py`.

- [ ] RED: `test_sweep_all_user_staging_mixed_tree` — build `wiki_root/` with: `111/Inbox-WIKI/raw/media/_staging/` containing one fresh + one backdated (`os.utime` -48h) staged file; `222/Inbox-WIKI/...` one backdated file; `_trash/` dir; `333/` (user dir, no `Inbox-WIKI`); a stray file `wiki_root/note.txt`. Assert `sweep_all_user_staging(wiki_root, ttl_s=24*3600)` == 2, the two backdated files are gone, the fresh one stays, nothing else touched. Also `test_sweep_all_user_staging_empty_root` → 0 on a non-existent / empty root.
- [ ] GREEN: implement per T-4 — iterate `wiki_root.iterdir()` (guard `wiki_root.exists()`), skip non-dirs and dirs whose `<child>/Inbox-WIKI` is absent, call `sweep_staging(child / INBOX_WIKI_DIRNAME, now=now, ttl_s=ttl_s)`, sum. `START_CONTRACT: sweep_all_user_staging` block. Emit `media.staging_sweep_all_done(wiki_root, removed, scanned_users)` when `removed` or always (match `sweep_staging`'s style — log when removed>0, plus a debug always is fine). Add `__all__` += `sweep_all_user_staging`, MODULE_MAP line, SCOPE update, CHANGE_SUMMARY `v0.0.3 - aisw-12t: + sweep_all_user_staging (per-user Inbox-WIKI sweep wrapper)`, VERSION 0.0.2→0.0.3.
- [ ] Verify: `uv run pytest tests/unit/inbox/test_staging.py`.

## Task 3: `VoiceHandler` / `PhotoIngestor` per-call `inbox_root`

**Files:** Modify `src/ai_steward_wiki/tg/voice.py`, `src/ai_steward_wiki/tg/photo.py` (VERSION + CHANGE_SUMMARY bump; `handle` CONTRACT updated). Tests: `tests/unit/tg/test_voice.py`, `tests/unit/tg/test_photo.py`.

- [ ] RED (voice): `test_voice_handler_handle_uses_per_call_inbox_root` — `VoiceHandler(t)` (no constructor root) + `await handler.handle(payload, run_id="r1", inbox_root=tmp/"A"/"Inbox-WIKI")` → `ref.staging_path` under `tmp/A/Inbox-WIKI/raw/media/_staging/`. `test_voice_handler_no_root_anywhere_raises` — `VoiceHandler(t).handle(payload, run_id="r1")` (neither) → `ValueError`. Keep the existing `test_voice_handler_stages_and_transcribes` working by giving it `inbox_root=` on `.handle(...)` (or constructing with the root — pick one; design says `__init__` keeps the optional default, so the existing test can stay as-is if it passes `inbox_root` to `__init__`).
- [ ] RED (photo): analogous — `PhotoIngestor().handle(b"...", run_id="r", mime="image/jpeg", inbox_root=tmp/"A"/"Inbox-WIKI")` lands under the override; `PhotoIngestor().handle(..., )` with no root anywhere → `ValueError`. Update the existing 3 tests to pass `inbox_root` (constructor or call).
- [ ] GREEN: `VoiceHandler.__init__(self, transcriber, *, inbox_root: Path | None = None)`; `handle(self, audio_bytes, *, run_id, hint_lang=None, mime="audio/ogg", ext="ogg", inbox_root: Path | None = None)` → `root = inbox_root if inbox_root is not None else self._inbox_root` → `if root is None: raise ValueError("VoiceHandler.handle: inbox_root required (no constructor default)")` → `stage_media(..., inbox_root=root, ...)`. Update the `handle` `START_CONTRACT` (INPUTS += `inbox_root: Path | None`). Same for `PhotoIngestor`. CHANGE_SUMMARY: `v0.0.3 - aisw-12t (Phase-E.a): per-call inbox_root override (per-user Inbox-WIKI media staging, D-022); __init__ root now optional default`.
- [ ] Verify: `uv run pytest tests/unit/tg/test_voice.py tests/unit/tg/test_photo.py`.

## Task 4: `scheduler/maintenance.py` — per-user sweep job

**Files:** Modify `src/ai_steward_wiki/scheduler/maintenance.py` (VERSION + MODULE_CONTRACT/MAP + CHANGE_SUMMARY; rename param + callable; swap DEPENDS line). Test: `tests/unit/scheduler/test_maintenance.py`.

- [ ] RED: rewrite `test_register_media_sweep_job_*` to call `register_media_staging_sweep_job(sched, wiki_root=tmp_path)` (was `staging_root=`); add `test_run_all_user_media_sweep_across_users` — two user dirs each with a backdated staged file under `Inbox-WIKI/raw/media/_staging/`, plus a fresh one → `await _run_all_user_media_sweep(tmp_path, 24*3600)` removes 2, leaves fresh. (Use `stage_media(..., inbox_root=tmp_path/"<tid>"/"Inbox-WIKI")` to set up.)
- [ ] GREEN: `register_media_staging_sweep_job(scheduler, *, wiki_root: Path, ttl_s=DEFAULT_STAGING_TTL_S, hour=4, minute=30)` → `add_job(_run_all_user_media_sweep, CronTrigger(...), id=MEDIA_STAGING_SWEEP_JOB_ID, replace_existing=True, args=[wiki_root, ttl_s])`. New `async def _run_all_user_media_sweep(wiki_root: Path, ttl_s: int) -> int:` → `removed = await asyncio.to_thread(sweep_all_user_staging, wiki_root, ttl_s=ttl_s)` → log `maintenance.media_sweep.done(wiki_root=str(wiki_root), removed=removed)` → return. Delete `_run_media_sweep`. `register_all_retention_jobs`: rename kwarg `media_staging_root: Path | None` → `wiki_root_for_media_sweep: Path | None`; the `if … is not None:` branch calls `register_media_staging_sweep_job(scheduler, wiki_root=wiki_root_for_media_sweep)`. Update import: `from ai_steward_wiki.inbox.staging import DEFAULT_STAGING_TTL_S, sweep_all_user_staging` (drop `sweep_staging`). MODULE_CONTRACT DEPENDS line, MODULE_MAP (`register_media_staging_sweep_job - … per-user Inbox-WIKI sweep`), SCOPE, CHANGE_SUMMARY `v0.0.4 - aisw-12t (Phase-E.a): media sweep iterates per-user Inbox-WIKI/raw/media/_staging (was a single shared dir)`, VERSION 0.0.3→0.0.4.
- [ ] Verify: `uv run pytest tests/unit/scheduler/test_maintenance.py`.

## Task 5: `tg/pipeline.py` — wire `wiki_root` + per-message `inbox_root`

**Files:** Modify `src/ai_steward_wiki/tg/pipeline.py` (MODULE_CONTRACT DEPENDS += `inbox.materialize.inbox_wiki_path`; CHANGE_SUMMARY; VERSION bump). Tests: the relevant `tests/unit/tg/test_pipeline*.py`.

- [ ] RED: in whichever `test_pipeline*.py` already build a `DefaultPipeline` with `voice=`/`photo=`, add an assertion that a staged file from `on_voice`/`on_photo` lands under `<wiki_root>/<tid>/Inbox-WIKI/raw/media/_staging/` when `DefaultPipeline(..., wiki_root=tmp_path)`; and that with `wiki_root=None` (the fallback) it lands under the handler's constructor root (back-compat with existing tests). Search: `grep -rln "voice=\|photo=" tests/unit/tg/test_pipeline*.py`.
- [ ] GREEN: `DefaultPipeline.__init__(..., wiki_root: Path | None = None)` → `self._wiki_root = wiki_root`. Add `def _inbox_root_for(self, telegram_id: int) -> Path | None: return inbox_wiki_path(telegram_id, wiki_root=self._wiki_root) if self._wiki_root is not None else None`. In `on_voice`: `ref, transcript = await self._voice.handle(audio_bytes, run_id=run_id, ext=ext, mime=mime, inbox_root=self._inbox_root_for(telegram_id))`. In `on_photo` and the image-document branch (`self._photo.handle(...)`): pass `inbox_root=self._inbox_root_for(telegram_id)`. Import `inbox_wiki_path` from `ai_steward_wiki.inbox.materialize`; add to MODULE_CONTRACT DEPENDS; CHANGE_SUMMARY `v0.x.0 - aisw-12t (Phase-E.a): media staging root is now per-user (<wiki_root>/<telegram_id>/Inbox-WIKI/...) via inbox_wiki_path; DefaultPipeline gains optional wiki_root`; bump VERSION.
- [ ] Verify: `uv run pytest tests/unit/tg/`.

## Task 6: `settings.py` — drop `media_staging_root`

**Files:** Modify `src/ai_steward_wiki/settings.py` (remove field + 3-line comment; CHANGE_SUMMARY note; VERSION bump if the file bumps per-change). Test: `tests/unit/test_settings*.py` if any reference it — `grep -rn media_staging_root tests/`.

- [ ] RED/GREEN: delete the `media_staging_root: Path = Path("/var/lib/ai-steward-wiki/workspace/media-staging")` line and its preceding comment; keep `voice_*` / `photo_*`. CHANGE_SUMMARY: `vX - aisw-12t (Phase-E.a): - media_staging_root (media staging is now per-user Inbox-WIKI; see inbox_wiki_path)`. Remove any test asserting its default.
- [ ] Verify: `uv run python -c "from ai_steward_wiki.settings import Settings"` + `uv run pytest tests/unit -k settings`.

## Task 7: `__main__.py` — wire it together

**Files:** Modify `src/ai_steward_wiki/__main__.py` (CHANGE_SUMMARY; VERSION bump). Test: covered by existing `__main__` smoke tests (`grep -rln "amain\|__main__" tests/`) — adjust if they assert the old wiring.

- [ ] GREEN: in `START_BLOCK_MEDIA_PIPELINE_WIRING` — `VoiceHandler(FasterWhisperTranscriber(model_size=settings.voice_whisper_model_size))` (no `inbox_root=`); `PhotoIngestor()` (no `inbox_root=`). `runtime.media_pipeline.wired` log: drop nothing essential (whisper_model stays). In `register_all_retention_jobs(...)`: replace `media_staging_root=settings.media_staging_root` with `wiki_root_for_media_sweep=settings.wiki_root`. `runtime.scheduler.started` log: replace `media_staging_root=str(settings.media_staging_root)` with `wiki_root=str(settings.wiki_root)`. In `DefaultPipeline(...)`: add `wiki_root=settings.wiki_root`. Update line ~510 comment ("binary stays in media_staging_root") → "binary stays in the sender's Inbox-WIKI/raw/media/_staging until promotion". CHANGE_SUMMARY `vX - aisw-12t (Phase-E.a): media staging is per-user Inbox-WIKI; VoiceHandler/PhotoIngestor built without a fixed inbox_root; DefaultPipeline(wiki_root=…); media sweep over wiki_root`; bump VERSION.
- [ ] Verify: `uv run python -c "import ai_steward_wiki.__main__"` + run the `__main__` test if present.

## Task 8: Full gate + grace sync + Finish

- [ ] `make lint` (ruff check + ruff format --check + mypy src + grace lint) → 0.
- [ ] `make total-test` semantics: `uv run pytest tests/unit` (coverage ≥80%), `uv run inv lint` (if that's the inv-lint target — check `make total-test`), grace lint. Integration suite is gated (`RUN_INTEGRATION` / skipped under `CLAUDECODE=1`) — note in the report.
- [ ] `grace-refresh` (full) — sync `knowledge-graph.xml` (new exports: `inbox_wiki_path`, `sweep_all_user_staging`; changed `maintenance` signature) ; `grace-refresh --verify` — sync `verification-plan.xml` (new marker `media.staging_sweep_all_done`; removed `media_staging_root` refs). Clears the Phase-D report deferred #6 media-marker note.
- [ ] `_report` (minor — one short completion report `docs/reports/20260512-inbox-wiki-per-user-media-staging-report.md`): scope, decisions T-1..T-10, test results, deferred (bit 1 → aisw-5sd; `ops/retention.purge_staging` dead code).
- [ ] `smart-commit` — group commits: `feat(M-INBOX): inbox_wiki_path + sweep_all_user_staging (per-user media staging, aisw-12t)`, `refactor(M-TG-MEDIA): per-call inbox_root for VoiceHandler/PhotoIngestor (aisw-12t)`, `feat(M-SCHEDULER): media sweep iterates per-user Inbox-WIKI (aisw-12t)`, `refactor(M-TG-PIPELINE): wire per-user media staging root (aisw-12t)`, `chore(settings): drop media_staging_root (aisw-12t)`, `chore(runtime): wire per-user media staging (aisw-12t)`, `chore(knowledge-graph): refresh for aisw-12t`, `docs(report): aisw-12t completion report`. (Or fewer logical commits if cleaner.)
- [ ] `bd close aisw-12t --reason="Phase-E.a per-user media _staging complete; bit 1 → aisw-5sd"` (this also resolves the aisw-64c subsumption). `bd dolt push`.

## Self-review checklist

- [ ] Every T-1..T-10 → has task(s). ✓ (T-1→T1, T-2/T-3→T3/T5, T-4→T2, T-5→T4, T-6→T6, T-7→T7, T-8→all, T-9→tests in each, T-10→T8)
- [ ] Every FR-1..FR-6 (E.a subset) → covered. FR-1→T1; FR-2→T3+T5; FR-3→T2+T4; FR-4→T6; FR-5→tests; FR-6→T2/T4 logs.
- [ ] No new prompt/migration/dependency/user-string. ✓
- [ ] `promote_path_to_raw` untouched. ✓ (not in any task's modify list except as "unchanged")
- [ ] Import-cycle check for `staging` ← `materialize.INBOX_WIKI_DIRNAME`. ✓ (Task 2 note)
- [ ] grace-lint stays green (refresh in T8). ✓
