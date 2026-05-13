---
bd_id: aisw-163
title: "Inbox-WIKI digest: actionable inline cards (±2h)"
date: 2026-05-13
phase: discovery
status: draft
related:
  adr:
    - ADR-025-digest-interactive-surface.md
    - ADR-006-inbox-wiki-reminder-cron-bridge.md
    - ADR-024-digest-presentation.md
  spec_decisions:
    - D-023-confirmation-flow
    - D-024-digest-format
  bd_parent: aisw-269
  bd_epic: aisw-t2r
fr:
  - id: FR-1
    text: "Digest emits a separate Telegram message with inline keyboard for every owner reminder_job whose fire-time falls in [now-2h, now+2h] window at digest fire time."
  - id: FR-2
    text: "Reminder cards carry category-specific button sets — medication: ✅Принял / ⏰+30мин / ❌Skip; event: 📍Я в пути / ⏰Опаздываю / ❌Отменить; generic: ✅Сделал / ⏰+30мин / ❌Skip."
  - id: FR-3
    text: "Reminder card buttons mutate persistent state in jobs.db (NOT in sessions.db) — done/snoozed/skipped — so the next digest no longer surfaces a resolved card."
  - id: FR-4
    text: "Snooze (+30мин) reschedules the same job to fire 30 minutes later (single new APScheduler DateTrigger), preserving job_id, message, lead_time_min."
  - id: FR-5
    text: "pending_confirmation surface stays out of digest cards (data-source mismatch: TTL 10min vs digest cadence — see ADR-025 §Options.1); covered later by a separate long-lived needs-answer queue."
  - id: FR-6
    text: "Cards appear AFTER the digest summary message but BEFORE the document fallthrough (D-025 hybrid order); read-only items stay buttonless in the summary."
  - id: FR-7
    text: "Card emission is opt-out via user_digest_prefs.cards_enabled (default ON); reuses ADR-026 toggle infra."
  - id: FR-8
    text: "Each button press is acked with a single short ru reply (≤1 line), no Claude call; idempotent — pressing the same button twice is a no-op."
nfr:
  - id: NFR-1
    type: latency
    text: "Card emission per reminder ≤50ms (no Claude, no HTTP — pure DB scan + TG send)."
  - id: NFR-2
    type: observability
    text: "Log anchors: tg.command.reminder_card.{pressed,done,snoozed,skipped,idempotent_noop,owner_mismatch}; scheduler.digest.cards_emitted with count by category."
  - id: NFR-3
    type: security
    text: "Callback owner-check: only the reminder_job.owner_telegram_id may resolve the card; foreign user_id ⇒ silent ack + owner_mismatch log."
  - id: NFR-4
    type: backwards_compat
    text: "Existing reminder_job rows (no category, no state) keep firing unchanged via DateTrigger; only the digest surface materialises cards for them as 'generic'."
  - id: NFR-5
    type: testability
    text: "≥90% line coverage on new modules; integration test: create reminder → fire digest → assert card → press button → assert state + assert next digest skips it."
constraints:
  - "No new third-party deps."
  - "Schema change is alembic/jobs/0002_*; no sessions.db migration in this feature."
  - "ReminderPayload version bump 0.0.3 → 0.0.4; legacy rows MUST keep validating (category default 'generic', state default 'pending')."
  - "Ru-only strings (D-032)."
risks:
  - id: R-1
    text: "Schema-rollout risk: existing rows missing category/state. Mitigation — migration backfills 'generic'/'pending' for all existing reminder_job rows in one DDL+UPDATE alembic step."
  - id: R-2
    text: "Card explosion: a heavy reminder schedule could emit ≥10 cards per digest, drowning the summary. Mitigation — hard cap N=8 per digest, oldest first, residual count appended to summary footer."
  - id: R-3
    text: "Idempotency race: user double-taps in <1s. Mitigation — DB-level UPDATE … WHERE state='pending' (single-row CAS); second hit returns 0 rows ⇒ idempotent_noop ack."
  - id: R-4
    text: "Snooze recursion: snooze emits a new card 30min later, which user snoozes again. Mitigation — max_snooze_count=3 per job; 4th press collapses to skip ack."
  - id: R-5
    text: "Category misclassification: medication labelled as event ⇒ wrong button set. Mitigation — classification happens at create_reminder_job time (NL pipeline), changeable via /reminder_edit (deferred — out of scope)."
scope:
  in:
    - "ReminderPayload.category: Literal['medication','event','generic'] = 'generic' (semver 0.0.3 → 0.0.4)."
    - "jobs.jobs.user_state column (alembic/jobs/0002_user_state): TEXT NOT NULL DEFAULT 'pending' CHECK IN ('pending','done','snoozed','skipped')."
    - "jobs.jobs.snooze_count column: INTEGER NOT NULL DEFAULT 0."
    - "M-DIGEST-CARDS new module: window_query → card render → emit; integrates into fire_digest_job between summary delivery and document fallthrough."
    - "M-TG-CALLBACKS new module: callback_data parser, owner-check, state mutator, snooze rescheduler, ack sender."
    - "user_digest_prefs.cards_enabled BOOL DEFAULT 1 (sessions.db, alembic/sessions/0002 — extends ADR-026 row)."
    - "/digest_now and cron digest both emit cards (reuse via fire_digest_job)."
    - "Integration tests: card emission, button press, snooze, skip, idempotency, owner-check."
  out:
    - "pending_confirmation cards (no long-lived needs-answer queue yet — own future bd)."
    - "Card editing / reschedule UX beyond snooze+30."
    - "Per-category templates configurable by user."
    - "i18n / non-ru strings."
    - "Card delivery on /expand (only /digest_now and cron digest)."
    - "Webhook/inline-mode equivalents."
