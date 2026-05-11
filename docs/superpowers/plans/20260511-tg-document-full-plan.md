---
feature: tg-document-full
module_id: M-TG-DOCUMENT-FULL
bd_id: aisw-wku
chunk: 22
date: 2026-05-11
discovery_ref: docs/superpowers/specs/20260511-tg-document-full-discovery.md
design_ref: docs/superpowers/specs/20260511-tg-document-full-design.md
status: approved
---

# Implementation Plan — chunk 22 M-TG-DOCUMENT-FULL

## Sequence (TDD: RED → GREEN → REFACTOR per group)

### Task 1 — pypdf dependency

- Add `pypdf>=4.0,<5` to `pyproject.toml [project.dependencies]`.
- Run `uv lock` to refresh `uv.lock`.

### Task 2 — Constants and ru-only strings

In `src/ai_steward_wiki/tg/pipeline.py` (constants block ~line 97):

```python
ACK_DOC_UNSUPPORTED_RU = "Этот тип файла пока не поддерживается."
ACK_DOC_PDF_NO_TEXT_RU = "Не вижу текста в PDF. Попробуйте отправить страницу как фото."
ACK_DOC_TOO_LARGE_RU = "Файл слишком большой (лимит 25 МБ)."
MAX_DOC_BYTES = 25 * 1024 * 1024
PDF_MAX_EXTRACT_CHARS = 50_000
SUPPORTED_IMAGE_MIMES = frozenset({"image/jpeg", "image/jpg", "image/png", "image/webp"})
```

### Task 3 — Extend `_run_text_pipeline` source literal

`source: Literal["text", "voice", "document"]` — adds `"document"` value; no other logic change. Affects log fields only.

### Task 4 — Module-level helper `_extract_pdf_text`

```python
def _extract_pdf_text(data: bytes, *, max_chars: int = PDF_MAX_EXTRACT_CHARS) -> str:
    import io
    import pypdf
    from pypdf.errors import PdfReadError
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "") for p in reader.pages]
    except (PdfReadError, Exception):
        return ""
    text = "\n\n".join(s.strip() for s in pages if s.strip())
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[truncated]"
    return text
```

Note: broad `Exception` catch is acceptable here per Discovery R-4 — pypdf can raise non-PdfReadError types (zlib, struct, etc.); we want full graceful fallback.

### Task 5 — Inject PIIRedactor into DefaultPipeline

`__init__` add `pii: PIIRedactor | None = None`; store `self._pii = pii or PIIRedactor()`.

### Task 6 — `_safe_filename_log` method

```python
def _safe_filename_log(self, filename: str) -> str:
    normalized = (filename or "unnamed").lower().strip()
    return self._pii.hash_token(normalized)
```

### Task 7 — Rewrite `on_document`

Body sequence:

1. `if not await self._l1_check(...)`: return
2. `hashed_filename = self._safe_filename_log(filename)` for log lines
3. `if len(doc_bytes) > MAX_DOC_BYTES`: log `tg.pipeline.document.rejected reason=too_large` + send `ACK_DOC_TOO_LARGE_RU` + return
4. L2 dedup: `sha256, match = await self._idem.check_content(telegram_id, "file", doc_bytes)`
   - On hit: log `dedup_hit`, `await self._idem.record_dedup_choice(sha256, telegram_id, "duplicate_doc")`, send `ACK_DEDUP_RU`, return.
5. Log `tg.pipeline.document.received` with `sha256_short=sha256[:8]`, `hashed_filename`, `mime`, `size`.
6. Mime dispatch:
   - `application/pdf`: full pipeline ready check; if not — ack `ACK_DOC_RU`. Else `text = _extract_pdf_text(doc_bytes)`; if empty → reject `ACK_DOC_PDF_NO_TEXT_RU`; else `await self._run_text_pipeline(text=text, source="document", ...)`.
   - `mime.startswith("text/")`: decode `doc_bytes.decode("utf-8-sig", errors="strict")` inside try/except UnicodeDecodeError → reject `ACK_DOC_UNSUPPORTED_RU` on failure; else full pipeline ready check + `_run_text_pipeline(source="document")`.
   - `mime.lower() in SUPPORTED_IMAGE_MIMES`: if `self._photo is None` → ack `ACK_PHOTO_RU` (fallback); else reuse on_photo body inline (stage + log + ack `ACK_PHOTO_RU`).
   - else: reject `ACK_DOC_UNSUPPORTED_RU`, log `rejected reason=unsupported_mime`.

### Task 8 — Update `__main__.py` wiring

`DefaultPipeline(...)` call gets `pii=PIIRedactor(hash_secret=settings.pii_hash_secret.get_secret_value().encode())` or rely on default factory if simpler. Inspect existing line 394 area to decide.

### Task 9 — MODULE_CONTRACT update

`src/ai_steward_wiki/tg/pipeline.py` header: extend SCOPE with "document mime routing (DEC-L3): PDF text-extract, text/* inline, image/* photo path, else reject"; bump VERSION minor; add `LAST_CHANGE` line.

### Task 10 — Tests

New file `tests/unit/tg/test_pipeline_document.py` with ≥10 test functions covering branches per Design verification_strategy. Use existing `FakeSender` and create local `FakeIdempotency`, `FakeClassifier`, `FakeRunner`, `FakeDelivery`, `FakePhotoIngestor` (or import from conftest if present). Static fixture `tests/fixtures/sample.pdf` and `tests/fixtures/empty.pdf` generated via pypdf in `conftest.py` `tmp_path` fixture (so no binary files in git).

Remove or rewrite legacy tests `test_on_document_logs_and_acks` and `test_on_document_skips_on_l1_duplicate` (semantics change).

### Task 11 — knowledge-graph.xml + verification-plan.xml

Add `<M-TG-DOCUMENT-FULL>` entry per Discovery §10. Add `<V-M-TG-DOCUMENT-FULL>` block with the 10 test names. Use `grace-refresh --verify` if convenient; otherwise edit directly.

### Task 12 — make total-test

`make lint` + `make test-unit` + `grace lint` (if available locally — else CI catches). Fix any drift.

### Task 13 — smart-commit

Atomic commit: `feat(M-TG-DOCUMENT-FULL): wire mime-based document routing with L2 dedup`.

### Task 14 — Finish

`grace-refresh` final, write completion report under `docs/reports/20260511-tg-document-full-report.md`, update README "Message flow" if applicable, `bd close aisw-wku`.

## Self-review checklist

- [x] FR-1..7 → tasks 7 + 10
- [x] NFR-1 (≥8 tests) → task 10 (10 tests planned)
- [x] NFR-2 (lint clean) → task 12
- [x] NFR-3 (ru-only) → task 2
- [x] NFR-4 (log markers) → task 7
- [x] R-1..5 mitigations → tasks 4, 7
- [x] DEC-L3 four branches covered → task 7
- [x] D-018 L2 dedup → task 7 step 4
- [x] Filename tier-2 hash → tasks 6, 7

## Notes for executor

- Context7 not required (APIs pre-verified in Design).
- TDD ordering: write tests in task 10 FIRST (RED), then implement tasks 2-9 (GREEN), then task 12 (REFACTOR clean).
- Do NOT bypass pre-commit hooks. Fix root cause if hook fails.
- Conventional Commits with MODULE_ID scope.
