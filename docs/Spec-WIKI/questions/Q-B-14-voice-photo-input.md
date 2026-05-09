# Q-B-14: Голосовые и фото-вход

**Tier:** B
**Источник:** [overview §9 п.14](../raw/20260507-ai-steward-wiki-only-overview.md)

## Формулировка

STT (faster-whisper) и OCR (pytesseract) — обязательны для inbox-сценария или text-only MVP?

## Варианты

1. **A. Text-only MVP.** Сначала текст, голос/фото в iter-2. Согласуется с overview §10.
2. **B. Полный мультимодал в MVP.** STT + OCR обязательны. Сложнее, но «честный inbox».
3. **C. Stub в MVP.** Голос/фото принимаются, но конвертируются заглушкой («голос: <transcribe pending>»), включить позже.

## Решение

- [x] **Вариант D** — voice-first + photo через Claude vision, без отдельной OCR-инфры:
  - **Voice:** faster-whisper local (CTranslate2 backend, RU/EN multilingual, small/medium model по итогам bench на VPS).
  - **Photo:** отправляется в Claude Sonnet (CLI image content) как часть Stage-1 — никакого `pytesseract`.
  - **Async-ingest:** bot отвечает «🎙️ слушаю…» / «🖼️ смотрю…», создаёт job категории `media_ingest`, по завершении продолжает обычный flow (router → Stage-1).
  - **Hash для dedup** ([D-018](../decisions/D-018-ingest-idempotency.md)): bytes файла + отдельный hash от транскрипта/vision-extract'а.
  - **Timeout** ([D-021](../decisions/D-021-timeouts-kill-policy.md)): новая категория `media_ingest` — STT 60s, vision 30s.
  - Original media сохраняется в `<wiki>/raw/media/` (immutable per Karpathy method) с filename `<ts>_<hash>.<ext>`.
- [x] оформлено как [D-022](../decisions/D-022-voice-photo-input.md)

## Связанные

1. [Inbox-WIKI](../entities/inbox-wiki.md)
