# Completion Report — Inbox-WIKI Phase-D.b.2b: digest interactive surface

**bd:** `aisw-269` · **date:** 2026-05-12 · **type:** feature
**Spec/design/plan:** `docs/superpowers/specs/20260512-inbox-wiki-digest-interactive-{discovery,design}.md`, `docs/superpowers/plans/20260512-inbox-wiki-digest-interactive-plan.md` · **ADR:** `docs/adr/ADR-025-digest-interactive-surface.md`

## What shipped

The second half of the `aisw-19o` Plan-Sizing split, **re-scoped at the Q&A gate** from the parent's 5 candidate FR down to 3 — actionable cards and per-user section toggles deferred (see below).

1. **`/digest_now` — the bot's first slash command + a `Command`-filter handler** (`tg/handlers.py`). Selects the calling owner's enabled `digest_job` rows (`status=='scheduled'`, `kind=='digest_job'`) via `firing.list_owner_digest_job_ids` and runs the existing `firing.fire_digest_job(job_id)` per row — the whole Phase-D.b.1/2a pipeline reused as-is (per-WIKI `LockAcquirer` inside `run_wiki_session`, `deliver_output(kind='digest', job_id=…)`, `_build_planner_context`, 3-strike). Zero digest jobs → a ru hint to create one. A per-job exception is caught (`tg.command.digest_now.job_failed`) so the rest still run. **No `_run_digest` core refactor, no new lock, no ephemeral path.** New anchors `tg.command.digest_now`(`.empty`/`.job_failed`/`.done`).
2. **`/expand <section>`** (`tg/handlers.py` + `firing.run_section_expand` + `prompts/digest_expand.md`). Keys `today | meds | trackers | wiki` mirror the four D-024 `<b>`-headers. The handler resolves the owner's WIKI set and re-runs Claude scoped to that section via a generalised `DigestRunner(*, …, section: str | None = None)` — `None` ⇒ `prompts/digest.md` (byte-identical to today), a key ⇒ the new `prompts/digest_expand.md` (`semver 0.1.0`, the section name in `user_input`). Reply via `message.answer` (no `run_outputs` row — on-demand command reply, not a cron output). Unknown/missing key → a ru usage line; empty model text → «По этому разделу за период ничего нет.». New anchors `tg.command.expand`(`.bad_section`/`.delivered`/`.failed`). Both command handlers wrap their body in `try/except` so nothing bubbles to the aiogram dispatcher; the text handler already excludes `/`-prefixed messages, so command order is irrelevant.
3. **Named-subset WIKI selection at digest-creation time.** `DigestPayload.wiki_scope` widened `Literal['all']` → `'all' | Annotated[list[str], Field(min_length=1)]` (`VERSION 0.0.4→0.0.5`); **no `jobs.db` Alembic migration** (`'all'` stays valid; `wiki_scope` is not the union discriminator). `create_digest_job` accepts `wiki_scope: str | list[str]` and logs it on `scheduler.digest.scheduled`. The digest fast-path (`tg/pipeline.py` `_handle_digest_intent`) gains an optional `owner_wikis_resolver` dep and `extract_wiki_names(text, owner-stems)` — whole-token, case-insensitive ∩ with the owner's `*-WIKI/` dir-stems → `list[str]`; no name mentioned → `'all'`; a capitalised token after «по …» matching no stem → ru clarification (`tg.pipeline.digest.wiki_unknown`), no confirm draft, no job created. `fire_digest_job`, after resolving the owner WIKI set, intersects it with `payload.wiki_scope` when it's a list (`scheduler.digest.scope_filter` with `requested`/`kept`/`vanished`; empty kept → a ru "all scoped WIKIs vanished" notice, `scheduler.digest.delivered` `empty='scope_vanished'`, **no strike**, status stays `'scheduled'`). The recap/ack name the WIKIs when scoped. `_handle_digest_confirm` passes the list shape through (no more `str()` coercion). `build_digest_recap` gained a `wiki_scope` arg.
4. **GRACE:** new `prompts/digest_expand.md` node; updated `M-SCHEDULER-FIRING` (`VERSION 0.3.0→0.4.0`: `DigestRunner +section`, `fire_digest_job` scope filter, `create_digest_job` list scope, `list_owner_digest_job_ids` + `run_section_expand`), `M-TG-HANDLERS-WIRING` (`VERSION 0.0.4→0.1.0`: the two slash commands, DEPENDS `+M-SCHEDULER-FIRING`), `M-TG-PIPELINE-CLASSIFIER` (`VERSION 0.8.0→0.9.0`: `extract_wiki_names`, `DIGEST_WIKI_UNKNOWN_RU`, `owner_wikis_resolver`), `M-STORAGE-JOBS` (`DigestPayload.wiki_scope` union), `M-RUNTIME-WIRING` (`v0.5.1→v0.5.2`: `_DigestRunnerAdapter` section mode + `digest_expand` prompt path + `owner_wikis_resolver` into `DefaultPipeline`); `knowledge-graph.xml` + `verification-plan.xml` + `development-plan.xml` (Phase-D.b.2a marked `done`, new Phase-D.b.2b) refreshed; new `CrossLink M-TG-HANDLERS-WIRING → M-SCHEDULER-FIRING`. ADR-025.

