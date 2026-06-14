# ADR-010: Defer per-user Linux UID isolation for the life-MVP

- Status: Accepted
- Date: 2026-06-14
- Deciders: @bgs
- Amends: D-038 (per-user hard isolation)
- Related: ADR-008 (dev/life separation), ADR-009 (config dir), D-007

## Context

`D-038` chose **hard kernel isolation in the MVP**: one dedicated Linux user per
WIKI user (`useradd` at onboarding via the `D-030` approve flow, `userdel` at
hard-delete), with each Claude CLI invocation launched through
`systemd-run --scope --uid=<N>` plus `ProtectHome=tmpfs`, `ReadOnlyPaths`, a
no-`Bash` tool profile, and `CAP_SETUID` on the bot. Its purpose: defend against
prompt-injection cross-tenant file reads that application-level path validation
(`--add-dir <wiki>`, `D-007`) cannot catch, because that validation only inspects
paths Claude returns, not what a tool invocation actually touches.

`D-038` itself records (`D-038:11`) that the overview proposed deferring this to
multi-tenant production, and that hard isolation in the MVP was a deliberate
choice at the time.

The context has since changed:

1. **`ai-steward-wiki` is now an exclusively life service** (ADR-008) on its own
   server, for a **small, trusted** userbase (family). Access is gated by a
   `telegram_id` allowlist (`users.toml`, `runtime.allowlist.loaded`), so only
   vetted users reach the bot at all — this removes the untrusted-user threat
   that motivated D-038. Kernel-level isolation *between* trusted users guards a
   narrow residual risk.
2. **The primary injection vector is already closed.** Stage-1 runs with
   `--disallowedTools "Bash" "WebFetch"` (`D-038:121`) and the Stage-0
   classifier runs with `--tools ""` (`classifier/backend.py`). The dangerous
   channel (`Bash`) is disabled regardless of UID isolation.
3. **Hard isolation has real costs.** `CAP_SETUID` on the bot is itself an
   attack surface — a compromised bot can `setuid` into any `aisw-*` user
   (`D-038:148`). Plus `useradd`/`userdel` per onboarding/delete, per-user
   slices, and per-process limits are significant moving parts to build and
   maintain.
4. **It is not implemented today.** The bot currently runs as a single user with
   no systemd scoping. Deferring `D-038` dismantles nothing.

## Decision

1. **For the life-MVP, defer per-user Linux UID isolation.** The bot runs under
   the **existing single account (`bgs`)** — **no dedicated service user** — with
   per-WIKI isolation enforced at the **application level** (`--add-dir <wiki>` +
   path validation, `D-007`) plus the existing no-`Bash` / no-`WebFetch` tool
   profile. A dedicated `aisw-bot` service user was considered and **rejected as
   overkill** for this trusted, allowlisted, hobby-scale deployment.
2. **Keep the fixed dedicated `CLAUDE_CONFIG_DIR`** (ADR-009). Because the bot
   shares the `bgs` account with the developer's personal, actively-used
   `~/.claude/`, the dedicated dir keeps the bot's subscription auth and CLI
   state from mixing with (and contending over) the developer's interactive
   Claude usage.
3. **Re-trigger `D-038` hard isolation** if/when the service ever admits
   untrusted or anonymous users. This is precisely the multi-tenant trigger that
   `D-038` originally rejected as its Variant B.

## Alternatives considered

1. **Keep `D-038` hard isolation as-is.** Rejected for the life-MVP:
   disproportionate for a trusted family userbase with `Bash` already disabled,
   while carrying the `CAP_SETUID` attack surface and ongoing operational
   complexity.
2. **Container / namespace per request instead of UIDs.** Rejected: more
   infrastructure than a single-user app-scoped model, unjustified at this trust
   level.

## Consequences

Positive:

- Drastically simpler operations: no `useradd`/`userdel`, no `CAP_SETUID`, no
  per-user slices or limits, no separate service account to provision.
- Smaller bot attack surface (no `CAP_SETUID` capability on the long-running
  process).

Negative / trade-offs:

- Cross-tenant isolation is now application-level ("on trust"), acceptable
  **only** under the trusted-userbase assumption. This is the explicit,
  documented trade-off; it reverts via the multi-tenant trigger above.
- The bot shares the developer account `bgs`; a bot compromise's blast radius
  therefore includes that account (personal `~/.claude/`, home, projects).
  Accepted deliberately given the `telegram_id` allowlist of trusted users and
  the hobby-scale deployment; revisit if the userbase or exposure grows.
- `D-038` in `docs/Spec-WIKI/decisions/` should be marked amended-by ADR-010
  (with a `log.md` entry), as a follow-up in the life-zone wiki.

## Sources

- D-038 (per-user hard isolation — amended by this ADR)
- D-007 (application-level `--add-dir` scope)
- `src/ai_steward_wiki/classifier/backend.py` — `--tools ""`
- ADR-008 (dev/life separation), ADR-009 (config dir)
