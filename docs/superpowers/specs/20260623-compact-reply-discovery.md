---
feature: compact-reply
bd_id: aisw-2n2
status: approved
risk: medium
risk_justification: 2 source modules (wiki/runner.py, tg/pipeline.py) + 1 prompt, reversible, no DB schema change, no public-API break, no security surface.
open_questions: []
functional_requirements:
  - id: FR-1
    text: Final user-facing reply MUST contain only the last assistant turn (text after the last tool_use), not concatenated inter-tool narration.
  - id: FR-2
    text: Stripped inter-tool narration MUST be surfaced as ephemeral loader progress in the ⏳ placeholder (slow path), then replaced by the clean final answer.
  - id: FR-3
    text: prompts/wiki.md MUST forbid the self-generated "## Выполнено / Операция / Зафиксировано / Краткое резюме / Рекомендация" footer and the duplicated summary; one compact answer + at most one thin confirmation line.
  - id: FR-4
    text: Fast path (run completes within timeout) MUST also deliver only the last assistant turn.
non_functional_requirements:
  - id: NFR-1
    text: No new dependencies. Pure-function change to text aggregation + delivery wiring.
  - id: NFR-2
    text: Backward-tolerant — if events lack a recognisable final turn, fall back to existing aggregate/outcome.text/ACK behaviour (no empty replies).
  - id: NFR-3
    text: structlog anchors preserved; technical details (log.md write, operation kind) stay in internal logs, never in the user reply.
scope_in:
  - src/ai_steward_wiki/wiki/runner.py (final-turn extractor)
  - src/ai_steward_wiki/tg/pipeline.py (slow/fast path delivery + loader progress)
  - prompts/wiki.md (response-format rules)
  - tests (unit for extractor + delivery)
scope_out:
  - classifier/inbox prompts (separate confirmation message stays)
  - voice/OCR paths
  - digest/cron prompts
risks:
  - id: R-1
    text: A legitimate multi-paragraph answer that the model splits across assistant messages could be truncated to the last turn only.
    mitigation: Extract the last CONTIGUOUS run of text-only assistant messages (those not followed by a tool_use), not literally one message.
---

# Discovery — Compact WIKI final reply

## Problem (verified in code)

On "что со здоровьем?" the bot emits a wall of text mixing three defects:

1. **Narration leak.** `aggregate_text()` (`src/ai_steward_wiki/wiki/runner.py:265-298`) and the slow-path final build (`src/ai_steward_wiki/tg/pipeline.py:2714`) concatenate the `text` block of EVERY `assistant` message across the whole agentic run. Claude emits narration before each tool call ("Прочитаю сырьё…", "Вижу…", "Теперь понятно…") — all of it ships to the user.
2. **Self-generated footer.** `## Выполнено / Операция / Зафиксировано / Краткое резюме / Рекомендация` is NOT in any prompt (grep over `prompts/` → only `wiki.md:20` "Краткое резюме"). The model invents this completion-report block, duplicating the inline summary and leaking the `log.md` timestamp.
3. **Repeated classification.** "Запрос … относится к Medical-WIKI" is already shown in the confirmation message (step 2 of the dialogue); the model restates it in the final reply.

## Root cause

Stream shape: `claude --output-format stream-json` emits one `assistant` event per turn; each turn's `content[]` may carry a `text` block (narration) + a `tool_use` block. The aggregator is turn-agnostic and sums all text. The final answer is the trailing text-only turn(s) after the last `tool_use`.

## Decisions (user-confirmed this session)

1. Narration → ephemeral loader progress (reuse `StreamEditor` + `⏳ Думаю…` placeholder, `pipeline.py:306`).
2. Footer → removed; keep at most one thin confirmation line; technical/log details stay internal.
