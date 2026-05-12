---
feature: inbox-wiki-digest-section-toggles
bd_id: aisw-pv8
phase: Inbox-WIKI Phase-D.b.2c
status: discovery
date: 2026-05-12
spec_refs:
  - docs/Spec-WIKI/decisions/D-024-digest-format.md
  - docs/adr/ADR-024-digest-presentation.md
  - docs/adr/ADR-025-digest-interactive-surface.md
  - docs/adr/ADR-007-inbox-wiki-digest-job.md
depends_on:
  - aisw-269   # Phase-D.b.2b digest interactive surface (closed) — this was deferred out of it at the Q&A gate
functional_requirements:
  - id: FR-1
    text: "user_digest_prefs table in sessions.db — per-owner on/off state for the two optional digest sections named in ADR-025: `trackers` (📈 Трекеры) and `wiki` (📝 Обновления WIKI). Default for both = ON (no row ⇒ everything shown — today's behaviour). The TL;DR (📌) and `today` (📅 Сегодня) sections stay always-on (TL;DR is the summary itself; `today` is the planner-window core of the digest). FK to users.user_id with ON DELETE CASCADE — mirrors the existing inbox_hint_cache pattern; reuses storage.sessions.users.resolve_user_id(telegram_id → user_id). Storage shape (generic per-section row vs fixed two-boolean columns) is a Brainstorming decision."
  - id: FR-2
    text: "alembic/sessions/versions/0002_*.py — the first incremental sessions.db migration past 0001_baseline. Creates only the user_digest_prefs table (op.create_table under batch mode for SQLite — NOT Base.metadata.create_all, which is baseline-only). down_revision = '0001_sessions_baseline'. tests/unit/storage/test_baselines.py expected-tables set for sessions gains `user_digest_prefs`, plus a test that `alembic upgrade head` applied stepwise (0001 → 0002) yields the table. The create_all/op.create_table convention split is a Brainstorming decision."
  - id: FR-3
    text: "/digest_sections command — the flip surface (without it the table is inert; ADR-025 §8). Shows the caller's current per-section state and lets them flip `trackers` / `wiki` on↔off. UX form (inline-keyboard toggle buttons with a `digestsec:` callback prefix mirroring the existing `confirm:` pattern, vs `/digest_sections <key> <on|off>` text args) is a Brainstorming decision. Registered in the same commands-router group introduced by aisw-269 (matched before the NL fall-through). Ru-only strings. New log anchors `tg.command.digest_sections(.shown|.toggled|.bad_arg)`."
  - id: FR-4
    text: "Honour the toggles in digest assembly — fire_digest_job (or the _DigestRunnerAdapter it calls) reads the owner's user_digest_prefs and, when any optional section is OFF, injects a directive into the digest prompt input naming the disabled section(s) so Claude omits them. When all sections are enabled (the default / no prefs row) the prompt input MUST be byte-identical to today's (zero behaviour change for the common case). prompts/digest.md gains a sentence explaining the «disabled sections» directive. New log anchor `scheduler.digest.sections_filtered` (job_id, disabled). The exact injection mechanism (extra line appended to planner_context / user_input vs a new prompt variable) is a Brainstorming decision."
  - id: FR-5
    text: "/expand <section> behaviour vs the toggle — decide and document: explicit `/expand trackers` overrides a disabled `trackers` toggle (an explicit request wins), so /expand needs no change; OR /expand on a disabled section returns a ru hint «раздел отключён в /digest_sections». Default leaning: explicit-wins, no change to /expand. Brainstorming confirms."
  - id: FR-6
    text: "ADR-026 written (per-user digest section toggles — the user_digest_prefs schema + FK choice, the first incremental sessions.db migration & the op.create_table-vs-create_all convention, the /digest_sections flip-UX choice, the prompt-injection mechanism, the /expand-override decision). GRACE refreshed: knowledge-graph (new M-STORAGE-SESSIONS model + repo fn, updated M-TG-HANDLERS-WIRING / M-SCHEDULER-FIRING / M-RUNTIME-WIRING contracts, prompts/digest.md node bump); verification-plan log anchors for every new branch."
