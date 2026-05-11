---
feature: tg-document-full
module_id: M-TG-DOCUMENT-FULL
bd_id: aisw-wku
chunk: 22
date: 2026-05-11
ssot: docs/superpowers/plans/20260511-ai-steward-wiki-launch/breakdown.xml chunk-22
decisions_ref: [DEC-L3, D-022, D-018]
status: approved
functional_requirements:
  - id: FR-1
    text: "Replace ack-only DefaultPipeline.on_document with mime-based router per DEC-L3."
  - id: FR-2
    text: "Branch application/pdf: extract text via pypdf, stage as inline text through classifier pipeline; if no extractable text → reject with hint to re-send as photo."
  - id: FR-3
    text: "Branch text/*: decode bytes as UTF-8 (with BOM tolerance via utf-8-sig), route through DefaultPipeline._run_text_pipeline (existing classifier path)."
  - id: FR-4
    text: "Branch image/*: route bytes through existing PhotoIngestor.handle() — same path as on_photo."
  - id: FR-5
    text: "Branch else (unsupported mime): reply ACK_DOC_UNSUPPORTED_RU ru-only, no audit error, log tg.pipeline.document.rejected."
  - id: FR-6
    text: "L2 dedup on doc_sha256 (raw bytes) via IdempotencyService.check_content(kind=\"file\") BEFORE mime branching; on hit emit ACK_DEDUP_RU and record_dedup_choice."
  - id: FR-7
    text: "Filename PII tier-2 hash via PIIRedactor.hash_token(normalized_filename) for all log lines — never log raw filename."
non_functional_requirements:
  - id: NFR-1
    text: "≥8 unit tests covering 4 mime branches + L2 dedup hit + rejection + filename hashing + UTF-8 BOM decode."
  - id: NFR-2
    text: "mypy --strict clean; ruff lint+format clean; grace-lint 0/0; coverage threshold preserved (≥80%)."
  - id: NFR-3
    text: "All ru-only user-facing strings; no English leak."
  - id: NFR-4
    text: "Log markers: tg.pipeline.document.{received, dedup_hit, rejected, routed_text, routed_image, routed_pdf, pdf_no_text} with hashed_filename + mime + size + sha256_short fields."
  - id: NFR-5
    text: "Datetime UTC; existing IdempotencyService TTL (30d per D-018) reused."
constraints:
  - "Do NOT add OCR/image-conversion dependencies (no pytesseract, no pdf2image) — out of D-022 scope."
  - "WikiRunner.run currently accepts text only; no Protocol change in this chunk."
  - "PhotoIngestor.handle accepts only {jpeg, jpg, png, webp}; image/* mimes outside this set → reject as unsupported (NOT image branch)."
  - "Filename PII tier-2 hash applied in logs only; not in storage path (stage_media uses sha256 prefix, not filename)."
dependencies:
  - "M-TG-PIPELINE-CLASSIFIER (chunk 20) — _run_text_pipeline reuse."
  - "M-INBOX (idempotency.IdempotencyService.check_content + record_dedup_choice)."
  - "M-OPS-PII (PIIRedactor.hash_token, default singleton)."
  - "M-TG-MEDIA (PhotoIngestor for image/* branch)."
  - "pypdf library (new dependency, pure-python, MIT)."
risks:
  - id: R-1
    text: "Scanned-only PDFs have no extractable text → user confusion."
    mitigation: "Reject with explicit ru-only hint suggesting photo upload."
  - id: R-2
    text: "Mime mismatch (e.g. filename .pdf but mime=text/plain)."
    mitigation: "Trust mime parameter (TG-side detection); filename is UX-only and hashed in logs."
  - id: R-3
    text: "Oversized PDF could blow memory during pypdf parse."
    mitigation: "Cap MAX_DOC_BYTES at 25 MB (configurable); reject larger as unsupported with ru-only message."
  - id: R-4
    text: "Corrupted/encrypted PDF causes pypdf exception."
    mitigation: "Catch pypdf.errors.PdfReadError + generic Exception in narrow try/except; log tg.pipeline.document.pdf_parse_error; ack ACK_DOC_UNSUPPORTED_RU."
  - id: R-5
    text: "Empty filename / no extension."
    mitigation: "Handler defaults to 'unnamed'; hash_token handles any string uniformly."
scope_in:
  - "src/ai_steward_wiki/tg/pipeline.py — on_document rewrite with router + branches."
  - "Pyproject pypdf>=4.0 pin + uv.lock refresh."
  - "MODULE_CONTRACT update for M-TG-PIPELINE (extend SCOPE: document routing)."
  - "MODULE_CONTRACT new for M-TG-DOCUMENT-FULL (logical sub-module documented in pipeline.py header)."
  - "tests/unit/tg/test_pipeline_document.py (new file, ≥8 cases)."
  - "knowledge-graph.xml entry for M-TG-DOCUMENT-FULL."
  - "verification-plan.xml entries for new tests."
scope_out:
  - "WikiRunner Protocol change to accept MediaRef (deferred — would require runner+vision rework, separate chunk)."
  - "Real OCR (pytesseract / Tesseract) — out of D-022 scope."
  - "PDF→image rasterization (pdf2image / poppler) — out of scope."
  - "OCR for scanned-only PDFs — rejected with hint (out of scope)."
  - "Document type detection beyond mime (magic bytes) — trust TG mime."
  - "Multi-page PDF page-by-page handling — extract concatenated text or first N pages (decide in design)."
