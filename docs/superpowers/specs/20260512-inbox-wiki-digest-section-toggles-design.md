---
feature: inbox-wiki-digest-section-toggles
bd_id: aisw-pv8
phase: Inbox-WIKI Phase-D.b.2c
status: design
date: 2026-05-12
discovery: docs/superpowers/specs/20260512-inbox-wiki-digest-section-toggles-discovery.md
adr: docs/adr/ADR-026-digest-section-toggles.md   # to be written in step 8/13
covers: [FR-1, FR-2, FR-3, FR-4, FR-5, FR-6]
stack_decisions:
  - id: TD-1
    text: "Storage shape — one row per owner with two boolean columns: user_digest_prefs(user_id INTEGER PK FK→users.user_id ON DELETE CASCADE, trackers_enabled BOOLEAN NOT NULL DEFAULT 1, wiki_enabled BOOLEAN NOT NULL DEFAULT 1, updated_at_utc DATETIME NOT NULL). NOT a generic (user_id, section_key, enabled) EAV table — the toggleable set is closed by ADR-025 (exactly `trackers`, `wiki`); TL;DR and `today` stay always-on. FK target = users.user_id (surrogate), mirroring inbox_hint_cache; resolved from telegram_id via storage.sessions.users.resolve_user_id. Absent row (or absent User) ⇒ both default True (opt-out feature — never opt-in)."
  - id: TD-2
    text: "alembic/sessions/versions/0002_user_digest_prefs.py — first incremental sessions.db migration past 0001_sessions_baseline. revision='0002_user_digest_prefs', down_revision='0001_sessions_baseline'; upgrade() = op.create_table('user_digest_prefs', …) (render_as_batch=True already set in env.py); downgrade() = op.drop_table. CONVENTION (recorded in ADR-026): baseline stays a whole-schema Base.metadata.create_all snapshot, each NNNN_* migration is the delta via op.* — and new ORM models are added to storage/sessions/models.py so a from-empty `upgrade head` (which runs baseline) also creates them; stepwise (0001→0002) and from-empty `upgrade head` must converge. jobs.db and audit.db untouched."
  - id: TD-3
    text: "Shared section-key vocabulary — TOGGLEABLE_DIGEST_SECTIONS: tuple[str,...] = ('trackers','wiki') (a subset of the existing tg/handlers.EXPAND_SECTION_KEYS = ('today','meds','trackers','wiki')) + SECTION_DISPLAY_NAME mapping key→ru label with emoji ('trackers'→'📈 Трекеры', 'wiki'→'📝 Обновления WIKI'). Defined once (next to EXPAND_SECTION_KEYS in tg/handlers.py) and reused by the command, the keyboard, the firing directive, and the migration/model column names. Test: set(TOGGLEABLE_DIGEST_SECTIONS) ⊆ set(EXPAND_SECTION_KEYS)."
  - id: TD-4
    text: "Repo module storage/sessions/digest_prefs.py — frozen dataclass DigestPrefs(trackers_enabled: bool, wiki_enabled: bool) with property disabled_keys -> tuple[str,...]; async get_digest_prefs(session_maker, telegram_id) -> DigestPrefs (resolve user_id; no row / no User ⇒ DigestPrefs(True, True)); async set_digest_section(session_maker, telegram_id, *, section: str, enabled: bool) -> DigestPrefs (upsert: create row with defaults if absent, set the one column, bump updated_at_utc; if telegram_id has no User row ⇒ no write, return DigestPrefs(True, True) — the command must not crash if invoked before onboarding)."
  - id: TD-5
    text: "/digest_sections command — aiogram Command('digest_sections') handler registered in build_router alongside Command('digest_now')/Command('expand') (before the F.text catch-all). Replies with a ru header + InlineKeyboardMarkup: one button per toggleable section, text f'{SECTION_DISPLAY_NAME[k]}: {\"вкл ✅\" if on else \"выкл ⬜\"}', callback_data f'digestsec:{k}:{0 if on else 1}' (carries the TARGET state ⇒ idempotent on a stale message). New callback handler @router.callback_query(F.data.startswith('digestsec:')) → parse_digestsec_callback(data)->(section, target_enabled)|None → set_digest_section → edit_reply_markup with the rebuilt keyboard → cb.answer. parse_digestsec_callback: split ':' into exactly 3, section ∈ TOGGLEABLE_DIGEST_SECTIONS, flag ∈ {'0','1'}; else None. Independent from the confirm: prefix/parser. build_router gains access to the sessions session_maker (same channel firing.set_digest_context already uses for jobs/audit makers). Log anchors tg.command.digest_sections.shown / .toggled / .bad_callback. Ru-only strings, no i18n (D-032)."
  - id: TD-6
    text: "Honour toggles in fire_digest_job — firing.set_digest_context gains a sessions_session_maker (or a bound get_digest_prefs); in fire_digest_job, after resolving the owner + WIKI set and before calling runner: prefs = await get_digest_prefs(sessions_session_maker, owner_telegram_id) (any failure ⇒ treated as DigestPrefs(True,True) — degrade to all-on, never skip the digest); disabled = prefs.disabled_keys; if disabled: planner_context = f'{planner_context}\\n\\nНе включай разделы: {\", \".join(SECTION_DISPLAY_NAME[k] for k in disabled)}.' and log scheduler.digest.sections_filtered{job_id, owner_telegram_id, disabled}; else planner_context unchanged (BYTE-IDENTICAL to today). runner(..., section=None) as today; the DigestRunner Protocol and _DigestRunnerAdapter are NOT changed (the directive rides inside the existing planner_context string). /expand path (run_section_expand) is NOT changed — an explicit /expand <section> overrides the toggle (the toggle governs only the unattended cron digest)."
  - id: TD-7
    text: "prompts/digest.md — semver 0.1.0 → 0.1.1: add one sentence after the «Запланировано…» paragraph: «Если в сообщении есть строка вида „Не включай разделы: …" — полностью пропусти перечисленные секции, даже если по ним есть содержимое.» prompts/digest_expand.md untouched."
  - id: TD-8
    text: "No new third-party dependency. Exactly one new SQLite table and one new Alembic migration, both sessions.db only. DigestPayload / storage/jobs/* untouched (no jobs.db migration). tg/pipeline.py untouched. All datetime UTC. mypy --strict for src/. structlog with ts/event/correlation_id/owner_telegram_id/job_id."