non_functional_requirements:
  - id: NFR-1
    text: "No new third-party dependency. Exactly one new SQLite table (user_digest_prefs) and exactly one new Alembic migration (alembic/sessions/versions/0002_*) — both in sessions.db only; jobs.db and audit.db untouched."
  - id: NFR-2
    text: "WAL + busy_timeout=5000 + foreign_keys=ON apply to the new table via the existing storage.pragmas.apply_sqlite_pragmas connect-listener — no new pragma surface."
  - id: NFR-3
    text: "All datetime in DB UTC (created_at_utc / updated_at_utc on the new row if timestamped); user-TZ only at input/output. Ru-only user-facing strings (D-032), no i18n catalog."
  - id: NFR-4
    text: "Type hints mandatory (mypy --strict for src/); Pydantic on all boundaries where one applies; structlog with ts/event/correlation_id/user_id/job_id (wiki_id where a WIKI is in scope)."
  - id: NFR-5
    text: "fire_digest_job keeps catching every exception (scheduler/loop must survive a prefs-read failure — degrade to «all sections shown»); the /digest_sections handler catches-and-replies, never bubbles to the aiogram dispatcher."
  - id: NFR-6
    text: "TDD: RED → GREEN → REFACTOR; ≥80% core coverage; unit tests offline — the DigestRunner Protocol is the seam (no real Claude CLI in unit tests); the migration test runs alembic against a tmp sqlite file."
  - id: NFR-7
    text: "Plan-Sizing: one context window. Touch set ≈ storage/sessions/models.py + a new repo fn (storage/sessions/digest_prefs.py) + alembic/sessions/versions/0002_* + tg/handlers.py (one new command handler, maybe a callback branch) + scheduler/firing.py (prefs read + directive injection) + __main__ wiring (session_maker passed to the command/firing context) + prompts/digest.md + tests. Estimated ~35-45% of an Opus-4.7 window. Monolith — confirm at the Discovery gate."
risks:
  - id: R-1
    text: "First incremental sessions.db migration past baseline. 0001_baseline does Base.metadata.create_all of the whole schema; 0002 must instead op.create_table the single new table under render_as_batch=True (env.py already sets it). Risk: a fresh DB created by `alembic upgrade head` (baseline create_all already includes every current model) vs an existing DB upgraded 0001→0002 must converge to the same schema — the new model is added to models.py so baseline's create_all picks it up too, and 0002 is the delta for already-migrated DBs. Mitigation: test BOTH paths (stamp 0001 then upgrade; and upgrade head from empty) and assert the table set matches; add `user_digest_prefs` to test_baselines.py expected sets."
  - id: R-2
    text: "FK target choice — users.user_id (surrogate, like inbox_hint_cache; needs an onboarded User row + resolve_user_id) vs telegram_id (canonical, no join, but no referential integrity to users). A digest job's owner is by construction an onboarded user, so user_id+CASCADE is consistent with the existing pattern; chosen unless Brainstorming surfaces a reason otherwise. Risk: a digest fires for an owner whose User row was deleted — CASCADE already removed the prefs, so the read returns «no prefs ⇒ all on», which is the safe default."
  - id: R-3
    text: "Byte-identical default. FR-4's directive injection must produce exactly today's digest prompt input when no section is disabled (the overwhelming common case). Risk: an always-appended «отключённые разделы: (нет)» line changes the input and could perturb Claude's output / break the digest_e2e golden assertions. Mitigation: inject the directive ONLY when the disabled set is non-empty; a test asserting the no-prefs path calls the runner with the same user_input as before."
  - id: R-4
    text: "Section-key vocabulary drift. The keys `today|meds|trackers|wiki` already exist in three places (prompts/digest_expand.md, tg/handlers.EXPAND_SECTION_KEYS, ADR-025). user_digest_prefs must use the SAME keys for the two it covers (`trackers`, `wiki`) — and the /digest_sections command, the prompt directive, and the table CHECK/enum (if any) must all reference one shared constant, not re-spell them. Mitigation: a single module-level tuple/Literal reused everywhere; a test that the prefs keys ⊆ EXPAND_SECTION_KEYS."
  - id: R-5
    text: "/digest_sections is the bot's 3rd slash command — same router-order concern as aisw-269's R-1, already mitigated by the dedicated commands-router-before-catch-all pattern; this just adds one handler to that group. Low risk, but the router-order regression test should cover a non-command message still reaching the pipeline with the new handler registered."
  - id: R-6
    text: "Scope creep toward scheduling-time section selection (choose sections when creating the digest job, like wiki_scope in aisw-269) or per-WIKI-per-section toggles. Out of scope — this phase is one global-per-owner on/off pair, flipped by a command, read at fire time. The DigestPayload is NOT touched (no jobs.db migration)."
