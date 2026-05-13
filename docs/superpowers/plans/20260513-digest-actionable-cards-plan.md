---
bd_id: aisw-163
title: "Implementation plan — actionable cards in digest"
date: 2026-05-13
phase: plan
discovery: ../specs/20260513-digest-actionable-cards-discovery.md
design: ../specs/20260513-digest-actionable-cards-design.md
---

# Implementation plan — aisw-163

Five phases. Each ends with `make lint` + targeted pytest green, then a single conventional commit. Phase boundaries are natural pause points — context-window-fit.

## Phase 1 — schema foundation

**Goal:** the data model can express `category`, `user_state`, `snooze_count`. No behaviour change.

1. `tests/unit/storage/test_payloads.py` — add RED test:
   - `test_reminder_category_default_generic` — round-trip without `category` ⇒ `'generic'`.
   - `test_reminder_category_explicit` — `'medication' | 'event' | 'generic'` round-trip.
   - `test_reminder_category_invalid_rejected` — `'gibberish'` ⇒ `ValidationError`.
2. `src/ai_steward_wiki/storage/jobs/payloads.py` — GREEN:
   - Add `category: Literal["medication", "event", "generic"] = "generic"`.
   - Bump VERSION 0.0.5 → 0.0.6; MAP + CHANGE_SUMMARY updated.
3. `tests/unit/storage/test_payloads.py` — verify the legacy stored shape (`{kind:'reminder_job', message:'x'}`) still parses (backwards-compat regression test).
4. `src/ai_steward_wiki/storage/jobs/models.py` — add `user_state: str` (default 'pending') + `snooze_count: int` (default 0). Update MODULE_MAP.
5. `alembic/jobs/versions/0002_reminder_user_state.py` — explicit ALTER (idempotent via `try/except OperationalError` like ADR-026 convention) + partial index on `(user_state, kind, scheduled_at_utc) WHERE kind='reminder_job'`.
6. `tests/unit/storage/test_baselines.py` — extend to cover the new columns exist after baseline + after 0002 upgrade.
7. `tests/unit/storage/test_payloads.py` — assert ReminderPayload still round-trips with the new field set.
8. `make lint` → green.
9. **Commit:** `feat(M-STORAGE-JOBS): ReminderPayload.category + jobs.user_state/snooze_count (aisw-163 P1)`.

## Phase 2 — sessions.cards_enabled toggle (extends ADR-026)

**Goal:** the per-user opt-out switch is readable/writable. No card emission yet.

1. `tests/unit/storage/test_digest_prefs.py` — RED:
   - `test_cards_enabled_default_true` — absent row ⇒ `True`.
   - `test_set_cards_enabled_round_trip` — set False, read False.
2. `src/ai_steward_wiki/storage/sessions/models.py` — add `cards_enabled: bool` (server_default text("1")).
3. `src/ai_steward_wiki/storage/sessions/digest_prefs.py`:
   - Extend `DigestPrefs` dataclass with `cards_enabled: bool = True`.
   - Add `set_cards_enabled(session_maker, telegram_id, *, enabled: bool)` helper.
   - Update MAP + CHANGE_SUMMARY.
4. `alembic/sessions/versions/0003_cards_enabled.py` — idempotent ALTER (sessions convention).
5. `make lint` + targeted pytest green.
6. **Commit:** `feat(M-STORAGE-SESSIONS): user_digest_prefs.cards_enabled (aisw-163 P2)`.

## Phase 3 — M-DIGEST-CARDS module (render + window query)

**Goal:** given an owner + now_utc, emit the card messages. Pure read-side.

1. `tests/unit/digest/test_cards.py` — RED:
   - `test_render_medication_card` — keyboard has 3 buttons with expected callback_data `r:<id>:done|snz|skp`.
   - `test_render_event_card` — different button labels.
   - `test_render_generic_card` — default labels.
   - `test_cap_at_8` — given 12 pending in window ⇒ emit 8, return tuple `(emitted=8, total=12)`.
   - `test_only_pending_in_window` — done/skipped/snoozed and outside-window rows excluded.
2. `src/ai_steward_wiki/digest/__init__.py` — new package.
3. `src/ai_steward_wiki/digest/cards.py`:
   - `BUTTON_TEMPLATES: dict[str, list[tuple[str, str]]]` — per-category label + action.
   - `_render_card(payload, job_id) -> tuple[str, InlineKeyboardMarkup]`.
   - `async def emit_reminder_cards(*, sender, owner_telegram_id, chat_id, now_utc, sessions, cap=8) -> tuple[int, int]`.
   - Full MODULE_CONTRACT, MAP, anchors.
4. Log anchor: `digest.cards.emitted` with `{owner_telegram_id, emitted, total, by_category}`.
5. `make lint` + pytest green.
6. **Commit:** `feat(M-DIGEST-CARDS): card render + window query (aisw-163 P3)`.

