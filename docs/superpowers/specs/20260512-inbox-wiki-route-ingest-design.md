---
feature: inbox-wiki-route-ingest
bd_id: aisw-zd9
epic: aisw-t2r
phase: "Inbox-WIKI Phase-B"
status: design
created: 2026-05-12
discovery_ref: docs/superpowers/specs/20260512-inbox-wiki-route-ingest-discovery.md
approach: "After Phase-A's router.route(), for ROUTE/CREATE_WIKI intents: resolve-or-create the target <Domain>-WIKI (WikiLifecycleManager), write a fresh raw payload + promote any media binary into the target WIKI, run a Stage-1b librarian session there (run_wiki_session + prompts/wiki.md + prompts/domain-*.md overlay), reply with notes + the ingest summary. New pure module inbox/route.py + a runtime _LibrarianAdapter behind a narrow Librarian Protocol; the pipeline orchestrates the two-step."
technology:
  stack:
    - name: pydantic
      version: ">=2 (in project)"
      use: "WikiName already; RouteTarget/IngestOutcome are plain frozen dataclasses (no new pydantic types needed)."
      context7: "not needed — no library API in question; WikiLifecycleManager/run_wiki_session/promote_path_to_raw are internal, already read."
  no_new_dependencies: true
  no_new_db_table: true
  new_modules:
    - src/ai_steward_wiki/inbox/route.py    # RouteTarget, RouteRejection, RouteOutcome, resolve_target_wiki, render_target_raw, stage_raw_into_wiki, build_ingest_prompt, pick_domain_overlay
  changed_modules:
    - src/ai_steward_wiki/tg/pipeline.py     # Librarian Protocol; after router.route(), ROUTE/CREATE_WIKI → librarian.ingest() → deliver
    - src/ai_steward_wiki/__main__.py        # _LibrarianAdapter + wiring; injects librarian= into DefaultPipeline
  decisions_ref:
    - "Q&A 2026-05-12 (6 decisions) — see discovery_ref §Resolved design questions"
  adr_candidates:
    - "ADR — Inbox-WIKI Phase-B route+ingest: auto-create on missing ROUTE target; move-raw-before-Stage-1b (forward-only retry); dedicated Librarian adapter. (decide in Step 8 — leaning yes; it sets the Stage-1b ingest pattern Phases C–D build on, and ADR-003 already set the Stage-1a pattern.)"
---

# Design — Inbox-WIKI Phase-B: RouterDecision → domain WIKI select/materialise + move + Stage-1b ingest

**bd:** `aisw-zd9` (epic `aisw-t2r`) · **date:** 2026-05-12 · Discovery + Q&A: see `discovery_ref`. FR/NFR/risks/scope live there.

## 1. Approach (chosen)

In `tg/pipeline.py::_run_text_pipeline`, after the Phase-A router branch produces a `RouterDecision`:

```
decision = await self._router.route(...)
if decision.intent in {ROUTE, CREATE_WIKI} and self._librarian is not None:
    outcome = await self._librarian.ingest(
        decision, telegram_id=..., user_text=text, source=source,
        media_paths=media_paths, correlation_id=...,
    )
    if outcome.status == "ok":
        await self._output.deliver(chat_id, telegram_id, run_id=outcome.run_id, text=outcome.reply)
    else:  # "rejected" (cap/bad-name) | "run_failed" (Stage-1b WikiRunnerError)
        await self._sender.send_message(chat_id, outcome.reply)
    return
# decision.intent in {CLARIFY, REJECT}  → Phase-A behaviour:
await self._sender.send_message(chat_id, decision.notes); return
# (and when self._librarian is None — graceful fall-through to Phase-A behaviour for routable intents too)
```

`_LibrarianAdapter.ingest(decision, *, telegram_id, user_text, source, media_paths, timeout_s=None, correlation_id) -> IngestOutcome`:
1. `route_outcome = resolve_target_wiki(decision, lifecycle=self._lifecycle, owner=telegram_id, default_template_id="_default")`:
   - `CREATE_WIKI`, or `ROUTE` with `lookup→None` → `lifecycle.create_wiki(owner, decision.target_wiki, "_default")` → `RouteTarget(wiki_name, wiki_dir, created=True)` (for ROUTE-missing also log `inbox.route.route_target_was_missing`).
   - `ROUTE` with `lookup→WikiName` → `RouteTarget(wiki_name, wiki_dir, created=False)`.
   - `AntiSpamCapError` → `RouteRejection(reason="cap", hint="Достигнут лимит вики — удали ненужную и попробуй снова.")`.
   - `WikiNameError` (from `normalize_wiki_name` inside `create_wiki`) → `RouteRejection(reason="bad_name", hint="Не смог разобрать имя вики — переформулируй.")`.
   - `wiki_dir = wiki_root / str(owner) / wiki_name.primary`.
