---
feature: inbox-wiki-digest-interactive
bd_id: aisw-269
phase: Inbox-WIKI Phase-D.b.2b
status: discovery
date: 2026-05-12
spec_refs:
  - docs/Spec-WIKI/decisions/D-024-digest-format.md
  - docs/adr/ADR-024-digest-presentation.md
  - docs/adr/ADR-007-inbox-wiki-digest-job.md
  - docs/superpowers/specs/20260512-inbox-wiki-digest-presentation-discovery.md   # parent — its FR-5..9 are split here, then re-scoped
depends_on:
  - aisw-w3k   # Phase-D.b.2a presentation core (closed)
functional_requirements:
  - id: FR-1
    text: "/digest_now command — ad-hoc digest trigger for the calling owner; introduces the bot's first aiogram Command-filter handler + a slash-command router that sits BEFORE the NL fall-through (commands matched first, everything else → pipeline as today). It selects the owner's enabled digest_job rows (status=='scheduled', kind=='digest_job') and runs the existing fire_digest_job(job_id) for each — reusing set_digest_context, run_wiki_session's per-WIKI LockAcquirer (held inside the runner), deliver_output(kind='digest', job_id=...), the planner-window query, and the 3-strike auto-disable; concurrent cron digests just queue behind the per-WIKI flock. Zero digest jobs → ru line «У вас нет настроенной сводки. Создайте, например: «делай сводку каждый день в 9».»"
  - id: FR-2
    text: "/expand <section> command — on-demand detail for one digest section. Allowed section keys mirror the four D-024 <b>-headers: today (📅 Сегодня) | meds (💊 Лекарства) | trackers (📈 Трекеры) | wiki (📝 Обновления WIKI). The handler resolves the owner's WIKI set (resolve_owner_wikis — Inbox excluded) and re-runs Claude scoped to that section via a generalised DigestRunner(*, ..., section: str | None = None) — None ⇒ the full D-024 digest (prompts/digest.md, today's behaviour), a key ⇒ section detail (new prompts/digest_expand.md, section name substituted). Output via sender.send_message (short on-demand reply — no run_outputs audit row; that is for cron outputs). Unknown/missing key → ru usage line listing the four keys. «Section was empty over the period» is a valid answer, not an error."
  - id: FR-3
    text: "Named-subset WIKI selection at digest-creation time — DigestPayload.wiki_scope widens from Literal['all'] to 'all' | list[str] (non-empty list of WIKI dir-stems). The digest fast-path in tg/pipeline.py (_handle_digest_intent) extracts the name list heuristically: tokens of the NL turn ∩ the owner's *-WIKI/ dir names (case-insensitive); resolved ⇒ list[str], none mentioned ⇒ 'all', a name-shaped token that does not resolve ⇒ ru clarification and NO job created. create_digest_job persists the new shape (its «'all' if … else …» branch is already there; the Literal is the only blocker). fire_digest_job honours the list — intersect with resolve_owner_wikis(owner); vanished names skip-and-log; empty intersection ⇒ log empty='scope_vanished' + a ru line. The digest recap/ack names the chosen WIKIs. No jobs.db migration ('all' stays valid; existing rows keep validating)."
  - id: FR-4
    text: "ADR-025 written (digest interactive surface — slash-command router, /expand scoped re-run, /digest_now reuse-not-fork, wiki_scope widening, AND the explicit deferrals of cards + per-user toggles with rationale). GRACE refreshed: knowledge-graph (new prompts/digest_expand.md; updated MODULE_CONTRACTs M-SCHEDULER-FIRING / M-TG-PIPELINE / M-STORAGE-JOBS / M-RUNTIME-WIRING); verification-plan log anchors for every new branch (tg.command.digest_now, tg.command.expand, scheduler.digest.scope_filter, …)."
non_functional_requirements:
  - id: NFR-1
    text: "No new third-party dependency. No new SQLite table / Alembic migration in this phase (per-user toggles — the only thing that needed one — is deferred)."
  - id: NFR-2
    text: "All datetime in DB UTC; user-TZ only at input/output (reuse pipeline._resolve_user_tz / firing's tz handling). No new datetime surface here beyond what fire_digest_job already does."
  - id: NFR-3
    text: "Ru-only user-facing strings (D-032), no i18n catalog."
  - id: NFR-4
    text: "Type hints mandatory (mypy --strict for src/); Pydantic on all boundaries (the DigestPayload.wiki_scope union is the schema change — Annotated/Field discriminator unaffected); structlog with ts/event/correlation_id/user_id/wiki_id/job_id."
  - id: NFR-5
    text: "fire_digest_job keeps catching every exception (scheduler/loop must survive); /digest_now and /expand handlers catch-and-reply, never bubble to the aiogram dispatcher; a per-job failure inside /digest_now (one of N digest jobs strikes) must not abort the others."
  - id: NFR-6
    text: "TDD: RED → GREEN → REFACTOR; ≥80% core coverage; unit tests offline — the DigestRunner Protocol (now with section=) is the seam, no real Claude CLI in unit tests."
  - id: NFR-7
    text: "Plan-Sizing: one context window. Estimate after the cards+toggles deferral: ~35-45% of an Opus-4.7 window (touch set = tg/pipeline.py digest-section + a new command-router block, scheduler/firing.py, storage/jobs/payloads.py, two prompts, __main__ wiring, tests). Monolith confirmed at the Discovery gate."
