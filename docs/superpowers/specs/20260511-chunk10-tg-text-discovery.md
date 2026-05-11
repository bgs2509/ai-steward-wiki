---
feature: M-TG-TEXT
bd_id: aisw-187
status: stable
date: 2026-05-11
chunk: 10
sources:
  - docs/Spec-WIKI/research/tech-spec-draft.md §9 (TG I/O), §10.4 (retention)
  - docs/Spec-WIKI/decisions/D-023-tg-confirmations.md
  - docs/Spec-WIKI/decisions/D-025-output-size.md
  - docs/Spec-WIKI/decisions/D-026-tg-streaming.md
  - docs/Spec-WIKI/decisions/D-031-allowlist-hot-reload.md
  - docs/Spec-WIKI/decisions/D-042-identity-vocabulary.md
---

# Discovery — Chunk 10 / M-TG-TEXT

## Goal

Build the text-side Telegram I/O layer for ai-steward-wiki MVP: aiogram 3 dispatcher
with allowlist gating, graduated 3-tier confirmation flow, output-size hybrid
policy, HTML-safe streaming edits, and `audit.run_outputs` persistence.

## Functional Requirements

1. **FR-1 aiogram 3 dispatcher** — single `Bot` + `Dispatcher` factory wiring
   middleware + handlers. Async. No global side-effects on import.
2. **FR-2 Allowlist gate** — middleware rejects updates whose `from_user.id` is
   not in `auth.allowlist.get_global()`. Replies a single Russian denial line;
   logs `auth.deny` event. Allowed updates carry `user_record` into handler data.
3. **FR-3 Graduated confirmations (D-023)** — three levels:
   1. `auto` — execute immediately, 1-line ack.
   2. `implicit` — recap + optional inline keyboard, no block on click.
   3. `explicit` — recap + 3-button keyboard (`✅ Подтвердить` / `✏️ Изменить`
      / `❌ Отмена`) with mandatory click/free-form. Persist draft in
      `sessions.pending_confirms` with 10-minute TTL (configurable per
      category). Expired pending → status transitions, audit event.
4. **FR-4 Output size hybrid (D-025)** — given an output string + chat:
   - `≤ 3500 chars` → single message, HTML parse_mode, balanced tags.
   - `3500 < N ≤ 10000` → chain-split into ≤3 messages with `(i/M)` markers;
     splits prefer `<b>` header → blank line → sentence boundary; HTML balancer
     closes/reopens tags across boundaries.
   - `> 10000 chars` → Haiku-summary (≤1500 chars, RU) + `send_document` with
     full text as `<run_id>.md`. Summary delivered as a single TG message,
     full body always persisted to disk.
   - **Always-persist:** write `<wiki>/data/runs/<YYYY-MM-DD>/<run_id>.md`
     with YAML frontmatter, and upsert a row into `audit.run_outputs`.
5. **FR-5 Stream-edit (D-026)** — async accumulator that edits a single TG
   message; tick interval `1.5s` OR `Δ ≥ 50 chars` (whichever first).
   On approaching `4000 chars` chain-split (open new placeholder message,
   move edit-target). **Final-flush guarantee:** on stream end / exception /
   cancel the final state is always emitted; HTML tags balanced.
6. **FR-6 Persist `run_outputs`** — every delivered output (inline, chain,
   document) records `run_id, job_id?, wiki_id, owner_telegram_id, kind,
   output_path, output_bytes, output_sha256, summary_chars?, started_at_utc,
   finished_at_utc` in `audit.db.run_outputs`.

## Non-functional Requirements

1. **NFR-1 Type safety** — `mypy --strict` clean for all new modules.
2. **NFR-2 Structured logging** — every code path logs structlog events with
   `correlation_id, user_id (telegram_id), wiki_id?, job_id?, run_id?, event`.
3. **NFR-3 Identity vocabulary (D-042)** — code uses `telegram_id` for
   external id, `chat_id` for delivery target, never aliases them.
4. **NFR-4 Russian user-facing strings** — every TG-bound text in Russian.
5. **NFR-5 No real network in unit tests** — bot/Telegram interactions
   exercised via fakes (recorder of send/edit/document calls).
6. **NFR-6 Idempotency on confirmation** — duplicate `(telegram_id, payload_hash)`
   pending records collapsed; race confirm-vs-expire resolved by UPDATE on
   `WHERE status='pending'`.
7. **NFR-7 Hooks defended** — `make lint`, `make grace-lint`, `make total-test`
   must pass after the chunk; no `--no-verify` bypass.

## Scope

In: src/ai_steward_wiki/tg/{__init__.py, bot.py, middleware_auth.py,
confirm.py, output.py, stream_edit.py}; tests/unit/tg/* mirroring the layout;
new sessions migration adds `status`, `category`, `draft_json` columns to
`pending_confirms` *only if absent* (current baseline has minimal schema).

Out: voice/photo handlers (chunk 11), onboarding/admin flow (chunk 12),
real integration tests against the TG sandbox (deferred to RUN_INTEGRATION).

## Risks

1. Schema drift between `D-023` storage sketch and existing `PendingConfirm`
   ORM. Mitigation: add a thin in-memory + DB-row adapter that maps fields,
   and add an Alembic migration with the required columns (category, status,
   draft_json, recap_message_id, chat_id) preserving baseline.
2. HTML balancer correctness across truncation. Mitigation: pure function with
   exhaustive unit tests on nested + interleaved tags.
3. Throttle correctness under bursty streams. Mitigation: monotonic-clock
   based controller fake-clock unit-tested.

## Acceptance

`uv run pytest tests/unit/tg -q` ≥ 30 tests passing; `make total-test` exit 0;
`make lint` 0 errors; `make grace-lint` 0 errors; commit
`feat(M-TG-TEXT): ...` with `bd_id: aisw-187` trailer.
