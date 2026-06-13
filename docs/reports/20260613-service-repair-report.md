# Completion report — service-repair (epic aisw-8l5)

**Date:** 2026-06-13 · **Branch:** `feat/service-repair` (not pushed) · **Driver:** /superautocoder

## Goal

Align bot behavior with its user-facing `/start` + `/manual` texts. Closes the 6
discrepancies found in the 2026-06-13 verification audit.

## Chunks (all closed)

| bd_id | Chunk | Result |
|-------|-------|--------|
| aisw-htz | Docs honesty (#1 OCR, #4 wiki-linked, #6 wiki-create) | reworded `templates/manual.ru.md` |
| aisw-864 | Expose `/cron_add` (#5) | `set_my_commands` 7 cmds + `/help` line |
| aisw-578 | NL digest control: reschedule + disable (#2) | firing fns + rule-based dispatch + Intent.DIGEST routing |
| aisw-5wr | «за N до» second reminder (#3) | lead extraction + second reminder_job at T−lead |

## Per-discrepancy outcome

1. **#1 OCR** — wording fixed (vision stays; no OCR engine). [reword]
2. **#2 NL digest control** — `выключи`/`переноси сводку` now work. [built]
3. **#3 «за N до»** — two notifications scheduled (T and T−N). [built]
4. **#4 WIKI-linked reminder** — example removed from docs (deferred feature). [reword]
5. **#5 `/cron_add` menu** — now visible in the ≡ menu + `/help`. [built]
6. **#6 WIKI-create 1–3 questions** — docs match the real single-confirm flow. [reword]

## Key decisions

- **DEC-C3-1 (deviation):** digest action detection is **rule-based** in the pipeline
  (`_detect_digest_action`/`_extract_hhmm`), not a classifier-payload field. Rationale:
  the whole digest fast-path is already deterministic (rule-based recurrence parser);
  this avoids an LLM round-trip and a prompt/schema change.
- **Intent.DIGEST routing:** previously unhandled (fell to legacy runner); now dispatched
  to the digest handler, making both create and control reachable.
- **DEC-C4-1:** `ReminderPayload.lead_time_min` is a dormant field (never consumed at
  fire time), so the pre-reminder is a **second real `reminder_job`** at T−lead, not a
  fire-time offset.

## Verification

- `make total-test`: green after every chunk — final **894 passed, coverage 86.78%**
  (ruff, ruff-format, mypy --strict, grace lint, 14 invariants all PASS).
- `full-audit` (Phase 6): 19 PASS / 0 FAIL / 3 WARN / exit 0. **0 CRITICAL.**
- New tests: `test_pipeline_digest_control.py` (helpers), digest disable/reschedule in
  `test_pipeline_digest.py` + `test_firing.py`, lead offset in `test_pipeline_reminder.py`.

## Follow-ups (MINOR, pre-existing — not fixed in this run)

- **aisw-358** — bump `pypdf` past 2026 CVEs (pip-audit WARN); vulture `project_mapping`
  unused var; interrogate docstring %.

## Known limitations

- `_extract_lead_minutes` is a heuristic: a reminder whose *content* contains «за час до»
  (e.g. «встретиться за час до фильма») would also schedule a pre-reminder. Acceptable for
  MVP (worst case: one extra ping; no data loss).

## Not pushed

Per Git Push Policy, the branch is committed locally only. Review + `git push` are manual.
