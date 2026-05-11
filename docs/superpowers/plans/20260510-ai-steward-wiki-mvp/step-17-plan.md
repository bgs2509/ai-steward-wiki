# Step 17 — M-VERIFICATION (Chunk 17)

**bd_id:** `aisw-3tn`
**Module:** `M-VERIFICATION`
**Depends on:** all prior chunks (1–16) closed
**Window budget:** ~0.30

## Goal

Close the MVP verification surface: automate all 14 invariants from
`docs/Spec-WIKI/research/tech-spec-draft.md §0`, expose a coverage gate, wire a
nightly target, and document a manual E2E smoke checklist.

## Inputs

- `scripts/lint_invariants.py` (was INV-7-only)
- `tests/integration/` (chunk 5/6/7 leftovers)
- `Makefile` (existing `qa`, `total-test`)
- `pyproject.toml` (dependency-groups)

## Outputs

1. `scripts/lint_invariants.py` — 14-check runner. Code-level checks: INV-3, INV-4,
   INV-6, INV-7, INV-10, INV-11, INV-12, INV-13. Spec-doc advisory checks: INV-1,
   INV-2, INV-5, INV-8, INV-9, INV-14. Exit 1 on any code-level violation.
2. `tests/e2e_checklist.md` — 10-section manual E2E smoke checklist anchored to
   modules + INVs + D-decisions.
3. `Makefile` — new `inv-lint`, `test-cov`, `nightly` targets; `inv-lint` wired into
   `qa` and `total-test`.
4. `pyproject.toml` — `pytest-cov==6.0.0` added to dev group.

## Verification

1. `make inv-lint` → exit 0, all 14 checks pass.
2. `make test-cov` → exit 0, total coverage ≥80% (baseline 92% / 2894 statements).
3. `make total-test` → exit 0 (ruff + format + mypy + grace lint + inv-lint + unit
   + integration).
4. `bd close aisw-3tn`.

## Decisions

1. **INV-7 allowlist expanded** to include `ops/retention.py` and `ops/snapshot.py`
   (PII purge + snapshot purge — they delete inbox-staging and snapshot dirs, not
   WIKI dirs). Without this the script flagged pre-existing chunk-13/14 code which
   is by-design.
2. **Spec-doc INVs (1/2/5/8/9/14) are advisory.** They depend on `docs/Spec-WIKI/`
   which is a life-zone; flipping them to hard-fail would create cross-zone
   coupling that the project explicitly forbids. The script still presence-checks
   each marker so deletion of the SSoT line surfaces as a `warn`.
3. **Coverage gate at 80%** per chunk-17 acceptance. Current measured: 92% → wide
   margin, no scope creep needed.
