# deploy/systemd

Two deployment models for the bot. **The current one is the simple single-user unit.**

## Current — `aisw-bot.service` (ADR-009 / ADR-010)

Single-user deploy: the bot runs as **`bgs`**, using that account's default
**`~/.claude`** for subscription auth (ADR-009 — no dedicated config dir, no
`AISW_CLAUDE_CONFIG_DIR`) and `~/.local/bin` for the `claude`/`uv` binaries.

Per-user Linux UID isolation (D-038) is **deferred** (ADR-010): the userbase is
small, trusted, and allowlisted by `telegram_id`, and the dangerous tool (`Bash`)
is already disabled in the CLI invocation. So no `aisw-bot` service user, no
`CAP_SETUID`, no per-CLI `systemd-run --scope` / slices.

```bash
sudo cp aisw-bot.service /etc/systemd/system/aisw-bot.service
sudo systemctl daemon-reload && sudo systemctl enable --now aisw-bot
sudo journalctl -u aisw-bot -f
```

## Deferred — `aisw-bot.service.d038-deferred` + `*.slice` + `aisw-sysusers.conf`

The full **D-038** hard-isolation model: dedicated `aisw-bot` user with
`CAP_SETUID`, per-WIKI-user UIDs, per-CLI transient scopes under
`aisw-bot.slice` / `aisw-stt.slice`, `ProtectHome=tmpfs`, read-only auth dir.

Kept as the reference unit for a future **untrusted / multi-tenant** deployment.
Do **not** install as-is for the current setup — notably `ProtectHome=tmpfs`
hides `~/.claude` and breaks subscription auth, and it expects a dedicated
`aisw-bot` user and `/opt/ai-steward-wiki` layout that the current deploy does
not use.

Re-trigger condition: the service admits untrusted or anonymous users (the
multi-tenant trigger from ADR-010 / D-038 Variant B).