## Phase 4 — M-TG-CALLBACKS (state mutation)

**Goal:** button presses change state idempotently + snooze reschedules.

1. `tests/unit/tg/test_callbacks.py` — RED:
   - `test_parse_callback_data_valid` + `test_parse_invalid` (bad fmt, foreign action).
   - `test_done_marks_state_done_cas` — second press ⇒ idempotent_noop, no state change.
   - `test_skip_marks_skipped`.
   - `test_snooze_reschedules_30min_and_increments_count`.
   - `test_snooze_cap_3_collapses_to_skip`.
   - `test_owner_mismatch_silent_ack`.
2. `src/ai_steward_wiki/tg/callbacks.py`:
   - `CallbackContext` dataclass (scheduler, sessions_maker, sender) + `set_callback_context`.
   - `parse_reminder_callback(data: str) -> tuple[int, Literal["done","snz","skp"]] | None`.
   - `async def on_reminder_card(callback: CallbackQuery) -> None` — main handler.
   - `_cas_set_state(session, job_id, target_state, expected='pending') -> int` (rowcount).
   - `_snooze(session, scheduler, job)` — increments + reschedules via `create_reminder_job`-equivalent (or direct DateTrigger).
   - MODULE_CONTRACT, MAP, anchors `tg.callback.reminder_card.{done,snz,skp,idempotent_noop,owner_mismatch,bad_data,snooze_cap_hit}`.
3. `make lint` + pytest green.
4. **Commit:** `feat(M-TG-CALLBACKS): reminder card callbacks + CAS state + snooze (aisw-163 P4)`.

## Phase 5 — wiring (firing + handlers + runtime)

**Goal:** integrate cards into the live digest path; register callback handler.

1. `tests/unit/scheduler/test_firing.py` — extend RED:
   - `test_digest_emits_cards_when_prefs_enabled` — mock `emit_reminder_cards`, assert called between `deliver_output` and final commit.
   - `test_digest_skips_cards_when_prefs_disabled`.
   - `test_digest_cards_failure_does_not_strike_digest` — degrade-to-skip on emit failure.
2. `src/ai_steward_wiki/scheduler/firing.py`:
   - Inside `fire_digest_job`, after `deliver_output` success: if `prefs.cards_enabled`, `try: await emit_reminder_cards(...)` ; on exception log `scheduler.digest.cards_failed` and continue.
   - Bump VERSION; CHANGE_SUMMARY entry; update MODULE_MAP if API changes.
3. `tests/unit/tg/test_commands.py` (or new `test_callbacks_wiring.py`) — assert `CallbackQuery(F.data.startswith("r:"))` handler registered before catch-all.
4. `src/ai_steward_wiki/tg/handlers.py` — register `on_reminder_card` in `build_router` before existing handlers.
5. `src/ai_steward_wiki/__main__.py` — wire `set_callback_context(scheduler, sessions_maker, sender)` at startup; pass `sessions_maker` into `emit_reminder_cards` via `set_digest_context` (already plumbed for prefs in aisw-pv8).
6. `make total-test` (lint + grace + coverage ≥80%).
7. `grace-refresh` — update `docs/knowledge-graph.xml` + `docs/verification-plan.xml`.
8. **Commit:** `feat(M-SCHEDULER-FIRING,M-TG-HANDLERS-WIRING,M-RUNTIME-WIRING): wire cards (aisw-163 P5)`.

## Verification matrix

| FR | Phase | Test |
|----|-------|------|
| FR-1 (±2h window query) | 3 | `test_only_pending_in_window` |
| FR-2 (per-category buttons) | 3 | `test_render_*_card` |
| FR-3 (jobs.db state) | 1+4 | `test_done_marks_state_done_cas` |
| FR-4 (snooze reschedule) | 4 | `test_snooze_reschedules_30min_and_increments_count` |
| FR-5 (no PendingConfirm in cards) | n/a | excluded by design — only reminder_job queried |
| FR-6 (after summary, before doc) | 5 | `test_digest_emits_cards_when_prefs_enabled` |
| FR-7 (cards_enabled opt-out) | 2+5 | `test_digest_skips_cards_when_prefs_disabled` |
| FR-8 (idempotent ack ≤1 line) | 4 | `test_done_marks_state_done_cas` second-press branch |

## Out of scope (carry-over)

1. `/cards on|off` slash command — extends ADR-026 toggle UI in a future bd.
2. pending_confirmation cards (needs long-lived needs-answer queue — separate future bd).
3. Per-category snooze override.
4. Card editing post-creation.

## Notes for executor

1. **Strict TDD:** RED → run pytest → see fail → GREEN → run pytest → see pass. No write of impl before failing test in target phase.
2. **Context7 triggers:** only on library error or unfamiliar method — pre-verified APIs in design §7.
3. **No `--no-verify` ever.** Pre-commit failures = fix root cause.
4. **Deviation gate:** if a phase grows by >2 unplanned files, prompt user.
