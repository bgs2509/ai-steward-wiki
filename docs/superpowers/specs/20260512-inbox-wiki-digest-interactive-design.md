---
feature: inbox-wiki-digest-interactive
bd_id: aisw-269
phase: Inbox-WIKI Phase-D.b.2b
status: design
date: 2026-05-12
discovery: docs/superpowers/specs/20260512-inbox-wiki-digest-interactive-discovery.md
adr: docs/adr/ADR-025-digest-interactive-surface.md   # to be written in step 8/13
covers: [FR-1, FR-2, FR-3, FR-4]
stack_decisions:
  - id: TD-1
    text: "aiogram Command filter for the first slash-command surface — a dedicated commands Router (or commands registered FIRST inside the existing m-tg-handlers-wiring router) so `/digest_now` and `/expand` match before the catch-all F.text message handler; ordinary text keeps falling through to MessagePipeline unchanged. No new dependency (aiogram.filters.Command is stdlib of aiogram 3.x)."
  - id: TD-2
    text: "Reuse fire_digest_job(job_id) verbatim for /digest_now — no _run_digest core refactor, no new lock; the per-WIKI flock inside run_wiki_session already serialises a concurrent cron digest (queue-behind accepted)."
  - id: TD-3
    text: "Generalise the DigestRunner Protocol with `section: str | None = None` (None ⇒ prompts/digest.md, today's behaviour byte-identical; key ⇒ prompts/digest_expand.md). _DigestRunnerAdapter in __main__ swaps overlay_prompt_path and the user_input accordingly. No new set_digest_context tuple element — the existing `runner` covers both modes."
  - id: TD-4
    text: "DigestPayload.wiki_scope: Literal['all'] → 'all' | list[str] (list min_length=1) in storage/jobs/payloads.py. No jobs.db Alembic migration ('all' stays valid; existing rows keep validating; Pydantic union discriminator unaffected — wiki_scope is not the discriminator)."
  - id: TD-5
    text: "Heuristic WIKI-name extraction lives in the digest fast-path (tg/pipeline.py _handle_digest_intent / a small helper next to humanize_recurrence) — tokens of the NL turn ∩ the owner's *-WIKI/ dir-stems (case-insensitive, whole-token). No Haiku call. The owner's WIKI dir list comes from the same resolver the firing context already has (resolve_owner_wikis) or the wiki-lifecycle listing used elsewhere in pipeline.py — pick whichever is already imported there."
---

# Design — Inbox-WIKI Phase-D.b.2b: digest interactive surface (`aisw-269`)

> Requirements / scope / risks SSoT: the discovery doc. This doc = the solution: approach, data shapes, control flow, error handling, testing. Q&A decision log at the end.

## 1. Approach in one paragraph

Add the bot's first two slash commands behind a small command router that runs before today's NL catch-all. `/digest_now` just calls the existing `fire_digest_job(job_id)` once per the calling owner's enabled `digest_job` rows — reusing the entire Phase-D.b.1/2a pipeline (lock, planner context, `deliver_output`, 3-strike); zero digest jobs → a ru hint to create one. `/expand <section>` re-runs Claude scoped to one of the four D-024 sections via a generalised `DigestRunner(section=…)` (`None` = the full digest as today, a key = a new `prompts/digest_expand.md`) and replies with `send_message`. Separately, `DigestPayload.wiki_scope` is widened from `Literal["all"]` to `"all" | list[str]` so a recurring digest can be scoped at creation time; the digest fast-path extracts the name list heuristically against the owner's `*-WIKI/` dir-stems and `fire_digest_job` intersects it with the live set. New ADR-025; GRACE refresh. **No cards, no per-user toggles, no new SQLite table** — both deferred to their own bds (rationale in discovery `scope_out` and ADR-025).

## 2. Components & responsibilities

