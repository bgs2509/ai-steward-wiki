---
feature: service-repair
slug: 20260613-service-repair
date: 2026-06-13
status: draft
kind: superautocoder-input
target_repo: /home/bgs/ai-steward/Gena_Beeline_Local/ai-steward-wiki
approval_model: fully-auto-approved
source: "Verification audit 2026-06-13 — 6 discrepancies between user-facing /start + /manual texts and implementation"
---

# Service Repair — Align bot behavior with its user-facing instructions

> **Purpose of this document:** decision-complete input for `/superautocoder`.
> Every product fork is already resolved in §4 so the auto-approved run does NOT
> need to guess (3x-rule) or halt (`questions-answers`). Each work item (§5) is a
> natural chunk with explicit inputs/outputs/verification/depends_on.

## 1. Goal & Context

The bot's `/start` and `/manual` texts (in `templates/*.ru.md`) promise behavior
that the code does not implement, and use one wrong term. A verification audit on
2026-06-13 found 6 discrepancies. This epic closes the gap two ways:

1. **Build** the missing features that cause active user harm (a documented command
   that silently does nothing).
2. **Reword** the docs for features we deliberately do NOT build now, so the manual
   stops lying.

Outcome: every claim in `/start` and `/manual` is either true in code or removed/softened.

## 2. Non-Goals / Deferred (explicit — do NOT implement in this run)

1. **No real OCR engine.** Photos are handled by Claude vision (`--add-dir` + Read
   tool, `wiki/runner.py`); we keep that and only fix wording (F3).
2. **No WIKI-linked relative reminders** («за день до поездки в Лиссабон»). Too large
   and ambiguous for an auto-approved run (event lookup in WIKI files + date
   resolution). Deferred to a separate manual `feature-workflow`; here we only soften
   the doc (F2).
3. **No spec-frontmatter data fix** (`aisw-rui`, 17 malformed/missing frontmatters).
   Orthogonal to service behavior; out of scope (F5).
4. No `git push` (Git Push Policy). Stop at merged-on-local-branch.

## 3. Global Constraints (inherited from project CLAUDE.md — do not violate)

1. **Ru-only** user-facing strings (D-032). No i18n catalog.
2. **mypy --strict** on `src/`; **Pydantic** on all boundaries.
3. **TDD**: RED → GREEN → REFACTOR. No production code without a failing test first.
4. **structlog** with required fields: `correlation_id, user_id, wiki_id, job_id`
   (and `owner_telegram_id, chat_id` where applicable) at every decision point.
5. **All DB datetimes UTC**; user_tz only at input/output.
6. **Conventional Commits + GRACE MODULE_ID** scope; `bd_id` trailer per chunk.
7. **No hook bypass** (`--no-verify`, `SKIP=`). `make total-test` must end green.
8. Identity vocab (D-042): `telegram_id` / `owner_telegram_id` / `chat_id` / `user_id`.

## 4. Pre-Resolved Decisions (F1–F6) — authoritative; treat as already-decided

| ID | Fork | Decision |
|----|------|----------|
| F1a | #2 NL digest control (reschedule/disable) | **BUILD** (WI-3) |
| F1b | #3 «за N до» / second reminder | **BUILD** (WI-4) |
| F1c | #6 WIKI-create «1–3 уточняющих вопроса» | **REWORD docs** to match current single-confirm flow (WI-1) |
| F2 | #4 WIKI-linked relative reminder | **DEFER** — reword/soften doc only (WI-1) |
| F3 | #1 «OCR» wording | **REWORD** — vision stays; replace "OCR" with neutral "распознаю изображение" (WI-1) |
| F4 | #5 `/cron_add` visibility | **EXPOSE** — add to `set_my_commands` + one line in `/help` (WI-2) |
| F5 | `aisw-rui` frontmatter fix | **OUT OF SCOPE** for this epic |
| F6 | Dry-run first | Operational: run `--dry-run` before the real run (not a draft item) |

