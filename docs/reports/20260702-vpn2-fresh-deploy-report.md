# Completion Report — Fresh deploy of ai-steward-wiki on vpn-2

- **bd_id:** aisw-b1j
- **module:** deployment / infra (no `src/` module — operational task)
- **date:** 2026-07-02
- **decision origin:** `vpn-gpu-1` (former prod, `144.124.249.120`) died with no access; fresh deploy needed on a new host, same Telegram prod token (old instance dead)

## What changed

Deployed `ai-steward-wiki` from scratch on a new VPS, **vpn-2** (Ubuntu 24.04, 2 vCPU / 3.8 GB RAM, no swap at provisioning time), Variant B: native process as user `bgs` under `~/works`, not containerized.

1. **Code + auth:** repo cloned to `/home/bgs/works/ai-steward-wiki`; Claude Code subscription auth via `~/.claude` (native, not the `CLAUDE_CONFIG_DIR`-isolated path `docs/runbook/deploy.md` describes for the *target* multi-user design — this single-operator host reuses the operator's own login).
2. **Resource isolation:** `bots.slice` created to cap this and any future bot services sharing the host; `aisw-bot.service` itself sets `MemoryMax=2G` / `MemoryHigh=1.5G` / `TasksMax=4600` and is bound to that slice (`bots.slice` outer bound: `MemoryMax=3G` / `TasksMax=1024` — verified live 2026-07-08, see Verification).
3. **Swap:** 4G swapfile added (host shipped with none) — headroom on a 3.8G host running Python + SQLite + a Claude/Codex CLI subprocess per turn.
4. **Firewall:** `ufw` enabled, default deny incoming — described in the original bd close-reason as "SSH-only" at deploy time.
5. **Config fix:** three `.env` path variables (`AISW_PROMPTS_DIR`, `AISW_WIKI_TEMPLATE_DIR`, `AISW_PROFILES_DIR`) were still template placeholders; filled with real absolute paths as part of this deploy.
6. **Verification:** end-to-end Telegram reply confirmed working before closing the task.

**Deferred at deploy time:** monitoring (Beszel/Uptime Kuma) — explicitly out of scope for this pass.

## Files

No `src/` changes — this is an operational/infra task. `.env` on vpn-2 (not tracked in git; template is `.env.example`).

## Verification (evidence)

- Original bd close-reason (2026-07-02): "bot active, cgroup limits (bots.slice/2G/150%), swap 4G, ufw, Claude authed, end-to-end reply confirmed."
- **Re-verified live on vpn-2, 2026-07-08** (this report was written after the full-audit pass found the runbook's own checklist didn't match reality — see `docs/reports/2026-07-08-audit.md` and the `docs(deploy)` fix that followed it):
  - `bots.slice`: `MemoryMax=3221225472` (3G), `TasksMax=1024` — not `2G/150%` as the original close-reason stated; either it was retuned since 2026-07-02 or the original note was approximate.
  - `aisw-bot.service`: `MemoryMax=2147483648` (2G), `MemoryHigh=1610612736` (1.5G), `TasksMax=4600`, `Slice=bots.slice`.
  - Swap: `/swapfile`, 4G, confirmed present and in use (`swapon --show`).
  - `ufw`: `active`, default `deny (incoming)` — but by 2026-07-08 several non-SSH ports are also allowed (443, three UDP ports, 2096, 24949, 8443, plus SSH from 5 whitelisted IPs). This is **no longer** "SSH-only" as originally deployed; other services now appear to share this host. Not investigated further here — flagged for a separate infra audit if this host's blast radius matters for `ai-steward-wiki`'s threat model.
  - `bgs` group membership: `bgs,adm,sudo,users,docker` — `adm` was added 2026-07-08 (Codex fallback deploy session) for non-sudo `journalctl` access; not part of the original 2026-07-02 deploy.

## Known limitations / deferred

- Monitoring (Beszel/Uptime Kuma) — deferred at deploy time, still not present as of 2026-07-08.
- `docs/runbook/deploy.md` did not describe this host-level setup (`bots.slice`, swapfile, `ufw`) at all until this report and the accompanying `docs(deploy)` fix — the only prior record was this bd issue's close-reason and operator memory. If vpn-2's infra setup needs to be reproduced (disaster recovery, a second bot on the same host), this report plus the two verified `systemctl show` commands above are the current SSoT.
- The drift between `ufw`'s original "SSH-only" scope and its current broader ruleset is a separate open question — not resolved here, since it depends on what else now runs on vpn-2 (unknown from this session).
