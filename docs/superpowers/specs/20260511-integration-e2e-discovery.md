---
feature: integration-e2e
bd_id: aisw-vb9
module_id: M-INTEGRATION-E2E
date: 2026-05-11
chunk: 23
ssot: docs/superpowers/plans/20260511-ai-steward-wiki-launch/breakdown.xml chunk-23
status: stable
fr:
  - id: FR-1
    text: "Integration suite spins DefaultPipeline with real Claude CLI classifier and verifies on_text → classify → runner → deliver path under RUN_INTEGRATION=1 gate"
  - id: FR-2
    text: "Voice scenario: STT result is injected into pipeline (fake transcriber), then real classifier resolves intent, fake runner+output assert delivery"
  - id: FR-3
    text: "Photo+confirm scenario: fake OCR injects text, pipeline emits confirm prompt, callback resolve path completes the turn"
  - id: FR-4
    text: "PDF document scenario: on_document with application/pdf MIME triggers OCR fake → classify → runner → deliver"
  - id: FR-5
    text: "make integration target runs the suite via uv run pytest tests/integration with RUN_INTEGRATION=1"
  - id: FR-6
    text: "runbook/operations.md documents nightly manual invocation pattern (no CI auto-trigger)"
nfr:
  - id: NFR-1
    text: "Full suite completes ≤180s on dev VPS"
  - id: NFR-2
    text: "Suite is opt-in: passes silently (skipped) when RUN_INTEGRATION=1 absent or `claude` binary missing"
  - id: NFR-3
    text: "No real network egress to Telegram (FakeAiogramBot captures send_message/edit/send_document calls)"
  - id: NFR-4
    text: "Idempotency DB uses tmp_path per test; no shared state"
risks:
  - id: RISK-1
    text: "Real Claude CLI latency spikes blow the 180s budget"
    mitigation: "Cache classifier prompt; cap scenarios to 4; one classify call per scenario; fake the wiki runner to avoid second CLI round"
  - id: RISK-2
    text: "Test pollution between runs via shared sqlite jobs/audit DBs"
    mitigation: "Use tmp_path-scoped sqlite URLs in fixtures; never touch /var/lib/ai-steward-wiki"
  - id: RISK-3
    text: "Existing test_pipeline_classifier_e2e.py uses RUN_CLAUDE_CLI_INTEGRATION gate, Makefile uses RUN_INTEGRATION — double gate makes nothing run"
    mitigation: "New suite gates on RUN_INTEGRATION=1 (matches Makefile); existing chunk-20 test keeps its dual gate for backward compat OR migrate it"
scope:
  in:
    - "tests/integration/test_e2e_pipeline.py — 4 scenarios"
    - "FakeAiogramBot stub satisfying TgSender protocol (extends existing FakeSender pattern)"
    - "Fakes for VoiceHandler (transcribe) and PhotoIngestor (OCR)"
    - "tests/integration/conftest.py with shared fixtures (tmp DB paths, prompt cache)"
    - "Makefile: keep make test-integration; ensure it picks up new file"
    - "runbook/operations.md: append §Integration testing"
  out:
    - "Real runner CLI invocation (Stage-2 wiki agent) — mocked to control latency"
    - "Real Telegram API calls — FakeAiogramBot only"
    - "CI auto-trigger — manual nightly per breakdown.xml"
    - "Cutover checklist execution — separate gate"
references:
  - "tests/integration/test_pipeline_classifier_e2e.py (chunk-20 era reference)"
  - "tests/integration/classifier/test_real_cli.py (raw CLI test)"
  - "src/ai_steward_wiki/tg/pipeline.py DefaultPipeline.__init__ signature"
  - "src/ai_steward_wiki/tg/bot.py TgSender protocol"
---

# Integration E2E suite — Discovery

## Intent

Final safety net before production cutover. Unit tests cover composition with mocks; this suite exercises the **same DefaultPipeline composition** against the **real Claude CLI classifier** through 4 representative user-facing paths.

## Why now

Chunks 20–22 closed: classifier+runner+deliver wired, streaming editor live, document MIME routing complete. Cutover checklist begins after chunk 23 closes. Without integration evidence, cutover is blind.

## Approach summary

1. **Real components:** Classifier (`ClaudeCliBackend` → `claude` binary), DefaultPipeline composition, idempotency (sqlite in tmp), confirmation service (sqlite in tmp), structlog.
2. **Fakes:** WikiRunner (returns canned `WikiRunOutcome`), OutputDelivery (records calls), VoiceHandler (returns canned transcript), PhotoIngestor (returns canned OCR), TgSender (FakeAiogramBot recording sends/edits/docs).
3. **Gate:** `RUN_INTEGRATION=1` + `shutil.which("claude")` skip-if. Matches Makefile target.
4. **Existing chunk-20 test** uses `RUN_CLAUDE_CLI_INTEGRATION` — migrate it to `RUN_INTEGRATION=1` to unify gates (one gate, one Makefile entry).

## Open questions

None — scope is bounded by breakdown.xml chunk-23 spec. Auto-approving per memory.