---

# Design — Inbox-WIKI Phase-D.b.2c: per-user digest section toggles (`aisw-pv8`)

> Requirements / scope / risks SSoT: the discovery doc (`…-digest-section-toggles-discovery.md`). This doc = the solution: approach, data shapes, control flow, error handling, testing. Q&A decision log at the end.

## 1. Approach in one paragraph

Add the deferred-from-`aisw-269` personalisation knob: a `user_digest_prefs` table in `sessions.db` (one row per owner, two boolean columns `trackers_enabled` / `wiki_enabled`, default both `True`, FK `users.user_id` `ON DELETE CASCADE` — mirroring `inbox_hint_cache`), reached by the first incremental `sessions.db` migration (`alembic/sessions/versions/0002_user_digest_prefs.py`, `op.create_table` delta on top of the `create_all` baseline). A new third slash command `/digest_sections` shows the caller's state as an inline keyboard of toggle buttons (`digestsec:<key>:<target>` callbacks, message edited in place). At digest fire time, `fire_digest_job` reads the owner's prefs and — **only when something is off** — appends `«Не включай разделы: 📈 Трекеры.»` to the `planner_context` string that goes into the digest prompt; when all sections are on, the prompt input is byte-identical to today. `prompts/digest.md` gains one sentence describing the directive. `/expand <section>` is unchanged (an explicit request overrides the toggle). New ADR-026 (incl. the migration-convention rule); GRACE refresh. **No `DigestPayload` change, no `jobs.db` migration, no scheduling-time section selection, no cards** (cards still deferred per ADR-025 §8).

## 2. Components & responsibilities

