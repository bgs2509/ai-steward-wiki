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
allowlisted text / voice / photo / document messages and confirm-callbacks
flow through `M-TG-HANDLERS-WIRING` → `DefaultPipeline` (L1 idempotency dedup,
optional voice/photo staging, ack delivery, confirmation resolve). Document
messages route by MIME (`M-TG-DOCUMENT-FULL`): `application/pdf` → pypdf text
extract → text pipeline, `text/*` → UTF-8 decode → text pipeline, `image/*` →
photo stage, else → polite rejection. L2 dedup on `doc_sha256` and tier-2
filename hashing protect against duplicates and PII leakage in logs.

## Quality gates

```bash
make lint          # ruff + ruff format + mypy --strict
make total-test    # lint + grace + 14 invariants + unit (≥80% coverage)
```

## Roadmap

1. **MVP** (chunks 1–17) — closed. See `docs/reports/20260511-ai-steward-wiki-mvp-report.md`.
2. **Post-MVP done:** chunk 18 `M-RUNTIME-WIRING`, chunk 19 `M-TG-HANDLERS-WIRING`.
3. **Path to production launch** (planned, pre-Beads draft):
   `docs/superpowers/plans/20260511-ai-steward-wiki-launch/` —
   `breakdown.xml` (chunks 20–23 with scope/depends/exit-criteria),
   `breakdown-summary.md` (human-readable), and
   `cutover-checklist.md` (one-shot production cutover runbook).
4. **Operational runbooks** (permanent): `docs/runbook/{deploy,operations,restore}.md`.
