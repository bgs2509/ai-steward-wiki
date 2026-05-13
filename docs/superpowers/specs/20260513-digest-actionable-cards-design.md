---
bd_id: aisw-163
title: "Inbox-WIKI digest: actionable inline cards (±2h) — design"
date: 2026-05-13
phase: design
status: draft
discovery: 20260513-digest-actionable-cards-discovery.md
related_adrs:
  - ADR-025-digest-interactive-surface.md
  - ADR-026-digest-section-toggles.md
  - ADR-006-inbox-wiki-reminder-cron-bridge.md
stack:
  - python: "3.11+"
  - aiogram: "3.x — CallbackQuery handlers + InlineKeyboardBuilder"
  - sqlalchemy: "2.0 async — single-row CAS UPDATE for state mutation"
  - alembic: "per-DB (jobs/0002, sessions/0002)"
  - apscheduler: "AsyncIOScheduler — DateTrigger for snooze rescheduling (existing path)"
modules:
  new:
    - M-DIGEST-CARDS  # window query + render + emit; called by fire_digest_job
    - M-TG-CALLBACKS  # CallbackQuery router, owner-check, state CAS, ack
  changed:
    - M-STORAGE-JOBS         # ReminderPayload.category, Job.user_state, Job.snooze_count
    - M-SCHEDULER-FIRING     # fire_digest_job calls cards-emit hook
    - M-STORAGE-SESSIONS     # user_digest_prefs.cards_enabled (extends ADR-026)
    - M-TG-HANDLERS-WIRING   # register CallbackQuery handlers before catch-all
    - M-RUNTIME-WIRING       # wire cards module into bot startup
choice: "Option A — Job row column for user_state + snooze_count"
---

# Design — actionable cards (aisw-163)

## 1. Three architecture options considered

### Option A — `user_state` column on `jobs.jobs` (RECOMMENDED)

Add two NOT NULL columns directly to `jobs.jobs`:

```python
user_state: Mapped[str] = mapped_column(
    String(16), nullable=False, default="pending", index=True
)  # 'pending' | 'done' | 'snoozed' | 'skipped'
snooze_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

**Pros:**
1. Single-table write — CAS UPDATE is one statement, race-free without optimistic locking.
2. Index on `(user_state, kind, scheduled_at_utc)` makes the digest window query O(log n).
3. No new FK joins in the hot digest path.
4. Backwards-compat trivial — defaults backfill legacy rows.

**Cons:**
1. Mixes lifecycle state (`status`) and user-resolution state (`user_state`) on one row — *semantic* but not *structural* mix; the two never collide.
2. No audit trail of state transitions (acceptable for MVP — see Option C if requested).

### Option B — Separate `reminder_state` table

```sql
CREATE TABLE reminder_state (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK (state IN ('pending','done','snoozed','skipped')),
    snooze_count INTEGER NOT NULL DEFAULT 0,
    updated_at_utc TIMESTAMP NOT NULL
);
```

**Pros:**
1. Clean separation of concerns.
2. Easier to extend with history table later.

**Cons:**
1. Every card render joins; every press updates 1 row + reads 1 row.
2. Card-emission window query needs LEFT JOIN + COALESCE(state, 'pending') — ugly.
3. Migration is two DDL statements vs one.
4. **YAGNI** — we don't need history yet, and Option C handles that need later if it arises.

### Option C — `user_state` column + append-only `reminder_state_log`

Hybrid: Option A for hot path + a log table for audit.

**Pros:** full audit trail.

**Cons:** two writes per press, double the test surface, no current FR demands history. **Defer.**

### Decision: Option A

KISS + indexability + race-free CAS in one statement. Option C trivially additive later if FR-9-history ever appears.

## 2. Data model — final

### 2.1 `ReminderPayload` (storage/jobs/payloads.py)

```python
class ReminderPayload(_PayloadBase):
    kind: Literal["reminder_job"] = "reminder_job"
    message: str
    lead_time_min: int = Field(default=0, ge=0)
    category: Literal["medication", "event", "generic"] = "generic"  # NEW

