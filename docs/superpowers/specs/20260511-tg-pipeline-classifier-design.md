---
feature: tg-pipeline-classifier
chunk_id: 20
bd_id: aisw-96y
module_id: M-TG-PIPELINE-CLASSIFIER
status: approved
created_utc: 2026-05-11
discovery_ref: docs/superpowers/specs/20260511-tg-pipeline-classifier-discovery.md
stack:
  language: Python 3.11+
  frameworks_used:
    - aiogram 3.x (existing — no version bump)
    - pydantic v2 (existing)
    - structlog (existing)
    - sqlalchemy.async (existing)
  new_libraries: []
  decisions:
    - id: DEC-TPC-1
      title: Three thin Protocols in tg/pipeline.py (Classifier, WikiRunner, OutputDelivery)
      choice: Define narrow Protocol surfaces inside tg/pipeline.py; concrete adapters wrapping classify()/run_wiki_session()/deliver_output() live in __main__.py.
      alternatives_considered:
        - "Direct injection of low-level callables — rejected: leaks disk paths and audit_session_maker into pipeline; explodes ctor arg count."
        - "Cross-module adapter classes in dedicated tg/adapters.py — rejected: premature factoring for 3 small adapters used only at composition root; deferred until 2nd reuse case."
      rationale: Mirrors existing pattern (TgSender, Spawner, HaikuSummarizer Protocols). DefaultPipeline stays test-friendly with trivial fakes. Adapters in __main__.py keep all I/O concerns at composition root, per GRACE rule on module boundaries.
    - id: DEC-TPC-2
      title: WikiRunner Protocol returns aggregated text, not events list
      choice: '`WikiRunner.run(...) -> WikiRunOutcome(run_id: str, text: str, latency_ms: int)`. Adapter extracts assistant text from `WikiRunResult.events` by concatenating `assistant_chunk` payload content fields.'
      alternatives_considered:
        - "Pass raw WikiRunResult through — rejected: couples DefaultPipeline to streaming event schema."
        - "Add aggregate_text(events) helper to wiki.runner and reuse — accepted for adapter implementation but kept out of pipeline.py contract."
      rationale: Pipeline cares about text-out only; events are an implementation detail of Stage-1 runner.
    - id: DEC-TPC-3
      title: L2 dedup uses existing IdempotencyService.check_content (no new Inbox API)
      choice: Pipeline injects IdempotencyService directly (already does for L1). For text it calls check_content(telegram_id, "text", text); on collision short-circuits with ru-only "уже видел такое" reply and records the L2 hit via record_dedup_choice(... action="auto_skip").
      alternatives_considered:
        - "Add stage_text / stage_voice helpers to inbox/staging.py — rejected: not needed; would create surface that has only one caller."
      rationale: breakdown.xml chunk-20 phrasing maps to existing audit.seen_files semantics. Anti-hallucination — names match real code.
    - id: DEC-TPC-4
      title: Error handling at pipeline boundary — never propagate
      choice: try/except around each of (classify, runner.run, output.deliver). Each except branch logs (structlog .exception), emits one ru-only safe ack via sender, and returns. No raise.
      alternatives_considered:
        - "Let aiogram's outer error middleware handle — rejected: middleware swallow + retry is a footgun for already-deduplicated updates."
      rationale: User must always get a reply; chat UX requires single-message terminal state.
    - id: DEC-TPC-5
      title: Voice path = stage_media (existing) + STT (existing) + classifier+runner+deliver on transcript
      choice: VoiceHandler.handle remains unchanged. After transcript obtained, run check_content with kind="text" on transcript.text; then classifier; then runner; then deliver. If transcript.text is empty → fallback ack ACK_TEXT_RU.
      alternatives_considered:
        - "Dedup on raw audio bytes (kind='voice') — accepted as L2 layer-A but kept separate from this chunk to avoid double-dedup edge cases. Transcript-level dedup covers the user-perceived duplicate case (re-record of same words)."
      rationale: Same Claude pipeline is reused; voice is just text after STT.
    - id: DEC-TPC-6
      title: Adapters wire wiki_path resolution
      choice: WikiRunner adapter resolves wiki_path via telegram_id → owner profile (existing convention in this repo: `/var/lib/ai-steward-wiki/wikis/<telegram_id>/`). For MVP-launch (single dev user) we hard-resolve from Settings.wikis_root / str(telegram_id). Multi-domain routing per D-029 stays deferred.
      alternatives_considered:
        - "Inject a WikiPathResolver — rejected: premature, no second consumer yet."
      rationale: Resolves only one path today; one-line constant function.
log_markers:
  - tg.pipeline.classify.begin {correlation_id, telegram_id, chars}
  - tg.pipeline.classify.done {correlation_id, telegram_id, intent, confidence, latency_ms}
  - tg.pipeline.inbox.l2_dedup_hit {correlation_id, telegram_id, kind, sha8}
  - tg.pipeline.runner.dispatched {correlation_id, telegram_id, run_id, intent}
  - tg.pipeline.runner.completed {correlation_id, telegram_id, run_id, exit_code, chars, latency_ms}
  - tg.pipeline.deliver.sent {correlation_id, telegram_id, run_id, n_messages, output_bytes}
  - tg.pipeline.classify.error {correlation_id, telegram_id, error_class}
  - tg.pipeline.runner.error {correlation_id, telegram_id, run_id, error_class}