scope_in:
  - "user_digest_prefs table in sessions.db (FK users.user_id CASCADE) — on/off for `trackers` and `wiki` sections, default ON; a small repo module (get/set the owner's prefs)."
  - "alembic/sessions/versions/0002_* — first incremental sessions.db migration; op.create_table for the new table; down_revision = 0001_sessions_baseline; test_baselines.py expected-tables updated; stepwise-upgrade test."
  - "/digest_sections command (the flip surface) — shows current state, flips trackers/wiki; registered in the aisw-269 commands router; ru-only; new log anchors."
  - "Honour the toggles in fire_digest_job / the digest runner — inject a «disabled sections» directive into the digest prompt ONLY when something is off; byte-identical default; prompts/digest.md updated; new log anchor."
  - "Decide & document /expand-vs-toggle interaction (default: explicit /expand overrides the toggle, no change to /expand)."
  - "ADR-026 + GRACE refresh (knowledge-graph, development-plan, verification-plan log anchors)."
scope_out:
  - "Scheduling-time section selection (picking sections when creating the digest job, the way wiki_scope is picked) — DigestPayload / jobs.db untouched."
  - "Per-WIKI-per-section toggles; toggles for the always-on sections (TL;DR, today); a `meds` toggle (the medication section is auto-omitted when empty, so a toggle adds little — revisit if asked)."
  - "Actionable inline cards (medication-due-now / event-soon / pending_confirmation) — still deferred per ADR-025 §8 (needs a job-category model or a long-lived «needs your answer» queue first)."
  - "Digest management UX: /jobs_list, cancel/snooze/edit of an existing digest job; the asyncio.PriorityQueue worker-loop consumer; the jobs.jobs ↔ APScheduler reconciler; data/runs/ retention; i18n."
  - "Refactoring fire_digest_job into a reusable _run_digest core; an ephemeral /digest_now without a configured job; Haiku-assisted parsing anywhere on this path."
open_questions:
  - "FR-1 storage shape: generic per-section rows ((user_id, section_key, enabled), UNIQUE(user_id, section_key)) vs a single row per user with `trackers_enabled BOOLEAN, wiki_enabled BOOLEAN`. Generic = extensible (add `meds` later with no migration); fixed = simpler, matches «exactly two». → Brainstorming."
  - "FR-3 /digest_sections UX: inline-keyboard toggles (callback `digestsec:<key>:<on|off>`, nicer mobile UX, reuses the callback_query machinery) vs `/digest_sections <key> <on|off>` text args (KISS, no new callback prefix). → Brainstorming."
  - "FR-2 migration convention: keep adding new models to models.py so baseline's create_all stays the whole schema and each NNNN_* is the delta (current implicit pattern) — confirm this is the intended convention, or switch baseline to explicit op.create_table per table. → Brainstorming / possibly ADR."
  - "FR-4 injection mechanism: append a line to the existing planner_context/user_input string vs add a distinct prompt template variable. → Brainstorming."
  - "FR-5: does `/expand trackers` work when `trackers` is toggled off? Default leaning: yes (explicit wins). Confirm."
---

# Discovery — Inbox-WIKI Phase-D.b.2c: per-user digest section toggles

## Context

At the `aisw-269` (Phase-D.b.2b) Q&A gate the digest *interactive surface* was re-scoped — three features shipped (`/digest_now`, `/expand <section>`, `DigestPayload.wiki_scope` named-subset) and two were deferred to their own bds with rationale (ADR-025 §8):