| Unit | File | Change | Depends on |
|------|------|--------|-----------|
| commands router | `src/ai_steward_wiki/tg/handlers.py` (or a new `tg/commands.py` included by `build_router`) | NEW: `@router.message(Command("digest_now"))` → `_handle_digest_now`; `@router.message(Command("expand"))` → `_handle_expand`. Registered **before** the existing `F.text` handler. Both wrap their body in try/except → ru error reply, never bubble. | `aiogram.filters.Command`, the digest firing context (for `/digest_now`), a digest runner + `resolve_owner_wikis` (for `/expand`) |
| `/digest_now` body | same | NEW: `SELECT id FROM jobs WHERE owner_telegram_id=? AND kind='digest_job' AND status='scheduled'` (via the jobs session_maker the firing ctx holds, or a thin accessor); for each → `await firing.fire_digest_job(id)`; 0 rows → `sender.send_message(chat, _DIGEST_NOW_NONE_RU)`. Per-job exception caught & logged (`tg.command.digest_now.job_failed`), other jobs still run. | `scheduler.firing.fire_digest_job`, jobs sessionmaker |
| `/expand` body | same | NEW: parse `<section>` arg → one of `{today, meds, trackers, wiki}` else `sender.send_message(chat, _EXPAND_USAGE_RU)`; `wikis = await resolve_owner_wikis(owner)`; empty → `_EXPAND_NO_WIKI_RU`; `(primary_id, primary_path), *rest = wikis`; `text = await digest_runner(wiki_id=primary_id, wiki_path=primary_path, extra_add_dirs=[p for _,p in rest], planner_context="", correlation_id=f"expand:{owner}:{section}", section=section)`; `sender.send_message(chat, text or _EXPAND_EMPTY_RU)`. | `DigestRunner`, `resolve_owner_wikis`, `TgSender` |
| `DigestRunner` Protocol | `src/ai_steward_wiki/scheduler/firing.py` | CHANGE: add `section: str | None = None` to the Protocol `__call__`. `fire_digest_job` keeps calling `runner(..., )` with the default (= full digest) — byte-identical. | — |
| `_DigestRunnerAdapter` | `src/ai_steward_wiki/__main__.py` | CHANGE: `run(..., section=None)` — `if section is None`: today's path (`overlay_prompt_path=digest.md`, `user_input=planner_context`); `else`: `overlay_prompt_path=settings.prompts_dir/"digest_expand.md"`, `user_input` = a short ru line naming the section (e.g. `f"Детализируй раздел сводки: {section}"`), `planner_context` unused. Same `extra_add_dirs`, same timeout, same `LockAcquirer`. Wire `digest_expand_prompt_path` in the adapter ctor. | `run_wiki_session`, settings |
| expand prompt | `prompts/digest_expand.md` | NEW: ru prompt — "ты получаешь имя раздела дайджеста (`today`/`meds`/`trackers`/`wiki`); дай развёрнутую детализацию ИМЕННО этого раздела за период по доступным WIKI; HTML whitelist как в digest.md; если по разделу за период ничего — ответь одной строкой «По этому разделу за период ничего нет.»" + `semver 0.1.0`. | — |
| `DigestPayload` | `src/ai_steward_wiki/storage/jobs/payloads.py` | CHANGE: `wiki_scope: Literal["all"] | list[str] = "all"` (a `list` constrained non-empty — `Annotated[list[str], Field(min_length=1)]` or a `field_validator`). Bump `VERSION` 0.0.4 → 0.0.5; MAP/CHANGE_SUMMARY. | pydantic |
| `create_digest_job` | `src/ai_steward_wiki/scheduler/firing.py` | CHANGE: `wiki_scope: str | list[str] = "all"`; build `DigestPayload(wiki_scope="all" if wiki_scope=="all" else list(wiki_scope), …)`. (the branch is already there — just the param type + the list cast.) | — |
| `fire_digest_job` scope filter | `src/ai_steward_wiki/scheduler/firing.py` | CHANGE: after `wikis = list(await resolve_owner_wikis(owner_id))`, if `payload.wiki_scope != "all"`: keep `(wid, p)` whose `wid` (or dir-stem) ∈ `set(payload.wiki_scope)`, lower-cased; log dropped names (`scheduler.digest.scope_filter` with `requested`, `kept`, `vanished`); if the kept list is empty → `sender.send_message(chat, _DIGEST_SCOPE_VANISHED_RU)`, mark finished, log `empty="scope_vanished"`, return (no strike — it's a config drift, not a failure). | — |
| fast-path name extraction | `src/ai_steward_wiki/tg/pipeline.py` (`_handle_digest_intent` + a `_extract_wiki_names(text, owner_wiki_stems) -> list[str] | Literal["all"] | None` helper near `humanize_recurrence`; `None` = a name-shaped unresolved token → caller asks for clarification) | NEW helper + CHANGE `_handle_digest_intent`: after recurrence parse, call the extractor; `None` → ru clarification (`_DIGEST_WIKI_UNKNOWN_RU` listing the owner's WIKI names), don't build the confirm draft; otherwise pass `wiki_scope` into the `category='digest'` confirm draft → `create_digest_job(... wiki_scope=...)`. Recap/ack: when scoped, append «по: Health, Money» (reuse/extend `build_digest_recap`). | wiki-lifecycle listing already imported in pipeline.py (or the firing resolver) |
| ru strings | wherever the others live (`tg/pipeline.py` constants block / `handlers.py`) | NEW: `_DIGEST_NOW_NONE_RU`, `_EXPAND_USAGE_RU`, `_EXPAND_NO_WIKI_RU`, `_EXPAND_EMPTY_RU`, `_DIGEST_SCOPE_VANISHED_RU`, `_DIGEST_WIKI_UNKNOWN_RU`. Ru-only (D-032). | — |
| GRACE | `docs/knowledge-graph.xml`, `docs/development-plan.xml`, `docs/verification-plan.xml` | `grace-refresh` + `--verify`: new `prompts/digest_expand.md` node; updated `M-SCHEDULER-FIRING` / `M-TG-PIPELINE` / `M-STORAGE-JOBS` / `M-RUNTIME-WIRING` (+ `M-TG-TEXT`/`M-TG-HANDLERS` if the router lands there) headers; new log anchors. | — |

## 3. Control flow

**`/digest_now`** — owner sends `/digest_now` → command router → `_handle_digest_now`:
1. `correlation_id = f"digest_now:{owner}:{uuid4().hex[:8]}"`; `_log.info("tg.command.digest_now", owner_telegram_id=owner, correlation_id=…)`.
2. Query enabled `digest_job` ids for the owner. None → `send_message(chat, _DIGEST_NOW_NONE_RU)`; `_log.info("tg.command.digest_now.empty")`; done.
3. For each `job_id`: `try: await firing.fire_digest_job(job_id) except Exception as exc: _log.warning("tg.command.digest_now.job_failed", job_id=…, error_class=type(exc).__name__)` — continue with the rest. (`fire_digest_job` itself never raises, but the wrap is belt-and-braces and covers the lookup path.)
4. `_log.info("tg.command.digest_now.done", n_jobs=…)`.

**`/expand <section>`** — owner sends `/expand trackers` → command router → `_handle_expand`:
1. Parse the arg. Missing / not in `{today, meds, trackers, wiki}` → `send_message(chat, _EXPAND_USAGE_RU)`; `_log.info("tg.command.expand.bad_section", got=arg)`; done.
2. `wikis = list(await resolve_owner_wikis(owner))`. Empty → `send_message(chat, _EXPAND_NO_WIKI_RU)`; done.
3. `text = await digest_runner(wiki_id=primary_id, wiki_path=primary_path, extra_add_dirs=…, planner_context="", correlation_id=f"expand:{owner}:{section}", section=section)`.
4. `send_message(chat, (text or "").strip() or _EXPAND_EMPTY_RU)`; `_log.info("tg.command.expand.delivered", section=section, chars=len(text or ""))`.
5. Any exception in 2-4 → caught at the handler boundary → `send_message(chat, generic ru error)`; `_log.warning("tg.command.expand.failed", error_class=…)`.

**Digest creation with a named subset** — owner: «делай сводку по Health и Money каждый день в 9» → digest fast-path → `_handle_digest_intent`:
1. Recurrence parsed as today.
2. `scope = _extract_wiki_names(turn_text, owner_wiki_stems)`. `scope is None` (a token like «Money» looked like a WIKI name but the owner has no `Money-WIKI/`) → `send_message(chat, _DIGEST_WIKI_UNKNOWN_RU.format(known=", ".join(owner_wiki_stems)))`; no confirm draft; `_log.info("tg.pipeline.digest.wiki_unknown", token=…)`; done.
3. Build the `category='digest'` explicit confirm draft as today, but carry `wiki_scope=scope` (`"all"` or `list[str]`); recap names the WIKIs when scoped.
4. On confirm → `create_digest_job(... wiki_scope=scope ...)` → `DigestPayload(wiki_scope=…)` persisted; `CronTrigger` registered. `_log.info("scheduler.digest.scheduled", …, wiki_scope=…)`.

**Cron digest fires with a scoped payload** — `fire_digest_job(job_id)` as today, plus after `wikis = …`:
- `payload.wiki_scope == "all"` → unchanged.
- else → `kept = [(wid, p) for (wid, p) in wikis if wid.lower() in {s.lower() for s in payload.wiki_scope}]`; `vanished = set(payload.wiki_scope) - {wid for wid,_ in kept}`; `_log.info("scheduler.digest.scope_filter", job_id=…, requested=payload.wiki_scope, kept=[w for w,_ in kept], vanished=sorted(vanished))`. `kept` empty → `send_message(chat, _DIGEST_SCOPE_VANISHED_RU)`, `job.finished_at_utc=…`, commit, `_log.info("scheduler.digest.delivered", empty="scope_vanished")`, return. Else continue with `wikis = kept`.

## 4. Error handling

- **Slash-command handlers** never let an exception reach the aiogram dispatcher — outermost `try/except Exception` in each → a generic ru "что-то пошло не так" reply + a `_log.warning` with `error_class`. Per-job failures inside `/digest_now` are caught individually so the remaining jobs run.
- **`/expand` empty result** — "по разделу за период ничего нет" is a normal reply (`_EXPAND_EMPTY_RU` or the model's own one-liner), not an error.
- **Scoped digest, all names vanished** — config drift, not a run failure → ru notice, no 3-strike, job stays `scheduled` (the owner may re-create those WIKIs).
- **`fire_digest_job` invariants unchanged** — still catches everything, still 3-strike on real run/delivery failures; the scope filter sits before the runner call so it cannot trip a strike.
- **Fast-path unresolved WIKI name** — clarification, no job created (fail-fast at the boundary), so we never persist a `wiki_scope` that points at nothing.

## 5. Testing (TDD, unit, offline)

- `tests/unit/tg/test_commands.py` (or extend `test_handlers.py`): `/digest_now` with 0 / 1 / N digest jobs (asserts `fire_digest_job` called per id, ru hint when 0); `/digest_now` where one `fire_digest_job` raises (others still called); `/expand trackers` happy path (asserts `digest_runner` called with `section="trackers"`, reply sent); `/expand` bad/missing section → usage line; `/expand` no WIKI → notice; non-command text still routed to the pipeline (router-order regression).
- `tests/unit/scheduler/test_firing.py`: `fire_digest_job` with `wiki_scope=["health"]` keeps only that WIKI (asserts `runner` got the filtered set); with a vanished name → ru notice, no strike, status stays `scheduled`, log anchor `scheduler.digest.scope_filter`; `create_digest_job(wiki_scope=["health","money"])` persists the list shape.
- `tests/unit/storage/test_payloads.py`: `DigestPayload(wiki_scope="all")` ok; `wiki_scope=["health"]` ok; `wiki_scope=[]` rejected; round-trip `model_dump(mode="json")` / `parse_job_payload` for the list shape; an existing `'all'` dict still validates.
- `tests/unit/tg/test_pipeline.py` (digest fast-path): «сводка по Health каждый день в 9» with the owner having `Health-WIKI/` → confirm draft carries `wiki_scope=["Health"]`, recap names it; with «Money» and no `Money-WIKI/` → clarification, no draft; no names mentioned → `wiki_scope="all"` (today's behaviour).
- `_DigestRunnerAdapter.run(section="trackers")` → asserts `run_wiki_session` got `overlay_prompt_path` = the expand prompt and the section in `user_input`; `section=None` → unchanged digest call (regression).
- Coverage ≥80% core; `make lint` clean; `grace lint --failOn errors` 0; `python -c "import ai_steward_wiki.__main__"` smoke.

## 6. Out of scope (see discovery `scope_out` + ADR-025)

Actionable cards; per-user section toggles + `user_digest_prefs` + `alembic/sessions/0002`; `_run_digest` core refactor; ephemeral `/digest_now` without a configured job; Haiku-assisted name extraction; `run_outputs` row for `/expand`; digest management UX; recurrence-parser wiring; queue consumer; reconciler; retention; i18n.

## 7. Q&A decision log (2026-05-12)

1. **Cards → deferred entirely.** No real data source (`reminder_job` has no medication/event subtype or done-state; `pending_confirm` 10-min TTL ⇒ a cron digest essentially never finds one `pending`). Future bd.
2. **Monolith** — after the trims, ~35-45% of an Opus window; one `feature-workflow` pass.
3. **`/digest_now` = run all the owner's enabled `digest_job` rows** via `fire_digest_job(job_id)` per row; 0 → ru hint. No `_run_digest` refactor, no ephemeral path.
4. **`/expand` = scoped Claude re-run** via `DigestRunner(section=…)` (`None` ⇒ `digest.md`, key ⇒ `digest_expand.md`); reply `send_message`; keys `{today, meds, trackers, wiki}` mirroring the D-024 `<b>`-headers.
5. **Per-user toggles → deferred entirely** (inert without a flip surface; the flip UX + first `sessions.db` migration want their own pass). No `sessions.db` migration here.
6. **Named-subset WIKI → kept.** `wiki_scope: "all" | list[str]`; heuristic extraction in the fast-path against the owner's `*-WIKI/` dir-stems; unresolved name-token ⇒ clarify; `fire_digest_job` intersects with the live set.
7. **ADR → new ADR-025** ("digest interactive surface"; records the cards/toggles deferrals too).
