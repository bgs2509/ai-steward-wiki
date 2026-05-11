# Operations runbook — `ai-steward-wiki`

> **Scope:** day-2 ops — service control, logs, per-CLI inspection, common incidents. SSoT for restore — `restore.md`.

## 1. Service control

| Action | Command |
|--------|---------|
| Start | `sudo systemctl start aisw-bot` |
| Stop (graceful, 30s timeout) | `sudo systemctl stop aisw-bot` |
| Restart | `sudo systemctl restart aisw-bot` |
| Reload allowlist (SIGHUP) | `sudo systemctl kill --signal=SIGHUP aisw-bot` |
| Status | `systemctl status aisw-bot aisw-bot.slice aisw-stt.slice` |

`SIGHUP` triggers `users.toml` hot-reload (D-031). Watchdog fallback re-reads on file mtime change.

## 2. Logs

1. **Live tail:** `journalctl -u aisw-bot -f -o cat`.
2. **Last hour, JSON:** `journalctl -u aisw-bot --since "1 hour ago" -o json | jq .`.
3. **By correlation_id:** `journalctl -u aisw-bot -o json | jq 'select(.MESSAGE | fromjson? | .correlation_id == "<cid>")'`.
4. **Per-CLI scope:** `journalctl -u cli-<job_id>.scope`.
5. structlog fields guaranteed: `ts, event, correlation_id, user_id, wiki_id, job_id`. Anchors follow `[Module][function][BLOCK_NAME]`.

## 3. Per-CLI scope inspection

| Question | Command |
|----------|---------|
| List active scopes | `systemctl list-units 'cli-*.scope' --no-legend` |
| Show one scope's caps | `systemctl show cli-<job_id>.scope -p MemoryMax,TasksMax,ProtectSystem,ReadOnlyPaths,ReadWritePaths` |
| Kill a runaway scope | `sudo systemctl stop cli-<job_id>.scope` |
| Aggregate slice state | `systemctl show aisw-bot.slice -p MemoryCurrent,TasksCurrent` |

## 4. Common incidents

### 4.1. Bot is `failed`

1. `systemctl status aisw-bot` — read last log lines.
2. `journalctl -u aisw-bot -n 200 --no-pager`.
3. If it's `.env` parse failure → fix `/etc/ai-steward-wiki/.env`, `systemctl restart aisw-bot`.
4. If it's DB-locked / migration drift → `restore.md` §1 (single-DB) or full snapshot restore.

### 4.2. CLI scopes hitting `MemoryMax=2G`

1. `journalctl -u cli-<job_id>.scope | grep -i 'killed\|oom'`.
2. If single user repeatedly OOMs → review prompts in `/opt/ai-steward-wiki/prompts/` for that domain; tune `wiki.md` to bound output.
3. Do NOT raise `MemoryMax` ad-hoc — change source in `deploy/runbook/deploy.md` §5 + open a Beads issue for the limit change.

### 4.3. Aggregate slice hitting `MemoryMax=16G`

1. `systemctl show aisw-bot.slice -p MemoryCurrent,TasksCurrent`.
2. List concurrent scopes; expected ceiling = 4 active CLI per tech-spec §10.1.
3. If > 4 → backpressure regression in scheduler; halt new dispatches via `bd update <ops-bd-id> --notes="halt:scheduler"` and investigate.

### 4.4. Allowlist not picked up

1. `cat /opt/ai-steward-wiki/users.toml` — verify edit landed.
2. `journalctl -u aisw-bot -g 'allowlist' -n 20`.
3. `sudo systemctl kill --signal=SIGHUP aisw-bot`. If still not visible, watchdog re-read on next mtime tick.

### 4.5. Stuck `cli-<job_id>.scope` (CLI hang)

1. `systemctl status cli-<job_id>.scope` — verify it's not making progress (no recent log lines).
2. Cross-check `bd show <job_id>` — if status still `in_progress` past timeout (D-021), bot's killer should fire.
3. Manual kill: `sudo systemctl stop cli-<job_id>.scope`. Bot emits a `[Scheduler][killed]` log line on next tick and updates Beads.

## 5. Backup & restore

1. State-DB snapshots — daily 03:00 UTC, `state/snapshots/<UTC-date>/{jobs,audit,sessions}.db`, retention 7d.
2. Per-WIKI git history — auto-commit per D-037.
3. Restore procedures — `restore.md` (state) and per-WIKI `git revert` / `git checkout @{N}` (content).
4. **No remote push.** D-037 §"Remote push" — git is not a disaster recovery channel.

## 6. Health checks

| Signal | How to read | Healthy |
|--------|-------------|---------|
| Bot uptime | `systemctl show aisw-bot -p ActiveEnterTimestamp` | matches expected restart cadence |
| TG webhook reachable | bot logs `[Bot][tg][polling][ok]` every poll cycle | continuous |
| Scheduler tick | `journalctl -u aisw-bot -g 'scheduler.*tick'` | every minute |
| State-DB writable | last `audit.db` insert via `journalctl -u aisw-bot -g 'audit.write'` | recent (≤ user activity) |
| Snapshot job | `journalctl -u aisw-bot -g 'db_snapshot' --since "26 hours ago"` | exactly one success line |