## FR coverage (vs discovery `functional_requirements: [FR-1..4]`)

| FR | Status | Evidence |
|----|--------|----------|
| FR-1 — `/digest_now` (+ first slash-command surface) reuses `fire_digest_job` per the owner's digest jobs; 0 → ru hint | ✅ | `tests/unit/tg/test_commands.py` (0/N jobs; one job raises → others run; `DigestNotInitialisedError` → ru notice); `tests/unit/scheduler/test_firing.py::test_list_owner_digest_job_ids` |
| FR-2 — `/expand <section>` (today\|meds\|trackers\|wiki) → scoped Claude re-run via `DigestRunner(section=)` + `prompts/digest_expand.md`; `send_message` reply | ✅ | `test_commands.py` (happy path asserts `run_section_expand` called with the section + reply; bad/missing key → usage; no WIKI → notice; empty text → fallback; runner error → generic reply); `test_firing.py::test_run_section_expand`; `tests/unit/test_main_digest_adapter.py` (`section=None` ⇒ `digest.md`, key ⇒ `digest_expand.md`, `run_id` prefix); router-order regression `test_plain_text_still_reaches_pipeline` |
| FR-3 — `DigestPayload.wiki_scope: 'all'\|list[str]`; heuristic extraction in the digest fast-path; `fire_digest_job` intersect-and-filter; recap names the WIKIs | ✅ | `tests/unit/storage/test_payloads.py::test_digest_wiki_scope_named_subset`; `test_firing.py::{test_create_digest_job_named_subset_scope,test_fire_digest_job_scope_filter_keeps_named_subset,test_fire_digest_job_scope_all_vanished_notice_no_strike}`; `tests/unit/tg/test_pipeline_digest.py::{test_digest_named_subset_scopes_to_matching_wiki,test_digest_unknown_wiki_name_clarifies,test_digest_no_wiki_name_stays_all,test_confirm_creates_scoped_digest_job}` |
| FR-4 — ADR-025 + GRACE refresh + log anchors | ✅ | `docs/adr/ADR-025-digest-interactive-surface.md`; MODULE_CONTRACT headers bumped (firing 0.4.0 / handlers 0.1.0 / pipeline 0.9.0 / payloads 0.0.5 / __main__ 0.5.2); `knowledge-graph.xml`/`verification-plan.xml`/`development-plan.xml` updated; `grace lint --failOn errors` → 0 |
| (deferred) actionable cards · per-user section toggles | → new bds | see "Deferrals" |

## Verification

- `make lint` → `ruff check` ✅, `ruff format --check` ✅ (192 files), `mypy src` ✅ (70 files).
- `grace lint --path . --profile standard --failOn errors` → **0 issues** (0 errors, 0 warnings).
- `uv run pytest tests/unit` → **605 passed**; coverage **92%** (`TOTAL 4666 / 385 miss`) — ≥80% gate met.
- `uv run python -c "import ai_steward_wiki.__main__"` → import ok (wiring smoke).
- Integration (`RUN_INTEGRATION=1`) — not run in this PR's gate (real Claude CLI).

## Deferrals (re-scope decided at the Q&A gate — recorded in ADR-025)

- **Actionable inline cards** (medication-due-now / event-soon / pending_confirmation) — no real data source in the current architecture: `reminder_job` (ADR-006) has no medication/event subtype or done-state; `sessions.PendingConfirm` (D-023) has a 10-min TTL so a cron digest essentially never finds one `pending`. Needs a job-category model or a long-lived "needs your answer" queue first → own future bd.
- **Per-user section toggles** (`tracker on/off`, `wiki-updates on/off`) + `user_digest_prefs` + `alembic/sessions/versions/0002_*` — a toggle table with no flip surface is inert; the flip UX + the first `sessions.db` migration past `0001_baseline` want their own design pass → own future bd.

## Sentrux

Skipped — no `.sentrux/rules.toml` (project not onboarded).

## Commits

- `1153125` docs(aisw-269): discovery + design + plan + ADR-025
- (step 1) `feat(M-STORAGE-JOBS): widen DigestPayload.wiki_scope to 'all'|list[str]`
- (steps 2-7) `feat(M-SCHEDULER-FIRING): DigestRunner +section; wiki_scope intersect-filter; create_digest_job list scope; /digest_now+/expand accessors; _DigestRunnerAdapter expand mode`
- (step 8) `feat(M-TG-HANDLERS-WIRING): /digest_now + /expand slash commands`
- (step 9) `feat(M-TG-PIPELINE-CLASSIFIER): named-subset WIKI in the digest fast-path`
- (step 10) `chore(knowledge-graph): refresh for aisw-269` + (this report + ADR-025 already in `1153125`) — meta commit
