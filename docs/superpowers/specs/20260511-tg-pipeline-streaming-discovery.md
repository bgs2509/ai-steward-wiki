---
id: 20260511-tg-pipeline-streaming
chunk: 21
module: M-TG-PIPELINE-STREAMING
bd_id: aisw-a3z
status: approved
related_decisions: [DEC-L2, D-026]
depends_on_chunks: [20]
fr:
  - id: FR-1
    text: Pipeline launches WikiRunner and starts a 5s timer; if runner completes in ≤5s use single deliver (fast path, no streaming).
  - id: FR-2
    text: If runner exceeds 5s, send a placeholder "⏳ Думаю…" message and begin streaming assistant chunks into it via StreamEditor (1.5s tick / Δ50 / chain-split 4000).
  - id: FR-3
    text: Final flush always emits full balanced text, even on runner exception (D-026 guarantee).
  - id: FR-4
    text: Streaming consumes assistant_chunk events from WikiRunner; chunk pipeline ends by writing the full text to deliver_output (audit log + final TG state).
  - id: FR-5
    text: Emit log markers tg.pipeline.stream.{begin,chunk,final,error}.
  - id: FR-6
    text: Back-compat — if streaming wiring is absent (None), pipeline behaves exactly as chunk-20 (single deliver, no placeholder).
nfr:
  - id: NFR-1
    text: Streaming path adds <50 ms of overhead per assistant_chunk on fast machines (verified by unit test fake-clock).
  - id: NFR-2
    text: Fast path (≤5s) MUST send exactly one TG message (no placeholder churn).
  - id: NFR-3
    text: HTML balance preserved at every edit boundary (StreamEditor already enforces; no regression).
  - id: NFR-4
    text: ru-only user-facing strings.
constraints:
  - id: CONS-1
    text: WikiRunner.run_wiki_session currently buffers all events before return — chunk 21 MUST extend it with an on_event callback hook (additive, non-breaking).
  - id: CONS-2
    text: WikiRunner has no estimated_duration_s metadata — trigger is wall-clock 5s race, NOT pre-estimate (DEC-L2 amended; see design DEC-TPS-1).
  - id: CONS-3
    text: Pipeline must use existing StreamEditor (src/ai_steward_wiki/tg/stream_edit.py); no new editor class.
  - id: CONS-4
    text: Final TG state and audit-log delivery routes through existing OutputDelivery.deliver (DEC-TPC-5 from chunk 20).
risks:
  - id: R-1
    text: Race between runner finishing and 5s timer firing — could double-send (placeholder + final). Mitigation - latch "streaming_started" flag; placeholder only sent if latch held when timer fires.
  - id: R-2
    text: on_event callback may raise and break the runner. Mitigation - try/except around callback; runner continues, marker tg.pipeline.stream.error logged.
  - id: R-3
    text: Buffered events processed faster than realtime when stream finishes after 5s — StreamEditor must accept burst-feed without losing chunks (already supported - buffer accumulates, throttle decides when to edit).
  - id: R-4
    text: Aggregated assistant text must match deliver_output text — single source of truth via aggregate_text() over the same events list.
  - id: R-5
    text: Fast path that races past 5s by 100ms still sends placeholder. Acceptable — degraded UX one-off, not a correctness issue.
scope_in:
  - Add on_event callback to run_wiki_session (additive arg, default None)
  - StreamingDelivery component in tg/pipeline that orchestrates placeholder + StreamEditor + race
  - Wire StreamingDelivery via Protocol injection into DefaultPipeline (back-compat None)
  - Unit tests for fast/slow/exception paths + chain-split + back-compat
scope_out:
  - Voice streaming (text-only this chunk; voice still goes through normal deliver)
  - Estimated_duration_s heuristic (deferred indefinitely; race pattern subsumes need)
  - TG MessageReactionUpdated / typing-action (not part of D-026)
sentrux:
  applies: false
  reason: .sentrux/rules.toml not present in repo
---

# Discovery — chunk 21 (M-TG-PIPELINE-STREAMING)

## Real intent

UX gap: Claude CLI responses regularly take 30–60 s. Without feedback the user thinks the bot is hung. D-026 specified streaming edits as the solution; chunk 10 built StreamEditor; chunk 21 wires it into the pipeline.

## Building blocks audit

1. **StreamEditor** (`tg/stream_edit.py`) — exists, complete (1.5s/Δ50/chain-split 4000, HTML-balanced, idempotent finalize). Reuse as-is.
2. **WikiRunner** (`wiki/runner.py`) — `run_wiki_session` returns `WikiRunResult` *after* full subprocess drain. It already streams stream-json events internally (`_drain` async generator), but the events list isn't exposed mid-run. **Need:** add `on_event: Callable[[StreamEvent], Awaitable[None]] | None` callback param (additive).
3. **aggregate_text** (`wiki/runner.py`) — pure helper from chunk 20; reuse to compute final delivered text from the same events list.
4. **DEC-L2 amendment:** breakdown.xml says "activates when estimated_duration_s > 5" — but that metadata doesn't exist. We use a **race pattern** (wall-clock 5s) which produces equivalent UX without speculative estimation. Captured as DEC-TPS-1 in design + ADR.

## Blind spots resolved

1. **What if runner finishes between 4.9s and 5.1s?** Either pre-trigger fast-path or post-trigger streaming-with-one-edit. Both are correct; both deliver the same final text via deliver_output.
2. **What if classifier already ran before this chunk's wiring?** Yes (chunk 20). Streaming wraps only the WikiRunner segment; classifier/L2/Inbox paths unchanged.
3. **L1/L2 dedup ACK short-circuits?** No runner invocation → no streaming. Already handled in chunk 20.

## Best-practice notes

1. **Race-then-stream** is the standard pattern (Slack/Discord typing indicators, ChatGPT first-token latency display).
2. **Latch-based placeholder** avoids double-send (Anthropic streaming SSE docs use the same idea: `message_start` event acts as the latch).
3. **Callback-on-event** is the additive, non-breaking way to expose streaming from a previously-buffered runner.

## Out-of-scope

1. Voice path streaming — voice ack is small + fast (transcript+short answer); add later if needed.
2. Replacing aggregate_text with mid-run incremental text — buffer-vs-stream symmetry preserved by feeding chunks AND letting deliver use aggregate_text on full events list.
