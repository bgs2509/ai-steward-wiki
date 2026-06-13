# ADR-001: Env-resolved `CLAUDE_CONFIG_DIR` (local override, VPS default)

- Status: Superseded by [ADR-009](ADR-009-claude-config-dir-single-field.md) (2026-06-14)
- Date: 2026-05-11
- Deciders: @bgs

> **Superseded (2026-06-14).** The `env`-resolved two-slot design below was
> retired by [ADR-009](ADR-009-claude-config-dir-single-field.md). The
> `AISW_ENV=vps → None → ~/.claude/` branch never worked after the
> `aisw-d3i` / `aisw-adj` refactors (the runtime requires a concrete path), and
> the `env`-coupling caused a silent no-reply outage on 2026-06-14.
> `claude_config_dir` is now a single explicit field decoupled from `AISW_ENV`;
> `AISW_ENV` governs only the Telegram token. This document is kept as the
> historical record of the original decision.

## Context

Claude Code CLI defaults its config (auth `credentials.json`, `settings.json`, agents, history, sessions) to `~/.claude/`. An undocumented env var `CLAUDE_CONFIG_DIR` overrides this location.

ai-steward-wiki runs in two profiles selected by `AISW_ENV` (`local` | `vps`):

- **local** — developer machine. The bot must NOT use the developer's personal `~/.claude/` (would mix credentials, hooks, MCPs, settings). It needs an isolated, dedicated config dir.
- **vps** — dedicated service host. The service user `aisw-bot` has no other purpose; using `~/.claude/` of that user is fine and matches the documented happy path.

Risks of `CLAUDE_CONFIG_DIR`:
- not documented in `code.claude.com/docs` (see Sources)
- behaviour may drift between CLI versions
- some state (`~/.claude.json` — OAuth state, MCPs, project state) is reported to leak to `~/` regardless of the override (issue #3833)
- IDE / VS-Code-ext don't fully respect it (irrelevant for our headless setup)

## Decision

`Settings.claude_config_dir` becomes an env-resolved `@property` backed by two slots:

```python
claude_config_dir_local: Path | None = Path("/var/lib/ai-steward-wiki/claude-code")
claude_config_dir_vps: Path | None = None  # None → CLI uses ~/.claude/

@property
def claude_config_dir(self) -> Path | None:
    return self.claude_config_dir_vps if self.env == "vps" else self.claude_config_dir_local
```

Subprocess wiring (`wiki/runner.py`, `classifier/backend.py`) only sets `CLAUDE_CONFIG_DIR` in `env` **if the resolved value is not None**; otherwise the variable is omitted and CLI uses its default `~/.claude/` lookup.

`.env.example`:

```dotenv
AISW_CLAUDE_CONFIG_DIR_LOCAL=/var/lib/ai-steward-wiki/claude-code
# AISW_CLAUDE_CONFIG_DIR_VPS=    # leave unset → CLI uses ~/.claude/
```

INV-6 (Stage-0 API credential MUST NOT collide with OAuth dir) is preserved — the validator compares against the resolved value, skipping the check when it is `None`.

## Alternatives considered

1. **Single optional `claude_config_dir: Path | None`** with no env-binding. Rejected: duplicates the env-selection mechanism already used for TG tokens; two independent rebases for one concept invite drift.
2. **Hardcoded `if env == "local"` inside subprocess wiring**. Rejected: violates Explicit > Implicit and OCP — adding a staging profile means code patch, not config.
3. **`systemd EnvironmentFile=` only**. Rejected as MVP: requires production-grade infra to be useful; pydantic-settings already gives the same isolation locally.

## Consequences

Positive:
- single SSoT for environment selection (`AISW_ENV`)
- production VPS uses the documented happy path (`~/.claude/`) — minimal exposure to the undocumented variable
- local dev keeps a dedicated dir, no risk of clobbering `~/.claude/`
- INV-6 stays intact

Negative / risks:
- still depends on `CLAUDE_CONFIG_DIR` semantics on local; pinned CLI version recommended in install scripts
- `~/.claude.json` leakage (issue #3833) — accepted; non-critical state only, can be symlinked into `$CLAUDE_CONFIG_DIR/` later if it becomes a problem

## Sources

- Issue #3833 — CLAUDE_CONFIG_DIR behavior unclear (still creates local `.claude/`)
- Issue #25762 — Feature request: env var to configure `.claude` location
- Issue #28808 — Feature request: `CLAUDE_CONFIG_DIR`
- Issue #4739 — `/ide` fails with `CLAUDE_CONFIG_DIR`
- Issue #2986 — Local install detection ignores `CLAUDE_CONFIG_DIR`
- Issue #30538 — VS Code extension ignores `CLAUDE_CONFIG_DIR`
- code.claude.com/docs/en/claude-directory (no mention of the variable)
