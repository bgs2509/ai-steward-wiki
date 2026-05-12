---
feature: media-handling-chunk1-runtime-wiring
bd_id: aisw-zny
status: approved
date: 2026-05-12
approach: "compose-root-wiring + settings-fields + core-dep + graceful-degradation"
stack:
  - "faster-whisper==1.1.0 — moved from [project.optional-dependencies].stt to core [project.dependencies]"
  - "pydantic-settings — new Settings fields (frozen, AISW_-prefixed)"
modules_touched:
  - M-RUNTIME-WIRING  # src/ai_steward_wiki/__main__.py
  - M-FOUNDATION-CONFIG  # src/ai_steward_wiki/settings.py
  - M-TG-PIPELINE-CLASSIFIER  # src/ai_steward_wiki/tg/pipeline.py (new ACK + exception)
  - M-TG-VOICE  # src/ai_steward_wiki/tg/voice.py (raise VoiceUnavailableError on ImportError)
---

# Design — chunk 1: M-RUNTIME-WIRING

## Подход

Минимальная провязка composition root, без рефакторинга `_amain`. Новый блок `START_BLOCK_MEDIA_PIPELINE_WIRING` рядом с `START_BLOCK_TEXT_PIPELINE_WIRING`.

## Изменения по файлам

### 1. `src/ai_steward_wiki/settings.py` (M-FOUNDATION-CONFIG)
Новые поля (после блока «Chunk 8: M-WIKI-LIFECYCLE» или в новом блоке «Media handling»):
```python
# Media handling (D-022, chunk: media-handling).
media_staging_root: Path = Path("/var/lib/ai-steward-wiki/workspace/media-staging")
voice_enabled: bool = True
voice_whisper_model_size: Literal["small", "medium"] = "small"
voice_stt_timeout_s: float = 60.0
photo_enabled: bool = True
photo_vision_timeout_s: float = 30.0
```
+ bump `CHANGE_SUMMARY` to note media fields. (Module ID in header is CONFIG; KG name — check, contract says "M-FOUNDATION-LOGGING consumes log_level" — leave existing LINKS, add note.)

### 2. `src/ai_steward_wiki/tg/voice.py` (M-TG-VOICE)
- New exception `VoiceUnavailableError(Exception)` in `__all__`.
- `FasterWhisperTranscriber._load_model`: wrap the `from faster_whisper import WhisperModel` import — on `ModuleNotFoundError`/`ImportError` raise `VoiceUnavailableError("faster-whisper not installed")`.
- bump `CHANGE_SUMMARY` (v0.0.2).

### 3. `src/ai_steward_wiki/tg/pipeline.py` (M-TG-PIPELINE-CLASSIFIER)
- New constant `ACK_VOICE_UNAVAILABLE_RU = "Голосовые сообщения сейчас недоступны — напишите текстом."` + add to `__all__`.
- `on_voice`: wrap `await self._voice.handle(...)` in `try/except VoiceUnavailableError` → `_log.warning("tg.pipeline.voice.stt_unavailable", telegram_id=telegram_id)` + `await self._sender.send_message(chat_id, ACK_VOICE_UNAVAILABLE_RU)`; return.
- Import `VoiceUnavailableError` from `ai_steward_wiki.tg.voice`.
- bump `CHANGE_SUMMARY`.

### 4. `src/ai_steward_wiki/__main__.py` (M-RUNTIME-WIRING)
New block before `pipeline = DefaultPipeline(...)`:
```python
# START_BLOCK_MEDIA_PIPELINE_WIRING (chunk: media-handling)
from ai_steward_wiki.tg.photo import PhotoIngestor
from ai_steward_wiki.tg.voice import FasterWhisperTranscriber, VoiceHandler

voice_handler: VoiceHandler | None = None
if settings.voice_enabled:
    voice_handler = VoiceHandler(
        FasterWhisperTranscriber(model_size=settings.voice_whisper_model_size),
        inbox_root=settings.media_staging_root,
    )
photo_ingestor: PhotoIngestor | None = (
    PhotoIngestor(inbox_root=settings.media_staging_root) if settings.photo_enabled else None
)
logger.info(
    "runtime.media_pipeline.wired",
    voice=voice_handler is not None,
    photo=photo_ingestor is not None,
    whisper_model=settings.voice_whisper_model_size if voice_handler else None,
)
# END_BLOCK_MEDIA_PIPELINE_WIRING
```
+ pass `voice=voice_handler, photo=photo_ingestor` into `DefaultPipeline(...)`.
+ `media_staging_root` added to `_ensure_data_dirs`-style mkdir? — `stage_media` does `mkdir(parents=True)` lazily, so not required; skip.

### 5. `pyproject.toml` / `uv.lock`
- Move `faster-whisper==1.1.0` from `[project.optional-dependencies].stt` into `[project.dependencies]`. Keep an empty/removed `stt` extra? — remove the now-redundant extra.
- `uv lock` to regenerate `uv.lock`.

### 6. `.env.example`
Add the six `AISW_*` media keys with default values + a comment.

### 7. `docs/adr/ADR-NNN-faster-whisper-packaging.md`
ADR for DEC-C1-1 (core dep vs extra).

## Тесты (TDD, RED→GREEN)

`tests/unit/tg/test_pipeline_voice.py` (extend or create):
- `on_voice` with fake VoiceHandler whose `handle` returns `(MediaRef, Transcript(text="привет"))` and full pipeline (mock classifier/runner/output) → `_run_text_pipeline` path taken → `output.deliver` called once.
- `on_voice` with fake VoiceHandler whose `handle` raises `VoiceUnavailableError` → `sender.send_message` called with `ACK_VOICE_UNAVAILABLE_RU`, classifier NOT called.
- `on_voice` with `_voice=None` (regression) → `ACK_TEXT_RU`, `tg.pipeline.voice.no_handler` (existing behaviour preserved).

`tests/unit/tg/test_pipeline_photo.py` (extend or create):
- `on_photo` with fake PhotoIngestor → `handle(photo_bytes, run_id=..., mime=...)` called, `ACK_PHOTO_RU` sent.
- `on_photo` with `_photo=None` (regression) → `ACK_PHOTO_RU`, `tg.pipeline.photo.no_handler`.

`tests/unit/test_settings.py` (if exists) or new: media fields present with expected defaults; env override works.

(No __main__ wiring unit-test infra exists for the full bootstrap — covered by `make total-test` smoke + manual acceptance.)

## Verification

1. `make total-test` exit 0.
2. `grep -n "voice=voice_handler" src/ai_steward_wiki/__main__.py` non-empty.
3. Manual: `uv run python -m ai_steward_wiki` → send voice → reply ≠ "Принято." (transcript / Stage-1 result), logs contain `runtime.media_pipeline.wired voice=true photo=true`, no `tg.pipeline.voice.no_handler`.
