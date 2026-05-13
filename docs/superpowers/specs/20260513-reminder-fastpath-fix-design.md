---
title: Reminder fast-path broken end-to-end — Design
bd_id: aisw-5q5
status: approved
risk: medium
evidence: strong
date: 2026-05-13
references:
  discovery: docs/superpowers/specs/20260513-reminder-fastpath-fix-discovery.md
modules_touched:
  - M-TG-PIPELINE
  - M-CLASSIFIER-STAGE0
  - M-CLASSIFIER-TIME
libraries_verified: []   # no new libs; existing dateparser 1.2.0 already in deps
open_questions: []
adr_candidates: []       # no new architectural forks
---

# Reminder fast-path fix — Design

## Chosen approach per RC

### RC-3 (hotfix, aisw-4dr)

**Local try/except in `_handle_reminder_intent`.** Wrap `await self._time_parser.parse_time(...)` (`tg/pipeline.py:1169`) in `try / except Exception`. On exception: emit `tg.pipeline.reminder.parser_failed` (correlation_id, error_class, error_msg=str(exc)[:200]), send `REMINDER_UNPARSEABLE_RU`, return.

Rejected alternatives:
- *Global aiogram error handler* — catches everything but cannot send domain-specific user-facing message; would also mask future regressions in other branches. Worse UX, worse observability.
- *Add `try` in `parse_time` itself* — wrong layer: parser should remain pure (raise on bad CLI envelope); the caller decides UX policy.

### RC-2 (distill, aisw-2mg)

**Stage-0 classifier emits `distilled_payload.time_expr` for `intent=reminder`. Pipeline consumes it; parser stays pure.**

Concretely:
1. `prompts/classifier.md` — add per-intent contract for `reminder`: «when intent=reminder, distilled_payload MUST include `time_expr` (verbatim natural-language time fragment, e.g. "через 5 минут", "в 18:00 завтра") and `reminder_text` (the action without the time, e.g. "пойти гулять")». This formalises both fields; `reminder_text` was already optimistically read by `pipeline.py:1202`.
2. `classifier/schema.py` — no change. `distilled_payload: dict[str, Any]` already accepts arbitrary keys.
3. `tg/pipeline.py:1169` — change argument: `text=distilled_payload.get("time_expr") or text`. Fallback to raw text preserves NFR-2 (no crash on older cached results).

Rejected alternatives:
- *`dateparser.search.search_dates(text, ...)` in `time_parse.py` Step-1* — noisy, slow, returns lists, can match wrong fragments («гулять» month names in EN). Stage-0 Haiku already reads the sentence; cheap to ask for one extra field there.
- *Regex pre-extraction in Python* — duplicates logic Haiku does for free; brittle for RU morphology («через пять минут», «через пол часа»).

### RC-1 (prompt rewire, aisw-ct9)

**Pre-pend context header to stdin in `classifier/time_parse.py`. No backend Protocol change.**

Concretely:
1. `classifier/time_parse.py:111` — before `haiku_backend.call(text=text, …)` build:
   ```python
   payload = (
       f"NOW_ISO: {now_utc.astimezone(UTC).isoformat()}\n"
       f"USER_TZ: {user_tz}\n"
       f"---\n"
       f"{text}"
   )
   ```
   then `await haiku_backend.call(text=payload, prompt_path=…, correlation_id=…)`.
2. `prompts/time-parse.md` — rewrite «You receive…» section: «Input is two blocks separated by a line containing only `---`. First block: header with `NOW_ISO:` (UTC ISO 8601) and `USER_TZ:` (IANA name). Second block: the user message.» Tighten Rules: «Output a single JSON object. No prose, no code fences, no clarifying questions. If the time expression is genuinely ambiguous given the context, set `ambiguous: true`.»
3. Bump `prompts/time-parse.md` semver `1.0.0 → 1.1.0` (input contract changed). `claude_cli.spawn` already logs `prompt_semver` — observable.

Rejected alternatives:
- *Template-render the prompt file with `{now_iso}` / `{user_tz}` placeholders* — requires `ClassifierBackend.call` Protocol change (must accept template vars). Broader blast radius, all backends (incl. `FakeClaudeRunner`, `AnthropicApiBackend`) must be updated. Rejected (KISS, YAGNI).
- *Set env vars `AISW_NOW_ISO` / `AISW_USER_TZ` and instruct Haiku to read env* — Haiku CLI does not surface env to the model. Non-starter.

## Module map (after fix)

```
prompts/classifier.md          — declares time_expr field for intent=reminder
prompts/time-parse.md          — declares NOW_ISO/USER_TZ header + JSON-only rule (semver 1.1.0)
classifier/time_parse.py       — Step-1 dateparser on distilled expr; Step-2 builds stdin payload with header
classifier/stage0.py           — unchanged (pass-through of distilled_payload)
classifier/schema.py           — unchanged (distilled_payload is dict[str, Any])
classifier/backend.py          — unchanged (Protocol stays narrow)
tg/pipeline.py                 — _handle_reminder_intent: try/except + use distilled time_expr
__main__.py                    — unchanged (adapter signature stable)
```

## Data model sketch

`distilled_payload` for `intent=reminder` (additive — older payloads without these keys still work):

```json
{
  "intent": "reminder",
  "confidence": 0.95,
  "distilled_payload": {
    "time_expr": "через 5 минут",
    "reminder_text": "пойти гулять"
  }
}
```

## Log anchors (per `_logging` skill convention)

New:
- `tg.pipeline.reminder.parser_failed` — correlation_id, telegram_id, error_class, error_msg (truncated)
- `tg.pipeline.reminder.distill_used` — correlation_id, time_expr_present (bool), used_raw_fallback (bool)
- `classifier.time.parse` — already exists; extend with `source="dateparser"` confirmation that distillation eliminates Haiku-fallback for happy path

## UX flow (before / after)

**Before:** «напомни мне пойти гулять через 5 минут» → «⏳ Думаю…» → ⊘ (placeholder deleted, no reply).

**After:** «напомни мне пойти гулять через 5 минут» → «⏳ Думаю…» → either (a) recap card with confirm/cancel buttons («Напомнить «пойти гулять» в 15:32 МСК?»), or (b) on genuine ambiguity/failure: ru «Не понял время — перефразируй, пожалуйста».

## Verification strategy

Per RC, ≥3 unit tests + log-anchor assertions. Integration test (`RUN_INTEGRATION=1`) for RC-1 only (real Haiku call with header → valid JSON). Full matrix lives in updated `verification-plan.xml` (Step 7).

## Risk × Evidence

- **Risk: medium** — 4 modules touched, no breaking public API, no DB migration, no security.
- **Evidence: strong** — every alternative cited with rejection reason; production traceback grounds RC-1; local repro grounds RC-2; existing pattern (`reminder_text`) cited for RC-2 fix.
- **Decision:** Gate auto-approved (`--auto-approve`).
