# D-022: voice/photo input — voice-first (faster-whisper) + photo via Claude vision

**Статус:** accepted
**Дата:** 2026-05-09
**Контекст:** [Q-B-14](../questions/Q-B-14-voice-photo-input.md), overview §9.14, §10, [D-013](D-013-claude-cli-auth.md), [D-018](D-018-ingest-idempotency.md), [D-021](D-021-timeouts-kill-policy.md)

## Проблема

Inbox-WIKI должен принимать voice memos и фото (билеты, рецепты, скрины). Вопрос: какие движки и в какой scope MVP. Overview §10 п.4 рекомендовал text-only, но это создаёт серьёзную UX-дыру.

## Варианты

1. **A — Text-only MVP.**
2. **B — Полный мультимодал** (faster-whisper + pytesseract).
3. **C — Voice MVP + photo iter-2.**
4. **D — Voice (faster-whisper) + photo через Claude vision** (без OCR-инфры). ⭐
5. **E — Async ack со stub-обработкой.**

## Выбор

**Вариант D.**

### Voice (STT)

1. **Engine:** `faster-whisper` (CTranslate2 backend) — local CPU, multilingual (RU/EN), small/medium model по результатам bench на VPS.
2. **Trigger:** TG `voice` / `audio` / `video_note` content type.
3. **Pipeline:**
   1. Bot ack «🎙️ слушаю…» (placeholder msg для D-026 streaming).
   2. Скачать file → save в `<wiki>/raw/media/<ts>_<hash>.<ext>`.
   3. Транскрипт через `faster-whisper` (timeout 60s).
   4. Транскрипт идёт в обычный flow router → Stage-1.
4. **Bench-критерий:** RTF (real-time factor) ≤ 0.5 на small model на текущем VPS; если не проходит — рассмотреть medium-int8 или Whisper-API fallback.

### Photo

1. **Engine:** Claude Sonnet vision (через CLI `--image` или image content в prompt).
2. **NO `pytesseract`** — Claude vision качественно лучше для phone shots; уже в нашем стеке (subscription mode по [D-013](D-013-claude-cli-auth.md)).
3. **Trigger:** TG `photo` / `document` content type, если MIME — image.
4. **Pipeline:**
   1. Bot ack «🖼️ смотрю…».
   2. Скачать файл → `<wiki>/raw/media/<ts>_<hash>.<ext>`.
   3. Передать image content в Stage-1 prompt вместе с user caption (если есть).
   4. Vision-extract идёт в обычный flow router (timeout 30s).

### Job category

Новая категория `media_ingest` в jobs.db / scheduler taxonomy. Используется только для async ack pattern; собственно processing работает в реальном time под TG webhook handler.

### Timeout policy ([D-021](D-021-timeouts-kill-policy.md))

| Подкатегория | Timeout |
|--------------|---------|
| `media_ingest:stt` | 60s |
| `media_ingest:vision` | 30s |

### Idempotency hook ([D-018](D-018-ingest-idempotency.md))

1. **L2 hash:** SHA-256 от bytes файла (raw, без перекодирования).
2. **Дополнительно:** SHA-256 от нормализованного транскрипта/vision-extract'а — пишется в `seen_files` отдельной row с `content_kind='voice_transcript'` / `'photo_vision'`.
3. **Dedup-confirmation:** только на bytes-level совпадении (повторное сообщение того же файла); семантический совпадение транскриптов лежит в L3 (отложено).

### Storage

```
<wiki>/raw/media/
├── 2026-05-09T08-12-34_a1b2c3d4.ogg     # voice
├── 2026-05-09T09-45-12_e5f6g7h8.jpg     # photo
└── …
```

Filename: `<ISO8601-with-dashes>_<sha256[:8]>.<ext>`. Immutable per Karpathy LLM Wiki method (`raw/` неизменяема).

## Последствия

1. Закрыта главная UX-дыра inbox'а — voice работает с MVP.
2. Photo поддержан без отдельной OCR-инфры (используется Claude vision из subscription).
3. Single новый dependency: `faster-whisper` + ctranslate2 + ONNX-runtime.
4. Запреты:
   1. **Не использовать `pytesseract`** — выпилен из стека.
   2. **Не модифицировать `<wiki>/raw/media/`** post-ingest (immutable).
   3. **Не превышать `media_ingest` timeouts без override в payload.**
5. Будущее: Whisper-API fallback (если local CPU не тянет); voice-output (TTS via `edge-tts`) — отдельное решение.

## Перенос в ADR

- [ ] перенесено в `docs/adr/ADR-022-voice-photo-input.md` (когда финализируется)
