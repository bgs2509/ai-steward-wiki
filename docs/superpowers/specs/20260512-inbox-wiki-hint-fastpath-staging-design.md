---
feature: inbox-wiki-per-user-media-staging
bd_id: aisw-12t
epic: aisw-t2r
phase: "Inbox-WIKI Phase-E.a"
status: draft
created: 2026-05-12
discovery: docs/superpowers/specs/20260512-inbox-wiki-hint-fastpath-staging-discovery.md
supersedes_in_scope: "bit 1 (## Inbox hint router-bypass) → split out to Phase-E.b (own feature-workflow iteration); this iteration = bit 2 only (per-user media _staging, subsumes aisw-64c)"
technology:
  decisions:
    - id: T-1
      text: "New pure helper inbox_wiki_path(telegram_id: int, *, wiki_root: Path) -> Path in inbox/materialize.py returns wiki_root / str(telegram_id) / INBOX_WIKI_DIRNAME. No IO — path arithmetic only; the dir tree is created lazily by stage_media's mkdir(parents=True). ensure_inbox_wiki (CLAUDE.md first-contact materialiser) is unchanged and still owned by the _RouterAdapter. (OQ-A/R-2.)"
    - id: T-2
      text: "Per-user staging root is injected into DefaultPipeline as wiki_root: Path (one new constructor kwarg, optional; None ⇒ media handlers fall back to the legacy fixed inbox_root they were constructed with — keeps existing pipeline unit tests that build handlers with a tmp inbox_root green without a wiki_root). The pipeline computes inbox_root = inbox_wiki_path(telegram_id, wiki_root=self._wiki_root) per message and passes it to the handler. (OQ-A: shape (b).)"
    - id: T-3
      text: "VoiceHandler / PhotoIngestor: inbox_root moves from a required __init__ kwarg to an optional .handle(..., inbox_root: Path | None = None) override. __init__ keeps inbox_root as the default (so a handler can still be constructed standalone with a fixed root — used by integration tests and as the pipeline-fallback path). At call time: effective_root = inbox_root if inbox_root is not None else self._inbox_root. Both must not be None ⇒ a handler with no constructor root requires a per-call root (assert / ValueError). (OQ-A.)"
    - id: T-4
      text: "New wrapper sweep_all_user_staging(wiki_root: Path, *, now: datetime | None = None, ttl_s: int = DEFAULT_STAGING_TTL_S) -> int in inbox/staging.py: for each child of wiki_root that is a directory AND has an <child>/Inbox-WIKI/ subdir, call sweep_staging(child / INBOX_WIKI_DIRNAME, now=now, ttl_s=ttl_s) and sum the removed counts. Skips _trash/, stray files, and dirs without Inbox-WIKI. sweep_staging itself (keyed on one inbox_root) is unchanged. Emits media.staging_sweep_all_done(wiki_root, removed, scanned_users)."
    - id: T-5
      text: "scheduler/maintenance.py: register_media_staging_sweep_job's staging_root: Path kwarg is renamed to wiki_root: Path; its job callable becomes _run_all_user_media_sweep(wiki_root, ttl_s) wrapping asyncio.to_thread(sweep_all_user_staging, ...). MEDIA_STAGING_SWEEP_JOB_ID, the 04:30 UTC cron, and idempotency (replace_existing=True) are unchanged. register_all_retention_jobs's media_staging_root: Path | None kwarg is renamed wiki_root_for_media_sweep: Path | None and threaded through. (The old _run_media_sweep over a single dir is removed — no internal backwards-compat shim.)"
    - id: T-6
      text: "settings.media_staging_root is REMOVED (it was an explicit MVP placeholder per DEC-C1-2; nothing has shipped to prod that reads it). settings.py drops the field + its 3-line comment; the settings.py CHANGE_SUMMARY notes the removal. (OQ-B: remove, confirmed at Discovery gate.)"
    - id: T-7
      text: "src/ai_steward_wiki/__main__.py: VoiceHandler/PhotoIngestor are constructed WITHOUT inbox_root (or with None); DefaultPipeline gains wiki_root=settings.wiki_root; register_all_retention_jobs is called with wiki_root_for_media_sweep=settings.wiki_root; the runtime.scheduler.started / runtime.media_pipeline.wired log fields that referenced media_staging_root now reference wiki_root. _WikiRunnerAdapter.run's promote_path_to_raw(media_path, wiki_root=<target wiki>) is unchanged — only the *origin* of the staged file changed (DEC-C4-1)."
    - id: T-8
      text: "No new third-party dependency, no new SQLite table, no new Alembic migration, no new user-facing string, no prompt change. ops/retention.purge_staging (a separate, currently-unwired single-dir helper) is left untouched — out of scope; flag for a later cleanup task if it stays dead."
    - id: T-9
      text: "Tests: tests/unit/inbox/test_materialize.py +inbox_wiki_path; tests/unit/inbox/test_staging.py +sweep_all_user_staging over a mixed tree (a numeric user dir with stale+fresh staged files, a _trash dir, a stray file, a user dir with no Inbox-WIKI); tests/unit/tg/test_voice.py + test_photo.py updated for the .handle(inbox_root=...) override + the no-root-anywhere error; tests/unit/scheduler/test_maintenance.py updated for register_media_staging_sweep_job(wiki_root=...) + a _run_all_user_media_sweep test; tests/unit/tg/test_pipeline*.py — wherever a DefaultPipeline is built with voice/photo, pass wiki_root=tmp_path (or rely on the None-fallback). Coverage stays ≥80%."
    - id: T-10
      text: "grace-refresh + grace-refresh --verify after the change: knowledge-graph.xml (moved/renamed exports: sweep_all_user_staging, inbox_wiki_path, the maintenance signature) + verification-plan.xml (new log markers media.staging_sweep_all_done; removed media_staging_root references). Also clears the Phase-D report's deferred #6 for the media markers."
