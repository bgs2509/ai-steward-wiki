# step-10-plan.md — Chunk 10 / M-TG-TEXT

**bd_id:** aisw-187
**Module:** M-TG-TEXT
**Window estimate:** 0.60
**Sources:** D-023 (confirmations), D-025 (output size), D-026 (streaming),
D-031/D-042 (allowlist + identity), §9 of tech-spec-draft.md.

## Goal

Build the text-side TG I/O layer: aiogram 3 dispatcher + allowlist middleware,
graduated 3-tier confirmations with 10-minute TTL, output-size hybrid
(≤3500 / ≤10000 / >10000), HTML-safe streaming edits with throttle 1.5s/Δ50
and chain-split at 4000 chars and final-flush guarantee, plus
`audit.run_outputs` persistence on every delivery.

## Steps (TDD)

1. **Recon** — confirm `audit.run_outputs` baseline, `sessions.pending_confirms`
   minimal schema; review D-023/D-025/D-026.
2. **Alembic** — `alembic/sessions/versions/0002_pending_confirms_d023.py` adds
   columns `status, category, chat_id, recap_message_id, draft_json` (additive,
   safe on empty tables). Update ORM `PendingConfirm` Mapped fields.
3. **Tests RED** — `tests/unit/tg/`:
   - `test_middleware_auth.py` — allow allowed user, deny stranger (Russian
     reply, deny log), `user_record` injected to handler data.
   - `test_confirm.py` — `auto_ack`, `implicit_ack`, `request_explicit` (writes
     pending row + sends recap with 3-button keyboard), `resolve(confirm|
     correct|cancel)`, `expire_due` flips stale rows, idempotent duplicate,
     race resolve-vs-expire.
   - `test_output.py` — small (≤3500) inline, mid (≤10000) chain-split with
     `(i/M)` markers + balanced HTML across boundaries, large (>10000) summary
     + document; persistence to disk under `runs/YYYY-MM-DD/<run_id>.md` with
     frontmatter; `audit.run_outputs` row written; HtmlBalancer pure-fn cases.
   - `test_stream_edit.py` — tick triggers edit, delta triggers earlier edit,
     chain-split at threshold, final-flush on success and on exception,
     balancer applied to in-flight buffer.
   - `test_bot.py` — dispatcher exposes `dp.update.outer_middleware` containing
     `AllowlistMiddleware`; handlers registered.
4. **GREEN** — implement:
   - `src/ai_steward_wiki/tg/__init__.py` (BARREL)
   - `src/ai_steward_wiki/tg/bot.py` (`build_bot`, `build_dispatcher`,
     `AiogramSender` adapter, `TgSender`/`SentMessage` Protocols)
   - `src/ai_steward_wiki/tg/middleware_auth.py` (`AllowlistMiddleware`)
   - `src/ai_steward_wiki/tg/confirm.py` (`ConfirmationService`,
     `ConfirmLevel`, keyboard builder)
   - `src/ai_steward_wiki/tg/output.py` (`HtmlBalancer`, `ChainSplitter`,
     `deliver_output`, `HaikuSummarizer` Protocol + `LengthCapSummarizer`,
     `DeliveryReceipt`, persistence)
   - `src/ai_steward_wiki/tg/stream_edit.py` (`StreamEditor`)
   - ORM extension for `PendingConfirm` (new optional fields)
5. **Quality gate** — all must be green:
   - `uv run pytest tests/unit/tg -q`
   - `uv run pytest tests/unit -q`
   - `make lint`
   - `make grace-lint`
   - `make total-test`
6. **Commit** — `feat(M-TG-TEXT): aiogram dispatcher + allowlist + confirm + output + stream-edit`
   with `bd_id: aisw-187` trailer.
7. **Post-commit** — update `breakdown.xml` RunState (CurrentChunk=11,
   ClosedChunks+=10, status="closed"), write report, close bd.

## Out of scope

1. Voice + photo handlers (chunk 11).
2. Onboarding / admin flow (chunk 12).
3. Real `Haiku` summarizer wiring (deferred to chunk 12; Protocol stays here).
4. Real TG sandbox integration (deferred to `RUN_INTEGRATION=1` future work).