risks:
  - id: R-1
    text: "First slash-command surface in the bot — handlers.py has zero Command-filter handlers today. /digest_now and /expand introduce aiogram Command routing; it must be registered so commands are matched first and ordinary text still falls through to the existing pipeline unchanged. Risk: wrong router/handler order swallows normal messages or double-handles. Mitigation: a dedicated commands router registered before the catch-all message handler; explicit Command('digest_now')/Command('expand') filters; a test asserting a non-command message still reaches the pipeline path."
  - id: R-2
    text: "/digest_now is a 2nd entry into fire_digest_job — reuse it as-is (call fire_digest_job(job_id) per row), do NOT refactor a parallel _run_digest core and do NOT add a new lock; the per-WIKI flock inside the runner already serialises, a concurrent cron digest just queues behind it (accept). Risk: tempting over-refactor of fire_digest_job into a reusable core — out of scope, would broaden the diff and the test surface."
  - id: R-3
    text: "/expand re-runs Claude — cost + latency on a command. Mitigation: small section-scoped prompt; reuse the DigestRunner seam (lock held inside); treat «nothing for this section over the period» as a normal reply. Risk: generalising the DigestRunner Protocol (adding section=) touches the __main__ closure that builds the runner — must keep section=None behaviour byte-identical to today's digest call."
  - id: R-4
    text: "DigestPayload widening — frozen, extra='forbid' Pydantic v2 model inside a Field(discriminator='kind') union. Widening wiki_scope: Literal['all'] → 'all' | list[str] is schema-compatible for existing 'all' rows (no jobs.db migration), but: create_digest_job's signature/recap, the fast-path extractor, and fire_digest_job's resolver branch all change shape; a list with a name that no longer maps to a *-WIKI/ dir is an expected runtime case, not a validation error."
  - id: R-5
    text: "The digest fast-path heuristic for FR-3 must not over-match — generic words («сводка», «здоровье» as a topic vs a «Health» WIKI dir) could spuriously resolve. Mitigation: match only against the owner's actual *-WIKI/ dir-stems (case-insensitive, whole-token), and on ANY name-shaped-but-unresolved token ask for clarification rather than silently widening to 'all'."
scope_in:
  - "/digest_now command + the bot's first slash-command router (reuses fire_digest_job per the owner's digest jobs; 0 jobs → ru hint)."
  - "/expand <section> command (today|meds|trackers|wiki) → section-scoped Claude re-run via generalised DigestRunner(section=) + new prompts/digest_expand.md; reply via send_message."
  - "DigestPayload.wiki_scope widened to 'all' | list[str]; heuristic name extraction in the digest fast-path; fire_digest_job intersect-and-filter; recap/ack names the WIKIs."
  - "ADR-025 (incl. the cards + toggles deferral rationale) + GRACE refresh (knowledge-graph, development-plan, verification-plan log anchors)."
scope_out:
  - "Actionable inline cards (medication-due-now / event-soon / pending_confirmation) — DEFERRED to a future bd: reminder_job (ADR-006) has no medication/event subtype or done-state; pending_confirm has a 10-min TTL so a cron digest essentially never finds one live. Cards need either a job-category model or a long-lived «needs your answer» queue first."
  - "Per-user section toggles (tracker on/off, wiki-updates on/off) + the user_digest_prefs table + alembic/sessions/versions/0002 — DEFERRED to a future bd: a toggle table with no flip surface is inert, and the flip UX + the first sessions.db migration past baseline want their own design pass."
  - "Refactoring fire_digest_job into a reusable _run_digest core; an ephemeral all-WIKIs /digest_now that works without a configured digest job."
  - "Haiku-assisted WIKI-name extraction for FR-3 (heuristic over the owner's own *-WIKI/ dir-stems is enough for MVP)."
  - "run_outputs audit row for /expand output (on-demand command reply, not a cron output)."
  - "Digest management UX: /jobs_list, cancel/snooze/edit of an existing digest job; the asyncio.PriorityQueue worker-loop consumer; monthly/interval/raw-cron recurrence; recurrence-parser wiring; jobs.jobs ↔ APScheduler reconciler; cross-WIKI run_outputs search; data/runs/ retention; i18n."
open_questions: []
preflight:
  pre_commit: "OK — core.hooksPath=.beads/hooks (bootstrap), .pre-commit-config.yaml present (pre-commit framework wired)."
  lint_baseline: "clean — make lint ✅: ruff check ✅, ruff format --check ✅ (190 files), mypy src ✅ (70 files)."
  sentrux: "skipped — no .sentrux/rules.toml (project not onboarded)."
covers_fr_from_parent: ["FR-7 (/digest_now ← parent FR-7)", "FR-6 (/expand ← parent FR-6)", "FR-9 (named-subset WIKI ← parent FR-9)", "FR-10 delta-half (ADR + GRACE)"]
deferred_from_parent: ["FR-5 (actionable cards)", "FR-8 (per-user section toggles)"]
---

