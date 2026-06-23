---
feature: compact-reply
bd_id: aisw-2n2
status: approved
risk: medium
approach: final-turn-extractor + 3 prose call-sites rewired + wiki.md format rules + loader progress
adr_candidate: false
libraries_touched: []
---

# Design — Compact WIKI final reply

## Approach (chosen)

Two complementary fixes (code strips narration; prompt strips footer) + one UX wiring (loader).

### 1. New pure function `final_turn_text(events)` — `wiki/runner.py`

Extract only the trailing assistant prose (the answer), discarding inter-tool narration.

Algorithm (mitigates risk R-1 — do NOT truncate to a single message):
1. Scan `events` left→right. Track the index of the last event that represents a
   tool invocation: either `ev.type == "tool_use"`, OR an `assistant_chunk` whose
   `message.content[]` contains an item with `type == "tool_use"`.
2. Aggregate text (same extraction shapes as `aggregate_text`) from `assistant_chunk`
   events strictly AFTER that index → the trailing contiguous answer turn(s).
3. Fallbacks (NFR-2, never empty):
   - No tool invocation at all → behave exactly like `aggregate_text` (whole answer).
   - Tool invocation present but no text after it → fall back to `aggregate_text`.

`aggregate_text` stays untouched (still used by router parse + digest + transcripts).

### 2. Rewire the three user-facing PROSE call-sites to `final_turn_text`

1. `tg/pipeline.py:2714` — slow-path `final_text` (streamed reply).
2. `__main__.py:468` — `_WikiRunnerAdapter.run` → `WikiRunOutcome.text` (fast path).
3. `__main__.py:1044` — inbox route ingest OK reply.

Out of scope (keep `aggregate_text`): `__main__.py:775` router block (parsed, not prose),
`__main__.py:551` digest summary (separate prompt/UX).

### 3. Kill the repeated classification in the ingest OK reply — `__main__.py:1044`

Current: `reply=f"{decision.notes}\n\n{summary or '(WIKI обновлена)'}"`.
`decision.notes` restates the classification already shown in the confirmation message.
New: `reply = final_turn or "(WIKI обновлена)"` (drop the `decision.notes` prefix for the
`status="ok"` case ONLY; clarify/reject/create_wiki/run_failed returns keep notes unchanged).

### 4. Prompt `prompts/wiki.md` — response-format rules (FR-3)

Replace "## Формат ответа" with explicit rules:
- ONE compact answer, no meta "completion report".
- FORBID the block `## Выполнено`, `Операция:`, `Зафиксировано:`, `Краткое резюме:`,
  `Рекомендация:` and any duplication of an already-stated summary.
- Technical details (log.md write, operation kind, file paths) → internal log only, never user reply.
- At most ONE thin status line, only when pages actually changed (e.g. `✏️ обновил <страницы>`).
- Do not restate the routing/classification (already shown in the confirmation message).

### 5. Loader progress (FR-2) — `tg/pipeline.py` slow path

The slow path already sends a `⏳ Думаю…` placeholder and feeds streamed chunks into a
`StreamEditor` (`run_and_deliver`, lines 2666-2693). Change the semantics:
- During the run, the placeholder shows the LIVE narration as transient progress (existing
  `feed()` of streamed chunks already does this — narration is fine AS progress).
- On finalize, the placeholder is REPLACED with `final_turn_text` (the clean answer), not the
  full aggregate. Implemented by building the final via `final_turn_text` (item 2.1) and using
  the editor's finalize to set that text.

## Test strategy

- `tests/unit/wiki/test_final_turn_text.py` — pure-function: narration-before-tool stripped;
  no-tool-call passthrough; empty-after-tool fallback; multi text-turn trailing run kept.
- Reuse existing pipeline/inbox tests; add assertion that ingest OK reply no longer prepends notes.