modules_touched:
  - src/ai_steward_wiki/tg/pipeline.py  # add 3 Protocols + ctor params + new on_text/on_voice
  - src/ai_steward_wiki/__main__.py     # build adapters + pass to DefaultPipeline
  - src/ai_steward_wiki/wiki/runner.py  # add aggregate_text(events) helper (small, doc'd)
  - tests/unit/test_pipeline_classifier_wiring.py  # new
  - tests/integration/test_pipeline_classifier_e2e.py  # new (RUN_INTEGRATION-gated)
---

# Design — chunk 20 (M-TG-PIPELINE-CLASSIFIER)

## 1. Architecture

```
aiogram handler (tg/handlers.py)
        │
        ▼
DefaultPipeline.on_text(text)
    │ 1. IdempotencyService.check_update_id  (L1 — existing)
    │ 2. IdempotencyService.check_content("text", text)  (L2 — text)
    │      └─ hit → log + ru-only reply + record_dedup_choice → return
    │ 3. classifier.classify(text) -> ClassifierResult
    │      └─ except ClassifierError → log + safe ack → return
    │ 4. runner.run(text, telegram_id, intent) -> WikiRunOutcome
    │      └─ except WikiRunnerError → log + safe ack → return
    │ 5. output.deliver(chat_id, telegram_id, run_id, text)
    │      └─ returns DeliveryReceipt; log + return
    ▼
TgSender.send_message  (HTTP to TG API)
```

Voice path identical from step 3, after VoiceHandler.handle produces transcript.

## 2. New Protocols in tg/pipeline.py

```python
class Classifier(Protocol):
    async def classify(self, text: str, *, correlation_id: str) -> ClassifierResult: ...

@dataclass(frozen=True)
class WikiRunOutcome:
    run_id: str
    text: str
    latency_ms: int

class WikiRunner(Protocol):
    async def run(
        self,
        *,
        text: str,
        owner_telegram_id: int,
        correlation_id: str,
        intent: Intent,
    ) -> WikiRunOutcome: ...

class OutputDelivery(Protocol):
    async def deliver(
        self,
        *,
        chat_id: int,
        telegram_id: int,
        run_id: str,
        text: str,
    ) -> None: ...
```

## 3. DefaultPipeline ctor

Existing required: sender, idempotency, confirmation. Existing optional: voice, photo.
**New optional:** `classifier: Classifier | None = None`, `runner: WikiRunner | None = None`, `output: OutputDelivery | None = None`.

Back-compat: when any of the three is None → fall back to current ack. This keeps chunk-19 unit tests green (they construct DefaultPipeline without the new triple).

## 4. Adapter sketches (`__main__.py`)

```python
class _ClassifierAdapter:
    def __init__(self, backend, prompt_path, audit_maker, cache):
        ...
    async def classify(self, text, *, correlation_id):
        async with self._audit_maker() as session:
            return await classify(text, correlation_id=correlation_id,
                                  backend=self._backend, prompt_path=self._prompt_path,
                                  audit_session=session, cache=self._cache)

class _WikiRunnerAdapter:
    def __init__(self, wikis_root, base_prompt, overlay_prompt, runtime_dir, acquirer, spawner, claude_config_dir):
        ...
    async def run(self, *, text, owner_telegram_id, correlation_id, intent):
        wiki_path = self._wikis_root / str(owner_telegram_id)
        run_id = f"run-{uuid4().hex[:12]}"
        result = await run_wiki_session(
            wiki_id=str(owner_telegram_id),
            wiki_path=wiki_path,
            base_prompt_path=self._base_prompt,
            overlay_prompt_path=self._overlay_prompt,
            run_id=run_id,
            correlation_id=correlation_id,
            runtime_dir=self._runtime_dir,
            acquirer=self._acquirer,
            spawner=self._spawner,
            config=_RunConfig(claude_config_dir=self._claude_config_dir),
        )
        return WikiRunOutcome(run_id=run_id,
                              text=aggregate_text(result.events),
                              latency_ms=result.latency_ms)

class _OutputDeliveryAdapter:
    def __init__(self, sender, runs_dir, wiki_id_for, audit_maker):
        ...
    async def deliver(self, *, chat_id, telegram_id, run_id, text):
        await deliver_output(
            sender=self._sender, chat_id=chat_id, telegram_id=telegram_id,
            wiki_id=self._wiki_id_for(telegram_id),
            run_id=run_id, text=text,
            runs_dir=self._runs_dir, audit_session_maker=self._audit_maker,
        )
```

## 5. `aggregate_text(events)` helper (`wiki/runner.py`)

```python
def aggregate_text(events: list[StreamEvent]) -> str:
    """Concatenate assistant text content from a stream-json event list.

    Pulls `payload.message.content[*].text` for events of type 'assistant_chunk'.
    Returns "" if no assistant text is present (caller decides fallback).
    """
```

Pure function. Unit test on isolation.

## 6. UX strings (ru-only)

```python
ACK_DEDUP_RU = "Уже видел такое сообщение — повторно не запускаю."
ACK_CLASSIFY_ERR_RU = "Не удалось распознать запрос, попробуйте ещё раз."
ACK_RUNNER_ERR_RU = "Задача заняла слишком много времени, попробуйте позже."
```

## 7. Verification plan delta

`docs/verification-plan.xml` — add `Phase-20` test entries pointing to the two new test files and the eight log markers above.

## 8. Open questions

None — adapter wiring is fully resolved.

## 9. Status

Approved (auto-approve gate per memory feedback_auto_approve_gates).
