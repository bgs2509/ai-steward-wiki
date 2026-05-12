# Implementation Plan — Streaming slow-path double reply (aisw-x92)

> SSoT for execution. Variant 1 (tg_send flag) + finalize no-op-on-unchanged.
> Specs: `docs/superpowers/specs/20260512-streaming-double-reply-{discovery,design}.md`

No new modules / no MODULE_CONTRACT changes — only `START_CHANGE_SUMMARY` bumps in 3 files.

## Task 1 — `deliver_output` gains `tg_send` (RED → GREEN)

**Test (RED)** — `tests/unit/tg/test_output.py`, new case `test_deliver_persists_without_tg_send`:
- `sender = FakeSender()`; call `deliver_output(..., run_id="r-skip", text="ответ", tg_send=False)`.
- Assert: `len(sender.sends) == 0`, `len(sender.documents) == 0`, `receipt.n_messages == 0`, `receipt.document_sent is False`, `receipt.output_path.exists()`.
- Assert audit row exists: `select(RunOutput)` → one row, `run_id == "r-skip"`.
- Run `uv run pytest tests/unit/tg/test_output.py::test_deliver_persists_without_tg_send` → FAIL (unexpected kwarg).

**GREEN** — `src/ai_steward_wiki/tg/output.py`:
- `deliver_output` signature: add `tg_send: bool = True` (keyword-only, place after `kind` or near `summarizer` — keep alphabetical-ish, doesn't matter, all kw-only).
- Wrap the `if len(text) <= INLINE_THRESHOLD: ... elif ... else ...` block in `if tg_send:`. Keep `summary_chars`, `document_sent`, `n_messages` at their defaults (`None`, `False`, `0`) when `tg_send` is False.
- `_persist_to_disk` stays before the block (already is); `_record_run_output` stays after (already is).
- `_log.info("tg.output.delivered", ...)` — add `tg_send=tg_send`.
- Bump header: `# VERSION: 0.0.2` and `LAST_CHANGE: v0.0.2 - aisw-x92: tg_send flag (skip TG send on streaming slow-path, keep persist+audit)`.
- Run the new test → PASS. Run full `tests/unit/tg/test_output.py` → all PASS.

## Task 2 — propagate `tg_send` through the Protocol + adapter

**GREEN (mechanical, covered by Task 3 test)** :
- `src/ai_steward_wiki/tg/pipeline.py:187` — `OutputDelivery.deliver` Protocol: add `tg_send: bool = True` kw-only param.
- `src/ai_steward_wiki/__main__.py:242` — `_OutputDeliveryAdapter.deliver`: add `tg_send: bool = True`; forward `tg_send=tg_send` to `deliver_output`.
- `uv run mypy src` → Success.

## Task 3 — streaming slow-path calls `deliver(..., tg_send=False)` (RED → GREEN)

**Test (RED)** — `tests/unit/tg/test_pipeline_streaming.py`: extend / add a slow-path case asserting the answer reaches the chat exactly once:
- Drive `DefaultStreamingDelivery.run_and_deliver` down the slow path (runner slower than timeout — existing tests already do this; mirror their setup).
- Use a fake `OutputDelivery` that records `(text, tg_send)` of each `deliver` call.
- Assert: exactly one `deliver` call, with `tg_send is False`. And the placeholder message got edited with the reply (existing assertions).
- Run → FAIL (current code calls `deliver` without `tg_send`, fake records `tg_send` default True / or signature mismatch).

**GREEN** — `src/ai_steward_wiki/tg/pipeline.py`, `DefaultStreamingDelivery.run_and_deliver` slow-path: the `await output.deliver(chat_id=..., telegram_id=..., run_id=outcome.run_id, text=final_text)` after `live_editor.finalize()` → add `tg_send=False`. Fast-path `deliver` call unchanged.
- Bump pipeline.py header `LAST_CHANGE` → add `... aisw-x92: slow-path deliver(tg_send=False) — no duplicate TG send`.
- Run → PASS. Run `tests/unit/tg/test_pipeline_streaming.py` → all PASS.

## Task 4 — `StreamEditor.finalize` no-op on unchanged text (RED → GREEN)

**Test (RED)** — `tests/unit/tg/test_stream_edit.py`, new case `test_finalize_skips_edit_when_unchanged`:
- `ed, sender, _ = _make_editor(delta_chars=10)`; `await ed.feed("hello world unchanged buffer")` (triggers a tick edit) → `n = len(sender.edits)` (== 1).
- `await ed.finalize()`.
- Assert: `len(sender.edits) == n` (no extra edit), `ed._finalized is True`.
- Run → FAIL (finalize currently issues a second edit → len == 2).

**GREEN** — `src/ai_steward_wiki/tg/stream_edit.py`:
- `__init__`: `self._last_sent_text: str | None = None`.
- `feed()`: after the throttled `edit_message_text(self._chat_id, self._current_msg_id, balanced)` — store `balanced` first in a local var, then `self._last_sent_text = balanced`. In the chain-split `while` block: after `edit_message_text(..., finalized_head)` set `self._last_sent_text = finalized_head`; after `send_message(... placeholder)` set `self._last_sent_text = placeholder`.
- `finalize()`: after computing `final_text`, before the `try`: `if final_text == self._last_sent_text: self._finalized = True; _log.info("tg.stream.finalized", chat_id=..., message_id=..., segment_idx=..., total_segments=..., size=len(self._buffer), skipped=True); return`.
- Bump header `LAST_CHANGE` → `v0.0.2 - aisw-x92: finalize() no-ops when final text == last sent (kills spurious final_flush_failed)`.
- Run new test → PASS. Run `tests/unit/tg/test_stream_edit.py` → all PASS (incl. `test_finalize_emits_final_state_idempotent`, `test_finalize_balances_html_tags`, `test_finalize_swallows_sender_errors`).

## Task 5 — full verification + commit

- `make lint` → ruff + ruff format + mypy clean.
- `grace lint --failOn errors` → 0 issues (CHANGE_SUMMARY bumps only).
- `uv run pytest tests/unit` → all PASS, no coverage regression on `tg/`.
- Commit: `fix(M-TG-TEXT): no duplicate TG reply on streaming slow-path (aisw-x92)` — single commit, all 5 files (output.py, pipeline.py, stream_edit.py, __main__.py, 3 test files).

## Verification matrix

| FR | Covered by |
|----|------------|
| FR-1 (one TG delivery on slow-path) | Task 3 test |
| FR-2 (persist + audit still happen) | Task 1 test |
| FR-3 (tg_send param, slow-path uses False) | Task 1 + Task 3 |
| FR-4 (finalize no-op on unchanged) | Task 4 test |
| FR-5 (unit tests enforce a + b) | Task 1, Task 4 |
| NFR-1 (fast-path / D-025 unchanged) | existing test_output.py 3 cases + fast-path streaming tests |
| NFR-2 (tg.output.delivered + tg_send field) | Task 1 |
| NFR-3 (existing tests green) | Task 5 |
