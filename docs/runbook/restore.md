# Restore runbook — `ai-steward-wiki`

> **Scope:** MVP local safety net (tech-spec §10.2 + D-037). Recovers from software corruption / accidental deletion only. **NOT** disaster recovery — VPS hardware loss is out of scope and a separate (deferred) decision.

## 1. State-DB restore (jobs / audit / sessions)

### 1.1 Daily snapshots

* Scheduled by APScheduler job `ops.db_snapshot`, daily **03:00 UTC**.
* Mechanism: `VACUUM INTO state/snapshots/<UTC-date>/{jobs,audit,sessions}.db`.
* Retention: rolling **7 days**, mode `0700`.

### 1.2 Manual restore steps

1. Stop the bot: `systemctl stop aisw-bot`.
2. Pick the snapshot directory you need: `ls -la /var/lib/ai-steward-wiki/state/snapshots/`.
3. Create a clean restore staging path: `mkdir -p /var/lib/ai-steward-wiki/state-restore-test/data`.
4. Copy the chosen snapshot files in place: `cp state/snapshots/<DATE>/{jobs,audit,sessions}.db state-restore-test/data/`.
5. Point a one-off Settings env at the restore path:
   ```bash
   export AISW_JOBS_DB_URL=sqlite+aiosqlite:///state-restore-test/data/jobs.db
   export AISW_AUDIT_DB_URL=sqlite+aiosqlite:///state-restore-test/data/audit.db
   export AISW_SESSIONS_DB_URL=sqlite+aiosqlite:///state-restore-test/data/sessions.db
   ```
6. Run smoke tests: `RUN_RESTORE_SMOKE=1 uv run pytest tests/restore -v`.
7. If smoke passes, swap the restored files into the live `data/` directory and `systemctl start aisw-bot`.
8. If smoke fails, escalate; do **NOT** swap files in.

### 1.3 Pre-release rehearsal

Manual rehearsal is **mandatory** before every release. Run §1.2 against the latest snapshot and confirm `tests/restore` passes.

## 2. Per-WIKI content restore

### 2.1 Mechanism

* Each WIKI directory carries its own local git repo (D-037).
* Every Stage-1b successful write triggers an auto-commit in `<job_id>(<category>): <title>` format.
* **No remote push is configured** (INV-3 / D-037 §"Remote push" п.1). Git is **not** the disaster-recovery channel.

### 2.2 Recover a bad Claude edit

1. `cd <wiki-root>`.
2. `git log --oneline -n 20` — find the last good revision.
3. Either revert the bad commit: `git revert <sha>` (creates a new commit), or check out a single file: `git checkout <sha> -- <relative-path>`.
4. Validate by hand (open the page in `less` / `bat`) — Claude commits may include multiple files per run.

### 2.3 Recover a deleted WIKI

1. The WIKI lives in `_trash/<Domain>-WIKI-<ts>/` for 30 days (see `M-WIKI-LIFECYCLE`).
2. Restore via NL prompt (`intent=restore_wiki`) — there is **no** `/wiki_restore` direct command (INV-7).

## 3. What this runbook does NOT cover

* VPS disk failure / total data loss.
* Ransomware.
* Off-site backup.

These classes are open risk and require a separate (currently deferred) decision — see backlog entry `Q-E-36 — backup WIKI и state-db`.
