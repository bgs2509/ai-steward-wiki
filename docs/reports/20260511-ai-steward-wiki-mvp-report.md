# ai-steward-wiki MVP — Completion Report

**Epic:** `aisw-fm0` — ai-steward-wiki MVP
**Date:** 2026-05-11
**Status:** All 17 chunks closed; epic ready to close
**Branch:** `master` (local; not pushed)

## Summary

17-chunk implementation of an isolated multi-user Telegram service that turns
Claude Code CLI into a Karpathy-style personal WIKI assistant. Delivered as a
single Python 3.11+ package (`src/ai_steward_wiki/`) with 3 SQLite engines, an
APScheduler-backed job queue, Stage-0 Haiku / Stage-1 Sonnet classifier and
runner, full Telegram I/O (text + voice + photo), onboarding/admin, PII
redaction, backup, deployment artefacts, and an automated 14-invariant gate.

## Chunks

| # | Module | bd_id | Closed |
|---|--------|-------|--------|
| 1 | M-FOUNDATION | aisw-u99 | 2026-05-10 |
| 2 | M-STORAGE | aisw-ewa | 2026-05-10 |
| 3 | M-AUTH-USERS | aisw-hnl | 2026-05-10 |
| 4 | M-SCHEDULER | aisw-kty | 2026-05-10 |
| 5 | M-CLASSIFIER-STAGE0 | aisw-v77 | 2026-05-10 |
| 6 | M-INBOX | aisw-qoi | 2026-05-10 |
| 7 | M-WIKI-RUNNER | aisw-x30 | 2026-05-10 |
| 8 | M-WIKI-LIFECYCLE | aisw-9s4 | 2026-05-10 |
| 9 | M-INGEST-IDEM | aisw-6t5 | 2026-05-11 |
| 10 | M-TG-TEXT | aisw-187 | 2026-05-11 |
| 11 | M-TG-MEDIA | aisw-dbe | 2026-05-11 |
| 12 | M-ONBOARD-ADMIN | aisw-zsg | 2026-05-11 |
| 13 | M-OPS-PII | aisw-p79 | 2026-05-11 |
| 14 | M-OPS-BACKUP | aisw-oqb | 2026-05-11 |
| 15 | M-TEMPLATES | aisw-dgk | 2026-05-11 |
| 16 | M-DEPLOY | aisw-jdd | 2026-05-11 |
| 17 | M-VERIFICATION | aisw-3tn | 2026-05-11 |

## Quality Gates

- `make lint` — ruff + ruff format + mypy --strict — exit 0
- `make grace-lint` — 60 governed files, 3 XML files, 0 errors/0 warnings
- `make inv-lint` — 14/14 INV checks pass
- `make qa` — 318 unit tests pass, 1 skipped (integration-only)
- `make test-cov` — coverage 92% on `src/ai_steward_wiki/` (2894 statements,
  238 missed) vs 80% gate
- Total tracked Python source files: 60 governed modules with MODULE_CONTRACT

## Key Decisions Applied

1. **3 × SQLite + Alembic per-DB** (D-006) — `jobs.db`, `audit.db`, `sessions.db`
   with bounded contexts; SQLAlchemyJobStore reuses `jobs.db`.
2. **Stage-0 Haiku CLI default + optional Anthropic-API backend** (D-009/D-013) —
   API backend requires a separate credential via systemd-credentials (INV-6).
3. **WIKI lifecycle = NL only** (D-041, supersedes D-029) — no `/wiki_*`
   commands. Enforced by `scripts/lint_invariants.py` INV-7.
4. **Backup = daily VACUUM INTO + per-WIKI git, no remote** (D-037) — git is not
   the DR channel. Enforced by INV-3.
5. **Idempotency 2-layer in audit.db** (D-018 amended 2026-05-10) — L1
   `tg_updates` (24h) + L2 `seen_files` (30d). Enforced by INV-4.
6. **Output sizing hybrid** (D-025) — ≤3500 inline / ≤10000 single Document /
   >10000 chunked summary + always-persist to `<wiki>/data/runs/<date>/`.
7. **Streaming edits throttled** (D-026) — 1.5s / Δ50 with chain-split at 4000
   chars and guaranteed final flush.
8. **3-strikes auto-disable with timeout-as-strike** (D-019/D-021, INV-12) —
   `killed`-by-timeout counts identically to permanent failure.
9. **PII tier-1 DROP / tier-2 MASK with HMAC** — idempotent marker stash, log
   processor chain, no plaintext PII in audit.

## Deviations vs Initial Breakdown

- **INV-7 allowlist expanded in chunk 17** to include
  `ops/{retention,snapshot}.py` (purge non-WIKI dirs). Pre-existing chunk-13/14
  code; the original chunk-8 INV-7 script would have flagged them — documented
  in step-17-plan.md decision (2).
- **Spec-doc invariants (INV-1/2/5/8/9/14) implemented as advisory checks**, not
  hard-fail. Reason: `docs/Spec-WIKI/` is a life-zone; hard-failing on its
  content creates cross-zone coupling the project forbids. Markers still
  presence-checked → deletion surfaces as a warn.
- **Coverage gate set at 80%** (per chunk-17 acceptance). Achieved 92%; no
  scope creep needed.

## Follow-ups (out of MVP scope)

1. Off-site backup (currently local-only; D-037 §"Remote push" п.2 — deferred).
2. i18n catalog (MVP is ru-only per D-032).
3. Integration tests against real Claude CLI on staging (nightly target wired;
   first full run pending VPS slot).
4. Manual E2E checklist (`tests/e2e_checklist.md`) — to be executed on the
   first release candidate.
5. Beads `bd_id` legacy column rename (cosmetic; tracked separately).

## Artefacts

- `docs/superpowers/plans/20260510-ai-steward-wiki-mvp/breakdown.xml` — final
  state (all chunks `status="closed"`).
- `docs/superpowers/plans/20260510-ai-steward-wiki-mvp/step-{05..14,16,17}-plan.md`.
- `docs/knowledge-graph.xml`, `docs/verification-plan.xml`, `docs/requirements.xml`,
  `docs/technology.xml`, `docs/development-plan.xml` — synchronised.
- `docs/runbook/{restore,deploy,operations}.md`.
- `tests/e2e_checklist.md` — 10-section manual smoke checklist.

## Sign-off

- Code: merged on local `master`. **Not pushed** (per Git Push Policy).
- Beads: chunks 1–17 closed; epic `aisw-fm0` to be closed at the end of this
  report session via `bd close aisw-fm0`.
- Next action: human operator runs `make nightly` on staging, then executes
  `tests/e2e_checklist.md`, then tags `v0.1.0-rc1` locally.
