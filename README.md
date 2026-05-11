# ai-steward-wiki

Isolated multi-user Telegram service that turns Claude Code CLI into a personal
Karpathy-style WIKI assistant. See `CLAUDE.md` for project conventions and
`docs/Spec-WIKI/research/tech-spec-draft.md` for the full technical specification.

## Local run (test bot)

```bash
# 1. Install deps
uv sync

# 2. Configure .env (already in repo root; fill in real values)
#    AISW_ENV=local
#    AISW_TG_BOT_TOKEN_LOCAL=<token from @BotFather>
#    AISW_TG_ADMIN_TELEGRAM_IDS=<your telegram_id>
#    AISW_JOBS_DB_URL=sqlite+aiosqlite:///data/jobs.db
#    AISW_AUDIT_DB_URL=sqlite+aiosqlite:///data/audit.db
#    AISW_SESSIONS_DB_URL=sqlite+aiosqlite:///data/sessions.db

# 3. Start (alembic migrations run automatically on first boot)
uv run python -m ai_steward_wiki

# Ctrl-C → graceful shutdown (stop polling → scheduler → engines → bot.session)
```

The bot starts, the allowlist middleware gates updates by `telegram_id`, and
unrouted messages are silently dropped. Production message handlers
(`TG → classifier → runner` pipeline) ship in a follow-up chunk.

## Quality gates

```bash
make lint          # ruff + ruff format + mypy --strict
make total-test    # lint + grace + 14 invariants + unit (≥80% coverage)
```
