---
feature: inbox-wiki-digest-presentation
bd_id: aisw-w3k
phase: Inbox-WIKI Phase-D.b.2
status: discovery
date: 2026-05-12
spec_refs:
  - docs/Spec-WIKI/decisions/D-024-digest-format.md
  - docs/Spec-WIKI/decisions/D-025-output-size.md
  - docs/adr/ADR-007-inbox-wiki-digest-job.md
depends_on:
  - aisw-oqq   # Phase-D.b.1 vertical slice (closed)
functional_requirements:
  - id: FR-1
    text: "fire_digest_job delivers the Stage-1 text via tg.output.deliver_output(kind='digest') instead of the plain truncated send_message — D-025 size hybrid (≤3500 inline / ≤10000 chain-split / >10000 Haiku-summary + send_document), full text persisted to <wiki>/data/runs/<date>/<run_id>.md, audit run_outputs row written."
  - id: FR-2
    text: "Long digest split prefers <b>-header section boundaries (D-024) with (n/m) continuity footer; falls through to D-025 send_document only if a single section still exceeds the chain threshold."
  - id: FR-3
    text: "prompts/digest.md enforces the D-024 output contract: a TL;DR section (3-5 lines) first, then sectioned summary (📅 Сегодня / 💊 Лекарства / 📈 Tracker / 📝 Wiki updates), HTML parse_mode, empty-digest line «🌿 Сегодня дел нет.»."
  - id: FR-4
    text: "Real planner context: fire_digest_job queries jobs.db for the owner's scheduled jobs in the digest window (±today, plus medication/event rows due within ±2h) and passes a structured planner block to the digest prompt instead of the current one-line note."
  - id: FR-5
    text: "Actionable inline cards — separate TG messages with inline keyboards ONLY for items needing action within ±2h: medication-due-now «✅ Принял»/«⏰ +30мин»/«❌ Skip» (jobs.db), event-soon «📍 Я в пути»/«⏰ Опаздываю»/«❌ Отменить» (jobs.db), pending_confirmation «✅ Подтвердить»/«✏️ Изменить»/«❌ Отмена» (sessions.db, reuse D-023 ConfirmationService). Read-only items stay in the summary without buttons."
  - id: FR-6
    text: "/expand <section> command — on-demand detail for a digest section (e.g. /expand tracker → full tracker summary for the period)."
  - id: FR-7
    text: "/digest_now command — ad-hoc digest trigger for the calling owner (runs the same digest pipeline immediately, ignores cron)."
  - id: FR-8
    text: "Per-user section toggles (tracker on/off, wiki-updates on/off) persisted per telegram_id; honoured both in the prompt (sections Claude is asked to fill) and in card emission."
  - id: FR-9
    text: "Named-subset WIKI selection in the digest-creation turn (e.g. «сводка по Health и Money каждый день в 9») — wiki_scope widens from the 'all' sentinel to an explicit name list resolved against the owner's *-WIKI/ dirs."
  - id: FR-10
    text: "ADR-024 written; GRACE artifacts (knowledge-graph, verification-plan, development-plan) refreshed; verification-plan log anchors for the new branches."
non_functional_requirements:
  - id: NFR-1
    text: "No new third-party dependency. New SQLite tables / Alembic migrations only for the per-user toggles (sessions.db)."
  - id: NFR-2
    text: "All datetime in DB UTC; user-TZ applied only at input/output (reuse pipeline._resolve_user_tz / default_user_tz)."
  - id: NFR-3
    text: "Ru-only user-facing strings (D-032), no i18n catalog."
  - id: NFR-4
    text: "Type hints mandatory (mypy --strict for src/); Pydantic on all boundaries; structlog with ts/event/correlation_id/user_id/wiki_id/job_id."
  - id: NFR-5
    text: "fire_digest_job must keep catching every exception (scheduler/event loop must survive); card-send failures must not abort the summary delivery."
  - id: NFR-6
    text: "TDD: RED → GREEN → REFACTOR; ≥80% core coverage; unit tests offline (no real Claude CLI)."
  - id: NFR-7
    text: "Plan-Sizing budget: each implementation phase must fit one context window with its files + contracts + tests + logs."