| Unit | File | Change | Depends on |
|------|------|--------|-----------|
| `UserDigestPrefs` model | `src/ai_steward_wiki/storage/sessions/models.py` | NEW ORM model (next to `InboxHintCache`): `user_id` PK + FK `users.user_id` CASCADE; `trackers_enabled` / `wiki_enabled` `Mapped[bool]` `default=True, server_default=text("1")`; `updated_at_utc` UTC. Update MODULE_MAP. | `Base`, `users` table |
| digest-prefs repo | `src/ai_steward_wiki/storage/sessions/digest_prefs.py` | NEW: `DigestPrefs` frozen dataclass (`trackers_enabled`, `wiki_enabled`, `disabled_keys` property); `async get_digest_prefs(session_maker, telegram_id) -> DigestPrefs`; `async set_digest_section(session_maker, telegram_id, *, section, enabled) -> DigestPrefs`. Module contract per GRACE. | `resolve_user_id`, `UserDigestPrefs`, `TOGGLEABLE_DIGEST_SECTIONS` |
| sessions migration | `alembic/sessions/versions/0002_user_digest_prefs.py` | NEW: `op.create_table('user_digest_prefs', …)`; `down_revision='0001_sessions_baseline'`; `downgrade()` drops it. ROLE: SCRIPT. | alembic env (`render_as_batch=True`) |
| section vocab | `src/ai_steward_wiki/tg/handlers.py` | NEW module-level `TOGGLEABLE_DIGEST_SECTIONS = ("trackers","wiki")`, `SECTION_DISPLAY_NAME = {"trackers":"📈 Трекеры","wiki":"📝 Обновления WIKI"}` (next to `EXPAND_SECTION_KEYS`). | — |
| `/digest_sections` handler | `src/ai_steward_wiki/tg/handlers.py` (`build_router`) | NEW: `@router.message(Command("digest_sections"))` → fetch prefs, reply ru header + `_build_sections_kb(prefs)`. try/except → ru error reply, never bubble. Log `tg.command.digest_sections.shown`. | `get_digest_prefs`, sessions session_maker, `TgSender` |
| `digestsec:` callback | `src/ai_steward_wiki/tg/handlers.py` (`build_router`) | NEW: `@router.callback_query(F.data.startswith("digestsec:"))` → `parse_digestsec_callback` → `set_digest_section` → `edit_reply_markup` with rebuilt keyboard → `cb.answer("Готово")`. Bad data → `cb.answer` + `tg.command.digest_sections.bad_callback`. | `parse_digestsec_callback`, `set_digest_section` |
| `parse_digestsec_callback` | `src/ai_steward_wiki/tg/handlers.py` | NEW (next to `parse_confirm_callback`): `data:str -> (section:str, target_enabled:bool)|None` — split `:` into exactly 3, `section ∈ TOGGLEABLE_DIGEST_SECTIONS`, flag ∈ `{"0","1"}`. | `TOGGLEABLE_DIGEST_SECTIONS` |
| `_build_sections_kb` | `src/ai_steward_wiki/tg/handlers.py` | NEW: `DigestPrefs -> InlineKeyboardMarkup` — one row per toggleable section, text `f"{SECTION_DISPLAY_NAME[k]}: {'вкл ✅' if on else 'выкл ⬜'}"`, `callback_data f"digestsec:{k}:{0 if on else 1}"`. | aiogram `InlineKeyboardMarkup` |
| digest firing | `src/ai_steward_wiki/scheduler/firing.py` | CHANGE: `set_digest_context(+sessions_session_maker=…)`; in `fire_digest_job`, after WIKI-set resolve / before `runner(...)`: read prefs (any failure ⇒ all-on), append `«Не включай разделы: …»` to `planner_context` only if `disabled` non-empty + log `scheduler.digest.sections_filtered`; call `runner(..., section=None)` as today. Protocol/adapter unchanged. | `get_digest_prefs`, `SECTION_DISPLAY_NAME` |
| runtime wiring | `src/ai_steward_wiki/__main__.py` | CHANGE: pass the `sessions` session_maker into `build_router(...)` and into `firing.set_digest_context(...)`. | sessions sessionmaker (already built at startup) |
| digest prompt | `prompts/digest.md` | CHANGE: `semver 0.1.0 → 0.1.1`; one new sentence describing the «Не включай разделы: …» directive. | — |

**Unchanged** (explicitly): `storage/jobs/*` incl. `DigestPayload`; `prompts/digest_expand.md`; `_DigestRunnerAdapter` and the `DigestRunner` Protocol; `tg/pipeline.py`; `tg/confirm.py`; `run_section_expand` / the `/expand` path; `alembic/jobs/*`, `alembic/audit/*`.

## 3. Data flow