**Design decision for WI-3 (digest control):** do NOT add new top-level intents.
Extend the existing `digest` intent's `distilled_payload` with an `action` field
∈ {`create`, `reschedule`, `disable`} (default `create` for backward compat). This
keeps the `Intent` enum (`classifier/schema.py:43`) stable and the seven-intent
contract in `prompts/classifier.md` unchanged.

## 5. Work Items (suggested chunks)

### WI-1 — Docs honesty (no behavior change)

**Scope:** edit only `templates/*.ru.md`. Pure text; zero code/logic change.

**Changes:**
1. **#1 (F3):** in `manual.ru.md`, replace the "распознаю через OCR" claim with a
   vision-accurate phrasing (e.g. «распознаю изображение и извлеку, что вижу»). Do
   not mention OCR anywhere.
2. **#6 (F1c):** in `manual.ru.md` (WIKI-creation scenario), change «Я задам 1–3
   уточняющих вопроса … после „да" создам» to the real flow: bot proposes the WIKI
   name and asks a single confirmation («Заведу новую вики „…". Подтверждаешь?»).
3. **#4 (F2):** in `manual.ru.md`, remove the «за день до поездки в Лиссабон»
   example (WIKI-linked relative reminder). Keep the other reminder examples that
   are true (one-time, daily, weekly, monthly).

**Inputs:** `templates/manual.ru.md` (+ scan `start-known.ru.md`, `help.ru.md`,
`onboarding-intro.ru.md` for the same claims).
**Outputs:** edited `templates/*.ru.md`.
**Verification:** `grep -i "OCR\|за день до поездки\|1–3 уточняющих" templates/` → 0
hits; existing template-lint (`scripts/lint_templates.py`) and `make total-test` green.
**depends_on:** none.

### WI-2 — Expose `/cron_add` (close #5)

**Scope:** make `/cron_add` discoverable, matching the "all commands visible" claim.

**Changes:**
1. Add a `BotCommand(command="cron_add", description=...)` entry to the
   `set_my_commands` list in `src/ai_steward_wiki/tg/bot.py` (currently 6 commands,
   lines ~218-223), Ru description.
2. Add one `/cron_add` line to the commands cheat-sheet in `templates/help.ru.md`.

**Inputs:** `src/ai_steward_wiki/tg/bot.py`, `templates/help.ru.md`.
**Outputs:** edited `bot.py`, `help.ru.md`.
**Verification:** unit/assertion that the registered command list includes `cron_add`
(7 commands); `make total-test` green.
**depends_on:** none.

### WI-3 — NL digest control: reschedule + disable (close #2)

**Scope:** let free-text «переноси сводку на 7:30» and «выключи ежедневную сводку»
actually work, via the extended `digest` intent (see §4 design decision).

**Functional requirements:**
- FR-1: classifier extracts `distilled_payload.action` ∈ {create, reschedule, disable}
  for `intent="digest"`; `reschedule` also yields the new `time_hhmm`. Update
  `prompts/classifier.md` digest section + `classifier/schema.py` payload model.
- FR-2: `pipeline.py` digest branch dispatches on `action`:
  - `reschedule` → update the owner's digest job trigger time (UTC-converted),
    confirm in Ru.
  - `disable` → set the owner's digest job(s) to a non-scheduled status, confirm in Ru.
  - `create` → existing behavior, unchanged.
- FR-3: new `firing.py` functions: reschedule digest job time, and disable digest
  job(s) for an owner (mirror the existing `create_digest_job` at `firing.py:492` and
  `list_owner_digest_job_ids` at `firing.py:388`). Reuse the `status=='scheduled'`
  convention for enabled.
- FR-4: if the owner has no digest job when rescheduling/disabling → polite Ru reply
  («У тебя нет активной сводки»), not an error.

**NFR:** mypy strict; structlog with `owner_telegram_id, job_id, correlation_id`;
UTC in DB; Pydantic on the payload; ≥1 unit test per action.

**Inputs:** `prompts/classifier.md`, `src/ai_steward_wiki/classifier/schema.py`,
`src/ai_steward_wiki/tg/pipeline.py`, `src/ai_steward_wiki/scheduler/firing.py`,
relevant storage in `src/ai_steward_wiki/storage/jobs/`.
**Outputs:** edited classifier prompt+schema, pipeline branch, firing functions, tests.
**Verification:**
- unit: «выключи ежедневную сводку» → `action=disable`; «переноси сводку на 7:30» →
  `action=reschedule, time_hhmm="07:30"`.
- unit: reschedule updates the job's trigger; disable flips status; no-job path replies politely.
- `make total-test` green.
**depends_on:** none (but ordered after WI-1/WI-2; see §6).

### WI-4 — «за N до»: second (earlier) reminder (close #3)

**Scope:** «напомни в субботу в 12, а ещё за час до» must produce TWO notifications:
the main one at T and an earlier one at T − N.

**Functional requirements:**
- FR-1: classifier extracts a lead offset from phrasing «а ещё за <N> до» / «за час до»
  into the reminder `distilled_payload` (minutes). Update `prompts/classifier.md`
  reminder payload contract + the reminder payload model.
- FR-2: `pipeline.py` reminder branch stops hardcoding `lead_time_min: 0`
  (`pipeline.py:1249`) and carries the parsed offset through the confirm draft.
- FR-3: scheduling produces both notifications. Design note: `create_reminder_job`
  (`firing.py:166`) and `ReminderPayload` already carry `lead_time_min` — REUSE it if
  its semantics are "fire an additional earlier reminder"; if its current semantics
  differ, create a second `reminder_job` (DateTrigger at T − N) instead. The design
  step picks the cleaner of the two; the BEHAVIOR (two notifications, one at T−N and
  one at T) is fixed.
- FR-4: if no lead is mentioned → exactly one reminder (unchanged behavior).

**NFR:** mypy strict; structlog reminder fields; UTC; Pydantic; unit tests for
single-offset, no-offset, and the T−N timing.

**Inputs:** `prompts/classifier.md`, the reminder payload model
(`src/ai_steward_wiki/storage/jobs/payloads.py` — `ReminderPayload.lead_time_min`),
`src/ai_steward_wiki/tg/pipeline.py` (line ~1249 + confirm path ~1953/1982),
`src/ai_steward_wiki/scheduler/firing.py` (`create_reminder_job`).
**Outputs:** edited classifier prompt, payload model (if needed), pipeline, firing, tests.
**Verification:**
- unit: «напомни в субботу в 12, а ещё за час до» → lead=60; two notifications scheduled
  (T and T−60min).
- unit: «напомни завтра в 9 …» (no lead) → exactly one reminder.
- `make total-test` green.
**depends_on:** WI-3 (both edit `prompts/classifier.md` and `pipeline.py`; serialize
to avoid reassess churn).

## 6. Suggested Chunk Order & Dependencies

1. **WI-1** (docs honesty) — first; stops the active "lie" at zero code risk.
2. **WI-2** (`/cron_add` visibility) — small, independent.
3. **WI-3** (digest control) — first feature build.
4. **WI-4** (lead reminders) — after WI-3 (shared files: `classifier.md`, `pipeline.py`).

Each chunk: its own `make total-test` green + `/clear` before the next (per
superautocoder Phase 5.5/5.6). Final `full-audit --ai` fixes any CRITICAL before close.

## 7. Global Acceptance

1. `grep -ri "OCR" templates/` → 0 hits.
2. Registered Telegram command menu includes `cron_add` (7 commands).
3. «переноси сводку на 7:30» and «выключи ежедневную сводку» change/disable the
   digest job and reply in Ru.
4. «… а ещё за час до» schedules two notifications.
5. `make total-test` green; `full-audit --ai` reports 0 CRITICAL.
6. All `/start` + `/manual` claims are either implemented or removed/softened.
