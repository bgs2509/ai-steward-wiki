# M-TG-HANDLERS-WIRING — Completion Report

**Chunk:** 19 (post-MVP)
**bd_id:** `aisw-ps8`
**Date:** 2026-05-11
**Status:** Closed

## Summary

Composes the existing building blocks (M-INBOX idempotency, M-TG-TEXT sender,
M-TG-CONFIRM service, M-TG-VOICE handler, M-TG-PHOTO ingestor) into a working
aiogram dispatch pipeline. Before this chunk, allowlist-passing updates were
silently ignored by aiogram default (chunk 18 only had a middleware gate). Now
text/voice/photo/document messages and confirm-callback queries flow through a
thin Router → MessagePipeline → existing services.

No business logic added; this chunk is composition + e2e tests only.

## What ships

- `src/ai_steward_wiki/tg/pipeline.py` — `MessagePipeline` Protocol (5 entry
  points) + `DefaultPipeline` concrete coordinator. L1 idempotency dedup,
  optional voice/photo staging, ack-only deliveries, confirmation resolve.
- `src/ai_steward_wiki/tg/handlers.py` — `build_router(pipeline) -> Router`
  factory. 5 decorated handlers extract `telegram_id`/`chat_id`/`update_id`,
  download bytes for media via `bot.get_file` + `bot.download`, delegate to the
  pipeline. Pure adapter — no business logic.
- `src/ai_steward_wiki/tg/bot.py` — `build_dispatcher` extended with optional
  `pipeline=...` kwarg; when given, includes the handler router.
- `src/ai_steward_wiki/tg/__init__.py` — barrel exports updated.
- `src/ai_steward_wiki/__main__.py` — constructs `AiogramSender`,
  `IdempotencyService`, `ConfirmationService`, `DefaultPipeline`, passes to
  `build_dispatcher(allowlist, pipeline=pipeline)`. Adds
  `runtime.handlers.registered` log marker.
- 28 unit tests across `tests/unit/tg/test_pipeline.py` (10) and
  `tests/unit/tg/test_handlers.py` (18).

## Out of scope (explicit deferral)

- No classifier (Stage-0 Haiku / Stage-1a/b Sonnet) invocation yet — pipeline
  acks only. Hooking M-CLASSIFIER + M-WIKI-RUNNER + `deliver_output` is a
  follow-up chunk; the `MessagePipeline` Protocol is the seam where it lands.
- Document handler is ack-only (logs mime/filename); full staging deferred.
- No webhook mode.

## Quality gates (all green)

| Gate | Result |
|---|---|
| `ruff check .` | passed |
| `ruff format --check .` | 159 files formatted |
| `mypy --strict src` | 64 files, no issues |
| `grace lint --failOn errors` | 0 errors / 0 warnings |
| `make inv-lint` | 14/14 INV checks pass |
| `pytest tests/unit` | 358 passed |
| Coverage | ≥80% gate satisfied |

## Tests added

| Test | Asserts |
|---|---|
| `test_parse_confirm_callback_valid_*` (3) | confirm/correct/cancel payloads parse |
| `test_parse_confirm_callback_wrong_prefix/bad_id/bad_action/parts` (5) | malformed payloads → None |
| `test_confirm_callback_prefix_constant` | `confirm:` prefix stable |
| `test_build_router_returns_router_with_handlers` | ≥4 message + ≥1 callback handlers registered |
| `test_build_dispatcher_with_pipeline_includes_router` | router attached |
| `test_build_dispatcher_without_pipeline_omits_router` | back-compat preserved |
| `test_download_bytes_reads_into_buffer` | get_file + download → bytes |
| `test_router_text_handler_dispatches_to_pipeline` | text → on_text(...) |
| `test_router_voice_handler_downloads_and_dispatches` | voice → bytes → on_voice |
| `test_router_callback_handler_parses_and_dispatches` | confirm cb → on_confirm_callback + ack |
| `test_router_callback_handler_malformed_data_just_answers` | bad payload → answer only |
| `test_on_text_sends_ack_when_new` | ack on new update |
| `test_on_text_skips_on_l1_duplicate` | L1 dedup short-circuit |
| `test_on_voice_with_handler_returns_transcript` | VoiceHandler.handle invoked, transcript echoed |
| `test_on_voice_without_handler_falls_back` | None VoiceHandler → default ack |
| `test_on_voice_empty_transcript_falls_back_to_default_ack` | empty text → default ack |
| `test_on_photo_with_ingestor_stages_and_acks` | PhotoIngestor.handle invoked, ack sent |
| `test_on_photo_without_ingestor_falls_back` | None ingestor → default ack |
| `test_on_document_logs_and_acks` | document ack |
| `test_on_document_skips_on_l1_duplicate` | L1 dedup short-circuit |
| `test_on_confirm_callback_delegates_to_service` | ConfirmationService.resolve invoked |

## Next chunk (suggested)

Plug Stage-0 classifier + WikiRunner into `DefaultPipeline.on_text`/`on_voice`
so acks are replaced with real Claude responses, and switch
`deliver_output(...)` for the final reply path.
