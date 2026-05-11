# Chunk 10 — M-TG-TEXT — Completion Report

- **bd_id:** aisw-187
- **Date:** 2026-05-11
- **Status:** complete (chunk closed)
- **Quality gate:** ruff check OK, ruff format OK, mypy OK (49 files), grace lint 0 errors, pytest unit 193/193 green.

## Scope delivered

1. `tg/bot.py` — aiogram 3 `Bot` factory with HTML default parse_mode, `Dispatcher` factory wiring `AllowlistMiddleware` as outer middleware, `TgSender` / `SentMessage` Protocols + `AiogramSender` adapter.
2. `tg/middleware_auth.py` — `AllowlistMiddleware` (D-031): denies unknown `telegram_id` with Russian one-liner, emits `auth.deny`, injects `user_record` + `telegram_id` into handler data on allow.
3. `tg/confirm.py` — graduated 3-tier confirmation flow (D-023): `auto_ack` / `implicit_ack` / `request_explicit` + race-safe `resolve` (UPDATE WHERE status='pending'), idempotent on `(telegram_id, payload_hash)`, 10-minute TTL, `expire_due` reaper. Persisted in `sessions.pending_confirms` (extended ORM additively).
4. `tg/output.py` — output-size hybrid (D-025): inline ≤3500 / chain-split ≤10000 (HTML balancer + semantic walk-back boundaries + `(i/M)` footers) / >10000 Haiku-summary (Protocol; `LengthCapSummarizer` fallback) + document. Always persists `<runs_dir>/<date>/<run_id>.md` (YAML frontmatter, sha256) and records `audit.run_outputs` row.
5. `tg/stream_edit.py` — streaming edits (D-026): 1.5 s / Δ50 throttle, 4000-char chain-split, final-flush idempotency, balancer-aware splitting, sender failures logged + swallowed.
6. ORM extension on `PendingConfirm` (additive columns `status`, `category`, `chat_id`, `recap_message_id`, `draft_json`) — relies on baseline `Base.metadata.create_all`.

## Tests

33 new tests under `tests/unit/tg/` — middleware (4), confirm (9), output (10), stream_edit (8), bot wiring (1) + 1 fixture module. All unit suites total 193/193.

## Design decisions (3x-rule log)

1. **TTL storage in sessions.db** rather than jobs.db — pending confirms are short-lived session state, not jobs.
2. **Stack-based HTML balancer** over regex tag-fixer — deterministic, supports nesting, whitelist `{b,i,u,s,a,code,pre}`.
3. **Synchronous "feed + decide" throttle** instead of background asyncio loop — deterministic and testable with `FakeClock`.
4. **`LengthCapSummarizer` fallback** for >10000 case — real Haiku adapter deferred to chunk 12 to avoid coupling to classifier backend during this chunk.

## Deviations from breakdown.xml

None.

## Follow-ups

1. Wire real Haiku summarizer adapter (Anthropic / Claude CLI backend) in a later chunk.
2. Integration test against real Telegram sandbox (`RUN_INTEGRATION=1`) — deferred to chunk 11+.
3. `audit.run_outputs` will get retention/rotation policy as part of ops chunks.
