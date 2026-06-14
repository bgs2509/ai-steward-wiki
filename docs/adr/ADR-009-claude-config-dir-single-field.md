# ADR-009: Use the run user's default `~/.claude` — no config-dir setting

- Status: Accepted
- Date: 2026-06-14
- Deciders: @bgs
- Supersedes: ADR-001
- Related: ADR-008 (dev/life separation), ADR-010 (isolation), D-013, D-038
- Note: this decision evolved within the 2026-06-14 design session. An earlier
  same-day draft of ADR-009 proposed a single explicit `claude_config_dir` field
  defaulting to a dedicated `/var/lib/ai-steward-wiki/claude-code`; that draft was
  superseded before merge by the decision recorded here.

## Context

ADR-001 made `Settings.claude_config_dir` an `env`-resolved two-slot property
(`claude_config_dir_local` for `local`, `None`→`~/.claude` for `vps`). Two defects
surfaced on 2026-06-14:

1. The `vps`/`None` branch was dead — the runtime requires a concrete path
   (`__main__` raised on `None`; `build_env`/`neutral_cwd` take non-optional
   `Path`). The "VPS uses `~/.claude/`" happy path never worked after the
   `aisw-d3i`/`aisw-adj` refactors.
2. The `env`-coupling caused a silent no-reply outage: `AISW_ENV=local` on the VPS
   resolved a dedicated dir that was never created → `FileNotFoundError` on every
   classification.

Decisions taken this session that frame the final choice:

- **ADR-010:** run under the existing `bgs` account, no dedicated service user.
- A dedicated `CLAUDE_CONFIG_DIR` was considered as a way to keep the bot's Claude
  state separate from the human's. But **verified** (claude-code-guide, claude
  2.1.175, `--help` + docs): `CLAUDE_CONFIG_DIR` relocates only settings /
  credentials / sessions — it does **not** relocate the user-layer memory file
  `~/.claude/CLAUDE.md`, which loads unconditionally regardless of
  `CLAUDE_CONFIG_DIR` and is not suppressed by `--system-prompt` or
  `--setting-sources ""`. So a dedicated dir does **not** provide
  instruction-isolation (that concern is tracked separately, see ADR-008 note and
  `aisw-aqo`). With instruction-isolation off the table as a justification, and the
  bot running under `bgs` whose `~/.claude` is already authenticated, a dedicated
  dir adds a provisioning step (`mkdir` + `claude login`) for no benefit the
  trusted single-user deployment needs.

## Decision

1. **Remove the `claude_config_dir` setting entirely** — no Settings field, no
   `AISW_CLAUDE_CONFIG_DIR` env var, no `.env.example` entry.
2. The bot uses the **run user's default `~/.claude`**, resolved at runtime via
   `claude_cli.common.default_claude_config_dir()` (`Path.home() / ".claude"`),
   which is fed to the classifier backend, wiki runner, and cron consumer
   (`build_env` / `neutral_cwd` / `systemd-run --setenv` unchanged — they still
   receive a concrete path, now the default). Setting `CLAUDE_CONFIG_DIR` to the
   absolute `~/.claude` keeps the restricted subprocess env (no `HOME`) working.
3. `AISW_ENV` governs **only** the Telegram token (`tg_bot_token_local/_prod`).
4. **Fail-fast** at startup (`_require_claude_config_dir()` in `_amain`) if
   `~/.claude` is missing/unauthenticated, with an actionable `claude login` hint.
5. INV-6 (API-credential isolation) compares the API credential against `~/.claude`.

## Alternatives considered

1. **Single explicit field, dedicated `/var/lib/...` default** (the earlier
   same-day draft). Rejected: under ADR-010 (run as `bgs`, no service user) the
   dedicated dir only added a `mkdir` + `claude login` step; it did not isolate
   instructions (verified above) and `~/.claude` is already authenticated.
2. **Keep an optional `AISW_CLAUDE_CONFIG_DIR` override.** Rejected per explicit
   user directive — remove it entirely; reintroduce only if a real need appears.
3. **Two env-resolved slots (ADR-001).** Rejected: dead `None` branch, caused the
   outage, zero working divergence.

## Consequences

Positive:

- Zero config and zero setup: `bgs` is already logged in to `~/.claude`; no
  dedicated dir to create or authenticate. Removes the outage class.
- One less setting; `AISW_ENV` has a single, clear job (the token).

Negative / trade-offs:

- The bot shares `~/.claude` with the developer's interactive Claude usage on the
  `bgs` account (auth, sessions, history). Same subscription/person; low risk of
  `~/.claude.json` contention from frequent `-p` runs; accepted for this trusted,
  single-user, hobby-scale deployment.
- This does **not** fix dev→life instruction mixing — the bot still loads
  `bgs`'s global `~/.claude/CLAUDE.md`. That was never solved by the config dir;
  see ADR-008 (corrected) and `aisw-aqo`.

## Sources

- ADR-001 (superseded), ADR-008, ADR-010
- claude-code-guide verification (claude 2.1.175): `CLAUDE_CONFIG_DIR` does not
  relocate `~/.claude/CLAUDE.md`; `--system-prompt` / `--setting-sources ""` do
  not suppress it; only `--bare` disables CLAUDE.md auto-discovery (needs
  `ANTHROPIC_API_KEY`).
- `src/ai_steward_wiki/claude_cli/common.py` (`default_claude_config_dir`),
  `settings.py`, `__main__.py`
