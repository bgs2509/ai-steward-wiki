# Chunk 22 — M-TG-DOCUMENT-FULL Completion Report

**Date:** 2026-05-11
**bd_id:** aisw-wku
**Scope:** Mime-based document routing with L2 dedup and tier-2 filename hash.
**SSoT:** `docs/superpowers/plans/20260511-ai-steward-wiki-launch/breakdown.xml` chunk-22.

## Delivered

1. `DefaultPipeline.on_document` rewritten from ack-only to a 4-branch MIME router per DEC-L3:
   - `application/pdf` → pypdf text extraction → existing text pipeline (classifier → runner → deliver).
   - `text/*` → UTF-8 (with BOM) decode → text pipeline. Non-UTF-8 rejected politely.
   - `image/*` (jpeg/png/webp) → `PhotoIngestor.handle` staging path.
   - Anything else → ru-only "type not supported" rejection.
2. L2 idempotency on `doc_sha256` via `Inbox.seen_files` (D-018) — duplicate documents short-circuit before any routing.
3. Tier-2 filename hashing via `PIIRedactor.hash_token` (D-034) — raw filenames never appear in structured logs.
4. 25 MB size cap (`MAX_DOC_BYTES`) with ru-only rejection ack before dedup/route.
5. Scanned-only PDFs (zero extractable text) get a ru-only hint suggesting photo re-upload — real OCR deferred.
6. `pypdf==5.1.0` added as pure-Python PDF text extractor (no Tesseract per D-022 minimalism).

## Tests

- New file `tests/unit/tg/test_pipeline_document.py` — 13 tests covering all 4 mime branches, L2 dedup, oversize reject, PDF parse error, UTF-8 BOM, non-UTF-8 reject, PII-safe logs.
- Total unit suite: **397/397 pass**.

## Quality gates

- `uv run ruff check` — clean.
- `uv run mypy --strict src/ai_steward_wiki/` — 64 files, no issues.
- `grace lint --failOn errors` — 0 errors / 0 warnings.

## Decisions

1. **pypdf instead of real OCR for PDFs.** `WikiRunner.run` accepts only text — adding media support is out of scope for chunk 22. Text-bearing PDFs cover the common case; scanned PDFs get an explicit hint.
2. **25 MB hard cap.** Telegram bot API document limit is 20 MB inbound; 25 MB gives headroom while protecting memory.
3. **`PIIRedactor()` default in pipeline constructor.** Allows tests to omit dependency; `__main__.py` wires the real redactor with `settings.pii_hash_secret`.

## Deferred

- Real OCR for scanned PDFs and image documents (needs WikiRunner media-input contract).
- Per-mime size limits (e.g. stricter cap for text).
- DOCX/XLSX support.
