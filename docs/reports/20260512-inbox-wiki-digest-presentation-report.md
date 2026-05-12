# Completion Report — Inbox-WIKI Phase-D.b.2a: digest presentation core

**bd:** `aisw-w3k` · **date:** 2026-05-12 · **type:** feature
**Spec/design/plan:** `docs/superpowers/specs/20260512-inbox-wiki-digest-presentation-{discovery,design}.md`, `docs/superpowers/plans/20260512-inbox-wiki-digest-presentation.md` · **ADR:** `docs/adr/ADR-024-digest-presentation.md`

## What shipped

`aisw-oqq` (Phase-D.b.1) delivered the digest via a plain truncated `sender.send_message` with a one-line `planner_context` stub. Phase-D.b.2a wires it into the real output pipeline:

1. **`fire_digest_job` → `tg.output.deliver_output(kind="digest")`** — D-025 size hybrid (≤3500 inline / ≤10000 `<b>`-header chain-split with `(i/M)` footers / >10000 `LengthCapSummarizer` summary + `send_document`), full text persisted to `<primary-wiki>/data/runs/<date>/<run_id>.md`, `audit.run_outputs` row written. `run_id = f"digest-{uuid4().hex[:12]}"`. The empty-WIKI line (`_DIGEST_NO_WIKI_RU`) stays a plain `send_message` (control message, not a Claude output). `_DIGEST_TG_LIMIT` truncation removed.
2. **`set_digest_context` +`audit_session_maker`** — `_digest_ctx` is now a 6-tuple `(scheduler, runner, resolve_owner_wikis, jobs_session_maker, audit_session_maker, sender)`. `__main__` passes `audit_session_maker=audit_maker`.
3. **`_build_planner_context`** (module-private in `scheduler/firing.py`) — selects `jobs.Job` rows with `status=='scheduled'` AND `scheduled_at_utc IS NOT NULL` AND `scheduled_at_utc <= now + window_hours`, renders «- HH:MM — <title>» in the owner's tz (`title` = payload `message`/`prompt_hint` → `job.kind`); empty → «На ближайшие N ч ничего не запланировано.». Recurring (cron-driven, `scheduled_at_utc IS NULL`) rows excluded.
4. **`prompts/digest.md`** rewritten to the D-024 contract (`semver 0.1.0`): `<b>📌 TL;DR</b>` first (3–5 lines), then only-if-content `<b>📅 Сегодня</b>` / `<b>💊 Лекарства</b>` / `<b>📈 Трекеры</b>` / `<b>📝 Обновления WIKI</b>`; HTML whitelist, escape `< > &`, no MarkdownV2; empty ⇒ exactly «🌿 Сегодня дел нет.»; consume the «Запланировано…» block.
5. **GRACE:** `M-SCHEDULER-FIRING` header `VERSION 0.2.0 → 0.3.0` (DEPENDS `+tg.output.deliver_output`, MAP/CHANGE_SUMMARY updated); `M-RUNTIME-WIRING` change-summary `v0.5.0 → v0.5.1`. New log anchor `scheduler.digest.planner_context`; `scheduler.digest.delivered` now carries `run_id`/`n_messages`/`document_sent`.

**FR-2 (section split + `(n/m)` + `send_document` fallthrough) needed no production code** — `ChainSplitter`/`deliver_output` already implement it (`<b>` is its top boundary priority); covered by two new digest-flavoured `tests/unit/tg/test_output.py` cases.

## FR coverage (vs discovery `covers_fr: [FR-1, FR-2, FR-3, FR-4, FR-10]`)

| FR | Status | Evidence |
|----|--------|----------|
| FR-1 — `deliver_output(kind=digest)`, D-025 hybrid, `data/runs/` persist, `run_outputs` row | ✅ | `test_fire_digest_job_delivers_via_deliver_output` (asserts `.md` file + audit row `kind=='digest'`, `job_id`, `owner_telegram_id`) |
| FR-2 — `<b>`-section split + `(n/m)` + `send_document` fallthrough | ✅ | `test_deliver_digest_splits_at_b_headers`, `test_deliver_digest_large_to_document` |
| FR-3 — `prompts/digest.md` TL;DR-section + sections + empty line, HTML | ✅ | file rewritten, `semver 0.1.0` |
| FR-4 — real `jobs.db` planner-window query | ✅ | `test_build_planner_context_lists_in_window_jobs` (in-window in, out-of-window out, other-owner out), `test_build_planner_context_empty` |
| FR-10a — ADR-024 + GRACE header refresh + log anchors | ✅ | `docs/adr/ADR-024-digest-presentation.md`; firing.py/`__main__.py` headers; D-024 «перенос в ADR» ticked + Spec-WIKI `log.md` entry |
| FR-5..9 — cards / `/expand` / `/digest_now` / per-user toggles / named-subset WIKI | → `aisw-269` | out of scope per the approved Plan-Sizing split |

## Verification

- `make lint` → `ruff check` ✅, `ruff format --check` ✅ (190 files), `mypy src` ✅ (70 files).
- `grace lint --failOn errors` → **0 issues** (0 errors, 0 warnings). `grace status` → standard lint clean, no stale verification entries from this change.
- `uv run pytest tests/unit` → **all passed**; coverage **92%** (`TOTAL 4528 / 376 miss`) — ≥80% gate met.
- `python -c "import ai_steward_wiki.__main__"` → import ok (wiring smoke).
- Integration (`RUN_INTEGRATION=1`) — not run in this PR's gate (real Claude CLI).

## Decomposition note

`aisw-w3k` was split at the Discovery gate (10 FR, 5+ modules, a migration → exceeds one context window per the Plan-Sizing budget) into Phase-D.b.2a (this) and **`aisw-269`** Phase-D.b.2b (interactive: actionable ±2h cards + callbacks, `/expand`, `/digest_now`, per-user section toggles + `alembic/sessions/0002_*`, named-subset WIKI selection). `aisw-269` depends on `aisw-w3k`, blocks `aisw-19o`.

## Sentrux

Skipped — no `.sentrux/rules.toml` (project not onboarded).

## Commits

- `761d775` docs(aisw-w3k): discovery + design + plan
- `5c5c63d` feat(M-RUNTIME-WIRING): `set_digest_context` +`audit_session_maker`; `prompts/digest.md` D-024 contract — *(bundled `M-SCHEDULER-FIRING` `deliver_output` routing + `_build_planner_context` + `test_firing.py` + `test_digest_e2e.py` due to a pre-commit stash interaction; intentional, all hooks passed)*
- `a3e1ec7` test(M-TG-TEXT): digest `deliver_output` — `<b>`-header split + `(i/M)` + >10000 `send_document`
- (this report + ADR-024 + D-024 tick + Spec-WIKI log entry) — meta commit
