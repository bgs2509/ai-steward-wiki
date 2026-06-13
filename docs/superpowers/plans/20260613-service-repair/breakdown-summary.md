# Breakdown summary — service-repair (DRY-RUN preview)

> Epic slug: `20260613-service-repair` · target repo: `ai-steward-wiki` · budget: 0.6 · branch: `feat/service-repair`
> **DRY-RUN:** no beads created, no code executed. `bd_id` are placeholders (`TBD-*`). The real run creates the epic + 4 tasks and runs the loop.

## Goal
Align bot behavior with `/start` + `/manual`: build the missing features that cause active harm, reword docs for the features deliberately not built. Closes the 6 audit discrepancies (2026-06-13).

## Chunks (4)

### Chunk 1 — Docs honesty (#1, #6, #4) · ~12% window · depends_on: —
- **Inputs:** `templates/manual.ru.md`, `start-known.ru.md`, `help.ru.md`, `onboarding-intro.ru.md`, `scripts/lint_templates.py`
- **Outputs:** edited `templates/*.ru.md` + chunk discovery/design/step-plan
- **Does:** drop "OCR" wording (vision stays), match WIKI-create text to real single-confirm flow, remove «за день до поездки» example
- **Verify:** `grep -i OCR templates/` → 0; template-lint exit 0; `make total-test` green

### Chunk 2 — Expose /cron_add (#5) · ~10% window · depends_on: —
- **Inputs:** `src/ai_steward_wiki/tg/bot.py`, `templates/help.ru.md`, `tg/cron_add.py`
- **Outputs:** edited `bot.py` (set_my_commands), `help.ru.md`, `tests/unit/tg/test_bot.py`
- **Does:** add `cron_add` to the Telegram command menu (7 commands total) + one /help line
- **Verify:** unit asserts menu includes `cron_add`; `make total-test` green

### Chunk 3 — NL digest control (#2) · ~40% window · depends_on: —
- **Inputs:** `prompts/classifier.md`, `classifier/schema.py`, `tg/pipeline.py`, `scheduler/firing.py`, `storage/jobs/`
- **Outputs:** extended digest payload (`action` field), pipeline dispatch, firing reschedule/disable, tests
- **Does:** make «переноси сводку на 7:30» and «выключи ежедневную сводку» actually work
- **Verify:** unit for action extraction + reschedule/disable + no-job polite reply; `make total-test` green
- **Split note:** if Discovery estimate >60% → 3.a (classifier+schema+prompt) / 3.b (pipeline+firing)

### Chunk 4 — «за N до» second reminder (#3) · ~38% window · depends_on: Chunk 3
- **Inputs:** `prompts/classifier.md`, `storage/jobs/payloads.py`, `tg/pipeline.py`, `scheduler/firing.py`
- **Outputs:** lead-offset extraction, pipeline carries offset (stop hardcoding 0 at `pipeline.py:1249`), firing schedules T and T−N, tests
- **Does:** «… а ещё за час до» → two notifications
- **Verify:** unit lead=60 → two notifications; no-lead → one; `make total-test` green
- **Depends on Chunk 3:** shared `classifier.md` + `pipeline.py`; serialized

## Pre-resolved decisions (no halt expected)
- F1a #2 BUILD · F1b #3 BUILD · F1c #6 reword · F2 #4 defer · F3 #1 reword · F4 #5 expose · F5 aisw-rui out-of-scope
- DEC-C3-1: extend `digest` payload `action` field, not new intents (enum stable)
- DEC-C4-1: behavior fixed (two notifications); mechanism decided in chunk-4 Discovery

## Order
1 (docs) → 2 (menu) → 3 (digest control) → 4 (lead reminders, after 3).

## To execute for real
```
cd /home/bgs/ai-steward/Gena_Beeline_Local/ai-steward-wiki
/superautocoder docs/superpowers/specs/20260613-service-repair-draft.md
```
