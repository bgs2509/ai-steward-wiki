---
feature: tg-pipeline-classifier
chunk_id: 20
bd_id: aisw-96y
module_id: M-TG-PIPELINE-CLASSIFIER
status: approved
created_utc: 2026-05-11
predecessors: [aisw-ps8 (chunk 19 M-TG-HANDLERS-WIRING)]
ssot_breakdown: docs/superpowers/plans/20260511-ai-steward-wiki-launch/breakdown.xml#chunk-20
requirements:
  functional:
    - FR-1: DefaultPipeline.on_text invokes Classifier→Inbox L2 dedup→WikiRunner→deliver_output once per unique text turn.
    - FR-2: DefaultPipeline.on_voice runs VoiceHandler (STT), then classifier+runner+deliver on the transcript with L2 dedup on transcript-sha256.
    - FR-3: L2 dedup hit at Inbox (existing IdempotencyService.check_content for kind="text") short-circuits the pipeline before WikiRunner is dispatched; user receives a ru-only "уже видел такое" reply (no second spawn).
    - FR-4: Classifier/WikiRunner/OutputDelivery are injected as Protocols into DefaultPipeline; passing None for any of them falls back to the current ack behavior (back-compat for tests and partial deployments).
    - FR-5: __main__.py constructs concrete Classifier/WikiRunner/OutputDelivery adapters from existing low-level functions (classify, run_wiki_session, deliver_output) and passes them to DefaultPipeline.
    - FR-6: Emit log markers tg.pipeline.classify.begin, tg.pipeline.classify.done, tg.pipeline.inbox.l2_dedup_hit, tg.pipeline.runner.dispatched, tg.pipeline.deliver.sent — each carrying correlation_id, telegram_id, chat_id, intent (when known).
    - FR-7: Classifier error → audit log + ru-only safe ack ("не удалось распознать запрос, попробуйте ещё раз"). No exception propagates to aiogram.
    - FR-8: WikiRunner timeout/error → audit log + ru-only safe ack ("задача заняла слишком много времени"). No exception propagates to aiogram.
  non_functional:
    - NFR-1: Test coverage ≥80% on tg/pipeline.py for new branches; ≥10 unit tests added.
    - NFR-2: mypy --strict clean, ruff/ruff-format clean, grace-lint 0 errors / 0 warnings, INV 14/14.
    - NFR-3: One nightly integration test (RUN_INTEGRATION=1) drives DefaultPipeline against real Claude CLI and asserts non-empty reply within 60s.
    - NFR-4: All datetime in DB UTC; user-facing strings ru-only (D-032).
    - NFR-5: structlog events carry correlation_id, telegram_id, intent fields where available.
  constraints:
    - CONS-1: No external library additions beyond existing pyproject deps.
    - CONS-2: Streaming edits (StreamEditor wrapping) are deferred to chunk-21 per DEC-L2 (>5s threshold).
    - CONS-3: Document mime-routing is deferred to chunk-22 per DEC-L3 (on_document body left as today's ack).
    - CONS-4: WikiRunner public Protocol must accept aggregated `text` output, hiding the raw events list from DefaultPipeline.
  risks:
    - R-1: WikiRunResult exposes events: list[StreamEvent], not aggregated text. Mitigation: concrete WikiRunner adapter extracts assistant text from `assistant_chunk` payload events; covered by unit test on extraction helper.
    - R-2: run_wiki_session requires wiki_path/prompt paths/acquirer/spawner — DefaultPipeline must not see these. Mitigation: hide them behind a thin Protocol/adapter constructed in __main__.py.
    - R-3: Classifier prompt_path + backend wiring is non-trivial. Mitigation: ClassifierAdapter wraps classify(...) with pre-bound prompt_path + backend + audit_session, exposes only `classify(text, *, correlation_id)`.
    - R-4: deliver_output needs runs_dir + audit_session_maker per call. Mitigation: OutputDeliveryAdapter pre-binds these.
    - R-5: L2 dedup on voice transcript uses transcript text (already-normalized via classifier consumer); identical re-uploads of the same audio file may produce slightly different transcripts. Acceptable for MVP — text L2 covers identical text, voice bytes L2 (kind="voice") could be added later.
  scope_in:
    - tg/pipeline.py: add Classifier/WikiRunner/OutputDelivery Protocols + DefaultPipeline ctor params + new on_text/on_voice bodies.
    - __main__.py: build adapters and pass to DefaultPipeline.
    - tests/unit/test_pipeline_classifier_wiring.py: ≥10 new tests (happy path, L1 dedup, L2 dedup hit, classifier error, runner error, voice happy, voice classifier error, intent UNKNOWN handling, idempotent same-text twice, log marker emission).
    - tests/integration/test_pipeline_classifier_e2e.py: RUN_INTEGRATION=1 gate, one happy-path text turn against real Claude CLI.
    - prompts/classifier.md: ensure exists (already in repo).
    - MODULE_CONTRACT updates in tg/pipeline.py + __main__.py.
  scope_out:
    - Streaming edits (chunk 21).
    - Document mime routing (chunk 22).
    - Integration suite covering all categories (chunk 23).
    - Changes to classify(), run_wiki_session(), deliver_output() internals — wrap only.
  open_questions: []
preflight:
  pre_commit_hooks: ok (.git/hooks/pre-commit exists, hooksPath=.beads/hooks; .pre-commit-config.yaml present)
  lint_baseline: green (ruff check + ruff format + mypy clean, all 64 src files)
  sentrux: not applicable (.sentrux/rules.toml absent — project not onboarded)
---

# Discovery — chunk 20 (M-TG-PIPELINE-CLASSIFIER)

## 1. Intent

Bot today acks every text/voice message but never invokes Claude. This chunk closes the core MVP-launch gap by wiring the three pre-built pieces (Stage-0 classifier, Stage-1a/1b runner, hybrid deliver_output) into DefaultPipeline so a `привет` actually reaches Claude and gets a real reply.

## 2. Real-state findings vs breakdown.xml

The breakdown.xml chunk-20 scope mentions `Inbox.stage_text` / `Inbox.stage_voice`. **These named functions do not exist.** The intended semantics map to existing code:

1. Text dedup → `IdempotencyService.check_content(owner_telegram_id, "text", text)` (audit.db seen_files; L2 via SHA-256 of NFKC-normalized text).
2. Voice staging → `stage_media(...)` (existing, already invoked by VoiceHandler). L2 dedup on the *transcript* via the same `check_content` with kind="text".

So no new Inbox surface is required for chunk-20 — composition is over `IdempotencyService.check_content`, not a fictitious `stage_text`. This finding is recorded explicitly so future readers don't search for `stage_text`.

## 3. Adapter pattern (key design seed)

Three concrete library APIs have wide signatures (classify needs prompt_path+backend; run_wiki_session needs wiki_path+acquirer+spawner+runtime_dir; deliver_output needs runs_dir+audit_session_maker). DefaultPipeline cannot accept these — would couple it to disk paths and DB makers. Solution: a thin **Adapter / Facade** layer that pre-binds long-tail dependencies and exposes narrow Protocols (Classifier / WikiRunner / OutputDelivery) — built in `__main__.py`, mocked in unit tests.

This is the only architecturally novel decision in chunk-20; the rest is plumbing.

## 4. Best practices (research-mode)

1. **Adapter / Facade over wide library APIs at injection seams.** Classic GoF; explicit recommendation in Hexagonal/Ports&Adapters literature. Aligns with our existing pattern (MessagePipeline Protocol in tg/pipeline.py; AsyncioSpawner in wiki/runner.py).
2. **Errors at composition boundaries map to user-facing safe replies, not exceptions.** Industry convention for chat bots; matches GRACE Rule 4 (verification: log evidence on failure, never surface a 500 to user).
3. **L2 dedup before expensive call.** Standard ingest pattern — call cost (Claude Sonnet ~30s) >> dedup cost (sqlite INSERT OR IGNORE ~1ms).
4. **Test seams via Protocols, not subclassing.** Already used throughout this codebase (TgSender, Spawner, HaikuSummarizer); chunk-20 follows the same convention.

## 5. Scope boundary check

In: pipeline composition + adapters + tests.
Deferred: streaming (21), documents (22), integration suite (23), cutover (post-23).

No new modules, no new DB tables, no new prompts, no new external dependencies.

## 6. Verification intent

Tests prove:

1. Happy path text → Classifier called once, Inbox L2 checked once, WikiRunner dispatched once, deliver_output called once, ru-only reply sent.
2. L1 duplicate update_id → none of the four are called.
3. L2 duplicate text → Classifier called (cheap), L2 hit, WikiRunner NOT called, "уже видел такое" reply.
4. Classifier raises → safe ack, audit log, no runner call.
5. WikiRunner raises → safe ack, audit log, no deliver call.
6. Voice happy path → VoiceHandler.handle → transcript → classifier → runner → deliver.
7. Voice transcript empty → ack fallback (current ACK_TEXT_RU).
8. Back-compat: classifier=None → current ack behavior (existing chunk-19 tests still green).
9. Log markers emitted at each stage with correlation_id.
10. Integration (RUN_INTEGRATION=1) → real Claude CLI returns non-empty reply.

## 7. Status

Approved (auto-approve gate per memory feedback_auto_approve_gates).
