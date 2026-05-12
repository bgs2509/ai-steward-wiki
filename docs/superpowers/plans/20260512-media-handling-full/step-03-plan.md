# step-03 plan — chunk 3 M-TG-HANDLERS-WIRING (bd_id: aisw-ahv)

> Executed 2026-05-12. SSoT for chunk-3 execution.

## Decisions (auto-applied)
- **DEC-C3-1**: `F.audio` and `F.video_note` both route to `pipeline.on_voice` (the STT path) — faster-whisper/pyav demuxes mp3/ogg/m4a and the audio track of mp4 video notes; staged file ext (`.ogg`) is cosmetic, transcription sniffs the container. More useful than a reject; satisfies D-022's voice/audio/video_note trigger list. No `ffmpeg` precondition surfaced (pyav bundles libav).
- **DEC-C3-2**: `message.caption` is forwarded only for **photo** in chunk 3 (D-022's caption requirement is photo-focused: "image content в Stage-1 prompt вместе с user caption"). Voice/audio captions and document captions are deferred (note in breakdown).

## Tasks (TDD)

1. **GREEN** — `src/ai_steward_wiki/tg/handlers.py`:
   - `@router.message(F.audio)` → `_download_bytes(message.audio.file_id)` → `pipeline.on_voice(...)`.
   - `@router.message(F.video_note)` → `_download_bytes(message.video_note.file_id)` → `pipeline.on_voice(...)`.
   - `_on_photo` forwards `caption=message.caption` to `on_photo`.
   - Header v0.0.2; SCOPE "Five handlers" → "Seven handlers"; MAP "5 handlers" → "7 handlers"; LINKS += D-022.
2. **GREEN** — `src/ai_steward_wiki/tg/pipeline.py`:
   - `MessagePipeline.on_photo(..., caption: str | None = None)` Protocol.
   - `DefaultPipeline.on_photo(..., caption=None)` — `text = PHOTO_CAPTION_PROMPT_RU.format(path=, caption=) if caption else PHOTO_PROMPT_RU.format(path=)`; `tg.pipeline.photo` log gains `has_caption`.
   - `PHOTO_CAPTION_PROMPT_RU` constant + `__all__` + MAP.
   - Header v0.3.4.
3. **GREEN** — `tests/unit/tg/test_handlers.py`: `FakeAudio`/`FakeVideoNote` dataclasses; `FakeMessage` += `audio`/`video_note`/`caption`; `_fake_bot_returning`, `_handler_by_name`; `test_router_audio_handler_routes_to_on_voice`, `test_router_video_note_handler_routes_to_on_voice`, `test_router_photo_handler_forwards_caption`.
4. **GREEN** — `tests/unit/tg/test_pipeline.py`: `test_on_photo_with_caption_passes_caption_in_prompt`.
5. **VERIFY** — `make total-test` exit 0 (428 tests, coverage 90.74%, ruff/mypy/grace/inv-lint clean).

## Acceptance
- `router.message.handlers` includes `_on_audio` and `_on_video_note` callbacks; photo handler passes `caption`.
- Manual (operator): send an mp3 / video note → transcribed like voice; photo with caption "занеси в health" → caption reaches Stage-1 prompt. (Pending operator run.)

## Out of scope (later chunks)
Document captions; voice/audio captions; video_note staged with correct ext; promote_to_raw + sweep (chunk 4 / aisw-8r9); e2e (chunk 5 / aisw-nzl).
