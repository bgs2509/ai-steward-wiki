---
feature: inbox-wiki-digest-section-toggles
bd_id: aisw-pv8
phase: Inbox-WIKI Phase-D.b.2c
follows: aisw-269
date: 2026-05-13
type: feature
status: complete
adr: docs/adr/ADR-026-digest-section-toggles.md
discovery: docs/superpowers/specs/20260512-inbox-wiki-digest-section-toggles-discovery.md
design: docs/superpowers/specs/20260512-inbox-wiki-digest-section-toggles-design.md
plan: docs/superpowers/plans/20260512-inbox-wiki-digest-section-toggles-plan.md
commits:
  - 1396d3a docs — discovery + design
  - ecb5ac2 docs — plan + design TD-2 refinement
  - f0037d8 feat(M-STORAGE-SESSIONS) — user_digest_prefs model + digest_prefs repo
  - bc34fec feat(M-STORAGE-SESSIONS) — alembic/sessions/0002 migration
  - 0b474ad feat(M-SCHEDULER-FIRING) — sessions sessionmaker in digest context + accessors
  - f4e068b feat(M-SCHEDULER-FIRING) — fire_digest_job honours user_digest_prefs
  - 2b218a2 feat(M-TG-HANDLERS-WIRING) — /digest_sections command + digestsec: callback
  - 9c1cb31 feat(M-RUNTIME-WIRING) — pass sessions sessionmaker to digest context
  - 4b94bd6 feat(prompts) — digest.md 0.1.1 directive
  - (this commit) docs(adr) — ADR-026 + GRACE refresh + report
---

# Completion report — Inbox-WIKI Phase-D.b.2c: per-user digest section toggles

## What shipped

The feature deferred out of `aisw-269` at the Q&A gate (ADR-025 §8): a digest-job owner can now permanently silence the `📈 Трекеры` and/or `📝 Обновления WIKI` sections of their recurring digest, persisted per `telegram_id`, honoured at digest fire time — without recreating the digest job.

