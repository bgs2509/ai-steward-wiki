# Completion report — Full media handling (voice / photo / document / audio / video_note)

**Epic:** `aisw-hcl` · slug `20260512-media-handling-full` · branch `feat/media-handling-full`
**Date:** 2026-05-12 · **Driver:** `/superautocoder` (5 chunks, all USER APPROVAL gates auto-approved per durable user directive)
**Source plan:** `docs/superpowers/plans/20260512-media-handling-full-plan.md` · **Breakdown:** `docs/superpowers/plans/20260512-media-handling-full/breakdown.xml`

## Why

A voice message to the bot produced only `ACK_TEXT_RU` ("Принято.") + a `tg.pipeline.voice.no_handler` warning: `DefaultPipeline` was constructed in `__main__.py` without `voice=`/`photo=`, and `faster-whisper` was an optional extra not installed by `uv sync`. Photos were merely staged and acked — never sent to Claude vision. `audio`/`video_note` content types and message captions were unhandled. `promote_to_raw`/`sweep_staging` existed but were never called. This epic closes all of it (D-022).

## What changed (by chunk)

| # | bd_id | module | commit | summary |
|---|-------|--------|--------|---------|
| 1 | aisw-zny | M-RUNTIME-WIRING | `06b1e92` | `DefaultPipeline` gets `VoiceHandler` + `PhotoIngestor`; `Settings` media fields (`media_staging_root`, `voice_enabled`, `voice_whisper_model_size`, `voice_stt_timeout_s`, `photo_enabled`, `photo_vision_timeout_s`); `faster-whisper` moved to core deps (ADR-002); `VoiceUnavailableError` → `ACK_VOICE_UNAVAILABLE_RU` + `tg.pipeline.voice.stt_unavailable`; `runtime.media_pipeline.wired` log. |
| 2 | aisw-m2m | M-WIKI-RUNNER | `bedf711` | `run_wiki_session(media_paths=...)` → media dirs appended to `--add-dir` (claude CLI 2.1.139 has no `--image`; Read tool on a granted dir is the only mechanism); `WikiRunner.run(media_paths=...)`; `_run_text_pipeline(media_paths=, skip_l2_dedup=, source+'photo')`; `PHOTO_PROMPT_RU`; `on_photo` stages → L2-dedups image bytes → runs the wiki pipeline with `media_paths`; image-document branch routed the same way. |
| 3 | aisw-ahv | M-TG-HANDLERS-WIRING | `1f7b357` | `@router.message(F.audio)` and `@router.message(F.video_note)` route to `pipeline.on_voice` (faster-whisper/pyav demuxes the audio track); `_on_photo` forwards `message.caption`; `PHOTO_CAPTION_PROMPT_RU`; `tg.pipeline.photo` log gains `has_caption`. |
| 4 | aisw-8r9 | M-INBOX | `66004c1` | `promote_path_to_raw(staging_path, *, wiki_root, now)` (re-hash → delegate to `promote_to_raw`); `register_media_staging_sweep_job` (daily 04:30 UTC cron → `sweep_staging`, logs `maintenance.media_sweep.done`); wired into `register_all_retention_jobs` and called directly from `__main__.py`; `_WikiRunnerAdapter.run` promotes each `media_paths` entry into `<wiki>/raw/media/` after a successful run (`runtime.media.promoted` / `.promote_missing` / `.promote_failed`). |
| 5 | aisw-nzl | M-INTEGRATION-E2E | (this commit) | integration scenarios for photo→vision (`media_paths` forwarded) and photo+caption; `test_photo_then_confirm_callback` updated for the new photo path; this report; epic closed. |

## Decisions (audit trail in breakdown.xml)

