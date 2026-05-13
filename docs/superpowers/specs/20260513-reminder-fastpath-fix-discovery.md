---
title: Reminder fast-path broken end-to-end — Discovery
bd_id: aisw-5q5
status: approved
risk: medium
evidence: strong
date: 2026-05-13
modules_touched:
  - M-TG-PIPELINE
  - M-CLASSIFIER-STAGE0
  - M-CLASSIFIER-TIME
  - prompts/classifier.md
  - prompts/time-parse.md
open_questions: []
sources:
  - correlation_id: "1396ef6b-4ff1-4636-b143-a2840ba3e347"
  - log_evidence: "/tmp/aisw.log @ 2026-05-13T12:25:16…12:25:30Z"
  - traceback_file: "src/ai_steward_wiki/classifier/backend.py:262 ClassifierSchemaError"
  - dateparser_probe: "local repl, dateparser 1.2.0, RU+EN, RELATIVE_BASE+PREFER_DATES_FROM=future"
  - tech_spec: "docs/Spec-WIKI/research/tech-spec-draft.md D-010 (NL time parsing)"
---

# Reminder fast-path broken e2e — Discovery

## What the user reports

User sends «напомни мне пойти гулять через 5 минут» → TG shows «⏳ Думаю…» → message disappears → no reply, no reminder. Reproduced three times in log (10:54Z / 11:12Z / 11:21Z) and confirmed again after stderr→log restart (12:25:16Z, traceback captured).

## Real intent

Reminder fast-path (aisw-kcz, Phase-D.a) is a core user-facing flow. «Поставь напоминание на N минут / в HH:MM» is the most common reminder phrasing in RU. Currently 100% miss → 100% UX failure.

## Evidence (production traceback, full)

`/tmp/aisw.log`, correlation_id `1396ef6b-4ff1-4636-b143-a2840ba3e347`, 2026-05-13T12:25:30Z:

```
tg.pipeline.dispatch.error
  exception:
    ClassifierSchemaError: claude CLI inner JSON parse failed:
      'Мне не хватает информации для точного разбора этого выражения времени.
       Чтобы правильно интерпретировать "через 5 минут", мне нужно знать:
       1. Текущее время (не только дата 2026-05-13, но и часы/минуты)
       2. Ваш часовой пояс (например, Europe/Moscow…'
  origin: classifier/backend.py:262 _unwrap_cli_envelope (JSONDecodeError)
  call_chain:
    tg/pipeline.py:874 _handle_reminder_intent
    → tg/pipeline.py:1171 self._time_parser.parse_time(...)
    → __main__.py:301 _TimeParserAdapter.parse_time
    → classifier/time_parse.py:111 haiku_backend.call
    → classifier/backend.py:232 _unwrap_cli_envelope
```

## Three root causes (independent, all required for full recovery)

### RC-1 — Haiku-fallback never receives `now` / `user_tz`

`prompts/time-parse.md` (semver 1.0.0) declares:
> «You receive the user message AND a "now" reference + user timezone in the system context.»

Reality (`classifier/backend.py:209-219` `ClaudeCliBackend.call`):

```python
rc, stdout, stderr = await self.spawner.spawn(
    argv,                          # claude -p --model haiku
                                   # + system_prompt_argv(prompt_path)  ← raw .md, no template render
    env=env,
    stdin=text.encode("utf-8"),    # ← only user message
    ...
)
```

`_TimeParserAdapter.parse_time` (`__main__.py:285-309`) and `_parse_time_fn`
(`classifier/time_parse.py:47`) accept `user_tz` / `now_utc` but never propagate them into `haiku_backend.call`. The prompt's claim is a lie. Haiku-4-5 correctly refuses with prose → `JSONDecodeError` in `_unwrap_cli_envelope`.

### RC-2 — `dateparser.parse` always misses on natural reminder phrasing

`classifier/time_parse.py:72` passes raw `text` (full user sentence) to `dateparser.parse`. Local probe (dateparser 1.2.0, RU+EN, `RELATIVE_BASE` + `PREFER_DATES_FROM='future'` identical to production):

```
'через 5 минут'                                    -> 2026-05-13 15:32:53+03:00  ✅
'через пять минут'                                 -> 2026-05-13 15:32:53+03:00  ✅
'in 5 minutes'                                     -> 2026-05-13 15:32:53+03:00  ✅
'напомни мне пойти гулять через 5 минут'           -> None                       ❌
'напомни через 5 минут'                            -> None                       ❌
'пойти гулять через 5 минут'                       -> None                       ❌
```

This is dateparser's design — `parse` resolves a clean time expression, not free-form text. So every natural reminder («напомни …») bypasses Step-1 and falls into Step-2 (Haiku-fallback) which is broken by RC-1.