2. On `RouteRejection` → return `IngestOutcome(status="rejected", reply=f"{decision.notes}\n\n{rej.hint}", run_id=None, target_wiki=None, created=False)`. Log `inbox.route.cap_reached` / `inbox.route.bad_name`.
3. On `RouteTarget`:
   - `staged = stage_raw_into_wiki(wiki_dir, source=source, user_text=user_text, media_paths=media_paths)` — writes `wiki_dir/raw/<utc-ts>_<source>.<ext>` (text → `.md` body verbatim; non-text → `.md` sidecar with YAML front-matter), and for each media path calls `promote_path_to_raw(p, wiki_root=wiki_dir)` → `wiki_dir/raw/media/<ISO8601>_<sha8>.<ext>` (returns absolute paths). Returns `StagedRaw(sidecar_rel: str, media_rel: list[str], media_abs: list[Path])`. Log `inbox.route.raw_moved` per artifact. **Phase-A's `Inbox-WIKI/raw/` sidecar is left in place as audit — not moved.**
   - `overlay = pick_domain_overlay(prompts_dir, wiki_name.slug)` → `prompts_dir/domain-<slug>.md` if it exists, else `prompts_dir/domain-default.md`.
   - `ingest_prompt = build_ingest_prompt(user_text, staged)` — a ru instruction referencing the raw path(s) and asking for an ingest (parse → write/update pages → backlinks → log.md → short summary).
   - `run_id = f"ingest-{uuid4().hex[:12]}"`; log `inbox.route.ingest.begin` (wiki_id, run_id).
   - `result = await run_wiki_session(wiki_id=f"{owner}/{wiki_name.primary}", wiki_path=wiki_dir, base_prompt_path=self._base_prompt_path (prompts/wiki.md), overlay_prompt_path=overlay, run_id=run_id, correlation_id=correlation_id, runtime_dir=self._runtime_dir, acquirer=self._acquirer, spawner=self._spawner, config=self._run_config, user_input=ingest_prompt, media_paths=staged.media_abs or None, timeout_s=None)` — except `WikiRunnerError as e`: log `inbox.route.ingest_failed`; return `IngestOutcome(status="run_failed", reply=f"{decision.notes}\n\nНе удалось разложить по полочкам — попробую позже.", run_id=run_id, target_wiki=wiki_name.primary, created=route_target.created)`.
   - `summary = aggregate_text(result.events)`; log `inbox.route.ingest.done` (latency_ms, chars).
   - return `IngestOutcome(status="ok", reply=f"{decision.notes}\n\n{summary or '(WIKI обновлена)'}", run_id=run_id, target_wiki=wiki_name.primary, created=route_target.created)`.

Non-routable intents (`CLARIFY/REJECT`) and the `librarian is None` case keep Phase-A's `send_message(decision.notes)` behaviour.

## 2. Data model — `src/ai_steward_wiki/inbox/route.py`

```python
@dataclass(frozen=True, slots=True)
class RouteTarget:
    wiki_name: WikiName        # from wiki.name
    wiki_dir: Path             # <wiki_root>/<owner>/<primary>
    created: bool              # True iff create_wiki actually made a new dir this call

@dataclass(frozen=True, slots=True)
class RouteRejection:
    reason: Literal["cap", "bad_name"]
    hint: str                  # ru, appended after decision.notes

RouteOutcome = RouteTarget | RouteRejection

@dataclass(frozen=True, slots=True)
class StagedRaw:
    sidecar_rel: str           # "raw/<ts>_<source>.md"  (relative to wiki_dir)
    media_rel: list[str]       # ["raw/media/<ISO8601>_<sha8>.<ext>", ...]
    media_abs: list[Path]      # absolute paths of the promoted media (for run_wiki_session media_paths)

def resolve_target_wiki(decision: RouterDecision, *, lifecycle: WikiLifecycleManager,
                        owner: int, wiki_root: Path, default_template_id: str = "_default",
                        on_route_missing: Callable[[], None] | None = None) -> RouteOutcome: ...
def render_target_raw(*, source, user_text, media_rel) -> tuple[str, str]: ...   # (filename, content)
def stage_raw_into_wiki(wiki_dir: Path, *, source, user_text, media_paths) -> StagedRaw: ...
def build_ingest_prompt(user_text: str, staged: StagedRaw) -> str: ...
def pick_domain_overlay(prompts_dir: Path, slug: str) -> Path: ...   # domain-<slug>.md | domain-default.md
```