# VERSION: 0.0.3 → 0.0.4
```

Legacy rows without `category` validate fine (default applies). No JSON migration needed.

### 2.2 `jobs.jobs` (alembic/jobs/versions/0002_reminder_user_state.py)

```sql
ALTER TABLE jobs ADD COLUMN user_state TEXT NOT NULL DEFAULT 'pending'
    CHECK (user_state IN ('pending','done','snoozed','skipped'));
ALTER TABLE jobs ADD COLUMN snooze_count INTEGER NOT NULL DEFAULT 0;
CREATE INDEX ix_jobs_user_state_kind_scheduled
    ON jobs(user_state, kind, scheduled_at_utc)
    WHERE kind = 'reminder_job';
```

Partial index — only reminder rows participate; digest/cron/purge rows ignored.

### 2.3 `user_digest_prefs` (alembic/sessions/versions/0002_cards_enabled.py)

```sql
ALTER TABLE user_digest_prefs ADD COLUMN cards_enabled BOOLEAN NOT NULL DEFAULT 1;
```

One column, extends the ADR-026 row.

## 3. Callback-data format

`r:<job_id>:<action>` where action ∈ `done|snz|skp`.

```python
# Total length ≤ 64 bytes (Telegram callback_data hard limit) — even with job_id=9_999_999_999.
# Parsing: "r:<int>:<3-char>" — fail-fast on malformed.
```

Owner-check: callback handler resolves `Job(id=job_id)`, asserts `from_user.id == job.owner_telegram_id`; mismatch → silent ack `''` + `tg.callback.reminder_card.owner_mismatch` log.

## 4. Module sketches

### 4.1 M-DIGEST-CARDS (`src/ai_steward_wiki/digest/cards.py` — NEW)

```python
async def emit_reminder_cards(
    *,
    owner_telegram_id: int,
    chat_id: int,
    now_utc: datetime,
    sender: Sender,
    sessions: SessionFactory,
    cap: int = 8,
) -> int:
    """Query jobs.jobs for reminder_jobs of owner, kind='reminder_job',
    user_state='pending', scheduled_at_utc ∈ [now-2h, now+2h]; render and emit
    one TG message per card up to `cap`; return count emitted (caller may
    append residual footer if return < total)."""
```

Internals:
1. `select(Job).where(...).order_by(scheduled_at_utc).limit(cap+1)` — fetch cap+1 to detect overflow cheaply.
2. For each: parse `payload` → `ReminderPayload` → pick keyboard template by `.category`.
3. `await sender.send_message(chat_id, text, reply_markup=kb)`.
4. Log `scheduler.digest.cards_emitted` with `{emitted_n, total_n, by_category}`.

### 4.2 M-TG-CALLBACKS (`src/ai_steward_wiki/tg/callbacks.py` — NEW)

```python
async def on_reminder_card(callback: CallbackQuery, ...) -> None:
    """Parse callback_data → resolve Job → owner-check → branch on action."""
```

Action branches:
1. **done** — `UPDATE jobs SET user_state='done', finished_at_utc=:now WHERE id=:id AND user_state='pending'`. Rows=1 → ack "✅ Готово"; Rows=0 → ack "уже отмечено" + `idempotent_noop` log.
2. **skp** — same CAS with `user_state='skipped'`. Ack "❌ Пропущено".
3. **snz** — CAS to `user_state='snoozed'`, `snooze_count = snooze_count + 1`; if `snooze_count >= 3` → collapse to skip ack "❌ Хватит откладывать"; else create new `reminder_job` via existing `create_reminder_job` path with `scheduled_at_utc = now + 30min`, same message + category. Ack "⏰ +30 мин".

Idempotency: the CAS UPDATE's affected-row-count is the single source of truth — no extra locking needed.

### 4.3 M-SCHEDULER-FIRING change

`fire_digest_job` already orchestrates summary + document. Add **between** them:

```python
# pseudo
prefs = await get_owner_digest_prefs(owner_telegram_id)
if prefs.cards_enabled:
    n = await emit_reminder_cards(
        owner_telegram_id=owner_telegram_id,
        chat_id=chat_id,
        now_utc=now,
        sender=sender,
        sessions=sessions,
    )
    _log.info("scheduler.digest.cards_emitted", job_id=job_id, emitted=n)
