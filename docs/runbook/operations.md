# Operations runbook — `ai-steward-wiki`

> **Scope:** day-2 ops — service control, logs, per-CLI inspection, common incidents. SSoT for restore — `restore.md`.

## 1. Service control

| Action | Command |
|--------|---------|
| Start | `sudo systemctl start aisw-bot` |
| Stop (graceful, 30s timeout) | `sudo systemctl stop aisw-bot` |
| Restart | `sudo systemctl restart aisw-bot` |
| Reload allowlist (SIGHUP) | `sudo systemctl kill --signal=SIGHUP aisw-bot` |
| Dump stacks (SIGUSR1, on-demand diagnostics) | `sudo kill -USR1 $(systemctl show aisw-bot -p MainPID --value)` |
| Status | `systemctl status aisw-bot` |

`SIGHUP` triggers `users.toml` hot-reload (D-031). Watchdog fallback re-reads on file mtime change.

## 2. Logs

1. **Live tail:** `journalctl -u aisw-bot -f -o cat`.
2. **Last hour, JSON:** `journalctl -u aisw-bot --since "1 hour ago" -o json | jq .`.
3. **By correlation_id:** `journalctl -u aisw-bot -o json | jq 'select(.MESSAGE | fromjson? | .correlation_id == "<cid>")'`.
4. **Per-CLI run:** there are NO per-CLI systemd units (ADR-010 simple model — see §3). Filter the bot's own log by `job_id`: `journalctl -u aisw-bot -o cat | grep '"job_id": <job_id>'`.
5. structlog fields guaranteed: `ts, event, correlation_id, user_id, wiki_id, job_id`. Anchors follow `[Module][function][BLOCK_NAME]`.

## 3. Per-CLI run inspection

> **Deployment model (ADR-010):** the bot runs as a simple single-user service
> (`User=bgs`, `Slice=system.slice`). Each Claude CLI invocation — interactive
> (`M-WIKI-RUNNER`) **and** cron-user (`M-SCHEDULER-CONSUMER`, aligned in aisw-abc) —
> is a **direct child subprocess** of `aisw-bot`, NOT a `systemd-run --scope`
> transient unit. There are no `cli-<job_id>.scope` units and no `aisw-*.slice`.
> The per-UID / per-scope hardening model (D-038: dedicated slices, `MemoryMax=2G`
> per CLI, `CAP_SETUID`) is **deferred** — see `deploy/systemd/*.d038-deferred`.