`IngestOutcome` (frozen dataclass) lives in `tg/pipeline.py` next to `WikiRunOutcome` (it is the Librarian Protocol's return type, like `WikiRunOutcome` is the WikiRunner's):

```python
@dataclass(frozen=True, slots=True)
class IngestOutcome:
    status: Literal["ok", "rejected", "run_failed"]
    reply: str                 # already composed (notes + summary | notes + hint)
    run_id: str | None         # set when a Stage-1b run happened (ok | run_failed)
    target_wiki: str | None    # primary name when resolved
    created: bool
```

## 3. Control flow — `tg/pipeline.py`

- New `Librarian` Protocol: `async def ingest(self, decision: RouterDecision, *, telegram_id: int, user_text: str, source: Literal["text","voice","document","photo"], media_paths: list[Path] | None = None, correlation_id: str) -> IngestOutcome`.
- `DefaultPipeline.__init__` gains `librarian: Librarian | None = None` → `self._librarian`.
- In `_run_text_pipeline`, inside the existing `# START_BLOCK_ROUTABLE_BRANCH`, **replace** `await self._sender.send_message(chat_id, decision.notes); return` with the dispatch shown in §1: on `decision.intent ∈ {RouterIntent.ROUTE, RouterIntent.CREATE_WIKI}` and `self._librarian is not None` → `librarian.ingest(...)` → deliver by `outcome.status`; otherwise (`CLARIFY/REJECT`, or no librarian) → `send_message(decision.notes); return`.
- New log anchors in the pipeline: `tg.pipeline.route.ingest_dispatched` (intent, source), `tg.pipeline.route.delivered` (status, target_wiki, created, run_id). `RouterError` handling from Phase-A is unchanged (still before this branch).
- `_routable_wired` / docs: `librarian` is optional — when absent, routable intents still get the Phase-A notes-echo (no regression).
- The legacy flat-run path (`_runner`/`_streaming`) is untouched.

## 4. Adapter & wiring — `__main__.py`

```python
class _LibrarianAdapter:  # implements Protocol Librarian
    def __init__(self, *, wiki_root, prompts_dir, lifecycle, runtime_dir, acquirer, spawner, run_config): ...
    async def ingest(self, decision, *, telegram_id, user_text, source, media_paths=None, correlation_id) -> IngestOutcome:
        # §1 steps 1–3; base_prompt_path = prompts_dir / "wiki.md"
```

- Construct in `_amain` next to the other adapters: `librarian_adapter = _LibrarianAdapter(wiki_root=settings.wiki_root, prompts_dir=settings.prompts_dir, lifecycle=WikiLifecycleManager(settings.wiki_root, max_per_user=settings.wiki_max_per_user, retention_days=settings.wiki_trash_retention_days), runtime_dir=runtime_dir, acquirer=WikiLockAdapter(lock_manager), spawner=AsyncioSpawner(), run_config=_RunConfig(model=settings.wiki_runner_model, timeout_s=settings.wiki_runner_timeout_s, term_grace_s=settings.wiki_runner_term_grace_s, claude_config_dir=settings.claude_config_dir))`.
- Pass `librarian=librarian_adapter` into `DefaultPipeline(...)`.
- `wiki_root` and `claude_config_dir` are already required by the runtime (the `_RouterAdapter`/`_WikiRunnerAdapter` path); no new Settings field.

## 5. Logging anchors (FR-6)

| event | where | fields (beyond ts/event, all + correlation_id, telegram_id) |
|---|---|---|
| `tg.pipeline.route.ingest_dispatched` | pipeline, before `librarian.ingest` | intent, source |
| `inbox.route.target_resolved` | `resolve_target_wiki` success | target_wiki, created |
| `inbox.route.route_target_was_missing` | `resolve_target_wiki`, ROUTE+lookup→None | target_wiki (warning) |
| `inbox.route.cap_reached` | `resolve_target_wiki`, AntiSpamCapError | target_wiki |
| `inbox.route.bad_name` | `resolve_target_wiki`, WikiNameError | raw_name |
| `inbox.route.raw_moved` | `stage_raw_into_wiki`, per artifact | src (or "text"), dest |
| `inbox.route.ingest.begin` | `_LibrarianAdapter`, before `run_wiki_session` | wiki_id, run_id, source, media_count |
| `inbox.route.ingest.done` | after `run_wiki_session` | wiki_id, run_id, latency_ms, chars |
| `inbox.route.ingest_failed` | on `WikiRunnerError` | wiki_id, run_id, error_class |
| `tg.pipeline.route.delivered` | pipeline, after `librarian.ingest` | status, target_wiki, created, run_id |

