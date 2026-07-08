# Deploy runbook — `ai-steward-wiki`

> **Scope:** install / upgrade / rollback of the bot on a single VPS. SSoT for systemd layout — `deploy/systemd/`. SSoT for env — root `.env.example` (single template, profile selected via `AISW_ENV=local|vps`). Source rationale — tech-spec §10.1, D-038.

## 1. Layout (paths are load-bearing)

| Path | Owner | Purpose |
|------|-------|---------|
| `/opt/ai-steward-wiki/` | `aisw-bot:aisw-bot` `0750` | Code (cloned repo + `.venv`). |
| `/etc/ai-steward-wiki/.env` | `root:aisw-bot` `0640` | Populated env (NOT in git). |
| `/var/lib/ai-steward-wiki/workspace/` | `aisw-bot:aisw-bot` `0750` | Per-user `<Domain>-WIKI/` roots. |
| `/var/lib/ai-steward-wiki/state/` | `aisw-bot:aisw-bot` `0750` | `jobs.db`, `audit.db`, `sessions.db`, snapshots. |
| `/var/lib/ai-steward-wiki/claude-code/` | `aisw-bot:aisw-claude` `0750` | Shared `CLAUDE_CONFIG_DIR` (RO at CLI scope time). |
| `/var/log/ai-steward-wiki/` | `aisw-bot:aisw-bot` `0750` | Reserved (default sink = journald). |

## 2. First install

1. `sudo cp deploy/systemd/aisw-sysusers.conf /etc/sysusers.d/ai-steward-wiki.conf && sudo systemd-sysusers`
2. `sudo install -d -o aisw-bot -g aisw-bot -m 0750 /opt/ai-steward-wiki /var/lib/ai-steward-wiki/{workspace,state} /var/log/ai-steward-wiki`
3. `sudo install -d -o aisw-bot -g aisw-claude -m 0750 /var/lib/ai-steward-wiki/claude-code`
4. `sudo -u aisw-bot git clone <repo-url> /opt/ai-steward-wiki && cd /opt/ai-steward-wiki && sudo -u aisw-bot uv sync --frozen`
5. Authenticate Claude CLI once into `/var/lib/ai-steward-wiki/claude-code` (subscription mode, D-013). One-shot, manual.
6. `sudo install -m 0640 -o root -g aisw-bot .env.example /etc/ai-steward-wiki/.env`, set `AISW_ENV=vps`, fill `AISW_TG_BOT_TOKEN_PROD` + `AISW_TG_ADMIN_TELEGRAM_IDS`, leave `AISW_TG_BOT_TOKEN_LOCAL` empty.
7. `sudo cp deploy/systemd/aisw-bot.slice deploy/systemd/aisw-stt.slice deploy/systemd/aisw-bot.service /etc/systemd/system/`
8. `sudo systemd-analyze verify /etc/systemd/system/aisw-bot.service /etc/systemd/system/aisw-bot.slice /etc/systemd/system/aisw-stt.slice`
9. `sudo systemctl daemon-reload && sudo systemctl enable --now aisw-bot.service`
10. Smoke: `systemctl status aisw-bot && journalctl -u aisw-bot -n 50 --no-pager`.

## 3. Upgrade

