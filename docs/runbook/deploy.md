# Deploy runbook — `ai-steward-wiki`

> **Scope:** install / upgrade / rollback of the bot on a single VPS. SSoT for systemd layout — `deploy/systemd/`. SSoT for env — `deploy/{staging,prod}/.env.example`. Source rationale — tech-spec §10.1, D-038.

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
6. `sudo install -m 0640 -o root -g aisw-bot deploy/{staging|prod}/.env.example /etc/ai-steward-wiki/.env` and edit secrets in place.
7. `sudo cp deploy/systemd/aisw-bot.slice deploy/systemd/aisw-stt.slice deploy/systemd/aisw-bot.service /etc/systemd/system/`
8. `sudo systemd-analyze verify /etc/systemd/system/aisw-bot.service /etc/systemd/system/aisw-bot.slice /etc/systemd/system/aisw-stt.slice`
9. `sudo systemctl daemon-reload && sudo systemctl enable --now aisw-bot.service`
10. Smoke: `systemctl status aisw-bot && journalctl -u aisw-bot -n 50 --no-pager`.

## 3. Upgrade

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
- [ ] `systemctl show aisw-bot.slice -p MemoryMax,TasksMax` shows `16G` / `512`.
- [ ] `id aisw-bot` shows membership in `aisw-claude`.
- [ ] `getcap` — none required; capabilities granted via unit, not file caps.
- [ ] Bot writes a startup log line tagged `[Bot][startup]` visible in `journalctl -u aisw-bot`.
