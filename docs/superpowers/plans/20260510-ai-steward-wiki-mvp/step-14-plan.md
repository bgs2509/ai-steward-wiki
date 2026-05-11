# Step 14 — M-OPS-BACKUP (chunk 14, bd_id=aisw-oqb)

> Backup MVP per tech-spec §10.2 + D-037. Local-only safety net against software corruption / accidental delete; NO disaster recovery, NO off-site, NO remote git push.

## Goals

1. Daily `db_snapshot` APScheduler job at 03:00 UTC:
   - `VACUUM INTO state/snapshots/<UTC-date>/{jobs,audit,sessions}.db`
   - Mode `0700` on snapshot dir tree.
   - 7-day rolling retention (inline purge after successful VACUUM).
   - Silent maintenance — no user-facing job-model surface (INV-2 closed kind set already includes `db_snapshot`).
2. Per-WIKI git auto-commit (D-037):
   - `git init` on WIKI materialize (idempotent).
   - `.gitignore` with `.wiki.lock` + `data/runs/`.
   - Auto-commit message format: `<job_id>(<category>): <title>`.
   - Local-only. **No remote push wiring** (INV-3).
   - Reuses existing repo-level `gitleaks` pre-commit (no per-WIKI hook bootstrap; per-WIKI repos are local working dirs, secrets-scan happens at parent repo on the commits we generate at the dev level — for WIKI commits at runtime, gitleaks is invoked inline from `auto_commit` when binary present, soft-fail otherwise).
3. Restore runbook + smoke test:
   - `docs/runbook/restore.md` — manual checklist.
   - `tests/restore/test_db_snapshot_restore.py` — smoke roundtrip.

## Files

1. `src/ai_steward_wiki/ops/snapshot.py` — VACUUM INTO + retention + scheduler registration.
2. `src/ai_steward_wiki/ops/wiki_git.py` — init/auto-commit helpers + .gitignore writer.
3. `src/ai_steward_wiki/ops/__init__.py` — extend barrel.
4. `src/ai_steward_wiki/scheduler/maintenance.py` — call `register_db_snapshot_job` from `register_all_retention_jobs`.
5. `src/ai_steward_wiki/settings.py` — add `snapshot_dir`, `snapshot_retention_days`.
6. `docs/runbook/restore.md` — runbook.
7. `tests/restore/__init__.py`, `tests/restore/test_db_snapshot_restore.py` — smoke roundtrip.
8. `tests/unit/ops/snapshot/test_snapshot.py` + `test_purge.py` + `test_scheduler.py`.
9. `tests/unit/ops/wiki_git/test_wiki_git.py`.
10. `docs/superpowers/plans/20260510-ai-steward-wiki-mvp/breakdown.xml` — chunk 14 → `status="closed"`.

## Verification

1. `make total-test` exit 0.
2. New tests cover: VACUUM INTO produces valid SQLite file, 0700 perms set, retention deletes stale dirs but keeps fresh, scheduler cron at 03:00 UTC, idempotent re-register, git init creates `.gitignore`, auto_commit produces message in canonical format, INV-3 (no remote in code).