```
Owner taps /digest_sections
  → tg.handlers _on_digest_sections
  → get_digest_prefs(sessions_sm, telegram_id)         # resolve_user_id; no row → DigestPrefs(True,True)
  → message.answer(header_ru, reply_markup=_build_sections_kb(prefs))
  → log tg.command.digest_sections.shown

Owner taps a toggle button (callback_data "digestsec:trackers:0")
  → @router.callback_query startswith "digestsec:"
  → parse_digestsec_callback → ("trackers", False)
  → set_digest_section(sessions_sm, telegram_id, section="trackers", enabled=False)   # upsert row
      → resolve_user_id; if no User → return DigestPrefs(True,True), no write
      → else: get-or-create row, row.trackers_enabled = False, row.updated_at_utc = now_utc, commit
  → cb.message.edit_reply_markup(_build_sections_kb(new_prefs))
  → cb.answer("Готово")
  → log tg.command.digest_sections.toggled{owner_telegram_id, section, enabled=False}

Cron digest fires (existing fire_digest_job path, unchanged up to here)
  → resolve owner WIKI set, build planner_context (today's logic)
  → prefs = get_digest_prefs(sessions_sm, owner_telegram_id)            # failure → DigestPrefs(True,True)
  → disabled = prefs.disabled_keys
  → if disabled:
        planner_context += f"\n\nНе включай разделы: {', '.join(SECTION_DISPLAY_NAME[k] for k in disabled)}."
        log scheduler.digest.sections_filtered{job_id, owner_telegram_id, disabled}
     # else: planner_context untouched → byte-identical to today
  → runner(wiki_id=…, wiki_path=…, extra_add_dirs=…, planner_context=planner_context, correlation_id=…, section=None)
  → deliver_output(kind='digest', job_id=…)   # unchanged; Claude omits the named sections per the prompt directive

/expand trackers  (when trackers is OFF)
  → run_section_expand(owner, "trackers")   # UNCHANGED — no prefs read; explicit request wins
  → returns the section detail as today
```

## 4. Error handling

1. `/digest_sections` handler and the `digestsec:` callback handler: whole body in `try/except` → ru error reply (`message.answer` / `cb.answer`), never bubbles to the aiogram dispatcher (NFR-5).
2. `parse_digestsec_callback` returns `None` on any malformed `callback_data` → `cb.answer("Не понял кнопку")` + `tg.command.digest_sections.bad_callback{owner_telegram_id, data}`; no DB write.
3. `set_digest_section` for a `telegram_id` with no `User` row → no write, returns `DigestPrefs(True, True)` (the command may be invoked before onboarding; must not crash).
4. `fire_digest_job` prefs read: any exception inside the prefs lookup is swallowed locally → `disabled = ()` → digest delivered with all sections (degrade-to-all-on). `fire_digest_job` keeps its outer catch-all so the scheduler loop survives regardless.
5. CASCADE: if a `User` row is deleted, its `user_digest_prefs` row is removed by FK CASCADE; the next `get_digest_prefs` returns the default — safe.

## 5. Testing (TDD, ≥80% core)

| Test file | Cases |
|-----------|-------|
| `tests/unit/storage/test_digest_prefs.py` (NEW) | get on a user with no row → `DigestPrefs(True, True)`; set `trackers=False` → get reflects; set `wiki=False` then set `wiki=True` → toggles back; `set_digest_section` for an unknown `telegram_id` → returns default, no row created; delete the `User` row → prefs row CASCADE-gone; `disabled_keys` returns `("trackers",)` etc. in the documented key order. |
| `tests/unit/storage/test_baselines.py` (EXTEND) | add `"user_digest_prefs"` to the sessions expected-tables set; NEW: stamp `0001_sessions_baseline` then `alembic upgrade head` on a tmp sqlite → table present (stepwise path); `alembic upgrade head` from empty → table present (baseline path); the two converge. |
| `tests/unit/tg/test_digest_sections.py` (NEW) | `/digest_sections` → `message.answer` called with a keyboard whose button texts match the stored state; callback `digestsec:trackers:0` → row updated, `edit_reply_markup` called with the rebuilt keyboard, `cb.answer` called; callback with garbage data → `bad_callback` logged, no DB write; idempotency: re-tapping `digestsec:trackers:0` when already off → still `False`, no error; `set(TOGGLEABLE_DIGEST_SECTIONS) ⊆ set(EXPAND_SECTION_KEYS)`. |
| `tests/unit/tg/test_handlers_router_order.py` (EXTEND, if it exists; else add to the existing router test) | an ordinary `F.text` message still reaches the `MessagePipeline` path with `Command("digest_sections")` and the `digestsec:` callback handler registered. |
| `tests/unit/scheduler/test_firing.py` (EXTEND) | no prefs row → `runner` called with the same `planner_context` string as the pre-feature baseline (byte-identical); `trackers` disabled → `runner`'s `planner_context` ends with `«Не включай разделы: 📈 Трекеры.»` and `scheduler.digest.sections_filtered{disabled=["trackers"]}` is logged; both disabled → both names in the directive; prefs read raises → digest still delivered with the unchanged `planner_context` (degrade). |
| `tests/unit/tg/test_digest_e2e.py` (EXTEND) | the existing e2e digest assertions still pass with no prefs row (regression guard for byte-identity). |