---

# Phase-E.a — Inbox-WIKI per-user media `_staging` — Design

> bd **aisw-12t** · epic **aisw-t2r** · subsumes **aisw-64c** · depends-on: aisw-dsg, aisw-64c (both done)
> Discovery: `docs/superpowers/specs/20260512-inbox-wiki-hint-fastpath-staging-discovery.md`. **Scope cut**: bit 1 (`## Inbox hint` router-bypass) is split to **Phase-E.b** (separate iteration) — too large + a real design fork on the matching mechanism; this iteration ships bit 2 only.

## 1. Goal

Move media staging from one shared dir (`settings.media_staging_root`) to per-sender `<wiki_root>/<telegram_id>/Inbox-WIKI/raw/media/_staging/`, and make the 24h `_staging` sweep iterate every user's Inbox-WIKI. Closes the `aisw-64c` deferral and the D-022 "per-WIKI staging when Inbox-WIKI lands" note. `promote_path_to_raw` (staged → target WIKI on success) is untouched — only where the staged file is *born* changes.

## 2. Flow (after)

```
on_voice / on_photo / image-document  (tg/pipeline.py, has telegram_id)
   └─ inbox_root = inbox_wiki_path(telegram_id, wiki_root=self._wiki_root)
        └─ VoiceHandler.handle(audio_bytes, run_id=…, inbox_root=inbox_root, …)
        └─ PhotoIngestor.handle(photo_bytes, run_id=…, inbox_root=inbox_root, …)
              └─ stage_media(bytes, …, inbox_root=inbox_root)   # mkdir(parents=True) → <wiki_root>/<tid>/Inbox-WIKI/raw/media/_staging/<run_id>_<sha8>.<ext>
   └─ _run_text_pipeline(…, media_paths=[ref.staging_path])
        └─ routable → _RouterAdapter (already calls ensure_inbox_wiki; --add-dir staging dir is now inside Inbox-WIKI, redundant-but-harmless)
        └─ non-routable → _WikiRunnerAdapter.run → on success: promote_path_to_raw(staged, wiki_root=<wiki_root>/<owner_tid>)   # UNCHANGED
   └─ failed run / no-WIKI intent / rejected confirm → file left in _staging

daily 04:30 UTC cron  →  _run_all_user_media_sweep(wiki_root)
   └─ sweep_all_user_staging(wiki_root): for <wiki_root>/<X>/Inbox-WIKI/ → sweep_staging(…) ; sum removed
```

