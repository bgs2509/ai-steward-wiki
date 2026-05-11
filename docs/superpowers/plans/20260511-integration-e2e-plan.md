# Integration E2E suite — Implementation plan

**bd_id:** aisw-vb9 · **chunk:** 23 · **SSoT:** breakdown.xml chunk-23
**Discovery:** docs/superpowers/specs/20260511-integration-e2e-discovery.md
**Design:** docs/superpowers/specs/20260511-integration-e2e-design.md

## Tasks

### T1 — RED: tests/integration/conftest.py with shared fixtures
Create `tests/integration/conftest.py` exporting `FakeAiogramBot` (re-export of `tests/unit/tg/conftest.py::FakeSender`) and async fixtures:
- `audit_sm`, `jobs_sm` — async_sessionmaker over tmp_path sqlite + Alembic upgrade head
- `inbox_root` — tmp_path/inbox
- `idempotency`, `confirmation` — services wired
- `fake_runner`, `fake_output` — AsyncMock-backed protocol stubs returning canned values
- `fake_voice` — VoiceHandler(stub_transcriber, inbox_root); stub_transcriber returns `Transcript(text="…", lang="ru", ...)`
- `fake_photo` — real PhotoIngestor(inbox_root)
- `real_classifier` — `_RealClassifierAdapter` (lifted from existing chunk-20 test)
- `pipeline` — fully-wired DefaultPipeline

Module-scoped `prompt_cache` for sharing PromptCache across scenarios.

### T2 — RED: tests/integration/test_e2e_pipeline.py with 4 scenarios
1. `test_text_turn_end_to_end` — real classifier on "напомни мне завтра в 9 утра позвонить маме"; assert fake_runner.run + fake_output.deliver awaited; intent is `Intent` instance.
2. `test_voice_turn_end_to_end` — `pipe.on_voice(audio_bytes=b"x")` with fake transcriber returning canned Russian; same downstream assertions.
3. `test_photo_then_confirm_callback` — `pipe.on_photo(photo_bytes=PNG_HEADER+b"…", mime="image/png")`; verify ack sent + file staged in inbox_root. Then seed a `PendingConfirm` via `ConfirmationService.request_explicit(...)` and call `pipe.on_confirm_callback(pending_id=row.pending_id, action="confirm")`; assert pending row flipped to status='confirmed' (read back via jobs_sm).
4. `test_pdf_document_end_to_end` — generate minimal one-page PDF with pypdf in-memory (or use static fixture), call `pipe.on_document(doc_bytes=PDF, mime="application/pdf", filename="note.pdf")`; assert classify/runner/deliver path.

All gated by `RUN_INTEGRATION=1` + `which("claude")`.

### T3 — REFACTOR: migrate existing tests to unified gate
- `tests/integration/test_pipeline_classifier_e2e.py`: `RUN_CLAUDE_CLI_INTEGRATION` → `RUN_INTEGRATION`.
- `tests/integration/classifier/test_real_cli.py`: same.

### T4 — Makefile sanity
Confirm `make test-integration` already runs `RUN_INTEGRATION=1 uv run pytest tests/integration -v` (it does — leave as-is).

### T5 — runbook/operations.md §Integration testing
Append section documenting:
- Gate: `RUN_INTEGRATION=1` + claude binary on PATH.
- Command: `make test-integration` (preferred) or `RUN_INTEGRATION=1 uv run pytest tests/integration -v`.
- Manual nightly cadence; no CI auto-trigger.
- Latency budget ≤180s.
- Required env: `CLAUDE_CONFIG_DIR` (subscription auth).

### T6 — make total-test + grace-refresh
- `make total-test` — lint, grace, inv-lint, coverage. If `total-test` requires `RUN_INTEGRATION=1`, run unit/lint subset only; integration covered separately.
- Actually: `make total-test` per Makefile already includes integration; for this verification run, skip if `claude` quota concerns. **Decision:** run `make lint` + `make grace-lint` + `RUN_INTEGRATION=1 make test-integration` (capped to new test file first to verify wiring).

### T7 — Completion artifacts
- `docs/reports/20260511-integration-e2e-report.md`.
- `grace-refresh` (full).
- `smart-commit` with `feat(M-INTEGRATION-E2E): wire integration E2E suite over real Claude CLI`.
- `bd close aisw-vb9`.
- Mark Phase-23 STATUS="done" in development-plan.xml.

## Verification

| Exit criterion | How verified |
|----------------|--------------|
| ≤180s suite | Wall-time of `make test-integration` |
| ≥4 green scenarios | pytest summary (4 passed) |
| make integration target | Already present + new test file picked up via pytest discovery |
| Nightly hook documented | runbook/operations.md §Integration testing |

## Risks during execution

- `claude` CLI quota — if exhausted, scenarios fail. Mitigation: PromptCache shared via module-scoped fixture; one classify per scenario; reuse cache hits across runs.
- Alembic env.py expects env vars — check `alembic/*/env.py` reads DSN from env vs ini; may need to monkeypatch URL.
