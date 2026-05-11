---
feature: integration-e2e
bd_id: aisw-vb9
module_id: M-INTEGRATION-E2E
date: 2026-05-11
status: stable
stack:
  - "pytest 8.3 + pytest-asyncio (auto mode) — already in pyproject"
  - "aiosqlite + SQLAlchemy async — for tmp-path jobs/audit DBs"
  - "structlog — observability, default config"
  - "real ClaudeCliBackend → claude binary on PATH"
contracts:
  - "tests/integration/conftest.py — fixtures (audit_sm, jobs_sm, fake_aiogram_bot, fake_runner, fake_output, fake_voice, fake_photo, real_classifier_adapter, inbox_root)"
  - "tests/integration/test_e2e_pipeline.py — 4 scenarios"
---

# Integration E2E suite — Design

## Architecture

```
tests/integration/
├── conftest.py                  # NEW — shared fixtures + FakeAiogramBot
├── test_e2e_pipeline.py         # NEW — 4 scenarios (chunk 23)
├── test_pipeline_classifier_e2e.py  # MIGRATE gate → RUN_INTEGRATION=1
├── classifier/test_real_cli.py  # MIGRATE gate → RUN_INTEGRATION=1
```

### FakeAiogramBot

Reuses `tests/unit/tg/conftest.py::FakeSender` pattern (already satisfies `TgSender`). Re-exported from integration conftest as `FakeAiogramBot` for naming clarity; one canonical implementation, two import aliases — no fork.

### Fixtures (pytest function-scoped)

1. `audit_sm` — async_sessionmaker over `sqlite+aiosqlite:///{tmp_path}/audit.db`, with Alembic upgrade head against `alembic/audit/alembic.ini`.
2. `jobs_sm` — same for `alembic/jobs/alembic.ini` → `jobs.db`.
3. `inbox_root` — `tmp_path / "inbox"` (pre-created).
4. `idempotency` — `IdempotencyService(audit_sm)`.
5. `confirmation` — `ConfirmationService(fake_bot, jobs_sm)`.
6. `fake_runner` — `MagicMock` with `run` returning a canned `WikiRunOutcome`.
7. `fake_output` — `MagicMock` with awaited `deliver`.
8. `fake_voice` — `VoiceHandler(stub_transcriber, inbox_root)` where `stub_transcriber.transcribe` returns a fixed `Transcript`.
9. `fake_photo` — real `PhotoIngestor(inbox_root=inbox_root)` (cheap, pure-FS, no OCR).
10. `real_classifier` — `_RealClassifierAdapter` (taken from existing chunk-20 test, lifted into conftest).
11. `pipeline` — `DefaultPipeline(sender=fake_bot, idempotency=idem, confirmation=confirm, voice=fake_voice, photo=fake_photo, classifier=real_classifier, runner=fake_runner, output=fake_output)`.

### Scenarios

1. **test_text_turn_end_to_end** — `pipe.on_text(...)` with Russian reminder text → assert `fake_runner.run` awaited with `intent` of `Intent` type + `fake_output.deliver` awaited.
2. **test_voice_turn_end_to_end** — `pipe.on_voice(audio_bytes=b"fake-ogg")` → fake transcriber returns canned text → real classifier → fake runner → fake output. Asserts `runner.run.await_count == 1`.
3. **test_photo_then_confirm_callback** — `pipe.on_photo(photo_bytes=PNG_BYTES, mime="image/png")` → PhotoIngestor stages real file in `inbox_root`, ack sent. Then `pipe.on_confirm_callback(pending_id=..., action="confirm")` → `ConfirmationService.resolve` invoked (probably returns "no_pending"/None given no preceding explicit confirm flow — assertion focuses on **no exception** + log line emitted, since photo path doesn't create a pending row).
4. **test_pdf_document_end_to_end** — minimal valid PDF bytes (≤1 page hello-world built with pypdf or a static fixture file) → `pipe.on_document(doc_bytes=PDF, mime="application/pdf", filename="note.pdf")` → `_extract_pdf_text` → classifier → runner → output.

### Gates

All scenarios share:
```python
pytestmark = [
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="set RUN_INTEGRATION=1"),
    pytest.mark.skipif(shutil.which("claude") is None, reason="claude binary missing"),
]
```

Existing two tests (`test_pipeline_classifier_e2e.py`, `classifier/test_real_cli.py`) migrate from `RUN_CLAUDE_CLI_INTEGRATION` → `RUN_INTEGRATION` for gate uniformity. This is a small semantic widening (recursive `claude` invocation no longer behind separate flag) — acceptable because the Makefile target was already gating on `RUN_INTEGRATION` and `claude`-binary presence is checked explicitly.

### Latency budget

| Scenario | Real CLI calls | Est. cost |
|----------|---------------|-----------|
| text     | 1 (classify)  | ~10–25s   |
| voice    | 1 (classify)  | ~10–25s   |
| photo+confirm | 0 (no classify on photo path) | ~0.2s |
| pdf      | 1 (classify)  | ~10–25s   |

Total ≈ 30–75s p50, ≤180s p99 budget. PromptCache shared across scenarios via fixture caching trims after first call.

### Trade-offs

| Option | Pro | Con | Choice |
|--------|-----|-----|--------|
| Real runner Stage-2 CLI | True E2E | Each scenario +30–90s, blows budget | Reject — keep runner fake |
| One unified gate `RUN_INTEGRATION` | Single Makefile knob, single doc | Loses recursive-CLI-only opt-in granularity | **Accept** (chunk-23 spec) |
| Separate fixture files per scenario | Cleaner isolation | Boilerplate | Reject — shared conftest |
| Use aiogram test-utils (`bot.session = MockedSession`) | Closer to real aiogram | Heavier; we only need TgSender protocol | Reject — FakeAiogramBot suffices |

### Decisions captured

- **DEC-E2E-1** — Runner fake, classifier real. *Rationale:* runner CLI cost is 3–5× classifier; spec wants "real Claude CLI" which is satisfied at Stage-0; runner determinism is owned by unit tests in tests/unit/wiki.
- **DEC-E2E-2** — Unified `RUN_INTEGRATION=1` gate. *Rationale:* single Makefile target, single runbook doc, fewer foot-guns.
- **DEC-E2E-3** — Reuse `FakeSender` from `tests/unit/tg/conftest.py` exported under alias `FakeAiogramBot`. *Rationale:* no fork; SSoT for the protocol stub.

No ADR required — these are scoped test-infra decisions.