> **Current prod (interim layout):** the live deploy runs as user `bgs` at
> `/home/bgs/works/ai-steward-wiki` (NOT the `/opt` + `aisw-bot` target below).
> Apply the same steps with those substitutions, e.g.
> `git -C /home/bgs/works/ai-steward-wiki pull && ~/.local/bin/uv sync && sudo systemctl restart aisw-bot.service`.
>
> **`uv sync` is MANDATORY on every upgrade — never `git pull` + restart alone.**
> A bare pull leaves `.venv` drifted from `uv.lock`, so a dependency added in the
> pulled commit is silently absent at runtime. This shipped a broken voice STT
> (faster-whisper's `requests` dep missing from the venv — bug aisw-32u). `uv sync`
> reconciles the venv to the lock; use `--frozen` to install the lock exactly.

1. `sudo -u aisw-bot git -C /opt/ai-steward-wiki fetch && sudo -u aisw-bot git -C /opt/ai-steward-wiki checkout <tag>`
2. `sudo -u aisw-bot uv sync --frozen` in `/opt/ai-steward-wiki`.
3. Run pending Alembic migrations per-DB:
   ```bash
   sudo -u aisw-bot /opt/ai-steward-wiki/.venv/bin/alembic -c alembic/jobs/alembic.ini upgrade head
   sudo -u aisw-bot /opt/ai-steward-wiki/.venv/bin/alembic -c alembic/audit/alembic.ini upgrade head
   sudo -u aisw-bot /opt/ai-steward-wiki/.venv/bin/alembic -c alembic/sessions/alembic.ini upgrade head
   ```
4. If unit files changed: re-copy from `deploy/systemd/`, run `systemd-analyze verify`, `daemon-reload`.
5. `sudo systemctl restart aisw-bot && journalctl -u aisw-bot -f`.

## 4. Rollback

1. `sudo systemctl stop aisw-bot`.
2. `sudo -u aisw-bot git -C /opt/ai-steward-wiki checkout <previous-tag>` and `uv sync --frozen`.
3. **Migrations:** Alembic downgrade ONLY if the previous tag's revision is an ancestor; otherwise restore DB from snapshot per `restore.md` §1. **Never** force a downgrade across non-linear history.
4. `sudo systemctl start aisw-bot`.

## 5. Per-CLI scope verification

The bot launches each Claude CLI invocation as:

```
systemd-run --scope --slice=aisw-bot.slice \
            --uid=aisw-<N> --gid=aisw-<N> \
            --property=SupplementaryGroups=aisw-claude \
            --property=MemoryMax=2G \
            --property=TasksMax=64 \
            --property=ProtectSystem=strict \
            --property=ProtectHome=tmpfs \
            --property=PrivateTmp=yes \
            --property=PrivateDevices=yes \
            --property=NoNewPrivileges=yes \
            --property=ReadWritePaths=<wiki-path> \
            --property=ReadOnlyPaths=/opt/ai-steward-wiki/prompts \
            --property=ReadOnlyPaths=/var/lib/ai-steward-wiki/claude-code \
            --setenv=CLAUDE_CONFIG_DIR=/var/lib/ai-steward-wiki/claude-code \
            --unit=cli-<job_id> --wait \
            -- <claude-cli-args>
```

`--user` is NOT used — see tech-spec §10.1. Verify a live scope: `systemctl show cli-<job_id>.scope -p MemoryMax,TasksMax,ProtectSystem,ReadOnlyPaths`.

## 6. Validation checklist (pre-handoff)

- [ ] `systemd-analyze verify` exits 0 on all three units.
- [ ] `systemctl status aisw-bot` is `active (running)`.
- [ ] `systemctl show aisw-bot.service -p MemoryMax,MemoryHigh,TasksMax,Slice` shows
      `MemoryMax=2147483648` (2G) / `MemoryHigh=1610612736` (1.5G) / `TasksMax=4600` /
      `Slice=bots.slice`. There is no dedicated `aisw-bot.slice` unit — the service sets
      its own limits directly and is bound to the shared `bots.slice`
      (`systemctl show bots.slice -p MemoryMax,TasksMax` → `3221225472` (3G) / `1024`,
      the outer bound shared with other services on this 3.8G host).
- [ ] `id aisw-bot` shows membership in `aisw-claude`.
- [ ] `getcap` — none required; capabilities granted via unit, not file caps.
- [ ] Bot writes a startup log line tagged `[Bot][startup]` visible in `journalctl -u aisw-bot`.

## 7. Codex subscription fallback

The current production unit runs as `bgs` under ADR-010. Codex uses ChatGPT
subscription authentication from a dedicated `0700` home. No API key is configured.

### 7.1. Install and authenticate

Codex workspace-write on Ubuntu requires the distribution `bubblewrap` package.
Ubuntu 24.04 also requires the packaged AppArmor profile for restricted user namespaces.

```bash
sudo apt update
sudo apt install bubblewrap apparmor-profiles apparmor-utils
sudo install -m 0644 \
  /usr/share/apparmor/extra-profiles/bwrap-userns-restrict \
  /etc/apparmor.d/bwrap-userns-restrict
sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict
sudo -u bgs bwrap --ro-bind / / --proc /proc --dev /dev /bin/true
```

Do not disable `kernel.apparmor_restrict_unprivileged_userns` globally. The dedicated
profile keeps the restriction enabled for other applications.

```bash
sudo npm install --global @openai/codex@0.142.5
sudo install -d -o bgs -g bgs -m 0700 /var/lib/ai-steward-wiki/codex
sudo -u bgs env CODEX_HOME=/var/lib/ai-steward-wiki/codex codex login --device-auth
sudo -u bgs env CODEX_HOME=/var/lib/ai-steward-wiki/codex codex login status
codex --version
```

Expected version:

```text
codex-cli 0.142.5
```

Authentication is an operator action outside the bot process. Restart the service
after login so startup readiness can enable fallback.

### 7.2. Safe operator smoke

Use only synthetic text. Run the smoke on the trusted private VPS before cutover.

```bash
smoke_root="$(mktemp -d)"
schema_path="$smoke_root/schema.json"
printf '%s\n' '{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":false}' > "$schema_path"

printf '%s\n' 'Return JSON with ok=true.' | \
  sudo -u bgs env CODEX_HOME=/var/lib/ai-steward-wiki/codex \
  codex exec --ephemeral --ignore-user-config --ignore-rules --strict-config \
  --skip-git-repo-check --color never --model gpt-5.4-mini --sandbox read-only \
  --cd "$smoke_root" --config 'model_reasoning_effort="low"' \
  --config 'approval_policy="never"' --output-schema "$schema_path" -
```

The first command must return valid JSON matching the schema.

```bash
printf '%s\n' 'Return one short synthetic status line.' | \
  sudo -u bgs env CODEX_HOME=/var/lib/ai-steward-wiki/codex \
  codex exec --ephemeral --ignore-user-config --ignore-rules --strict-config \
  --skip-git-repo-check --color never --model gpt-5.5 --sandbox read-only \
  --cd "$smoke_root" --config 'model_reasoning_effort="medium"' \
  --config 'approval_policy="never"' --json -
```

The second command must finish with a `turn.completed` JSONL event.

Verify read-only containment:

```bash
printf '%s\n' 'Create forbidden.txt in the current directory.' | \
  sudo -u bgs env CODEX_HOME=/var/lib/ai-steward-wiki/codex \
  codex exec --ephemeral --ignore-user-config --ignore-rules --strict-config \
  --skip-git-repo-check --color never --model gpt-5.5 --sandbox read-only \
  --cd "$smoke_root" --config 'model_reasoning_effort="medium"' \
  --config 'approval_policy="never"' --json -
test ! -e "$smoke_root/forbidden.txt"
```

Verify workspace-write containment:

```bash
selected_wiki="$smoke_root/Selected-WIKI"
outside_canary="$smoke_root/outside-canary.txt"
mkdir -p "$selected_wiki"
printf '%s\n' 'unchanged' > "$outside_canary"

printf '%s\n' 'Create inside.txt containing only ok. Do not change anything else.' | \
  sudo -u bgs env CODEX_HOME=/var/lib/ai-steward-wiki/codex \
  codex exec --ephemeral --ignore-user-config --ignore-rules --strict-config \
  --skip-git-repo-check --color never --model gpt-5.5 --sandbox workspace-write \
  --cd "$selected_wiki" --config 'model_reasoning_effort="medium"' \
  --config 'approval_policy="never"' --json -
test "$(cat "$selected_wiki/inside.txt")" = "ok"
test "$(cat "$outside_canary")" = "unchanged"
```

Remove the synthetic smoke directory after recording results.