1. **Actionable inline cards** — no data source exists yet (`reminder_job` has no medication/event subtype or done-state; `pending_confirm` has a 10-min TTL so a cron digest essentially never finds one live). Still deferred.
2. **Per-user section toggles** (`trackers` on/off, `wiki` on/off) — *"a toggle table with no flip surface is inert, and the flip UX **plus the first `sessions.db` Alembic migration past `0001_baseline`** want their own design pass"*. **This bd (`aisw-pv8`)** is that design pass.

So the work here is a tight, self-contained increment: one new `sessions.db` table, the first incremental `sessions.db` migration, the command that flips it, and the read that honours it in the digest run. The hard parts are *conventions*, not volume — the migration style (`op.create_table` vs the baseline's `create_all`), the FK target, the flip UX, and keeping the no-prefs default byte-identical to today's digest.

## Intent analysis

- **Literal ask:** "run feature-workflow on `aisw-pv8`" → build the deferred per-user digest section toggles.
- **Real goal:** let an owner who finds a recurring section noise (e.g. trackers they don't keep up, or WIKI-change chatter) silence it permanently without recreating the digest job — a low-friction, persistent personalisation knob.
- **Unstated assumptions:** the toggle is global per owner (not per WIKI, not per job); it's flipped interactively (a command), not at job creation; it only covers the two sections ADR-025 named; defaults preserve today's behaviour exactly.
- **What could go wrong:** the first incremental migration on `sessions.db` diverging from how a fresh DB is built; the directive injection perturbing the digest golden tests; section-key vocabulary drift across the four places keys live.
- **Stakeholders:** digest-job owners (the feature); future migrations on `sessions.db` (this sets the precedent for incremental-migration style); the GRACE graph (new model + repo fn + contract bumps).

## Best-practice notes

- **Sane defaults > config:** absence of a prefs row means "everything on" — the feature is opt-out, never opt-in, so a user who never touches `/digest_sections` sees zero change.
- **One vocabulary, one place:** the section keys (`today|meds|trackers|wiki`) already exist as `prompts/digest_expand.md`, `tg/handlers.EXPAND_SECTION_KEYS`, and ADR-025; the new table/command/prompt-directive must reference a single shared constant for the two keys they cover.
- **Incremental migrations are deltas, baseline is the snapshot:** the established Alembic-per-DB pattern keeps the baseline as `create_all` of the current ORM and each `NNNN_*` as the change since — `0002_*` should be `op.create_table` for just `user_digest_prefs`, and the new model is also added to `models.py` so a from-empty `upgrade head` (which runs baseline first) still produces it.
- **Mirror the closest existing pattern:** `inbox_hint_cache` is the precedent for a per-user side table in `sessions.db` — FK `users.user_id` with `ON DELETE CASCADE`, resolved from `telegram_id` via `resolve_user_id`. Reuse it rather than inventing a `telegram_id`-keyed table.

## Verification intent (sketch)

- Migration: `alembic upgrade head` on an empty tmp sqlite → `user_digest_prefs` present; stamp `0001_sessions_baseline` then `upgrade head` → same; `test_baselines.py` expected sets updated.
- Repo fn: get on a fresh user → defaults (both ON); set `trackers=off` → get returns it; deleting the `User` row CASCADEs the prefs away.
- `/digest_sections`: shown state matches stored; flipping persists; bad argument → ru hint, no write; a non-command message still reaches the pipeline (router-order regression).
- Digest assembly: no prefs row → runner called with byte-identical `user_input`; `trackers` off → directive line present naming `trackers`, `scheduler.digest.sections_filtered` logged with `disabled=['trackers']`; `fire_digest_job` survives a prefs-read exception (degrades to all-on).
- `/expand trackers` with `trackers` off → behaves per the FR-5 decision.

## Open questions

See `open_questions` in the frontmatter — five items, all design-shape choices for the Brainstorming step (storage shape, `/digest_sections` UX, migration-convention confirmation, injection mechanism, `/expand`-override). None block approving FR/NFR/scope.