risks:
  - id: R-1
    text: "Scope is large (10 FR across 5+ modules: scheduler/firing, tg/output, tg/pipeline, tg/confirm, storage/sessions, prompts, a new cards module, a new jobs-query helper, Alembic migration). Likely exceeds one context window → MUST split. Mitigation: split into Phase-D.b.2a (presentation core, FR-1..4,10) and Phase-D.b.2b (interactive + cards, FR-5..9 + its GRACE delta)."
  - id: R-2
    text: "Actionable cards span two bounded contexts (jobs.db medication/event rows + sessions.db pending_confirms) and need callback handlers — couples with D-023 ConfirmationService and with whatever 'medication-due-now' / 'event-soon' job classification exists today (the reminder_job from ADR-006 has no medication/event subtype yet → may need a job-kind/category lookup that doesn't exist)."
  - id: R-3
    text: "/digest_now bypasses cron — must reuse the same lock discipline (run_wiki_session's per-WIKI LockAcquirer) and the same set_digest_context registry, not a parallel path; risk of double-fire if a cron digest is in flight (per-WIKI flock already serialises, accept queue-behind)."
  - id: R-4
    text: "Section-boundary split: the existing ChainSplitter (tg/output.py) splits at <b>/blank-line/sentence boundaries already — need to confirm it treats <b>-headers as the top priority and emits (n/m); may be a no-op or a small tweak rather than new code."
  - id: R-5
    text: "Per-user toggles add a 6th sessions.db table — first schema migration on sessions.db beyond the baseline; must follow the commit-before-add ordering / WAL conventions and ship alembic/sessions/versions/0002_*."
scope_in:
  - "Replace plain send_message in fire_digest_job with deliver_output(kind='digest')."
  - "<b>-header section-boundary split + (n/m) markers + send_document fallthrough."
  - "prompts/digest.md D-024 contract (TL;DR section + sections + empty line)."
  - "Real jobs.db planner-window query feeding the digest prompt."
  - "Actionable inline cards (medication/event/pending_confirm) + their callback handlers."
  - "/expand <section> and /digest_now commands."
  - "Per-user section toggles (tracker / wiki-updates on/off)."
  - "Named-subset WIKI selection at digest-creation time."
  - "ADR-024, GRACE refresh, verification-plan log anchors."
scope_out:
  - "The asyncio.PriorityQueue worker-loop consumer (de-scoped out of aisw-19o entirely — own future bd)."
  - "Monthly / interval / raw-cron recurrence (still → escalate; owned by recurrence parser, not this phase)."
  - "Haiku-backed recurrence parser wiring (prompts/recurrence.md shipped, not wired)."
  - "Digest management UX: /jobs_list, cancel/snooze/edit of an existing digest job."
  - "jobs.jobs ↔ APScheduler reconciler for the millisecond-gap silent-miss (carried over from ADR-006)."
  - "Cross-WIKI search over run_outputs; data/runs/ retention policy."
  - "i18n / non-ru locales."
open_questions:
  - id: OQ-1
    text: "Split or monolith? Recommendation: SPLIT into Phase-D.b.2a (presentation core) + Phase-D.b.2b (interactive + cards). Confirm at the Discovery gate."
  - id: OQ-2
    text: "Where do medication-due-now / event-soon cards get their 'subtype' from? reminder_job (ADR-006) currently has no medication/event category. Option A: derive from the job's free-text title/payload heuristically; Option B: defer cards for medication/event to a later phase and ship only the pending_confirmation card now (it has a real D-023 source). To resolve in Brainstorming."
  - id: OQ-3
    text: "Per-user toggles storage: new sessions.db table `user_digest_prefs(user_id PK FK, tracker_enabled bool, wiki_updates_enabled bool, updated_at_utc)` vs a JSON column on `users`. Recommendation: dedicated table (SSoT, no JSON-blob drift). To resolve in GRACE Plan."
  - id: OQ-4
    text: "/expand <section> — does it re-run Claude with a section-scoped prompt, or replay/expand from the persisted run_outputs file? Recommendation: re-run Claude scoped to the section (the run file is the whole digest, not section detail). To resolve in Brainstorming."
preflight:
  pre_commit: "OK — core.hooksPath=.beads/hooks (bootstrap), .pre-commit-config.yaml present, pre-commit framework wired."
  lint_baseline: "clean — ruff check ✅, ruff format ✅ (190 files), mypy ✅ (70 files)."
  sentrux: "skipped — no .sentrux/rules.toml (project not onboarded)."