| Question | Command |
|----------|---------|
| List live CLI children | `pgrep -a -P $(systemctl show aisw-bot -p MainPID --value)` |
| Trace one job's run | `journalctl -u aisw-bot -o cat | grep '"job_id": <job_id>'` |
| Kill a runaway CLI child | `kill -TERM <child_pid>` (the bot's D-021 timeout killer normally handles this) |
| Service resource usage | `systemctl show aisw-bot -p MemoryCurrent,TasksCurrent` |

## 4. Common incidents

### 4.1. Bot is `failed`

1. `systemctl status aisw-bot` — read last log lines.
2. `journalctl -u aisw-bot -n 200 --no-pager`.
3. If it's `.env` parse failure → fix `/etc/ai-steward-wiki/.env`, `systemctl restart aisw-bot`.
4. If it's DB-locked / migration drift → `restore.md` §1 (single-DB) or full snapshot restore.

### 4.2. A CLI run consuming excess memory

> ADR-010: there is **no** per-CLI `MemoryMax` (no transient scope). A runaway
> CLI is bounded only by host memory until the bot's D-021 timeout fires.

1. `systemctl show aisw-bot -p MemoryCurrent` and `pgrep -a -P $(systemctl show aisw-bot -p MainPID --value)` — spot the heavy child.
2. Trace the offending job: `journalctl -u aisw-bot -o cat | grep '"job_id": <job_id>'`.
3. If a single user/domain repeatedly bloats → review prompts in the deployed `prompts/` dir for that domain; tune `wiki.md` to bound output.
4. Per-CLI memory caps return only with the deferred D-038 model (`deploy/systemd/*.d038-deferred`); do not improvise scope limits under ADR-010.

### 4.3. Host / service memory pressure

1. `systemctl show aisw-bot -p MemoryCurrent,TasksCurrent` + `free -h`.
2. Count concurrent CLI children (`pgrep -c -P <bot_pid>`); expected ceiling = 4 active CLI per tech-spec §10.1 (scheduler concurrency).
3. If > 4 → backpressure regression in scheduler; halt new dispatches via `bd update <ops-bd-id> --notes="halt:scheduler"` and investigate.

### 4.4. Allowlist not picked up

1. `cat /opt/ai-steward-wiki/users.toml` — verify edit landed.
2. `journalctl -u aisw-bot -g 'allowlist' -n 20`.
3. `sudo systemctl kill --signal=SIGHUP aisw-bot`. If still not visible, watchdog re-read on next mtime tick.

### 4.5. Stuck CLI child process (CLI hang)

1. Find the child: `pgrep -a -P $(systemctl show aisw-bot -p MainPID --value)`; cross-check its `job_id` via `journalctl -u aisw-bot -o cat | grep '"job_id": <job_id>'` (no recent lines = stalled).
2. Cross-check `bd show <job_id>` — if status still `in_progress` past timeout (D-021), the bot's killer should fire (`scheduler.consumer.exec.timeout`).
3. Manual kill: `kill -TERM <child_pid>` (escalate to `-KILL` if needed). The bot emits a killed/timeout log line on the next tick and updates Beads.

### 4.6. Bot frozen / unresponsive — event-loop hang (aisw-xbc)

Symptom: process is `active` but stops replying; **no log lines for minutes** (not even
scheduler jobs). The built-in diagnostics (diagnostics-only — no auto-restart) answer
*when / where / what* from the journal alone. Thresholds: `AISW_OBS_*` (see `.env.example`).

1. **WHEN — was it actually frozen, and since when?** Find the last heartbeat:
   ```bash
   journalctl -u aisw-bot -o cat | grep runtime.loop.heartbeat | tail -1
   ```
   `runtime.loop.heartbeat` fires every ~20s. A gap from its last `ts` to now = the freeze
   window. A preceding `runtime.loop.lag` (high `lag_ms`) flags synchronous blocking.
2. **WHERE — which call/handler stalled?**
   - `journalctl -u aisw-bot -o cat | grep -E '\.slow|\.error'` — boundary anchors
     (`tg.io.send_message`, `audit.io.record_run_output`, …) that ran long or failed.
   - A `tg.update.received` with **no matching `tg.update.handled`** for the same `update_id`
     ⇒ that handler started and never finished.
3. **WHAT — exact stuck frame?** Trigger an on-demand dump (also auto-fires when `lag_ms`
   exceeds `AISW_OBS_LOOP_LAG_DUMP_MS`):
   ```bash
   sudo kill -USR1 $(systemctl show aisw-bot -p MainPID --value)
   journalctl -u aisw-bot -o cat | grep runtime.diag.task_dump | tail -1 | jq .
   ```
   `runtime.diag.task_dump` lists every asyncio task with its suspended coroutine frames
   (`file:line in func`, **no argument values** — PII-safe). A `faulthandler` thread dump is
   written alongside (plain text to journald).
4. **Recover:** `sudo systemctl restart aisw-bot` (no auto-recovery by design). Capture the
   `task_dump` + last heartbeat ts into the incident before restarting.

## 5. Backup & restore

1. State-DB snapshots — daily 03:00 UTC, `state/snapshots/<UTC-date>/{jobs,audit,sessions}.db`, retention 7d.
2. Per-WIKI git history — auto-commit per D-037.
3. Restore procedures — `restore.md` (state) and per-WIKI `git revert` / `git checkout @{N}` (content).
4. **No remote push.** D-037 §"Remote push" — git is not a disaster recovery channel.

## 6. Health checks

| Signal | How to read | Healthy |
|--------|-------------|---------|
| Bot uptime | `systemctl show aisw-bot -p ActiveEnterTimestamp` | matches expected restart cadence |
| Event loop alive | `journalctl -u aisw-bot -o cat -g runtime.loop.heartbeat -n 1` | a `lag_ms`-bearing line within the last ~20s; near-zero `lag_ms` (§4.6) |
| TG webhook reachable | bot logs `[Bot][tg][polling][ok]` every poll cycle | continuous |
| Scheduler tick | `journalctl -u aisw-bot -g 'scheduler.*tick'` | every minute |
| State-DB writable | last `audit.db` insert via `journalctl -u aisw-bot -g 'audit.write'` | recent (≤ user activity) |
| Snapshot job | `journalctl -u aisw-bot -g 'db_snapshot' --since "26 hours ago"` | exactly one success line |


## Integration testing (chunk 23 — M-INTEGRATION-E2E)

> Last-resort safety net before each production cutover. Exercises `DefaultPipeline` against the **real Claude CLI classifier** with fake runner/output collaborators. Latency budget ≤ 180 s for 4 scenarios.

### Gate

The integration suite is **opt-in**. It runs only when all three conditions hold:

1. `RUN_INTEGRATION=1` environment variable is set.
2. The `claude` binary is on `PATH` (subscription auth via `CLAUDE_CONFIG_DIR`).
3. `CLAUDECODE` is **unset** — the suite is not inside a parent Claude Code session. Recursive `claude` invocation from within Claude Code breaks subscription auth (`rc=1` with no usable stderr).

When any condition fails, every test under `tests/integration/` is skipped silently — `make total-test` stays green on dev boxes without a Claude subscription and inside Claude Code agent sessions.

### Command

```bash
RUN_INTEGRATION=1 CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code \
  uv run pytest tests/integration -v
```

Or via the Makefile target:

```bash
make test-integration
```

### Cadence

- **Manual nightly** before each cutover window. No CI auto-trigger (subscription token cost; recursive `claude` invocation footgun).
- **Not part of `make total-test`** — integration is intentionally excluded from the pre-merge gate (env-sensitive: requires `socat`/`bubblewrap` for sandbox, valid subscription auth, and parent shell with `CLAUDECODE` unset). Run separately via `make test-integration`.
- Run from the dev VPS (`/opt/ai-steward-wiki`) or a developer workstation with the same `CLAUDE_CONFIG_DIR` mounted/copied.

### Scenarios

| File | Scenarios | Real components |
|------|-----------|-----------------|
| `tests/integration/test_e2e_pipeline.py` | text turn, voice turn, photo + explicit confirm, PDF document | Claude CLI Stage-0 classifier |
| `tests/integration/test_pipeline_classifier_e2e.py` | chunk-20 wiring regression | Claude CLI Stage-0 classifier |
| `tests/integration/classifier/test_real_cli.py` | low-level `ClaudeCliBackend.classify` | Claude CLI |

Runner Stage-1a/1b is **faked** in all scenarios — keeps wall-time inside the 180 s budget while still exercising the full pipeline composition.

### Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| All tests skipped | Gate var or binary missing, or inside Claude Code | `export RUN_INTEGRATION=1`; verify `which claude`; ensure `CLAUDECODE` is unset (run outside the Claude Code CLI) |
| `subprocess.TimeoutExpired` | Claude CLI cold start or quota | Re-run; check subscription dashboard |
| `OperationalError: database is locked` | Stale tmp_path artefacts | `rm -rf /tmp/pytest-*`; re-run |
| `_extract_pdf_text` empty | Latin-1 PDF stream — pypdf cannot decode | Accepted: scenario assertion tolerates either branch |
