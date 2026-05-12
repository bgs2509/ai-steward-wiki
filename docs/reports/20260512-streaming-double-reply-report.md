# Completion Report — Streaming slow-path double reply (aisw-x92)

**Date:** 2026-05-12  **Type:** bug fix  **Module:** M-TG-TEXT (`tg/output.py`, `tg/pipeline.py`, `tg/stream_edit.py`)

## Symptom

On a live run (`proc-df15507e`) a single user message produced **two** Telegram replies: the streamed/edited placeholder message plus a fresh duplicate. Logs also showed a noisy `tg.stream.final_flush_failed` / `TelegramBadRequest` on every short streamed reply.

## Root cause

`DefaultStreamingDelivery.run_and_deliver` slow-path delivered the answer twice:
1. via `StreamEditor` editing the placeholder (`feed` + `finalize`);
2. via an unconditional `output.deliver()` → `deliver_output()` → `sender.send_message()`.

`deliver_output` does double duty — disk-persist + audit (must always run) **and** TG send (already done on the slow-path). Secondary: `StreamEditor.finalize()` always re-edited even when the buffer was unchanged since the last tick → "message is not modified" 400.

## Fix (Variant 1 from `/best`)

- `deliver_output` / `OutputDelivery.deliver` / `_OutputDeliveryAdapter.deliver` gain keyword-only `tg_send: bool = True`. When `False`: skip the entire D-025 size-hybrid send branch, keep `_persist_to_disk` + `_record_run_output`; `tg.output.delivered` logs `tg_send=false`. Default `True` ⇒ fast-path and `DefaultPipeline` unchanged byte-for-byte.
- `DefaultStreamingDelivery.run_and_deliver` slow-path calls `output.deliver(..., tg_send=False)`.
- `StreamEditor` tracks `_last_sent_text`; `finalize()` is a no-op (logs `tg.stream.finalized skipped=true`) when the canonical final text equals the last sent text. Eliminates the spurious `final_flush_failed`.

Variant 2 (split `deliver_output` into persist+send) deferred — overkill for MVP; noted as future refactor if more delivery modes appear.

## Verification

- New unit tests: `test_output.py::test_deliver_persists_without_tg_send`; `test_stream_edit.py::test_finalize_skips_edit_when_text_unchanged`; assertions in `test_pipeline_streaming.py` (slow-path `tg_send is False`, fast-path `tg_send is True`).
- `make lint` (ruff + ruff format + mypy --strict src): clean.
- `grace lint --failOn errors`: 0 issues.
- `uv run pytest tests/unit`: 418 passed.

## Files

`src/ai_steward_wiki/tg/output.py` (v0.0.2), `src/ai_steward_wiki/tg/pipeline.py` (v0.3.1), `src/ai_steward_wiki/tg/stream_edit.py` (v0.0.2), `src/ai_steward_wiki/__main__.py`, + 3 test files. Specs/plan under `docs/superpowers/{specs,plans}/20260512-streaming-double-reply-*`.
