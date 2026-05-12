---
feature: media-handling-chunk1-runtime-wiring
bd_id: aisw-zny
epic: aisw-hcl
status: approved
date: 2026-05-12
fr:
  - id: FR-1
    text: "DefaultPipeline в рантайме получает работающий VoiceHandler — голосовое сообщение транскрибируется faster-whisper и транскрипт идёт в text pipeline (Stage-0→Stage-1)."
  - id: FR-2
    text: "DefaultPipeline в рантайме получает работающий PhotoIngestor — фото стейджится (chunk 1: ack; vision-вызов — chunk 2)."
  - id: FR-3
    text: "faster-whisper доступен в рантайме после стандартной установки зависимостей."
  - id: FR-4
    text: "Если STT недоступен (ImportError faster_whisper) — пользователю отвечает явное сообщение, а не тихий ACK_TEXT_RU."
  - id: FR-5
    text: "Настройки: путь стейджинга медиа, размер whisper-модели, таймауты STT/vision, флаги voice_enabled/photo_enabled."
nfr:
  - id: NFR-1
    text: "Whisper-модель не грузится при импорте модуля — только при первой транскрипции (lazy, уже реализовано в FasterWhisperTranscriber)."
  - id: NFR-2
    text: "Лог runtime.media_pipeline.wired с полями voice, photo, whisper_model — наблюдаемость старта."
  - id: NFR-3
    text: "mypy --strict для src/ — зелёный; ruff/format зелёные; покрытие core ≥80%."
risks:
  - "faster-whisper тянет ctranslate2+onnxruntime (~200МБ); первая транскрипция качает модель small (~480МБ) — приемлемо для voice-first сервиса (D-022)."
  - "Нет per-user Inbox-WIKI в lifecycle.py → MVP стейджит в единый workspace_root/media-staging; промоушен в target-wiki — chunk 4."
scope_in:
  - "Wiring VoiceHandler+PhotoIngestor в DefaultPipeline (__main__.py)."
  - "Settings-поля для медиа."
  - "faster-whisper в core deps + graceful degradation."
  - "ACK_VOICE_UNAVAILABLE_RU + VoiceUnavailableError."
  - "Unit-тесты pipeline.on_voice/on_photo."
scope_out:
  - "Фото→Claude vision (chunk 2)."
  - "F.audio/F.video_note/caption (chunk 3)."
  - "promote_to_raw/sweep_staging (chunk 4)."
  - "e2e (chunk 5)."
scope_later:
  - "Per-user Inbox-WIKI staging path (когда появится Inbox-WIKI в lifecycle)."
  - "Whisper-API fallback если CPU не тянет RTF≤0.5."
decisions:
  - id: DEC-C1-1
    text: "faster-whisper переносится в core deps (не extra). Voice — primary MVP-фича (D-022 закрывает главную UX-дыру inbox). Размер Docker-образа — deploy-concern, не correctness. + defensive ImportError→graceful как defence-in-depth. ADR-кандидат."
  - id: DEC-C1-2
    text: "media_staging_root = единый каталог (default /var/lib/ai-steward-wiki/workspace/media-staging), не per-user. Per-user Inbox-WIKI отложен — его нет в lifecycle.py. Промоушен в target-wiki — chunk 4."
  - id: DEC-C1-3
    text: "voice_whisper_model_size default 'small' (D-022 bench-критерий RTF≤0.5 на small)."
preflight:
  pre_commit: "ok — .git/hooks/pre-commit + .pre-commit-config.yaml present (hooksPath=.beads/hooks)"
  lint_baseline: "clean — ruff/format/mypy all green, 0 errors"
  sentrux: "skipped — no .sentrux/rules.toml"
---

# Discovery — chunk 1: M-RUNTIME-WIRING (wire voice+photo handlers)

## Реальная цель

Пользователь отправил голосовое в бот и получил «Принято.» — потому что `DefaultPipeline` собирается в `__main__.py` без `voice=`/`photo=`, срабатывает ветка `tg.pipeline.voice.no_handler`. Цель chunk 1 — закрыть этот gap: всё для voice уже написано (`M-TG-VOICE`, `FasterWhisperTranscriber`, `VoiceHandler`), не хватает только провязки в composition root + зависимости в окружении.

## Что уже есть (verified by Read 2026-05-12)

- `tg/voice.py`: `FasterWhisperTranscriber` (lazy `_load_model`), `VoiceHandler.handle(audio_bytes, run_id) -> (MediaRef, Transcript)`.
- `tg/photo.py`: `PhotoIngestor.handle(photo_bytes, run_id, mime) -> MediaRef`.
- `tg/pipeline.py`: `DefaultPipeline.__init__(..., voice: VoiceHandler|None=None, photo: PhotoIngestor|None=None, ...)`; `on_voice` уже умеет: `_voice is None → no_handler+ack`; иначе transcribe → если транскрипт пуст → ack; если pipeline неполный → `ACK_VOICE_RU\n<text>`; иначе `_run_text_pipeline(source="voice")`.
- `inbox/staging.py`: `stage_media(data, ext, run_id, inbox_root, mime)` пишет в `inbox_root/raw/media/_staging/<run_id>_<sha8>.<ext>`.
- `pyproject.toml:27`: `stt = ["faster-whisper==1.1.0"]` (extra, не ставится `uv sync`).
- `__main__.py`: `DefaultPipeline(sender=..., idempotency=..., confirmation=..., classifier=..., runner=..., output=..., streaming=..., pii=...)` — БЕЗ voice/photo.

## Blind spots / что учесть

1. `FasterWhisperTranscriber.transcribe` лениво импортит `faster_whisper` внутри `_load_model` — если пакета нет, `ModuleNotFoundError` всплывёт из `to_thread(_load_model)` уже во время обработки голосового, не на старте. Нужен перехват → доменное исключение → graceful ru-ответ.
2. `media_staging_root` каталог должен существовать (или создаваться `stage_media` — он делает `mkdir(parents=True)`). Достаточно передать путь.
3. `VoiceHandler.__init__(transcriber, *, inbox_root)` — `inbox_root` это и есть staging-корень.
4. Settings frozen + env_prefix `AISW_` → новые поля автоматически env-overridable (`AISW_VOICE_ENABLED`, `AISW_VOICE_WHISPER_MODEL_SIZE`, `AISW_MEDIA_STAGING_ROOT`, `AISW_VOICE_STT_TIMEOUT_S`, `AISW_PHOTO_VISION_TIMEOUT_S`, `AISW_PHOTO_ENABLED`).
