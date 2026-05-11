# Plan — chunk 21 (M-TG-PIPELINE-STREAMING)

bd_id: aisw-a3z. SSoT for execution. Each step has its own failing test before code.

## Step 1 — Extend WikiRunner with on_event callback

- Edit `wiki/runner.py` `run_wiki_session`: add `on_event: Callable[[StreamEvent], Awaitable[None]] | None = None` kw-only arg.
- Inside `_drain` after `events.append(ev)`, if callback present: `try: await on_event(ev) except Exception: _log.warning("wiki.run.on_event_error", error=...)`.
- Update __all__/MODULE_MAP if signature change leaks.
- Add tests in `tests/unit/wiki/test_runner_on_event.py`:
  - test_on_event_called_per_event (3 events → 3 callback calls)
  - test_on_event_exception_swallowed_and_logged
  - test_no_callback_back_compat

## Step 2 — Streaming protocol + StreamingDelivery class

In `src/ai_steward_wiki/tg/pipeline.py`:

- Add `StreamingDelivery` Protocol: `async def run_and_deliver(self, *, runner, output, chat_id, telegram_id, run_id, text, intent, correlation_id) -> None`.
- Add `DefaultStreamingDelivery` class implementing the race + latch + StreamEditor lifecycle (per design).
- Update `WikiRunner` Protocol: add `on_event` kw param.

## Step 3 — Wire optional `streaming` param into DefaultPipeline

- New ctor kwarg `streaming: StreamingDelivery | None = None`.
- Replace the runner+deliver block in `_run_text_pipeline` with conditional branch (per design).
- Voice path unchanged.

## Step 4 — Unit tests for streaming wiring

Create `tests/unit/tg/test_pipeline_streaming.py` covering 7 scenarios from design § Tests.

## Step 5 — Adapter in __main__.py

- Construct `DefaultStreamingDelivery(sender=...)` and pass into `DefaultPipeline(..., streaming=...)`.
- Update `_WikiRunnerAdapter.run` to forward `on_event`.

## Step 6 — Verification + finish

- `make lint`, `make total-test`, `grace lint --failOn errors`.
- Update verification-plan.xml with V-M-TG-PIPELINE-STREAMING.
- Update knowledge-graph.xml / development-plan.xml Phase-21 → done.
- Completion report.
- Commit `feat(M-TG-PIPELINE-STREAMING): ...`.
- `bd close aisw-a3z`, `bd dolt push`.