open_questions:
  - id: Q-1
    text: "Snooze ±30 — fixed at 30min or configurable per category (med ±15, event ±10)? (recommend fixed 30 for MVP)"
  - id: Q-2
    text: "user_state on Job row vs separate reminder_state table? Choice impacts indexability and audit trail. (recommend column for MVP — single-writer, no history needed yet)"
  - id: Q-3
    text: "Card cap N=8 — tuneable? (recommend hardcoded constant + log overflow; ADR if user requests)"
  - id: Q-4
    text: "Should /digest_now emit cards on demand even if scheduled digest already emitted them today (duplicate cards)? (recommend yes — /digest_now is user-initiated, idempotency on press handles duplicates safely)"
stakeholders:
  - role: owner (telegram user)
    impact: "Gets actionable cards in digest; can resolve reminders in one tap."
  - role: bot operator (admin)
    impact: "New module M-DIGEST-CARDS + M-TG-CALLBACKS, new alembic migration on jobs.db AND sessions.db."
  - role: scheduler
    impact: "Snooze creates new DateTrigger via existing add_job path."
preflight:
  lint_baseline_errors: 0
  hooks_path: ".beads/hooks"
  sentrux_rules_present: false
  parent_bd_aisw_269: closed
  adr_025_status: accepted
---

# Discovery — actionable inline cards in digest (aisw-163)

## 1. Intent

The user asked for the deferred half of D-024: every owner reminder firing in ±2h around the digest gets an **actionable card** — a separate TG message with inline buttons that mutate state. ADR-025 punted because `reminder_job` had no category and no done-state, and `sessions.PendingConfirm` had a 10-minute TTL that the cron digest can never catch.

The blocker is structural, not interactive: we must **add a category model + a per-job state field to `reminder_job` BEFORE the card surface can do anything useful**. That is the heart of this feature. The buttons themselves are trivial; the data model is the design decision.

`pending_confirmation` cards are explicitly **deferred again** — the 10-min TTL story has not changed and a new long-lived needs-answer queue is its own design pass (separate future bd).

## 2. What's already there

1. `ReminderPayload(kind='reminder_job', message: str, lead_time_min: int)` — no subtype, no state (storage/jobs/payloads.py:82–85).
2. `jobs.jobs.status ∈ scheduled/running/finished/failed/cancelled` — *lifecycle* state, not *user-resolution* state.
3. `fire_digest_job` (scheduler/firing.py:590) already does the planner-window query and delivers via `deliver_output(kind='digest', job_id=…)`; this is the integration point for cards.
4. `aiogram.filters.Command` slash-router is in place after ADR-025 — handler-registration order is solved, callbacks just register similarly.
5. `user_digest_prefs` table from ADR-026 exists in sessions.db — extending it for `cards_enabled` is one alembic step.
6. `D-023 ConfirmationService` is the existing button + ack pattern — we mimic its shape but not its TTL (cards are stateless beyond jobs.db state).

## 3. What's missing

1. **Category on the payload.** Without it the rendering layer can't pick button sets.
2. **User-resolution state.** Without `user_state ∈ pending/done/snoozed/skipped` plus `snooze_count`, every digest re-surfaces the same fired reminder forever.
3. **A card-render module** that joins payload + state and emits the inline keyboard.
4. **A callback handler** that mutates state with owner-check + idempotency.
5. **Snooze rescheduling** — the only button that produces a new APScheduler trigger.

## 4. FR / NFR / risks / scope

See frontmatter. Eight FR, five NFR, five risks, eight IN, six OUT, four open questions.

## 5. Best-practice notes

1. Owner-check on every callback — Telegram inline callbacks are spoofable across chats if you only filter by `callback_data`; always verify `callback.from_user.id == job.owner_telegram_id`.
2. State mutation via single-row CAS UPDATE (`WHERE state='pending'`) — the only race-free pattern for two-tap idempotency without optimistic locking framework.
3. Card cap with overflow footer — production Telegram bots that emit ≥10 inline messages per push routinely get rate-limited; N=8 is the empirical sweet spot.
4. Category default `'generic'` — additive enum migrations stay backwards-compatible as long as the legacy bucket is `'generic'`, not `NULL`.
5. Snooze cap (max 3) — prevents the "snooze-loop" anti-pattern that erodes the entire reminder UX.

## 6. Open questions (carried into Brainstorming)

See `open_questions` in frontmatter — to be resolved in Step 4 with recommended defaults applied unless the user objects in Step 5.

## 7. Verification anchors (sketch — formalised in Step 7)

1. Unit: payload validator for new category enum + legacy rows.
2. Unit: card-render produces the right keyboard per category.
3. Unit: state mutator idempotency (double press).
4. Integration: digest → card → press → next digest skips it.
5. Integration: snooze → 30min later card re-appears.
6. Trace: `scheduler.digest.cards_emitted` count matches rendered N (capped at 8).
