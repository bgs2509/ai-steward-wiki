# step-04 plan — chunk 4 M-INBOX (bd_id: aisw-8r9)

> Executed 2026-05-12. SSoT for chunk-4 execution.

## Decision (auto-applied, DEC-C4-1)
Promotion (staging → `<wiki>/raw/media/`) is done **adapter-side** in `_WikiRunnerAdapter.run`
after a successful `run_wiki_session`: the adapter knows `wiki_path` (= `wiki_root/<owner_telegram_id>`
in the current MVP) and receives `media_paths`. New `promote_path_to_raw(staging_path, *, wiki_root, now)`
in `staging.py` re-hashes the file and delegates to `promote_to_raw`. Pipeline untouched — clean
separation (pipeline produces staged media, adapter owns wiki_path). Failed runs leave the file in
`_staging` for the 24h sweep job (matches D-022 "No-WIKI flow").

## Tasks (TDD)

1. **GREEN** — `src/ai_steward_wiki/inbox/staging.py`: add `promote_path_to_raw(staging_path, *, wiki_root, now=None)` (re-hash → MediaRef → `promote_to_raw`); `__all__`, MAP, SCOPE updated. Header v0.0.2.
2. **GREEN** — `src/ai_steward_wiki/scheduler/maintenance.py`: `MEDIA_STAGING_SWEEP_JOB_ID`; `_run_media_sweep(staging_root, ttl_s)` (sync `sweep_staging` via `asyncio.to_thread`, logs `maintenance.media_sweep.done removed=N`); `register_media_staging_sweep_job(scheduler, *, staging_root, ttl_s=DEFAULT_STAGING_TTL_S, hour=4, minute=30)` (daily cron, idempotent); included in `register_all_retention_jobs(media_staging_root=...)`. Header v0.0.3.
3. **GREEN** — `src/ai_steward_wiki/__main__.py`: after `scheduler.start()` → `register_media_staging_sweep_job(scheduler, staging_root=settings.media_staging_root)`; `_WikiRunnerAdapter.run` promotes each `media_paths` entry via `promote_path_to_raw` after a successful run (logs `runtime.media.promoted` / `.promote_missing` / `.promote_failed`). Header v0.1.3; DEPENDS += scheduler.maintenance, inbox.staging; LINKS += M-INBOX.
4. **GREEN** — tests: `test_staging.py` — `promote_path_to_raw` moves to `<wiki>/raw/media/<ISO8601>_<sha8>.ext`; missing file → `FileNotFoundError`. `test_maintenance.py` — `register_media_staging_sweep_job` adds daily 04:30 UTC job (idempotent); `_run_media_sweep` invokes `sweep_staging` (old file removed, fresh kept).
5. **VERIFY** — `make total-test` exit 0 (432 tests, coverage 90.54%, ruff/mypy/grace/inv-lint clean).

## Acceptance
- `grep "promote_path_to_raw" src/ai_steward_wiki/__main__.py` non-empty (called in adapter).
- `MEDIA_STAGING_SWEEP_JOB_ID` job registered at runtime.
- Manual (operator): after a voice/photo run, the staged file appears in `<wiki>/raw/media/<ISO8601>_<sha8>.ext` and `_staging` is empty; a >24h-old `_staging` file is removed by the background job. (Pending operator run.)

## Notes / follow-ups
- `register_all_retention_jobs` is still not called from `__main__.py` (pre-existing gap, chunk 12-14 maintenance jobs not wired) — out of scope here; the media sweep is wired directly. Worth a separate task.
- Per-call vision timeout 30s (D-022 / DEC-C2-4) still deferred.
