---
id: 20260511-tg-pipeline-streaming
chunk: 21
module: M-TG-PIPELINE-STREAMING
bd_id: aisw-a3z
status: approved
decisions:
  - id: DEC-TPS-1
    title: Streaming trigger
    choice: Wall-clock 5s race (not pre-estimate). Latch-based placeholder; runner-completes-first → fast path single deliver; 5s elapsed → send placeholder + start StreamEditor.
    rationale: WikiRunner has no estimated_duration_s; pre-estimate is speculative. Race is deterministic, simple, equivalent UX.
    supersedes: DEC-L2 (interpretation only — DEC-L2 intent preserved)
  - id: DEC-TPS-2
    title: Runner streaming hook
    choice: Add on_event callback param to run_wiki_session. Additive, default None → existing behaviour. Callback awaited inside drain loop; exceptions logged and swallowed (runner not impacted).
    rationale: Non-breaking, minimal API surface, keeps StreamEvent type internal to wiki package.
  - id: DEC-TPS-3
    title: Streaming component placement
    choice: New class StreamingDelivery in tg/pipeline.py (alongside DefaultPipeline). Owns placeholder send, race timer, StreamEditor lifecycle.
    rationale: Same module as composition orchestrator; reuses TgSender; not framework-thin enough to warrant separate file.
  - id: DEC-TPS-4
    title: Final text source-of-truth
    choice: After runner.run returns (or raises), aggregate_text(events) is the canonical final text used by both StreamEditor.finalize and OutputDelivery.deliver. Events captured by a callback into a local list owned by StreamingDelivery.
    rationale: Single source — no drift between TG visible state and audit log.
  - id: DEC-TPS-5
    title: Back-compat
    choice: StreamingDelivery is an optional Protocol injection on DefaultPipeline (param `streaming`). If None → existing chunk-20 deliver path. All existing unit tests remain valid unchanged.
    rationale: Zero-risk rollout; tests prove no regression.
  - id: DEC-TPS-6
    title: Voice path scope
    choice: Voice on_voice does NOT use streaming in chunk 21 (transcripts are short, typical answer fits one message).
    rationale: Lowest risk MVP; can add later by feeding voice through same StreamingDelivery.
log_markers:
  - tg.pipeline.stream.begin
  - tg.pipeline.stream.chunk
  - tg.pipeline.stream.final
  - tg.pipeline.stream.error
---

# Design — chunk 21 (M-TG-PIPELINE-STREAMING)

## Architecture

```
DefaultPipeline.on_text
 ├─ classifier → L2 dedup (unchanged from chunk 20)
 └─ if streaming is None:
       runner.run(...) → output.deliver(text)           # chunk-20 path
    else:
       streaming.run_and_deliver(                       # new path
           runner_callable, chat_id, telegram_id,
           text, intent, correlation_id, output
       )
```

`StreamingDelivery.run_and_deliver`:

1. Create a `done = asyncio.Event()` + `events: list[StreamEvent] = []` + latch `placeholder_msg_id: int | None = None`.
2. Define `on_event(ev)`: append ev to events list.
3. Launch `runner_task = asyncio.create_task(runner.run(..., on_event=on_event))`.
4. Try `await asyncio.wait_for(runner_task, timeout=5.0)`:
   - Success → `text = aggregate_text(events) or runner_outcome.text`; `output.deliver(...)`; emit `tg.pipeline.stream.skipped` (fast path).
   - `TimeoutError` → enter slow path:
     - send placeholder `"⏳ Думаю…"` → `placeholder_msg_id`
     - emit `tg.pipeline.stream.begin`
     - create `StreamEditor(sender, chat_id, placeholder_msg_id)`
     - replay buffered events via `editor.feed(...)` for chunks accumulated so far
     - install live callback: subsequent `on_event` calls feed editor directly
     - await `runner_task` (no further timeout — overall runner has its own timeout)
     - on completion: `text = aggregate_text(events)`; `editor.finalize()`; `output.deliver(...)`; emit `tg.pipeline.stream.final`
5. On runner exception: `editor.finalize()` (if editor created); `output.deliver(...)` with partial text or runner-error ack; emit `tg.pipeline.stream.error`. Then re-raise (caller handles ack mapping).

Race-correctness: the latch is the `placeholder_msg_id` itself — set under `asyncio.Lock` only when entering slow path; runner_task completion path checks latch and stays fast if None.

## Runner change

```python
# wiki/runner.py
async def run_wiki_session(
    *,
    ...,
    on_event: Callable[[StreamEvent], Awaitable[None]] | None = None,
) -> WikiRunResult:
    ...
    async for ev in parse_stream_json(proc.stdout):
        events.append(ev)
        if on_event is not None:
            try:
                await on_event(ev)
            except Exception as exc:
                _log.warning("wiki.run.on_event_error", error=type(exc).__name__)
        ...
```

`WikiRunner` Protocol in `tg/pipeline.py` gets an optional `on_event` kwarg with the same signature. `_WikiRunnerAdapter` in `__main__.py` forwards it.

## Pipeline composition (text path, slow side)

The `_run_text_pipeline` helper from chunk 20 inserts one branch:

```python
if self._streaming is not None:
    outcome = await self._streaming.run_and_deliver(
        runner=self._runner, output=self._output,
        chat_id=chat_id, telegram_id=telegram_id,
        text=text, intent=intent, correlation_id=correlation_id, run_id=run_id,
    )
else:
    # chunk-20 path
    outcome = await self._runner.run(...)
    text_out = outcome.text or aggregate_text([])
    await self._output.deliver(chat_id=chat_id, telegram_id=telegram_id, run_id=outcome.run_id, text=text_out or ACK_TEXT_RU)
```

## Tests (TDD outline)

1. `test_fast_path_no_placeholder_single_deliver` — fake runner returns in 0.1s → no placeholder send, one deliver call.
2. `test_slow_path_sends_placeholder_then_streams` — fake runner takes 6s (faketime) emitting 5 chunks → 1 send (placeholder) + ≥1 edits + 1 deliver.
3. `test_buffered_events_replayed_after_placeholder` — runner emits 3 chunks before 5s mark → editor.feed called 3× during entry, then live.
4. `test_runner_exception_during_streaming_finalizes` — runner raises after 6s + 2 events → editor.finalize called, deliver called with partial-or-error text, exception re-raised.
5. `test_back_compat_no_streaming_injection_uses_chunk20_path` — DefaultPipeline(streaming=None) → exactly chunk-20 behaviour.
6. `test_on_event_callback_error_does_not_kill_runner` — callback raises → runner finishes normally, warning marker logged.
7. `test_aggregate_text_drives_final_deliver_text` — events with 3 text fragments → deliver receives the concatenation.

## Out-of-scope (re-stated)

Voice streaming, estimate-based trigger, mid-run cancellation UI. None block this chunk.
