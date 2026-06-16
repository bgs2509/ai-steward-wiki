# ADR-030: Debounce-aggregate split/burst messages before classify/route

**Status:** Accepted
**Date:** 2026-06-16
**Beads:** aisw-378
**Relates:** [D-018](../Spec-WIKI/decisions/D-018-ingest-idempotency.md) (idempotency), ADR-031 (routing)

## Context

Telegram clients split a long pasted message (>4096 chars) into several separate
messages, and aiogram dispatches each `Update` concurrently. The pipeline called
`pipeline.on_text` per message with no per-chat serialization, so **one logical
document became N independent classify/route decisions**. A 12 009-char coal report
arrived as two messages and was routed to two different WIKIs (Medical via the
keyword fast-path on the homonym "анализ", Investment via the Sonnet router) — the
whole document was never seen as one thing, so the "new topic" was never recognized.

Split text carries no Telegram grouping id (unlike photo albums' `media_group_id`),
so messages can only be recombined by **time + chat**.

## Decision

Introduce `tg.aggregator.InboxAggregator` between the handlers and the pipeline:

1. **Per-chat buffer + 3s debounce.** Each text `submit` appends to the buffer and
   restarts the window; when it elapses quietly, the buffered parts are concatenated
   in `message_id` order and handed to `pipeline.on_text` **once**. An `epoch`
   counter guards against a stale flush firing after a newer message.
2. **Loader lifecycle.** A "⏳ Думаю…" message is reposted on every message so it
   stays the chat's latest message, kept up through processing, removed after
   (loader failures never block aggregation).
3. **Pure asyncio + Protocol seams** (`LoaderControl`, `ProcessText`) so the timer
   logic is unit-tested without real sleeps or a live bot.
4. **Wiring:** `BotLoaderControl` (real loader via the Bot); `build_router` /
   `build_dispatcher` gain an `aggregator` param; `_on_text` submits to it instead of
   calling `on_text`. Window is `settings.tg_aggregate_delay_s=3.0`.
5. **Companion fast-path hardening:** the deterministic hint fast-path is skipped for
   long inputs (`hint_match.MAX_FASTPATH_CHARS=600`) — incidental keyword overlap on a
   long document (e.g. "анализ" hitting Medical) is unreliable, so long text always
   goes to the context-aware Sonnet router.

## Consequences

- A split/burst document is recombined into one input → one routing decision; the
  Medical+Investment fragmentation is gone (verified: `flush n_parts=2 chars=12009` →
  one classify).
- Every text message now waits up to 3s (user-accepted) — a deliberate latency/
  correctness trade; the loader keeps the UX honest.
- **Text only.** Voice/photo bursts need `pipeline.on_voice`/`on_photo` to split
  preprocessing (STT/OCR) from routing first — deferred to aisw-90t.
- New events: `tg.aggregator.buffered|flush|loader_*`.
