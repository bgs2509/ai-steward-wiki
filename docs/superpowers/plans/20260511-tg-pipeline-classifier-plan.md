# Implementation Plan — chunk 20 (M-TG-PIPELINE-CLASSIFIER)

> bd_id: aisw-96y · status: approved · ssot: breakdown.xml#chunk-20
> Sources: discovery 20260511-tg-pipeline-classifier-discovery.md, design 20260511-tg-pipeline-classifier-design.md

## Sequencing

1. **Test scaffolding (RED)** — write `tests/unit/test_pipeline_classifier_wiring.py` with fakes for Classifier/WikiRunner/OutputDelivery, 10 tests covering: happy text, L1 dup, L2 dup, classifier error, runner error, output error, voice happy, voice empty transcript, back-compat None classifier, log markers.
2. **Add `aggregate_text` helper** to `wiki/runner.py` (+ unit tests in existing `tests/unit/test_wiki_runner.py` if present, else inline a new tests/unit/test_wiki_runner_aggregate.py).
3. **Implement Protocols + DefaultPipeline.on_text new body** (GREEN for happy text + L1 dup tests).
4. **Implement DefaultPipeline.on_voice new body** (GREEN for voice tests).
5. **Implement error paths** (GREEN for classifier/runner/output error tests + back-compat).
6. **Implement L2 dedup branch** (GREEN for L2 test + record_dedup_choice + log marker).
7. **Build adapters in `__main__.py`** — `_ClassifierAdapter`, `_WikiRunnerAdapter`, `_OutputDeliveryAdapter`. Inject into DefaultPipeline. Add Settings if needed (wikis_root, base_prompt, overlay_prompt, runtime_dir, claude_config_dir).
8. **Integration test (RUN_INTEGRATION-gated)** — `tests/integration/test_pipeline_classifier_e2e.py`: real Claude CLI, asserts non-empty reply ≤60s.
9. **Update MODULE_CONTRACT headers** — tg/pipeline.py (new ROLE list), wiki/runner.py (export aggregate_text), __main__.py (DEPENDS updated).
10. **Update `docs/verification-plan.xml`** — Phase-20 with new tests + 8 log markers.
11. **Final quality gate** — make lint + make total-test (target unit + grace + INV), coverage ≥80% on tg/pipeline.py.
12. **Update README** — message-flow section to reflect classifier→runner→deliver path.
13. **Commit (smart-commit)** with scope `feat(M-TG-PIPELINE-CLASSIFIER)` and update bd close.

## Exit-criteria mapping (breakdown.xml#chunk-20)

| Criterion | Covered by |
|-----------|------------|
| on_text("привет") triggers Classifier→Inbox→Runner→deliver once | step 3, test happy_text |
| L2 idempotency dedups identical text | step 6, test l2_dedup_hit |
| __main__.py composes deps | step 7, integration test smoke |
| ≥10 unit tests covering branches | step 1 |
| Integration test green ≤60s | step 8 |
| Coverage ≥80%, grace 0/0, INV 14/14, mypy strict | step 11 |

## Out of scope (deferred)

Streaming (21), document mime (22), full e2e suite (23).
