# step-01 plan — chunk 1 M-RUNTIME-WIRING (bd_id: aisw-zny)

> Executed 2026-05-12. SSoT for chunk-1 execution. See discovery-chunk1.md / design-chunk1.md.

## Tasks (TDD)

1. **RED** — `tests/unit/tg/test_pipeline.py`: import `ACK_VOICE_UNAVAILABLE_RU` + `VoiceUnavailableError`; add `test_on_voice_stt_unavailable_sends_specific_ack`. Run → fails (ImportError). ✅
2. **GREEN** — `src/ai_steward_wiki/tg/voice.py`: add `VoiceUnavailableError(RuntimeError)`; `_load_model` wraps `from faster_whisper import WhisperModel` → raises it on `ImportError`. Header v0.0.2. ✅
3. **GREEN** — `src/ai_steward_wiki/tg/pipeline.py`: `ACK_VOICE_UNAVAILABLE_RU` const + `__all__` + MODULE_MAP; import `VoiceUnavailableError`; `on_voice` wraps `self._voice.handle(...)` in `try/except VoiceUnavailableError` → log `tg.pipeline.voice.stt_unavailable` + send `ACK_VOICE_UNAVAILABLE_RU`. Header v0.3.2. ✅
4. **GREEN** — `src/ai_steward_wiki/settings.py`: media fields (`media_staging_root`, `voice_enabled`, `voice_whisper_model_size`, `voice_stt_timeout_s`, `photo_enabled`, `photo_vision_timeout_s`). Header v0.0.9. ✅
5. **GREEN** — `src/ai_steward_wiki/__main__.py`: `START_BLOCK_MEDIA_PIPELINE_WIRING` — build `VoiceHandler`/`PhotoIngestor` from settings, log `runtime.media_pipeline.wired`, pass `voice=`/`photo=` to `DefaultPipeline`. Imports added. Header v0.1.1. ✅
6. **GREEN** — `pyproject.toml`: move `faster-whisper==1.1.0` to core deps, drop `stt` extra, add `faster_whisper.*` to mypy `ignore_missing_imports`. `uv lock` + `uv sync`. ✅
7. **GREEN** — `.env.example`: media `AISW_*` keys. ✅
8. **DOCS** — `docs/adr/ADR-002-faster-whisper-core-dependency.md` (DEC-C1-1). ✅
9. **VERIFY** — `uv run pytest tests/unit/tg/test_pipeline.py tests/unit/tg/test_voice.py tests/unit/tg/test_photo.py tests/unit/test_settings.py` green; `make lint` green; `make total-test` exit 0 (recorded in breakdown.xml/TotalTestLog).

## Acceptance

- `grep "voice=voice_handler" src/ai_steward_wiki/__main__.py` non-empty. ✅
- Manual (operator): `uv run python -m ai_steward_wiki` → send voice → reply ≠ "Принято."; logs `runtime.media_pipeline.wired voice=true photo=true`, no `tg.pipeline.voice.no_handler`. (Pending operator run.)

## Out of scope (later chunks)

Photo→Claude vision (chunk 2 / aisw-m2m); F.audio/F.video_note/caption (chunk 3 / aisw-ahv);
promote_to_raw + sweep_staging job (chunk 4 / aisw-8r9); e2e (chunk 5 / aisw-nzl).
