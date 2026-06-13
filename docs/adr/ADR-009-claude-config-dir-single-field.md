# ADR-009: Single explicit `CLAUDE_CONFIG_DIR`, decoupled from `AISW_ENV`

- Status: Accepted
- Date: 2026-06-14
- Deciders: @bgs
- Supersedes: ADR-001
- Related: ADR-008 (dev/life separation), ADR-010 (isolation), D-013, D-038

## Context

ADR-001 made `Settings.claude_config_dir` an `env`-resolved `@property` backed by
two slots: `claude_config_dir_local` (a dedicated dir) for `AISW_ENV=local`, and
`claude_config_dir_vps = None` for `AISW_ENV=vps`, where `None` was meant to let
the CLI fall back to its default `~/.claude/`.

Two problems surfaced on 2026-06-14:

1. **The `vps` / `None` branch is dead code.** The runtime requires a concrete
   path: `__main__.py` raises `RuntimeError("claude_config_dir is required ...")`
   when the resolved value is `None`, and `build_env()` / `neutral_cwd()`
   (`claude_cli/common.py`) take a non-optional `Path` and unconditionally set
   `CLAUDE_CONFIG_DIR=str(dir)` and `cwd=str(dir)`. So `AISW_ENV=vps` with
   `claude_config_dir_vps=None` crashes at startup. The ADR-001 "VPS uses
   `~/.claude/`" happy path stopped working after the `aisw-d3i` / `aisw-adj`
   refactors and was never exercised.
2. **The `env`-coupling caused a production outage.** On the VPS, `AISW_ENV=local`
   resolved the dedicated-dir slot to a path that was never created. Every
   classification then failed with `FileNotFoundError` on the CLI `cwd`, and the
   bot silently produced no reply (the pipeline raised before sending anything).

Why the dedicated dir is kept (the folder itself is justified; only the
`env`-selection of it is not). The bot runs under the developer account `bgs`
with **no dedicated service user** (ADR-010), and `bgs` already has a personal,
actively-used `~/.claude/`. Pointing the bot at the default `~/.claude/` would
mix the bot's subscription auth and CLI state (sessions, history,
`~/.claude.json`) with the developer's interactive Claude usage on the same
account — and let concurrent processes contend over the same state files. A
dedicated `CLAUDE_CONFIG_DIR` keeps the two apart. This is exactly ADR-001's
original *local* rationale; what was broken in ADR-001 was the `env`-coupling and
the dead `vps=None` branch, **not** the dedicated-dir idea itself.

(Were the bot ever moved to its own service user or the now-deferred `D-038`
hardening — where `ProtectHome=tmpfs` masks `$HOME` — a fixed path would be
required for other reasons too; that is not the current driver.)

So the *folder* is justified by the shared `bgs` account; only the *two-slot
`env`-selection of the folder* is the unnecessary, drift-prone part this ADR
removes.

## Decision

1. `claude_config_dir` becomes a **single explicit field**:
   `claude_config_dir: Path = Path("/var/lib/ai-steward-wiki/claude-code")`,
   overridable via the env var `AISW_CLAUDE_CONFIG_DIR`. **Decoupled from
   `AISW_ENV`.**
2. Remove `claude_config_dir_local`, `claude_config_dir_vps`, and the
   `env`-resolving `@property`.
3. `AISW_ENV` continues to govern **only** the Telegram token
   (`tg_bot_token_local` / `tg_bot_token_prod`) — the genuine, validator-enforced
   test/prod boundary *within* the life service.
4. The field is **required**; startup fails fast with an actionable message if
   the directory is missing or unauthenticated (the bug above becomes a clear
   error instead of a silent no-reply).

## Alternatives considered

1. **Keep two slots, make both explicit (no `None`).** Rejected: YAGNI / KISS —
   on a single-purpose host both slots point at the same dedicated dir, so the
   split carries zero behavioral difference and re-invites the same drift.
2. **Keep `env` as a hidden default-selector for one field.** Rejected:
   re-introduces implicit logic (a field's value depending on another field),
   against Explicit > Implicit, for purely cosmetic continuity.

## Consequences

Positive:

- One SSoT for the auth location; the dead `None` branch is gone; the silent
  no-reply outage class is eliminated (fail-fast at startup instead).
- Provisioning becomes explicit and documented: create the dir + `claude login`
  into it once.

Negative / migration:

- One-time `.env` migration: `AISW_CLAUDE_CONFIG_DIR_LOCAL` →
  `AISW_CLAUDE_CONFIG_DIR`.
- `settings.py` code change (field + validator + removal of the property) plus
  call-site review. Executed via `feature-workflow`, not in this ADR.

## Sources

- ADR-001 (superseded by this ADR)
- `src/ai_steward_wiki/settings.py` — env-resolved property and slots
- `src/ai_steward_wiki/claude_cli/common.py` — `build_env`, `neutral_cwd`
- `src/ai_steward_wiki/__main__.py` — `claude_config_dir is None` guards
- D-013 (subscription auth, shared config dir), D-038 (process isolation)