scope_later:
  - "WikiRunner MediaRef support → real vision-OCR for image/PDF (post-launch chunk)."
  - "Tier-3 PII (full content redaction) — D-019."
verification_strategy:
  - "Unit tests with FakeSender, FakeIdempotencyService, FakeClassifier+FakeRunner+FakeDelivery, FakePhotoIngestor, FakePIIRedactor."
  - "PDF fixture: 1-page text PDF (generated inline via reportlab in conftest or static fixture under tests/fixtures/)."
  - "Edge: empty PDF, encrypted PDF, oversized binary, UTF-8 BOM .txt, non-UTF-8 .txt."
preflight:
  pre_commit: "configured (.pre-commit-config.yaml present, hookspath=.beads/hooks coexists)"
  lint_baseline: "make lint green at HEAD 0065a76 (per project status)"
  sentrux: "not_onboarded (no .sentrux/rules.toml)"
---

# Discovery — chunk 22 M-TG-DOCUMENT-FULL

## Intent

Document handler is the last category-level gap before launch. `DefaultPipeline.on_document` currently L1-dedups, logs, and acks with `ACK_DOC_RU = "Файл получен."` — it never invokes the classifier, runner, or stages the file. This chunk replaces the ack with mime-based routing per DEC-L3 and closes the launch gate from breakdown chunk 22.

## Current state (evidence)

`src/ai_steward_wiki/tg/pipeline.py:501-524` — ack-only handler. `src/ai_steward_wiki/tg/handlers.py:144-165` — aiogram handler downloads bytes and supplies `mime=doc.mime_type or "application/octet-stream"`, `filename=doc.file_name or "unnamed"`.

## Real interpretation of DEC-L3 (PDF branch)

DEC-L3 phrasing "pdf → OCR stage (reuse photo OCR path)" collides with two existing realities:

1. **D-022** forbids `pytesseract` and uses Claude vision exclusively for image OCR.
2. **WikiRunner.run** at `src/ai_steward_wiki/tg/pipeline.py:123-134` accepts `text: str` only — no `MediaRef` parameter, no media support.
3. **PhotoIngestor.handle** at `src/ai_steward_wiki/tg/photo.py` stages bytes for a media-aware runner that does not yet exist; the current photo path is itself ack-only.

Therefore the literal "PDF → vision → runner reply" path requires runner-level changes outside chunk 22's `est_window=0.35` budget. Pragmatic interpretation chosen here:

- **PDF text-extraction via pypdf** (pure-python, no system deps). For text-bearing PDFs (invoices, articles, contracts — the 80% case) extracted text routes through the existing `_run_text_pipeline` (classifier → inbox → runner → deliver).
- **Scanned-only PDFs** (no extractable text) reject with explicit ru-only hint: *"Не вижу текста в PDF, попробуйте отправить страницу как фото."*
- **Real vision-OCR for PDFs/images** is deferred to a follow-up chunk that extends WikiRunner with MediaRef support.

This satisfies exit criterion #1 ("PDF upload → OCR → Inbox staged → WikiRunner reply") for the dominant case and provides a graceful path for the edge case.

## Branch summary

| mime | path | runner |
| --- | --- | --- |
| `application/pdf` | pypdf text-extract → `_run_text_pipeline` | yes (text intent) |
| `text/*` | UTF-8 decode (BOM-tolerant) → `_run_text_pipeline` | yes (text intent) |
| `image/{jpeg,png,webp}` | `PhotoIngestor.handle` (existing) | no — stage-then-ack (deferred to media-runner chunk) |
| other (incl. `application/octet-stream`, `image/heic`) | reject ru-only | no |

## L2 dedup

Pre-branch: `IdempotencyService.check_content(owner_telegram_id, kind="file", payload=doc_bytes)` → if match, send `ACK_DEDUP_RU`, call `record_dedup_choice(...action="duplicate_doc")`, emit `tg.pipeline.document.dedup_hit`, return. Otherwise stash sha256 for downstream log lines.

## PII (tier-2 filename hash)

All log lines use `hashed_filename = PIIRedactor.hash_token(filename.lower())` instead of raw filename. Raw filename never appears in structlog events. Storage paths via `stage_media` already use sha256 prefix and are PII-safe.

## Open questions resolved in Discovery

1. **MAX_DOC_BYTES** — 25 MB cap (mirrors TG document upload practical ceiling; protects pypdf memory). Configurable via `settings.tg.max_doc_bytes` if Settings supports it; otherwise module-level constant.
2. **Multi-page PDFs** — extract all pages, concatenate with `\n\n` page separators, cap result at 50_000 chars (truncate with `…\n[truncated]` suffix) to keep classifier+runner prompt manageable.
3. **Mime mismatch** — trust `mime` parameter from aiogram exclusively; filename is logged (hashed) for forensics but not used for routing.

## Best practices applied

- Fail-fast at boundaries (size cap, mime allowlist).
- Reuse existing pipelines (`_run_text_pipeline`, `PhotoIngestor.handle`) — composition over duplication.
- ru-only user-facing strings (D-032).
- Structured logging with PII-safe identifiers.
- Single new dependency (pypdf), pure-python, well-maintained.

## Stakeholders

- End-users (Геннадий, Татьяна, future onboarded users) — receive PDFs/text files/images via TG, expect responsive routing.
- Operations (Геннадий as admin) — audit log must trace document flow without PII leak.