## 3. Module deltas

| Module | Change | Contract impact |
|---|---|---|
| `inbox/materialize.py` | `+ inbox_wiki_path(telegram_id, *, wiki_root) -> Path` (pure) | new `START_CONTRACT: inbox_wiki_path`; MODULE_MAP += line; SCOPE += helper |
| `inbox/staging.py` | `+ sweep_all_user_staging(wiki_root, *, now, ttl_s) -> int` | new `START_CONTRACT`; MODULE_MAP += line; SCOPE += wrapper; `__all__` += name |
| `tg/voice.py` | `VoiceHandler.handle(..., inbox_root: Path | None = None)`; `__init__` `inbox_root` optional default | CONTRACT for `handle` updated (new INPUT); `__init__` doc |
| `tg/photo.py` | `PhotoIngestor.handle(..., inbox_root: Path | None = None)`; `__init__` `inbox_root` optional default | CONTRACT for `handle` updated; `__init__` doc |
| `tg/pipeline.py` | `DefaultPipeline(..., wiki_root: Path | None = None)`; `on_voice`/`on_photo`/image-doc compute & pass `inbox_root`; helper `self._inbox_root_for(tid)` returning `inbox_wiki_path(tid, wiki_root=self._wiki_root)` or `None` | MODULE_CONTRACT DEPENDS += `inbox.materialize.inbox_wiki_path`; CHANGE_SUMMARY bump; no public-API contract change beyond the new optional kwarg |
| `scheduler/maintenance.py` | `register_media_staging_sweep_job(staging_root→wiki_root)`; `_run_media_sweep`→`_run_all_user_media_sweep`; `register_all_retention_jobs(media_staging_root→wiki_root_for_media_sweep)` | MODULE_MAP + CONTRACT updates; DEPENDS swaps `inbox.staging.sweep_staging`→`…sweep_all_user_staging` |
| `settings.py` | `- media_staging_root` field + comment; CHANGE_SUMMARY note | n/a |
| `__main__.py` | handlers built w/o `inbox_root`; `DefaultPipeline(wiki_root=…)`; `register_all_retention_jobs(wiki_root_for_media_sweep=…)`; log fields swap | CHANGE_SUMMARY bump |

## 4. Tests (TDD order)

1. `test_materialize.py::test_inbox_wiki_path_arithmetic` — RED first.
2. `test_staging.py::test_sweep_all_user_staging_*` — mixed tree (numeric user w/ stale+fresh, `_trash` dir, stray file, user w/o `Inbox-WIKI`) → removes only the stale files, returns the right count.
3. `test_voice.py` / `test_photo.py` — `.handle(inbox_root=tmp/"A"/"Inbox-WIKI")` stages under the override; `.handle()` with neither constructor nor call root → `ValueError`/assert.
4. `test_maintenance.py` — `register_media_staging_sweep_job(sched, wiki_root=tmp)` → job id/cron/idempotency; `_run_all_user_media_sweep(tmp, 24h)` over two user dirs → removes the stale ones across both.
5. `test_pipeline*.py` — adjust `DefaultPipeline(...)` builders that wire voice/photo to pass `wiki_root=tmp_path`; assert a staged file from `on_voice`/`on_photo` lands under `tmp_path/<tid>/Inbox-WIKI/raw/media/_staging/`.
6. Full gate: `make lint` + `make total-test` semantics (ruff/format/mypy/grace-lint/inv-lint/coverage ≥80%); integration suite gated (skipped under `CLAUDECODE=1`).

## 5. Non-goals (this iteration)

bit 1 hint fast-path (→ Phase-E.b); per-call vision timeout (DEC-C2-4); captions (DEC-C3-2); `register_all_retention_jobs` callsite audit (DEC-C4-2); `video_note` ext (cosmetic); `ops/retention.purge_staging` cleanup (dead code, separate task).
