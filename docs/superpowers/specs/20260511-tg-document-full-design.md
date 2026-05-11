---
feature: tg-document-full
module_id: M-TG-DOCUMENT-FULL
bd_id: aisw-wku
chunk: 22
date: 2026-05-11
discovery_ref: docs/superpowers/specs/20260511-tg-document-full-discovery.md
status: approved
technology_stack:
  - name: pypdf
    version: ">=4.0,<5"
    purpose: "Pure-python PDF text extraction (no system deps, no Tesseract)."
    license: BSD-3
    alternatives_rejected:
      - "pdfplumber — heavier, depends on pdfminer.six; overkill for first-page-text."
      - "pymupdf — AGPL/commercial split, system poppler dependency."
      - "pdf2image — requires poppler binary, raster output, out of D-022 scope."
  - name: pytest (existing)
    purpose: "Unit tests for all branches."
contracts_touched:
  - M-TG-PIPELINE (extend on_document, add helpers _route_document, _extract_pdf_text, _safe_log_filename)
  - M-INBOX (no change — reuse IdempotencyService.check_content/record_dedup_choice with kind="file")
  - M-OPS-PII (no change — reuse PIIRedactor.hash_token)
  - M-TG-MEDIA (no change — reuse PhotoIngestor.handle for image/* branch)
data_model_changes: none
api_changes:
  - "DefaultPipeline.on_document signature unchanged (telegram_id, chat_id, update_id, doc_bytes, mime, filename)."
  - "Internal helpers added as private DefaultPipeline methods."
new_strings_ru:
  - "ACK_DOC_UNSUPPORTED_RU = 'Этот тип файла пока не поддерживается.'"
  - "ACK_DOC_PDF_NO_TEXT_RU = 'Не вижу текста в PDF. Попробуйте отправить страницу как фото.'"
  - "ACK_DOC_TOO_LARGE_RU = 'Файл слишком большой (лимит 25 МБ).'"
log_anchors:
  - "tg.pipeline.document.received { telegram_id, mime, size, sha256_short, hashed_filename }"
  - "tg.pipeline.document.dedup_hit { sha256_short, action='duplicate_doc' }"
  - "tg.pipeline.document.rejected { reason: 'unsupported_mime'|'too_large'|'pdf_no_text'|'pdf_parse_error', mime }"
  - "tg.pipeline.document.routed_text { source: 'pdf'|'text', chars }"
  - "tg.pipeline.document.routed_image { ext, sha256_short }"
verification_strategy:
  unit_tests:
    - "test_on_document_pdf_extracts_text_and_routes_through_text_pipeline"
    - "test_on_document_pdf_with_no_extractable_text_rejects_with_hint"
    - "test_on_document_pdf_parse_error_rejects_gracefully"
    - "test_on_document_text_plain_routes_through_text_pipeline_with_utf8_bom_decode"
    - "test_on_document_image_jpeg_routes_through_photo_ingestor"
    - "test_on_document_image_heic_rejected_as_unsupported"
    - "test_on_document_unsupported_mime_rejects_with_ru_message"
    - "test_on_document_octet_stream_treated_as_unsupported"
    - "test_on_document_l2_dedup_hit_short_circuits_before_routing"
    - "test_on_document_oversized_doc_rejected"
    - "test_on_document_log_uses_hashed_filename_never_raw"
---

# Design — chunk 22 M-TG-DOCUMENT-FULL

## Approach

Single-file edit to `src/ai_steward_wiki/tg/pipeline.py`. Replace ack-only `on_document` body with a mime router that delegates to existing pipelines. All four branches handled by ≤80 new lines plus three small helpers.

## Control flow

```
on_document(doc_bytes, mime, filename, ...)
  │
  ├── L1 dedup check (existing) ────────────► return
  ├── size check ──── reject ACK_DOC_TOO_LARGE_RU
  ├── L2 dedup check_content(kind="file", doc_bytes)
  │     └── hit → record_dedup_choice + ACK_DEDUP_RU → return
  │
  ├── mime branch:
  │     ├── application/pdf  → _extract_pdf_text(doc_bytes)
  │     │      ├── text!="" → _run_text_pipeline(text, source="document")
  │     │      └── else     → ACK_DOC_PDF_NO_TEXT_RU
  │     ├── text/*          → doc_bytes.decode("utf-8-sig") → _run_text_pipeline
  │     ├── image/{jpeg,png,webp} → PhotoIngestor.handle(...) → ACK_PHOTO_RU
  │     └── else            → ACK_DOC_UNSUPPORTED_RU
```

## Helper functions

```python
def _safe_filename_log(self, filename: str) -> str:
    """Return tier-2 PII-hashed filename token for log lines."""
    normalized = (filename or "unnamed").lower().strip()
    return self._pii.hash_token(normalized)

def _extract_pdf_text(data: bytes, *, max_chars: int = 50_000) -> str:
    """Pure-text extraction via pypdf. Empty string on no-text / parse error."""
    # pypdf.PdfReader(io.BytesIO(data)); iterate pages, page.extract_text()
    # Concatenate with "\n\n"; truncate to max_chars + "\n[truncated]" suffix.
    # Catch pypdf.errors.PdfReadError and broad Exception → return "".

async def _route_document(
    self, *, telegram_id, chat_id, update_id, doc_bytes, mime, filename, sha256_short
) -> None:
    """Dispatch by mime after L1/L2/size checks."""
```

## Dependency injection

`DefaultPipeline.__init__` already receives `_idem` (IdempotencyService), `_photo` (PhotoIngestor), `_classifier`, `_runner`, `_output`, `_sender`. New required dep: `_pii: PIIRedactor`. To keep back-compat with existing `__main__.py` wiring and tests, accept `pii: PIIRedactor | None = None` and fall back to module-level `default_redactor()` (existing helper in `ops/pii.py`).

## Constants

```python
MAX_DOC_BYTES = 25 * 1024 * 1024  # 25 MB
PDF_MAX_EXTRACT_CHARS = 50_000
SUPPORTED_IMAGE_MIMES = frozenset({"image/jpeg", "image/jpg", "image/png", "image/webp"})
```

## TDD plan (test groups)

1. **PDF happy path** — fixture: 1-page text PDF generated inline in a pytest fixture via pypdf.PdfWriter (or static fixture in `tests/fixtures/sample.pdf`). Asserts text flows to a FakeRunner via `_run_text_pipeline`.
2. **PDF no-text** — fixture: empty PDF (pypdf-built with no text) → asserts ACK_DOC_PDF_NO_TEXT_RU, no runner call.
3. **PDF parse error** — bytes `b"not-a-pdf"` with mime `application/pdf` → asserts rejection, log `pdf_parse_error`.
4. **text/plain happy** — `"привет\nмир".encode("utf-8-sig")` + mime `text/plain` → asserts decoded text in runner call.
5. **image/jpeg** — small bytes + mime `image/jpeg` → asserts PhotoIngestor.handle called, ACK_PHOTO_RU sent.
6. **image/heic** — rejected as unsupported.
7. **Unsupported octet-stream** — rejected.
8. **L2 dedup** — IdempotencyService preloaded with matching sha → asserts ACK_DEDUP_RU, no further routing, record_dedup_choice called.
9. **Oversized doc** — 26 MB bytes → ACK_DOC_TOO_LARGE_RU.
10. **Filename hashing** — assert raw filename never appears in any log call (capture structlog events; raw string must not be a substring of any event dict).

## Open architectural decision (ADR candidate)

None of significant weight. Choice of pypdf vs alternatives documented inline in `technology_stack` frontmatter; no ADR file. If reviewers disagree, an ADR will be created in Step 13.

## Compatibility

- `DefaultPipeline(...)` calls in `src/ai_steward_wiki/__main__.py` need an extra `pii=` kwarg (or rely on default factory).
- Existing tests for `on_document` (test_on_document_logs_and_acks, test_on_document_skips_on_l1_duplicate) are superseded; rewrite them to match new contract.

## Pre-verified APIs (Context7 not required in Execution)

- `pypdf.PdfReader(stream)`, `reader.pages`, `page.extract_text() -> str`, `pypdf.errors.PdfReadError` — stable since pypdf 3.0; pypdf 4.x maintains identical surface.
- `bytes.decode("utf-8-sig", errors="strict")` — stdlib, BOM auto-stripped.
- Existing module APIs (IdempotencyService, PIIRedactor, PhotoIngestor, _run_text_pipeline) — verified via Discovery exploration.
