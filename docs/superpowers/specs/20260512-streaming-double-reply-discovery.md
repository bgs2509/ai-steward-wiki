---
feature: streaming-double-reply
bd_id: aisw-x92
status: approved
date: 2026-05-12
functional_requirements:
  - id: FR-1
    text: "On the streaming slow-path, the assistant reply MUST be delivered to Telegram exactly once (via the StreamEditor placeholder edits); deliver_output MUST NOT additionally send a fresh TG message."
  - id: FR-2
    text: "deliver_output MUST still persist the reply to disk and record the run output in audit.db on the slow-path (persistence/audit are unconditional, independent of TG send)."
  - id: FR-3
    text: "deliver_output / OutputDelivery.deliver gains a tg_send: bool = True parameter; DefaultStreamingDelivery slow-path calls it with tg_send=False. Fast-path and DefaultPipeline keep the default (tg_send=True)."
  - id: FR-4
    text: "StreamEditor.finalize MUST skip the edit_message_text call when the rendered final text equals the text of the last edit already sent — eliminating the spurious tg.stream.final_flush_failed / TelegramBadRequest 'message is not modified' warning on short replies."
  - id: FR-5
    text: "Unit tests MUST enforce: (a) slow-path → sender.send_message NOT called by deliver_output, but disk-persist + audit record happen; (b) finalize with unchanged buffer → no edit call, no warning."
non_functional_requirements:
  - id: NFR-1
    text: "No change to fast-path behaviour, to OutputKind/DeliveryReceipt semantics, or to the D-025 hybrid policy (inline / chain-split / summary+document)."
  - id: NFR-2
    text: "structlog event tg.output.delivered keeps emitting on the slow-path (persistence happened); add a field tg_send=false so logs distinguish 'persisted only' from 'persisted + sent'."
  - id: NFR-3
    text: "All existing unit tests for output.py, pipeline.py streaming, and stream_edit.py must still pass."
constraints:
  - "OutputDelivery is a Protocol with two implementers in the codebase — both call sites and the Protocol signature must stay in sync (mypy --strict)."
  - "deliver_output already has many keyword args; tg_send is added as a keyword-only bool with a default, no positional churn."
risks:
  - "Risk: a third caller of deliver()/deliver_output exists that relies on the send. Mitigation: grep all call sites before edit; only DefaultPipeline (fast/non-stream) and DefaultStreamingDelivery (fast + slow) call it."
  - "Risk: finalize 'unchanged' check compares post-balance text; if HtmlBalancer is non-deterministic the comparison could be wrong. Mitigation: balance is pure/deterministic; compare the exact string that would be sent against a stored _last_sent_text."
scope:
  in:
    - "Add tg_send: bool = True (keyword-only) to deliver_output in src/ai_steward_wiki/tg/output.py — when False, skip the send_message/send_document branch but keep _persist_to_disk + _record_run_output + tg.output.delivered log (with tg_send field)."
    - "Add tg_send to OutputDelivery.deliver Protocol and to the concrete DefaultOutputDelivery.deliver wrapper in pipeline.py."
    - "DefaultStreamingDelivery.run_and_deliver slow-path: call output.deliver(..., tg_send=False) at pipeline.py:968."
    - "StreamEditor: track _last_sent_text; in finalize(), if final_text == _last_sent_text → return without edit (still set _finalized=True, log tg.stream.finalized with skipped=true). Update feed() to record _last_sent_text on every successful edit."
    - "Bump CHANGE_SUMMARY headers in output.py, pipeline.py, stream_edit.py."
    - "Tests: tests/unit/tg/test_output.py (tg_send=False path), tests/unit/tg/test_pipeline_streaming.py or equivalent (slow-path single delivery), tests/unit/tg/test_stream_edit.py (finalize no-op on unchanged buffer)."
  out:
    - "Splitting deliver_output into persist_and_audit() + send_reply_to_tg() — Variant 2, separate refactor."
    - "Reworking the race timeout / when slow-path triggers — out of scope, behaviour is correct."
    - "Token-level streaming (--include-partial-messages) — unrelated."
  later:
    - "If deliver_output grows more modes, revisit Variant 2 (SoC split)."
---

# Discovery — Streaming slow-path double reply

## Problem

Live run (2026-05-12, `proc-df15507e`, `tg-1710-763463467`): user sent a 22-char text; classifier returned `intent=unknown` (10.6s); wiki run took ~12.4s, so the 5s race timeout fired and the slow-path streaming kicked in:

```
tg.pipeline.stream.begin            message_id=1711   (placeholder sent)
wiki.run.event assistant_chunk      (whole ~600-char reply in one chunk)
wiki.run.event final
wiki.run.finish exit_code=0
tg.stream.final_flush_failed         message_id=1711  error=TelegramBadRequest
tg.output.delivered                  kind=reply size=600 n_messages=1 document_sent=false
tg.pipeline.stream.final             chars=600
tg.pipeline.runner.completed         chars=600 streamed=true
```

Result: **two messages in the chat** — the streamed/edited placeholder `1711` (already holds the full reply, since the 600-char chunk triggered a Δ≥50 tick edit) **plus** a brand-new message from `deliver_output`'s `sender.send_message`.

## Root cause

`DefaultStreamingDelivery.run_and_deliver` slow-path (`src/ai_steward_wiki/tg/pipeline.py:905-981`):

1. Sends placeholder → `StreamEditor` over it.
2. `feed()` edits the placeholder with the full balanced reply.
3. `finalize()` tries to edit it again with the *same* text → Telegram `Bad Request: message is not modified` → caught, logged `tg.stream.final_flush_failed` (harmless but noisy).
4. **Unconditionally** calls `output.deliver(...)` → `deliver_output()` (`tg/output.py:325`) which does `_persist_to_disk` + `_record_run_output` (needed) **and** `sender.send_message(...)` (the duplicate).

So `deliver_output` is doing double duty: persistence/audit (must always run) + TG send (already done by the StreamEditor on the slow-path). Fast-path is fine — no placeholder, single send.

Secondary: `StreamEditor.finalize` (`tg/stream_edit.py:175-205`) always issues an edit even when the buffer is unchanged since the last tick.

## Decision

Variant 1 from `/best` (2026-05-12): add a `tg_send` flag to `deliver_output` / `OutputDelivery.deliver`; the streaming slow-path calls it with `tg_send=False` (persist + audit only). Plus the small `finalize` no-op-on-unchanged fix. Variant 2 (split `deliver_output` into persist+send) deferred — overkill for MVP.

## Preflight

- Pre-commit: `.beads/hooks/pre-commit` bootstrap → `.pre-commit-config.yaml` present, `pre-commit 4.0.1` available. ✅
- Lint baseline clean: `ruff check` ✅ / `ruff format --check` 172 files formatted ✅ / `mypy src` Success, 66 files ✅
- Sentrux: no `.sentrux/rules.toml` → not onboarded, skipped.

## Open questions

None blocking. (Minor: name of the new structlog field — proposing `tg_send=false`.)
