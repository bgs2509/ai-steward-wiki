# Manual E2E Smoke Checklist — ai-steward-wiki MVP

> Runs once per release candidate on staging VPS with real Claude CLI, real Telegram bot, real
> SQLite triplet. Each step expects PASS; any FAIL → block release, file a `bd` bug, fix root
> cause. No bypass markers.

Reference: `docs/Spec-WIKI/research/tech-spec-draft.md` §11 acceptance, INV-1..INV-14.

## 0. Pre-flight

1. `uv sync` clean (no editable warnings).
2. `make lint` exit 0.
3. `make grace-lint` exit 0.
4. `make inv-lint` exit 0 (all 14 INV checks green).
5. `make test-cov` exit 0, total coverage ≥80%.
6. `systemctl status aisw-bot.service` → `active (running)`.
7. `journalctl -u aisw-bot.service -n 20` shows structured JSON lines with `correlation_id`.

## 1. Onboarding (M-ONBOARD-ADMIN, chunk 12)

1. From a Telegram account **not** in `users.toml` allowlist → bot replies with `pending_users`
   onboarding prompt (intro lint enforced).
2. Admin issues `/admin elevate <telegram_id>` → user moves to allowed roster, audit-row written.
3. Re-elevate same user → idempotent (no duplicate audit row, no error to admin).

## 2. Text intake → Stage-0 → Stage-1 (M-CLASSIFIER, M-WIKI-RUNNER)

1. Send plain text "купил кофе за 250р" → Stage-0 Haiku classifies → routes to `Inbox-WIKI` (or
   `Finance-WIKI` if exists).
2. Confirm response within 6s p95 (D-021 timeout budget).
3. Send same message twice in 2s → second hits L1 `tg_updates` dedup (audit row `DedupHit`, no
   second CLI invocation).
4. Send same content with different `tg_message_id` → L2 `seen_files` dedup fires.

## 3. Voice + photo (M-TG-MEDIA, chunk 11)

1. Send 5-second voice memo "напомни купить молоко завтра в 19:00" → faster-whisper transcribes →
   classifier routes → planner job created with `time_start=YYYY-MM-DD 19:00:00 Europe/Moscow`.
2. Send photo of lab result PDF page → vision delegation extracts text → routed to `Health-WIKI`.
3. Check `inbox/staging/raw/` cleanup after 30d retention (set fake mtime if needed).

## 4. WIKI lifecycle (M-WIKI-LIFECYCLE, chunk 8)

1. NL "создай wiki по бегу" → preflight grounding (5 steps) → creates `Running-WIKI/` with
   `CLAUDE.md` frontmatter v2.
2. NL "удали wiki Running" → soft-delete to `_trash/<ts>-Running-WIKI/` (D-041, 30d retention).
3. Verify INV-7: no `/wiki_init`, `/wiki_delete` direct commands exist (`grep -rE '/wiki_(init|
   delete|restore|purge|rename|merge|split)' src/` → empty).
4. Try to create `Bege-WIKI` after `Beg-WIKI` exists (Levenshtein ≤2) → graduated confirmation prompt
   per anti-spam SSoT (INV-14).

## 5. Scheduler + DLQ (M-SCHEDULER, chunk 4)

1. Queue a job that intentionally times out (simulate via CLI mock). Verify failure-strike counter
   increments (INV-12).
2. After 3 consecutive failures → job auto-disabled, row inserted into `jobs.db.jobs_dlq` (INV-11).
3. Restart bot → APScheduler `SQLAlchemyJobStore` re-hydrates jobs without duplication.

## 6. Backup + restore (M-OPS-BACKUP, chunk 14)

1. `systemctl start aisw-snapshot.service` (or wait for 03:00 UTC trigger) → produces
   `state/snapshots/YYYY-MM-DD/{jobs,audit,sessions}.db` with 0700 perms.
2. Per-WIKI `.git/` initialized, no remote configured (INV-3).
3. Run `docs/runbook/restore.md` procedure on a fresh VM → bot starts, latest user state visible.

## 7. PII + retention (M-OPS-PII, chunk 13)

1. Send a message containing a fake email + phone → audit log redacts both (`<redacted-email>`,
   `<redacted-phone>`).
2. After retention purge (manual trigger) → `inbox/staging/raw/` older than 30d gone, audit table
   beyond TTL pruned per D-006 §10.4 table.

## 8. Deploy hygiene (M-DEPLOY, chunk 16)

1. `systemd-analyze verify deploy/systemd/*.service` exit 0.
2. `aisw-bot.slice` shows CPUQuota / MemoryMax limits.
3. Per-CLI `cli-<job_id>.scope` exists during a live invocation, vanishes after exit.
4. `sysusers --dry-run deploy/systemd/aisw.conf` clean.

## 9. Cross-cutting INV spot-check

1. `grep -rE 'jobs\.db\.(seen_files|tg_updates)' src/` → empty (INV-4).
2. `grep -rE 'git\s+push' src/ai_steward_wiki/ops/` → empty (INV-3).
3. `grep -nE "FOREIGN KEY.*REFERENCES users" alembic/` → empty (INV-10).
4. Re-run `make inv-lint` → all green.

## 10. Sign-off

- [ ] All sections 0–9 PASS.
- [ ] `_report` artefact committed under `docs/reports/YYYYMMDD-mvp-rc-checklist.md`.
- [ ] Tag candidate `v0.1.0-rcN` (local only — no `git push`).
