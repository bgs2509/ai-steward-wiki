# step-11-plan.md — Chunk 11 / M-TG-MEDIA

**bd_id:** aisw-dbe
**Module:** M-TG-MEDIA
**Window estimate:** 0.40
**Sources:** D-022 (voice/photo input), §9 of tech-spec-draft.md
(lines 476–483), D-018 (idempotency via seen_files SHA-256), D-034
(PII tier for media filenames), depends on chunk 10 (M-TG-TEXT).

## Goal

Add voice + photo ingestion to the TG layer. Both media types land in
`Inbox-WIKI/raw/media/_staging/<run_id>_<sha8>.<ext>` first, then after
Stage-1a resolution + confirm runtime atomically moves them to
`<Domain-WIKI>/raw/media/<ISO8601>_<sha8>.<ext>` (immutable). Staging
files older than 24h are swept by a retention job. Voice uses
`faster-whisper` (CTranslate2, CPU, ru/en, RTF ≤ 0.5). Photo is
delegated to Sonnet vision via the existing CLI runner — no STT here.

## Steps (TDD)

1. **Recon** — read `tg/bot.py`, `tg/output.py`, `inbox/idempotency.py`
   from chunk 10/9. Confirm `seen_files` table is the SHA-256 sink and
   the run-id source (`audit.db`).
2. **Tests RED** — `tests/unit/tg/`:
   - `test_voice.py` — `transcribe_ogg(bytes) -> Transcript(text, lang,
     duration_s, model)`; falls back to "ru" on detect failure;
     RTF measurement helper; faster-whisper monkeypatched with a tiny
     fake model (no real download in unit tier).
   - `test_photo.py` — `handle_photo(message)` writes a staging file,
     returns `MediaRef(staging_path, sha256, mime)`; delegation hook to
     Sonnet vision is a Protocol stubbed in tests.
   - `test_staging.py` — `stage_media(bytes, ext, run_id) -> MediaRef`
     writes to `_staging/<run_id>_<sha8>.<ext>` atomically (tmp +
     `os.replace`); `promote_to_raw(media_ref, wiki_root)` atomic move
     to `<wiki>/raw/media/<ISO8601>_<sha8>.<ext>` and idempotent on
     repeat; `sweep_staging(now, ttl=24h)` deletes only files older than
     TTL, logs each removal with bd_id-style anchor.
3. **GREEN** — implement:
   - `src/ai_steward_wiki/tg/voice.py` — `VoiceTranscriber` Protocol +
     `FasterWhisperTranscriber` (lazy model load, `device="cpu"`,
     `compute_type="int8"`); message handler `on_voice` that downloads
     the ogg via aiogram Bot, calls staging, transcribes, hands the
     transcript to the classifier pipeline (re-uses chunk-10 confirm
     flow for any write intent).
   - `src/ai_steward_wiki/tg/photo.py` — `PhotoIngestor`, message
     handler `on_photo` that stages and emits a `MediaRef` for the
     Stage-1b vision call (vision call itself stays in
     `wiki/runner.py` — chunk 11 only delivers the ref).
   - `src/ai_steward_wiki/inbox/staging.py` — `stage_media`,
     `promote_to_raw`, `sweep_staging`, `MediaRef` dataclass; honours
     PII tier-2 (filename hashing — never embed user-provided name).
   - Register `on_voice`/`on_photo` in `tg/bot.py` `build_dispatcher`
     behind the existing `AllowlistMiddleware`.
   - APScheduler hook: register `sweep_staging_job` with cron 0700 UTC
     daily via the existing scheduler bootstrap (chunk 4) — additive
     `scheduler.bootstrap.register_maintenance_jobs(...)`.
4. **Quality gate**:
   - `uv run pytest tests/unit/tg -q`
   - `uv run pytest tests/unit/inbox -q`
   - `uv run pytest tests/unit -q`
   - `make lint`
   - `make grace-lint`
   - `make total-test`
5. **Commit** — `feat(M-TG-MEDIA): voice STT + photo staging + 24h
   sweep` with `bd_id: aisw-dbe` trailer.
6. **Post-commit** — update `breakdown.xml` RunState (CurrentChunk=12,
   ClosedChunks+=11), write report, close bd.

## Out of scope

1. Real Sonnet vision call (lives in `wiki/runner.py`, chunk 7 already
   handles Stage-1b; this chunk only delivers `MediaRef`).
2. Onboarding / admin flow (chunk 12).
3. Real faster-whisper model download in CI — unit tier uses fake
   model; integration tier (`RUN_INTEGRATION=1`) wires the real model.
4. PII tier-3 redaction of transcripts (chunk 13 / M-OPS-PII).