- **DEC-C1-1 / ADR-002** — `faster-whisper` is a core dependency, not an optional extra. Voice is a primary MVP feature (D-022); image size is a deploy concern, not correctness; no voice-less deployment profile. Defensive `ImportError → VoiceUnavailableError → ACK_VOICE_UNAVAILABLE_RU` kept as defence-in-depth.
- **DEC-C1-2** — single `media_staging_root` (default `/var/lib/ai-steward-wiki/workspace/media-staging`), not per-user; per-user Inbox-WIKI staging deferred (not in `lifecycle.py`).
- **DEC-C2-1** — image → Claude via `--add-dir <staged-file dir>` + path in `user_input` (Read tool); only viable mechanism, not a choice → no ADR.
- **DEC-C2-2** — `on_photo` L2-dedups raw image bytes (`kind="file"`), then `_run_text_pipeline(skip_l2_dedup=True)` — the synthetic photo prompt is constant, text-level dedup would false-positive.
- **DEC-C3-1** — `F.audio` and `F.video_note` route to `on_voice`; faster-whisper/pyav demuxes mp3/ogg/m4a and the mp4 audio track (pyav bundles libav — no `ffmpeg` precondition).
- **DEC-C4-1** — staging→raw promotion is adapter-side (`_WikiRunnerAdapter.run` knows `wiki_path`); failed runs leave the file in `_staging` for the 24h sweep (D-022 "No-WIKI flow").

## Deferred / follow-ups

1. **Per-call vision timeout 30s** (D-022) — adapter uses one `_RunConfig.timeout_s` (`wiki_runner_timeout_s`, 300s). Needs an optional timeout override in `WikiRunner.run`. (DEC-C2-4)
2. **Document captions + voice/audio captions** — chunk 3 forwarded captions only for photo (D-022's caption requirement is photo-focused). (DEC-C3-2)
3. **`register_all_retention_jobs` is still not called from `__main__.py`** — pre-existing gap (chunk 12-14 maintenance jobs unwired); the media sweep is wired directly. Worth a separate task. (DEC-C4-2)
4. **Per-user Inbox-WIKI staging path** — when `Inbox-WIKI` lands in `lifecycle.py`.
5. **`video_note` staged with the wrong ext** (`.ogg` for mp4) — cosmetic; transcription sniffs the container. Fix when `on_voice` gains an `ext`/`mime` hint.
6. **`grace-refresh`** — `grace lint` is green (governed=66 xml=3 errors=0 warnings=0), but `knowledge-graph.xml` / `verification-plan.xml` may want a full refresh to register the new log markers (`runtime.media_pipeline.wired`, `tg.pipeline.voice.stt_unavailable`, `tg.pipeline.photo.dedup_hit`, `maintenance.media_sweep.done`, `runtime.media.promoted`).

## Verification

`make total-test` — **PASS** after every chunk:

| chunk | tests | coverage | ruff | mypy | grace lint | inv-lint |
|---|---|---|---|---|---|---|
| 1 | 419 | 90.65% | 0 | 0 | 0/0 | 14/14 |
| 2 | 424 | 90.70% | 0 | 0 | 0/0 | 14/14 |
| 3 | 428 | 90.74% | 0 | 0 | 0/0 | 14/14 |
| 4 | 432 | 90.54% | 0 | 0 | 0/0 | 14/14 |
| 5 | 432 | 90.54% | 0 | 0 | 0/0 | 14/14 |

Integration suite (`tests/integration/test_e2e_pipeline.py`, 6 scenarios) is gated by `RUN_INTEGRATION=1` + `claude` on PATH + not `CLAUDECODE=1`; not run here (recursive Claude Code session). Manual operator acceptance pending: `uv run python -m ai_steward_wiki` → send voice / photo / audio / video_note / PDF / .txt and confirm the new behaviour + log markers.

## Files touched

`src/ai_steward_wiki/`: `__main__.py`, `settings.py`, `tg/pipeline.py`, `tg/voice.py`, `tg/photo.py` (contract), `tg/handlers.py`, `wiki/runner.py`, `inbox/staging.py`, `scheduler/maintenance.py` · `tests/unit/`: `tg/test_pipeline.py`, `tg/test_pipeline_document.py`, `tg/test_pipeline_streaming.py`, `tg/test_handlers.py`, `wiki/test_runner.py`, `inbox/test_staging.py`, `scheduler/test_maintenance.py` · `tests/integration/test_e2e_pipeline.py` · `pyproject.toml`, `uv.lock`, `.env.example` · `docs/adr/ADR-002-faster-whisper-core-dependency.md` · `docs/superpowers/specs/20260512-media-handling-full/{discovery,design}-chunk{1,2}.md` · `docs/superpowers/plans/20260512-media-handling-full/{breakdown.xml,step-0{1..5}-plan.md}` · `docs/superpowers/plans/20260512-media-handling-full-plan.md`.

Code not pushed to the git remote (per Git Push Policy — push on explicit user request). Beads persisted via `bd dolt push` after each chunk.