## 6. Log anchors (verification-plan)

| Event | Module/fn/block | Fields |
|-------|-----------------|--------|
| `tg.command.digest_sections.shown` | `tg.handlers._on_digest_sections` | `owner_telegram_id, trackers_enabled, wiki_enabled, correlation_id` |
| `tg.command.digest_sections.toggled` | `tg.handlers._on_digestsec_callback` | `owner_telegram_id, section, enabled, correlation_id` |
| `tg.command.digest_sections.bad_callback` | `tg.handlers._on_digestsec_callback` | `owner_telegram_id, data, correlation_id` |
| `scheduler.digest.sections_filtered` | `scheduler.firing.fire_digest_job` (block after WIKI-set resolve) | `job_id, owner_telegram_id, disabled` |

## 7. Out of scope (→ future bds / carried over)

- Scheduling-time section selection (choosing sections when creating the digest job, the way `wiki_scope` is picked) — `DigestPayload` / `jobs.db` untouched.
- Per-WIKI-per-section toggles; toggles for the always-on sections (TL;DR, `today`); a `meds` toggle (the medication section is auto-omitted when empty, so the value-add is marginal — revisit only if asked).
- Actionable inline cards (medication-due-now / event-soon / pending_confirmation) — still deferred per ADR-025 §8 (needs a job-category model or a long-lived «needs your answer» queue first).
- Rewriting `0001_baseline` (and the `jobs`/`audit` baselines) to explicit `op.create_table` — out of scope; `0002` follows the established `create_all`-snapshot + `op.*`-delta pattern, which ADR-026 now records as the convention.
- Digest management UX (`/jobs_list`, cancel/snooze/edit of a digest job); the `asyncio.PriorityQueue` worker-loop consumer; the `jobs.jobs ↔ APScheduler` reconciler; `data/runs/` retention; i18n.

## 8. Q&A decision log (2026-05-12)

1. **Storage shape** → two boolean columns in one per-user row (not a generic `(user_id, section_key, enabled)` EAV table) — the toggleable set is closed by ADR-025; KISS/YAGNI; defaults in schema; mirrors `inbox_hint_cache`. → TD-1.
2. **Migration style** → keep `0001` as a `create_all` snapshot; `0002` is an `op.create_table` delta with `down_revision='0001_sessions_baseline'`; new models also go in `models.py` so from-empty `upgrade head` produces them; ADR-026 records this as the project convention. → TD-2.
3. **`/digest_sections` UX** → inline-keyboard toggle buttons, message edited in place, new `digestsec:` callback prefix (mirrors the existing `confirm:` machinery) — better mobile UX, only two buttons, reuses the callback infrastructure. → TD-5.
4. **Prompt injection** → append a `«Не включай разделы: …»` line to the existing `planner_context` string, **only when the disabled set is non-empty** — byte-identical default, smallest surface, protects the digest golden tests; one explanatory sentence in `prompts/digest.md`. (Not a dedicated `{disabled_sections}` template variable, not a new `DigestRunner` Protocol argument.) → TD-6, TD-7.
5. **`/expand` vs toggle** → `/expand <section>` ignores the toggle; an explicit request always returns the detail; the toggle governs only the unattended cron digest; `/expand` code unchanged. → TD-6.

**Micro-question deferred to Writing Plans (does not block the design):** the exact `updated_at_utc` source helper to reuse (whatever `User`/`InboxHintCache` use for UTC `now`), and whether `set_digest_section` should `flush` or `commit` per the repo's existing session conventions — settle by reading the sibling repo modules.