```

### 4.4 Router order (M-TG-HANDLERS-WIRING)

aiogram router order: `Command(...)` group first, then `CallbackQuery(F.data.startswith("r:"))` for reminder cards (and future card types under their own prefix), then existing `F.text` catch-all. Router-order regression test guards.

## 5. UX flow

1. **Cron digest fires at 09:00.** Summary message goes out. `cards_enabled=true`. Three reminders in ±2h window:
   - 08:30 "лекарство — Розувастатин" (`category='medication'`)
   - 09:15 "встреча с Иваном" (`category='event'`)
   - 10:30 "позвонить врачу" (`category='generic'`)
2. **Three card messages follow.** Each with its own 3-button inline keyboard.
3. **User taps ✅ on the medication card.** Bot edits the card text (`Принял ✅ Розувастатин`) and removes the keyboard via `callback.message.edit_text(...)`; ack popup empty.
4. **Next digest at 21:00** — medication card is gone from window (user_state='done'), the others may re-appear if still in their ±2h windows.

## 6. Verification plan (sketch — Step 7 formalises)

1. **Unit tests:** `tests/unit/storage/test_reminder_payload.py` (category validation + legacy default), `tests/unit/digest/test_cards.py` (render templates), `tests/unit/tg/test_callbacks.py` (parser, owner-check, CAS idempotency, snooze cap).
2. **Integration tests:** `tests/integration/digest/test_cards_flow.py` — create reminder → fire digest → assert N card messages → simulate button press → assert state → fire digest again → assert excluded.
3. **Trace assertions:** `scheduler.digest.cards_emitted.emitted == rendered_card_count`; `tg.callback.reminder_card.done|snz|skp` per press.

## 7. Library APIs verified (Context7 / inline)

1. `aiogram.types.CallbackQuery.answer(text="...", show_alert=False)` — present in 3.x.
2. `aiogram.types.CallbackQuery.message.edit_text(text, reply_markup=None)` — present in 3.x.
3. `sqlalchemy.update(Job).where(...).values(...)` returning rowcount on async session — present in 2.0 (need `await session.execute(stmt)`, then `result.rowcount`).

Confirmed locally — same patterns already used in `tg/confirm.py` (ConfirmationService).

## 8. Open questions — recommended defaults

| Q | Recommendation | Rationale |
|---|----------------|-----------|
| Q-1 snooze duration | fixed 30min | KISS; per-category configurable is YAGNI |
| Q-2 state placement | column on Job | Option A above |
| Q-3 card cap N | hardcoded 8 | empirical TG rate-limit margin |
| Q-4 /digest_now duplicate cards | yes, emit | idempotency on press handles re-emission |

## 9. Risks revisited (after design)

| Risk | Mitigation present in design? |
|------|-------------------------------|
| R-1 legacy rows | ✅ defaults backfill |
| R-2 card explosion | ✅ cap=8 + overflow footer in summary |
| R-3 idempotency race | ✅ CAS UPDATE rowcount |
| R-4 snooze recursion | ✅ snooze_count cap=3 |
| R-5 misclassification | ✅ deferred /reminder_edit (out of scope) |

## 10. Out-of-scope reminders

1. Editing reminder category post-creation.
2. Per-user snooze duration override.
3. Long-lived needs-answer queue for `pending_confirmation` (its own future bd).
4. Card surface for `/expand`.
