---
feature: streaming-double-reply
bd_id: aisw-x92
status: approved
date: 2026-05-12
approach: tg-send-flag
technology_decisions:
  - id: TD-1
    text: "Add keyword-only `tg_send: bool = True` to deliver_output (src/ai_steward_wiki/tg/output.py). When False: skip the entire size-hybrid send branch (send_message / chain-split / summary+send_document); still run _persist_to_disk and _record_run_output; emit tg.output.delivered with tg_send=False, n_messages=0, document_sent=False. Default True keeps current behaviour byte-for-byte."
  - id: TD-2
    text: "Propagate tg_send through OutputDelivery Protocol (pipeline.py:187) → _OutputDeliveryAdapter.deliver (__main__.py:242). Keyword-only, default True. mypy --strict must stay green."
  - id: TD-3
    text: "DefaultStreamingDelivery.run_and_deliver slow-path (pipeline.py ~968): change `await output.deliver(chat_id=..., telegram_id=..., run_id=..., text=final_text)` to `await output.deliver(..., tg_send=False)`. Fast-path call (pipeline.py ~895) and DefaultPipeline non-stream call are unchanged."
  - id: TD-4
    text: "StreamEditor (stream_edit.py): add instance field `_last_sent_text: str | None = None`, set it to the exact string passed to edit_message_text after every successful edit in feed() and in the chain-split branch. In finalize(): compute final_text; if final_text == self._last_sent_text → set _finalized=True, log tg.stream.finalized with skipped=True, return WITHOUT calling edit_message_text. Otherwise edit as today (keeping the existing try/except → tg.stream.final_flush_failed warning as a real-error fallback)."
  - id: TD-5
    text: "Reject Variant 2 (split deliver_output into persist_and_audit + send_reply_to_tg): cleaner SoC but touches Protocol shape, DeliveryReceipt split, both call sites and more tests — disproportionate for an MVP bugfix. Revisit only if more delivery modes appear."
  - id: TD-6
    text: "Test approach: (a) tests/unit/tg/test_output.py — new case: deliver_output(tg_send=False) → fake sender records 0 send_message calls, but output file exists on disk and a run_output row is recorded; tg.output.delivered logged with tg_send=False. (b) tests/unit/tg/test_stream_edit.py — feed one chunk that triggers a tick, then finalize() → no second edit call, _finalized True, tg.stream.finalized skipped=True. (c) streaming pipeline test — slow-path run → exactly one TG message reaches the chat (the placeholder edit), deliver invoked with tg_send=False."
---

# Design — Streaming slow-path double reply (Variant 1: tg_send flag)

## Chosen approach: `tg_send` flag on `deliver_output`

`deliver_output` does two things: (1) persist reply to disk + record audit row — must always happen; (2) send the reply to Telegram per the D-025 size-hybrid policy. On the streaming slow-path the TG reply was already delivered by `StreamEditor` editing the placeholder, so (2) is a duplicate. A keyword-only `tg_send` flag lets the slow-path keep (1) and skip (2).

## Changes

### 1. `src/ai_steward_wiki/tg/output.py` — `deliver_output`

- Signature: add `tg_send: bool = True` (keyword-only, alongside the other kwargs).
- Body: wrap the existing `if len(text) <= INLINE_THRESHOLD: ... elif ... else ...` size-hybrid block in `if tg_send:`. When `tg_send` is False, leave `n_messages = 0`, `document_sent = False`, `summary_chars = None`.
- `_persist_to_disk` and `_record_run_output` stay unconditional (already before/after the send block — just ensure persist runs before the guarded block, audit after).
- `tg.output.delivered` log: add `tg_send=tg_send` field.
- Bump `START_CHANGE_SUMMARY` to a new patch version referencing `aisw-x92`.

### 2. `OutputDelivery` Protocol — `src/ai_steward_wiki/tg/pipeline.py:187`

```python
async def deliver(
    self, *, chat_id: int, telegram_id: int, run_id: str, text: str, tg_send: bool = True
) -> None: ...
```

### 3. `_OutputDeliveryAdapter.deliver` — `src/ai_steward_wiki/__main__.py:242`

Add `tg_send: bool = True` param, forward `tg_send=tg_send` to `deliver_output`.

### 4. `DefaultStreamingDelivery.run_and_deliver` — `src/ai_steward_wiki/tg/pipeline.py` slow-path

The `await output.deliver(...)` after `live_editor.finalize()` (≈ line 968) → add `tg_send=False`. Fast-path `deliver` (≈ line 895) untouched. Bump pipeline.py CHANGE_SUMMARY.

### 5. `StreamEditor` — `src/ai_steward_wiki/tg/stream_edit.py`

- `__init__`: `self._last_sent_text: str | None = None`.
- `feed()`: after the throttled `edit_message_text(... self._balance(self._buffer))`, set `self._last_sent_text = <the balanced string just sent>`. In the chain-split branch, set `self._last_sent_text = finalized_head` after that edit, and after sending the new placeholder reset `self._last_sent_text = placeholder`.
- `finalize()`: compute `final_text`; `if final_text == self._last_sent_text: self._finalized = True; _log.info("tg.stream.finalized", ..., skipped=True); return`. Else proceed with the existing edit + try/except.
- Bump stream_edit.py CHANGE_SUMMARY.

## Out of scope

Variant 2 (persist/send split), race-timeout tuning, token-level streaming.

## Test plan

See TD-6. RED → GREEN → REFACTOR for each. Existing `test_output.py`, streaming pipeline tests, `test_stream_edit.py` must stay green.