1. **`user_digest_prefs` table** (`sessions.db`) — one row per owner: `user_id` (PK, FK `users.user_id ON DELETE CASCADE`), `trackers_enabled` / `wiki_enabled` (`BOOLEAN NOT NULL DEFAULT 1`), `updated_at_utc`. Absent row ⇒ both on (opt-out feature — zero behaviour change for anyone who never touches it). New repo `storage/sessions/digest_prefs.py`: `DigestPrefs` frozen dataclass (+`disabled_keys`), `TOGGLEABLE_DIGEST_SECTIONS = ("trackers", "wiki")` (a subset of `EXPAND_SECTION_KEYS`), `SECTION_DISPLAY_NAME`, `get_digest_prefs`, `set_digest_section` (upsert; no-op + defaults if the `telegram_id` has no `User` row; `ValueError` on an unknown section).
2. **`alembic/sessions/versions/0002_user_digest_prefs.py`** — the first incremental `sessions.db` migration past `0001_sessions_baseline`. `upgrade()` = idempotent `Base.metadata.create_all(bind=op.get_bind())` (the baseline's `create_all` of live metadata already builds the table on a fresh DB, so `0002` only does work on an already-baselined existing DB), `downgrade()` = `op.drop_table("user_digest_prefs")`. The incremental-migration convention is recorded in ADR-026 §2.
3. **`/digest_sections` command** + **`digestsec:` callback** (`tg/handlers.py`) — the bot's third slash command. Replies with a ru header and one inline-keyboard button per toggleable section (`«📈 Трекеры: вкл ✅»` / `«… выкл ⬜»`, `callback_data` `digestsec:<section>:<target>`); tapping flips the row and edits the message in place. `parse_digestsec_callback` is independent of the `confirm:` parser; the handlers reach the prefs through `firing.get_owner_digest_prefs` / `firing.set_owner_digest_section` accessors. Registered in the same commands group as `/digest_now`/`/expand` (router-order regression still holds). Both handlers wrapped — nothing bubbles to the dispatcher.
4. **`fire_digest_job`** (`scheduler/firing.py`) — `set_digest_context` gained a sessions sessionmaker (digest-context tuple 6 → 7); `fire_digest_job` reads the owner's `user_digest_prefs` (degrade-to-all-on on any error → `scheduler.digest.prefs_read_failed`) and, when a section is off, appends `«Не включай разделы: 📈 Трекеры.»` to the `planner_context` string + logs `scheduler.digest.sections_filtered` — **byte-identical to today when nothing is off**. `runner(..., section=None)`, the `DigestRunner` Protocol, `_DigestRunnerAdapter` and `run_section_expand` (the `/expand` path) are untouched — an explicit `/expand <section>` overrides the toggle.
5. **`prompts/digest.md`** `semver 0.1.0 → 0.1.1` — one sentence describing the `«Не включай разделы: …»` directive.
6. **`__main__.py`** — passes the sessions sessionmaker into `firing.set_digest_context(...)`.
7. **ADR-026** + GRACE refresh (`knowledge-graph.xml`, `verification-plan.xml`, `development-plan.xml` — new `Phase-D.b.2c` entry, `Phase-D.b.2b` → `done`).

**Not touched:** `DigestPayload` / `storage/jobs/*` (no `jobs.db` migration); `tg/pipeline.py`; `prompts/digest_expand.md`. **No new third-party dependency. Exactly one new SQLite table + one new Alembic migration, `sessions.db` only.**

## Verification

| Check | Result |
|-------|--------|
| `uv run pytest tests/unit` | **exit 0** — full suite green (≈660+ tests; 25 new: 7 in `test_digest_prefs.py`, 18 in `test_digest_sections.py`, plus the `firing` directive-injection cases in `test_firing.py` and the stepwise-migration test in `test_baselines.py`) |
| `uv run pytest --cov=ai_steward_wiki.storage.sessions.digest_prefs` | **100%** (45/45 lines) on the new repo module |
| `uv run mypy src` | Success — no issues (73 source files) |
| `uv run ruff check src tests` / `ruff format --check` | All checks passed / all files formatted |
| `grace lint --profile standard --failOn errors` | **exit 0** — 0 issues |
| Pre-commit hooks | ran on every commit, no bypass |

Tests added/extended cover: `get_digest_prefs` defaults (no row / no user), `set_digest_section` round-trip + unknown-section `ValueError` + unknown-user no-op, `users` CASCADE removes the prefs row, `TOGGLEABLE_DIGEST_SECTIONS ⊆ EXPAND_SECTION_KEYS`; `alembic upgrade head` from empty AND stepwise (`0001`→`0002`) both produce `user_digest_prefs`; `parse_digestsec_callback` ok + reject variants; `/digest_sections` shows the stored state as a keyboard, the `digestsec:` callback persists + edits in place + answers, bad callback data → `bad_callback` + no write, idempotent re-tap; `fire_digest_job` no-prefs path calls the runner with the byte-identical `planner_context`, `trackers`-off appends the directive + logs `scheduler.digest.sections_filtered`, both-off lists both names, a prefs-read failure degrades to all-on; `get/set_owner_digest_section` accessors raise `DigestNotInitialisedError` when unwired.

## Notes / deviations from the plan

- **`docs/20260408_changelog.md`** referenced by the plan (Task 8 Step 3) does not exist in the repo — no changelog file present; skipped rather than create one just for this feature (the ADR + this report + the bd cover it).
- **Migration style (TD-2 refinement, committed in `ecb5ac2`):** the plan's original Task-2 snippet had `0002.upgrade()` = a bare `op.create_table` — that would raise *"table already exists"* on a fresh DB because the baseline's `Base.metadata.create_all` of live metadata already creates `user_digest_prefs` once the model is in `models.py`. Resolved during Writing Plans to an **idempotent `Base.metadata.create_all` delta** (no-op on fresh DBs, creates the table on already-baselined ones); the design TD-2 and the plan were updated before execution.
- **`__main__.py` one-liner** (the `sessions_session_maker=` kwarg) was committed during Task 3's commit (it was required for the full unit suite to pass — `test_runtime_wiring` exercises that call); the Task-6 header bump (`__main__.py` `VERSION` + `CHANGE_SUMMARY`) was a separate `M-RUNTIME-WIRING` commit (`9c1cb31`).
- **`digestsec:` callback edit** is wrapped in a `try/except` that swallows `TelegramBadRequest "not modified"` on a stale re-tap (`tg.command.digest_sections.edit_skipped`) — the DB write already succeeded; this matches the file's defensive style and wasn't in the plan snippet.
- **Sentrux postflight:** N/A — no `.sentrux/rules.toml` in this repo (project not onboarded).

## Out of scope (→ future bds / carried over)

Actionable inline cards (still deferred per ADR-025 §8 — needs a job-category model or a long-lived "needs your answer" queue first); scheduling-time section selection (`DigestPayload` / `jobs.db` left untouched on purpose); per-WIKI-per-section toggles; toggles for the always-on sections (TL;DR, `today`); a `meds` toggle (auto-omitted when empty); rewriting `0001_baseline` (and the `jobs`/`audit` baselines) to explicit `op.create_table`; digest management UX (`/jobs_list`, cancel/snooze/edit); the `asyncio.PriorityQueue` worker-loop consumer; the `jobs.jobs ↔ APScheduler` reconciler; `data/runs/` retention; i18n.