# Discovery — Inbox-WIKI Phase-D.b.2b: digest interactive surface (`aisw-269`)

## Context

`aisw-w3k` (Phase-D.b.2a, closed — ADR-024) shipped the digest **presentation core**: `fire_digest_job` → `tg.output.deliver_output(kind="digest")` (D-025 size hybrid, `data/runs/` persist, `audit.run_outputs` row), the `<b>`-section chain-split with `(n/m)` footers, `prompts/digest.md` rewritten to the D-024 contract, `_build_planner_context` (real `jobs.db` planner-window query), `set_digest_context` widened to a 6-tuple with `audit_session_maker`.

This issue is the **interactive half** of Phase-D.b.2b — and, after the Q&A gate (2026-05-12), a **re-scoped** one: the original five FR (cards, `/expand`, `/digest_now`, per-user toggles, named-subset WIKI) trimmed to **three** — the two highest-value, lowest-risk controls (`/digest_now`, `/expand`) plus the contained `wiki_scope` widening. **Cards and per-user toggles are deferred** to their own future bds (see `scope_out` for why).

## Real intent

Give the owner two small handles on the digest without leaving the chat: trigger it off-cycle (`/digest_now`), and pull more detail on one section on demand (`/expand <section>`). Plus let them scope a recurring digest to a named subset of WIKIs at creation time. Everything else stays the read-only D-024 summary that Phase-D.b.2a already ships.

## Q&A decisions (2026-05-12 — full log in the design doc)

1. **Cards → deferred entirely.** No real data source: `reminder_job` has no medication/event subtype or done-state; `pending_confirm` has a 10-min TTL so a cron digest essentially never finds one `pending`. Revisit when there is a job-category model and/or a long-lived "needs your answer" queue.
2. **Monolith** (one `feature-workflow` pass) — after the trims the touch set is small and cohesive (~35-45% of an Opus window).
3. **`/digest_now` = run all the owner's enabled `digest_job` rows now**, via the existing `fire_digest_job(job_id)` per row (reuses lock / audit / `job_id` / `wiki_scope` / `window` / 3-strike). 0 jobs → ru hint to create one. No `_run_digest` refactor, no ephemeral-digest path.
4. **`/expand` = scoped Claude re-run** via a generalised `DigestRunner(*, ..., section: str | None = None)` (`None` ⇒ today's full digest, key ⇒ `prompts/digest_expand.md`); reply via `send_message`; section keys mirror the four D-024 `<b>`-headers.
5. **Per-user toggles → deferred entirely** (a toggle table with no flip surface is inert; the flip UX + first `sessions.db` migration past baseline want their own pass). No `sessions.db` migration in this phase.
6. **Named-subset WIKI → kept.** `DigestPayload.wiki_scope: "all" | list[str]`; heuristic extraction in the digest fast-path (tokens ∩ owner's `*-WIKI/` dir-stems); unresolved name-token ⇒ clarification; `fire_digest_job` intersects with the live WIKI set.
7. **ADR → new ADR-025** ("digest interactive surface" — distinct cluster from ADR-024's "presentation format"; records the cards/toggles deferrals too).

## Blind spots surfaced (and how this phase handles them)

1. **First slash commands in the bot.** `handlers.py` has zero `Command`-filter handlers — everything is NL → `pipeline`. → R-1: a dedicated commands router registered *before* the catch-all, plus a test that a non-command message still reaches the pipeline path.
2. **`/digest_now` is a 2nd door into `fire_digest_job`.** → R-2: reuse it as-is per `job_id`; the per-WIKI flock already serialises; no new lock, no refactor.
3. **Generalising `DigestRunner` touches the `__main__` runner closure.** → R-3: `section=None` must stay byte-identical to today's digest call.
4. **`DigestPayload` is a frozen, `extra="forbid"` model in a discriminated union.** → R-4: widening `wiki_scope` is schema-compatible for `'all'` rows (no `jobs.db` migration); the consumers (`create_digest_job` recap, fast-path extractor, `fire_digest_job` resolver) branch on the new shape.
5. **The FR-3 heuristic can over-match generic words.** → R-5: match only against the owner's actual `*-WIKI/` dir-stems, whole-token, case-insensitive; any name-shaped-but-unresolved token ⇒ clarify, don't silently widen to `'all'`.

## Preflight results

- **pre-commit:** alive (`core.hooksPath=.beads/hooks` bootstrap + `.pre-commit-config.yaml`, pre-commit framework wired).
- **lint baseline:** clean — `make lint` ✅ (`ruff check` ✅, `ruff format --check` ✅ 190 files, `mypy src` ✅ 70 files). Any drift during the feature is fixed in the same PR.
- **sentrux:** no `.sentrux/rules.toml` → skipped.

## Beads

- `aisw-269` (`OPEN`) — depends on `aisw-w3k` (closed), blocks `aisw-19o`. Title re-scoped to the 3-FR set. Two new bds to be filed for the deferrals (actionable cards; per-user section toggles), each depending on `aisw-269`. Claim + `in_progress` at execution start.