## 6. Module map (post-change)

- **NEW** `M-INBOX-ROUTE` → `src/ai_steward_wiki/inbox/route.py` — `RouteTarget`, `RouteRejection`, `RouteOutcome`, `StagedRaw`, `resolve_target_wiki`, `render_target_raw`, `stage_raw_into_wiki`, `build_ingest_prompt`, `pick_domain_overlay`. DEPENDS: `M-WIKI-LIFECYCLE` (WikiLifecycleManager, WikiName, AntiSpamCapError, WikiNameError), `M-INBOX` (promote_path_to_raw), `M-INBOX-ROUTER` (RouterDecision, RouterIntent). ROLE: RUNTIME.
- `M-TG-PIPELINE-CLASSIFIER` — add `Librarian` Protocol + `IngestOutcome` dataclass + the ROUTE/CREATE_WIKI dispatch; +DEPENDS `M-INBOX-ROUTE`.
- `M-RUNTIME-WIRING` — add `_LibrarianAdapter`; +DEPENDS `M-INBOX-ROUTE`, `M-WIKI-LIFECYCLE` (it already imports `WikiLockAdapter`/`run_wiki_session`).
- prompts/templates: unchanged (Phase-B uses existing `prompts/wiki.md`, `prompts/domain-*.md`, `templates/_default.md`).

## 7. Test plan sketch (full plan in verification-plan.xml at Step 7)

Unit (`tests/unit/inbox/test_route.py`):
1. `resolve_target_wiki` — CREATE_WIKI → create + `created=True`; ROUTE existing → `created=False`; ROUTE missing → create + `created=True` + the `on_route_missing` callback fires; `AntiSpamCapError` → `RouteRejection(reason="cap")`; bad name → `RouteRejection(reason="bad_name")` (use a fake/real `WikiLifecycleManager` over `tmp_path`).
2. `render_target_raw` / `stage_raw_into_wiki` — text → `raw/<ts>_text.md` body verbatim; voice/photo → `.md` sidecar; media → `promote_path_to_raw` into `wiki_dir/raw/media/...`, `StagedRaw.media_abs` absolute & exists, `media_rel` relative.
3. `pick_domain_overlay` — `domain-health.md` exists → that; unknown slug → `domain-default.md`.
4. `build_ingest_prompt` — contains the raw path(s) and the user text.
Unit (`tests/unit/test_librarian_adapter.py`):
5. `_LibrarianAdapter.ingest` — happy CREATE_WIKI: creates the dir, stages raw, `run_wiki_session` called with `wiki_id=f"{tid}/{primary}"`, `wiki_path=wiki_dir`, `overlay=domain-default.md`, `user_input=<ingest prompt>`; returns `status="ok"`, `reply` starts with `decision.notes`; `WikiRunnerError` → `status="run_failed"`, reply has the ru hint, raw still in `wiki_dir/raw/`; cap → `status="rejected"`; log anchors via capsys (patch `run_wiki_session`).
Unit (`tests/unit/tg/test_pipeline_route_ingest.py`):
6. routable + router(ROUTE) + librarian → `librarian.ingest` called; `status="ok"` → `output.deliver(text=reply)`; `status="rejected"|"run_failed"` → `sender.send_message(reply)`; router(CLARIFY) → no `librarian.ingest`, `send_message(decision.notes)`; `librarian is None` → `send_message(decision.notes)` (fall-through); `tg.pipeline.route.*` markers via capsys.
Integration (`tests/integration/test_e2e_pipeline.py`, gated): scenario 6 — routable text → real Stage-1a router (CREATE_WIKI or ROUTE) → real Stage-1b ingest in the target WIKI → assert a non-`CLAUDE.md` page file appeared under `<wiki_root>/<tid>/<Domain>-WIKI/` and the bot reply contains the router notes. (+`pipeline_with_router_and_librarian` fixture / extend `pipeline_with_router`.)

## 8. Out of scope (carried to later phases)
Inline-button confirm before executing → `aisw-e45`. Router→cron bridge → `aisw-kcz`. `## Inbox hint` fast-path + per-user media `_staging` → `aisw-12t`. Domain→template_id mapping + full template-body rendering in `create_wiki` → later refinement. Per-WIKI git auto-commit on ingest (`M-OPS-BACKUP` wiring) → separate concern. Retry/sweep of un-ingested files left in `<Domain>-WIKI/raw/` → later ops task. Restore-from-trash on ROUTE to a soft-deleted name → later.
