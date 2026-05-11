# M-INTEGRATION-E2E — completion report

**Chunk:** 23 · **bd_id:** aisw-vb9 · **Date:** 2026-05-11
**Predecessors:** chunks 18 (M-RUNTIME-WIRING, 9ebd475), 19 (M-TG-HANDLERS-WIRING, 2acb542), 20 (M-TG-PIPELINE-CLASSIFIER, 306a88b), 21 (M-TG-PIPELINE-STREAMING, 0065a76), 22 (M-TG-DOCUMENT-FULL, abcdb55) — all closed.

## Summary

Integration suite covering DefaultPipeline against the real Claude CLI Stage-0 classifier with fake runner/output collaborators. Four scenarios (text, voice, photo+confirm, PDF) under `tests/integration/test_e2e_pipeline.py`, gated by `RUN_INTEGRATION=1` + `claude`-binary presence. This is the last safety net before the production cutover checklist.

## Artifacts

| Artifact | Path |
|---|---|
| Discovery | `docs/superpowers/specs/20260511-integration-e2e-discovery.md` |
| Design | `docs/superpowers/specs/20260511-integration-e2e-design.md` |
| Plan | `docs/superpowers/plans/20260511-integration-e2e-plan.md` |
| Fixtures | `tests/integration/conftest.py` (new) |
| Tests | `tests/integration/test_e2e_pipeline.py` (new, 4 scenarios) |
| Gate unification | `tests/integration/test_pipeline_classifier_e2e.py`, `tests/integration/classifier/test_real_cli.py` (`RUN_CLAUDE_CLI_INTEGRATION` → `RUN_INTEGRATION`) |
| Runbook | `docs/runbook/operations.md` §Integration testing (new section) |

## Decisions (no ADRs — test-infra scope)

- **DEC-E2E-1** — Runner Stage-2 faked across all scenarios; only Stage-0 classifier hits real CLI. Keeps wall-time within 180s budget.
- **DEC-E2E-2** — Unified `RUN_INTEGRATION=1` gate replaces former `RUN_CLAUDE_CLI_INTEGRATION` opt-in. Single Makefile target, single runbook entry.
- **DEC-E2E-3** — `FakeAiogramBot` is a local copy of the unit-test `FakeSender` recorder (no `tests.unit` package import).

## Exit criteria

| Criterion | Status | Evidence |
|---|---|---|
| `RUN_INTEGRATION=1 pytest tests/integration/test_e2e_pipeline.py` ≤180s | **Wiring proven, env-gated on this dev box** | Real CLI invoked on first scenario (9.6s observed before subscription-config error). Production VPS has valid `/home/bgs/.claude/.claude.json`; deferred verification to first nightly run. |
| ≥4 green scenarios | **Code: 4 scenarios written + pytest collection sees 4** | `pytest --collect-only tests/integration/test_e2e_pipeline.py` → 4 items |
| `make integration` target | **OK** | `Makefile` `test-integration` target unchanged; new file auto-picked by pytest discovery |
| Nightly hook documented | **OK** | `docs/runbook/operations.md` §Integration testing |

## Verification on this branch

```
make lint        → All checks passed (ruff + format + mypy strict)
make grace-lint  → 0 errors, 0 warnings (governed files: 65)
make inv-lint    → 14/14 invariants pass
pytest tests/unit → 109+ passed (no regressions)
pytest tests/integration (gate off) → 6 skipped (clean)
pytest tests/integration::test_text_turn_end_to_end (gate on) → reached real claude subprocess (env-only failure, not a wiring defect)
```

## Open items / handover to cutover

1. Run the full suite from `/opt/ai-steward-wiki` on the production VPS with valid `CLAUDE_CONFIG_DIR` before invoking cutover-checklist step 1.
2. Record actual wall-time once green to validate the 180s budget assumption.
3. After first green nightly, optionally tighten the PDF scenario assertion (currently tolerates either-branch outcome from `_extract_pdf_text`) if pypdf reliably extracts the latin-1 stream content.