### RC-3 — Unhandled exception silently kills the dialog

`tg/pipeline.py:1169` calls `self._time_parser.parse_time(...)` without `try/except`. On any parser failure:
1. Exception propagates to aiogram dispatcher (logged as `tg.pipeline.dispatch.error`).
2. No user-facing reply is sent.
3. `_slow_work_placeholder.finally` (`tg/handlers.py:152-161`) deletes the «⏳ Думаю…» message.
4. User sees «Думаю…» flash then nothing — perceived as "the bot disappeared".

`REMINDER_UNPARSEABLE_RU` already exists in the codebase but is only reachable on the *happy* `tp.escalate / tp.when_utc is None` path — not on exceptions.

## Functional Requirements

- **FR-1** Every `intent=reminder` Stage-0 result must either schedule a confirm draft OR send a user-facing message — no silent failures.
- **FR-2** Natural RU reminder phrasing («напомни мне … через N минут», «напомни в HH:MM», «через час сделай X») must be resolved by `dateparser` without invoking Haiku-fallback when the time expression is unambiguous.
- **FR-3** Haiku-fallback, when invoked, must receive `now_iso` (UTC) and `user_tz` (IANA) and return strict JSON `{when_iso, tz, ambiguous}` per `prompts/time-parse.md` schema.

## Non-Functional Requirements

- **NFR-1** No regression on Stage-0 latency budget (Haiku call P95 < 12s, already enforced).
- **NFR-2** Backward-compat: missing `distilled_payload["time_expr"]` from older classifier responses must fall back to raw `text` (no crash on rolling deploy / cached results).
- **NFR-3** Log anchor `tg.pipeline.reminder.parser_failed` (correlation_id + error_class) on every caught exception → observability for future regressions.
- **NFR-4** ≥3 unit tests per RC; ≥1 integration test gated on `RUN_INTEGRATION=1` for RC-1 (real Haiku, real prompt).

## Risks & mitigations

- **R-1** Classifier prompt change (RC-2) may regress non-reminder intents → mitigated by Stage-0 unit tests over the full intent matrix (already in `tests/unit/test_classifier_stage0.py`).
- **R-2** RC-1 stdin format change may confuse Haiku on edge cases (e.g. user message contains `NOW_ISO:` literally) → mitigated by using `---` separator + prompt instructing the model to treat the post-`---` block as user input.
- **R-3** Hotfix RC-3 alone restores UX but leaves the underlying parser broken — explicit acceptance: «Думаю…» disappears + RU «не понял время» reply instead of nothing. Followed up by RC-2 + RC-1 in same epic.

## Scope

**IN:**
- `tg/pipeline.py` _handle_reminder_intent (RC-3 try/except + log anchor)
- `classifier/stage0.py` + `prompts/classifier.md` (RC-2 add `time_expr` to distilled_payload for intent=reminder)
- `classifier/schema.py` (extend Stage0Result.distilled_payload contract)
- `classifier/time_parse.py` (RC-1 build stdin payload with NOW_ISO/USER_TZ header; RC-2 accept distilled expr)
- `prompts/time-parse.md` (RC-1 update prompt: declare input format honestly, enforce JSON-only)
- Unit tests in `tests/unit/test_pipeline_*`, `tests/unit/test_time_parse.py`, `tests/unit/test_classifier_stage0.py`

**OUT (this epic):**
- Switching Stage-0 to a deterministic regex-based reminder pre-filter (separate ADR, future)
- Multi-language NL time parsing beyond RU+EN
- Recurring reminders («каждый день в 9») — covered by digest fast-path, separate area

**LATER:**
- E2E test harness against real Haiku across reminder phrasing corpus

## Best practices applied

- **Fail-fast at parser boundary** — caller catches all exceptions, never crashes the dispatcher (Tanzu / Spring `@ControllerAdvice` analogue).
- **Distill at classification time, parse cleanly** — mirrors existing `reminder_text` pattern in `distilled_payload` (pipeline.py:1202). One classifier round, structured payload, downstream is pure.
- **Explicit input contract for sub-CLI calls** — header block + `---` separator is standard for stdin-only model invocations (Anthropic Claude CLI docs, Aider, etc.).
- **Log-driven observability** — new anchor `tg.pipeline.reminder.parser_failed` discoverable in `verification-plan.xml`, queryable post-hoc.

## Risk × Evidence (auto-approve matrix)

- **Risk: medium** — 4–5 modules touched, no breaking public API, no DB migration, no security surface.
- **Evidence: strong** — production traceback cited verbatim, dateparser probe reproduced locally, existing patterns (`reminder_text` distillation) cited with `file:line`, open_questions empty, no architectural fork.
- **Decision:** Gate auto-approved (`--auto-approve` per `feedback_auto_approve_gates.md`).