---

# Discovery — Inbox-WIKI Phase-D.b.2: D-024 digest presentation (`aisw-w3k`)

## Context

`aisw-oqq` (Phase-D.b.1, closed — ADR-007) shipped the runnable digest vertical slice: NL recurrence parsing → `CronTrigger`, `DigestPayload` widening, `create_digest_job`/`fire_digest_job` (direct-fire under `run_wiki_session`'s per-WIKI lock), the pipeline confirm flow, and **plain truncated `send_message` delivery**. ADR-007 explicitly de-scoped the D-024/D-025 presentation contract, the `tg/output.deliver_output` audit row, the real `jobs.db` planner-context query, named-subset WIKI selection, `/expand`, `/digest_now` and per-user toggles into **this** issue.

## Real intent

Make the morning/evening/weekly/`/today`-style digest actually usable on a phone: a scannable TL;DR-first HTML summary, audit-persisted like every other Claude output, with the *few* time-critical items surfaced as tappable cards — and give the owner the small controls (`/expand`, `/digest_now`, section on/off, "only these WIKIs") that turn it from a fixed broadcast into something they steer.

## Blind spots surfaced

1. **Cards need a data source that may not exist.** `reminder_job` (ADR-006) is a single free-text reminder with no `medication`/`event` subtype. The D-024 card table assumes those categories. → OQ-2.
2. **`/digest_now` is a second entry into `fire_digest_job`.** Must reuse `set_digest_context` + the per-WIKI lock, not fork a parallel path. → R-3.
3. **First sessions.db migration beyond baseline.** Per-user toggles add table #6; must follow WAL / commit-before-add conventions. → R-5.
4. **Section split may already be done.** `ChainSplitter` in `tg/output.py` splits at `<b>`/blank-line/sentence boundaries with `(i/M)` footers — FR-2 might be a verification + tiny tweak, not new code. → R-4.
5. **Scope is too big for one window.** 10 FR, 5+ modules, a new module, a migration. → R-1, OQ-1: **recommend split**.

## Recommended decomposition (OQ-1)

- **Phase-D.b.2a — presentation core** (`aisw-w3k` retitled or a child): FR-1 (`deliver_output(kind='digest')` in `fire_digest_job`), FR-2 (`<b>`-section split + `(n/m)` + send_document fallthrough), FR-3 (`prompts/digest.md` D-024 contract), FR-4 (real `jobs.db` planner-window query), FR-10a (ADR-024 + GRACE for this slice). Files: `scheduler/firing.py`, `tg/output.py` (verify/tweak), `prompts/digest.md`, a new `scheduler/digest_context.py`-style jobs-window query helper (or extend `firing.py`), tests, knowledge-graph.
- **Phase-D.b.2b — interactive + cards** (new child bd): FR-5 (actionable cards + callback handlers), FR-6 (`/expand`), FR-7 (`/digest_now`), FR-8 (per-user toggles + sessions.db migration), FR-9 (named-subset WIKI selection), FR-10b (GRACE delta). Files: `tg/pipeline.py`, `tg/confirm.py` (reuse), a new `tg/digest_cards.py`, `storage/sessions/models.py` + `alembic/sessions/versions/0002_*`, `classifier/recurrence.py` (named-subset), `scheduler/firing.py` (`/digest_now` hook), tests.

Each sub-phase is a coherent file set with shared imports and one test-fixture environment, ~40–55% of an Opus-4.7 window — fits the Plan-Sizing budget; the monolith would not.

## Preflight results

- **pre-commit:** alive (`core.hooksPath=.beads/hooks` bootstrap + `.pre-commit-config.yaml`).
- **lint baseline:** clean — `ruff check` ✅, `ruff format --check` ✅ (190 files), `mypy src` ✅ (70 files). Any drift during the feature is fixed in the same PR.
- **sentrux:** no `.sentrux/rules.toml` → skipped.

## Beads

- `aisw-w3k` claimed, `in_progress`.
- If the split is approved: `aisw-w3k` becomes Phase-D.b.2a (presentation core); a new child bd is created for Phase-D.b.2b (interactive + cards), depending on `aisw-w3k`, blocking `aisw-19o`.
