---
feature: inbox-wiki-router
bd_id: aisw-dsg
epic: aisw-t2r
phase: "Inbox-WIKI Phase-A"
status: done
created: 2026-05-12
discovery_ref: docs/superpowers/specs/20260512-inbox-wiki-router-discovery.md
approach: "Router-Claude run in Inbox-WIKI/ with a fenced-block reply parsed into RouterDecision; wired via a dedicated _RouterAdapter behind a narrow Router Protocol; replaces the flat Stage-1 run for routable intents."
technology:
  stack:
    - name: pydantic
      version: ">=2 (already in project)"
      use: "RouterDecision model (frozen, extra=forbid), RouterIntent str-enum."
      context7: "not re-verified — already used pervasively (ClassifierResult); no version change."
    - name: aiogram
      version: "3.x (already in project)"
      use: "unchanged — pipeline composition only."
      context7: "not needed — no aiogram API touched."
  no_new_dependencies: true
  prompt_change:
    file: prompts/inbox.md
    bump: "1.0.0 → 1.1.0"
    change: "add a strict response-format section requiring a single fenced ```router\\ntarget_wiki: ...\\nintent: ...\\nnotes: ...\\n``` block; ru-only notes."
  new_modules:
    - src/ai_steward_wiki/inbox/router.py   # RouterIntent, RouterDecision, parse_router_reply
  changed_modules:
    - src/ai_steward_wiki/tg/pipeline.py     # routable-intent branch → router.route(); deliver notes
    - src/ai_steward_wiki/__main__.py        # _RouterAdapter + wiring; Router Protocol injected into pipeline
  decisions_ref:
    - "Q&A 2026-05-12 (5 decisions) — see discovery_ref §Resolved design questions"
  adr_candidates:
    - "ADR — Inbox-WIKI router invocation: dedicated adapter + fenced-block contract (decide in Step 8 whether it warrants an ADR; leaning yes — it sets the Stage-1a routing pattern for Phases B–E)."
---

# Design — Inbox-WIKI Phase-A: stage raw content + Router-Claude invocation

**bd:** `aisw-dsg` (epic `aisw-t2r`) · **date:** 2026-05-12 · Discovery + Q&A: see `discovery_ref`.

> FR/NFR/risks/scope live in the discovery doc. This doc = the *solution*: data model, prompt contract, control flow, module map.

## 1. Approach (chosen)

For an incoming TG message whose Stage-0 intent is **routable** (`WIKI_INGEST | WIKI_QUERY | UNKNOWN`):

1. `ensure_inbox_wiki(telegram_id, wiki_root, template_path=templates/inbox-wiki/CLAUDE.md)` → `inbox_dir`.
2. Stage the raw payload into `inbox_dir/raw/<utc-ts>_<source>.<ext>`:
   - `source == "text"` → `<ts>_text.md` containing the message body verbatim.
   - `source ∈ {voice, photo, document}` → `<ts>_<source>.md` sidecar with YAML front-matter (`source`, `staged_path`, `received_utc`; voice → `transcript:` block; document → `filename:` + `mime:`). The binary itself stays in `media_staging_root` (Phase-E migrates it).
3. `run_wiki_session(wiki_id=f"{telegram_id}/Inbox-WIKI", wiki_path=inbox_dir, base_prompt_path=<base>, overlay_prompt_path=prompts/inbox.md, user_input=<text>, media_paths=<media_paths>, timeout_s=None, …)` → assistant text.
4. `parse_router_reply(text)` → `RouterDecision`.
5. Deliver `decision.notes` to the user as the turn reply (FR-5). No file move / no domain-WIKI ingest / no cron in Phase-A.

Non-routable intents (`REMINDER | DIGEST | WIKI_LINT | ADMIN`) keep their current path untouched.

Rationale recap (Q&A): no feature-flag (bot not deployed → YAGNI); strict fenced-block reply (robust parse, ru notes don't break it unlike JSON); dedicated `inbox/router.py` (cohesion, GRACE idiom); dedicated `_RouterAdapter` (ISP — different return type than `WikiRunOutcome`, zero risk to the existing path); media sidecar only (clean Phase-A↔E boundary).

## 2. Data model — `src/ai_steward_wiki/inbox/router.py`

```python
class RouterIntent(str, Enum):
    ROUTE = "route"            # belongs to an existing <Domain>-WIKI (target_wiki set)
    CREATE_WIKI = "create_wiki"  # no domain yet; proposes a new NL name (target_wiki = proposed name)
    CLARIFY = "clarify"          # needs a follow-up question (target_wiki = None)
    REJECT = "reject"            # not actionable / out of scope (target_wiki = None)

class RouterDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    intent: RouterIntent
    target_wiki: str | None      # validated: None unless intent in {ROUTE, CREATE_WIKI}; non-empty when set
    notes: str                   # ru text shown to the user; never empty (fallback fills it)
    raw: str                     # the original assistant text, for audit/log (truncated in logs)
    parsed_ok: bool              # False when the fenced block was missing/malformed and we fell back

def parse_router_reply(text: str) -> RouterDecision: ...
```

Parser rules:
1. Find the **last** ```` ```router … ``` ```` fenced block (last, so a model preamble that quotes the format doesn't win).
2. Inside: line-scan `^(target_wiki|intent|notes):\s*(.*)$`; `notes` may be multi-line (everything after `notes:` until the closing fence).
3. `target_wiki`: literal `null`/empty → `None`; else stripped string.
4. `intent`: must match `RouterIntent`; unknown value → fallback.
5. Cross-field: if `intent ∈ {CLARIFY, REJECT}` force `target_wiki = None`; if `intent ∈ {ROUTE, CREATE_WIKI}` and `target_wiki` is `None`/empty → demote to `CLARIFY` with a generic `notes` ("Уточни, пожалуйста, к какой теме это относится?").
6. No block / parse error → `RouterDecision(intent=CLARIFY, target_wiki=None, notes=<first 500 chars of text, trimmed; or a generic ru prompt if empty>, raw=text, parsed_ok=False)` and the adapter logs `inbox.router.parse_error`.

## 3. Prompt contract — `prompts/inbox.md` (bump 1.0.0 → 1.1.0)

Replace the loose "Формат ответа" list with:

```markdown
## Формат ответа

Ответь РОВНО одним блоком (ничего до и после него):

​```router
target_wiki: <имя существующей <Domain>-WIKI | предлагаемое имя для новой | null>
intent: <route | create_wiki | clarify | reject>
notes: <короткое пояснение на русском для пользователя>
​```

Правила:
- `route` — контент относится к уже существующей `<Domain>-WIKI` (укажи её имя в `target_wiki`).
- `create_wiki` — подходящей `<Domain>-WIKI` нет; предложи NL-имя в `target_wiki`, но НЕ создавай её сам.
- `clarify` — нужно уточнение; `target_wiki: null`, вопрос — в `notes`.
- `reject` — обращение не к месту / вне зоны; `target_wiki: null`, причина — в `notes`.
- Не выполняй side-effects на `<Domain>-WIKI` — это работа Stage-1b.
```

(Existing "Задачи" section stays.) The `​` zero-width chars above are just doc-escaping for the fences; the real file uses plain ```` ``` ````.

## 4. Control flow — `tg/pipeline.py`

`_run_text_pipeline` after `Classifier.classify`:

```
result = await classifier.classify(text, ...)
if result.intent in _ROUTABLE_INTENTS and self._router is not None:
    decision = await self._router.route(
        text=text, telegram_id=telegram_id, correlation_id=correlation_id,
        source=source, media_paths=media_paths, timeout_s=timeout_s,
    )
    await self._sender.send_message(chat_id, decision.notes)   # FR-5
    return
# else: existing flat-run path (self._runner / self._streaming) unchanged
```

- `_ROUTABLE_INTENTS = frozenset({Intent.WIKI_INGEST, Intent.WIKI_QUERY, Intent.UNKNOWN})` — module constant.
- New optional ctor arg `router: Router | None = None`; `_routable_wired` property = `classifier and router and output`; if a routable message arrives and `router is None`, fall through to the legacy path (graceful, matches the existing `is_wired` style). When `router` is wired it **takes precedence** for routable intents — the flat run is no longer reached for them.
- On `RouterError` (raised by the adapter for unrecoverable runner failures) → `await self._sender.send_message(chat_id, ACK_RUNNER_ERR_RU); return` (reuse the existing ack constant; no new user-facing string except possibly an inbox-specific one — decide in Step 9, default = reuse).
- Streaming: Phase-A does **not** stream the Router run (router output is a short structured block, not a long answer). `self._streaming` stays for the legacy text path only.

## 5. Adapter & wiring — `__main__.py`

```python
class _RouterAdapter:  # implements Protocol Router
    def __init__(self, *, wiki_root, inbox_template_path, base_prompt_path,
                 inbox_overlay_path, runtime_dir, acquirer, spawner, run_config): ...
    async def route(self, *, text, telegram_id, correlation_id, source,
                    media_paths=None, timeout_s=None) -> RouterDecision:
        inbox_dir = await ensure_inbox_wiki(telegram_id, wiki_root=self._wiki_root,
                                            template_path=self._inbox_template_path)
        await self._stage_raw(inbox_dir, source=source, text=text, media_paths=media_paths,
                              correlation_id=correlation_id)              # writes raw/<ts>_<source>.<ext|md>
        run_id = f"router-{uuid4().hex[:12]}"
        try:
            result = await run_wiki_session(
                wiki_id=f"{telegram_id}/Inbox-WIKI", wiki_path=inbox_dir,
                base_prompt_path=self._base_prompt_path, overlay_prompt_path=self._inbox_overlay_path,
                run_id=run_id, correlation_id=correlation_id, runtime_dir=self._runtime_dir,
                acquirer=self._acquirer, spawner=self._spawner, config=self._run_config,
                user_input=text, media_paths=media_paths, timeout_s=timeout_s,
            )
        except WikiRunnerError as e:
            raise RouterError(str(e)) from e
        decision = parse_router_reply(result.text)
        # logs: inbox.router.run.done (run_id, latency) + inbox.router.parsed (intent, target_wiki, parsed_ok)
        return decision
```

- `inbox_overlay_path` = `prompts/inbox.md` resolved from the prompts dir Settings already exposes.
- `inbox_template_path` = `templates/inbox-wiki/CLAUDE.md` (already on disk; Settings exposes the templates dir).
- Inject into the pipeline alongside the existing adapters: `build_dispatcher(... , router=router_adapter)` / `MessagePipeline(..., router=router_adapter)`.
- No `promote_path_to_raw` here — that's the legacy domain-WIKI path; the Router run never promotes.

## 6. Logging anchors (NFR-1)

| event | where | fields (beyond ts/event) |
|---|---|---|
| `inbox.router.materialized` | after `ensure_inbox_wiki` (only if newly created — reuse `inbox.materialized`) | telegram_id, path |
| `inbox.router.staged_raw` | after `_stage_raw` | correlation_id, telegram_id, source, raw_path |
| `inbox.router.run.begin` | before `run_wiki_session` | correlation_id, telegram_id, wiki_id, run_id, source, media_count |
| `inbox.router.run.done` | after `run_wiki_session` | correlation_id, telegram_id, wiki_id, run_id, latency_ms, chars |
| `inbox.router.parsed` | after `parse_router_reply` | correlation_id, telegram_id, run_id, intent, target_wiki, parsed_ok |
| `inbox.router.parse_error` | inside parser fallback (logged by adapter) | correlation_id, telegram_id, run_id, raw_preview (≤200 chars) |
| `tg.pipeline.router.dispatched` | pipeline, before `router.route` | correlation_id, telegram_id, intent |
| `tg.pipeline.router.error` | pipeline, on `RouterError` | correlation_id, telegram_id, error_class="RouterError" |

All carry `correlation_id = f"tg-{update_id}-{telegram_id}"` (existing convention).

## 7. Module map (post-change)

- **NEW** `M-INBOX-ROUTER` → `src/ai_steward_wiki/inbox/router.py` — `RouterIntent`, `RouterDecision`, `parse_router_reply`. DEPENDS: pydantic, re, enum. ROLE: RUNTIME.
- `M-INBOX` (`inbox/__init__.py`) — re-export `RouterIntent`, `RouterDecision`, `parse_router_reply` (barrel update).
- `M-TG-PIPELINE` (`tg/pipeline.py`) — add `Router` Protocol, `_ROUTABLE_INTENTS`, routable branch; +DEPENDS on `M-INBOX-ROUTER`.
- `M-RUNTIME-WIRING` (`__main__.py`) — add `_RouterAdapter`, `RouterError`; +DEPENDS on `M-INBOX` (ensure_inbox_wiki), `M-WIKI-RUNNER` (already), `M-INBOX-ROUTER`.
- prompts: `prompts/inbox.md` → 1.1.0 (artifact, not a code module).

## 8. Test plan sketch (full plan in verification-plan.xml at Step 7)

Unit (`tests/unit/inbox/test_router.py`):
1. `parse_router_reply` — happy path each `RouterIntent`; multi-line `notes`; `target_wiki: null`; preamble-before-block (last block wins); missing block → `parsed_ok=False, intent=CLARIFY`; malformed `intent:` value → fallback; cross-field demotion (ROUTE without target → CLARIFY).
Unit (`tests/unit/tg/test_pipeline_router.py`):
2. routable intent + router wired → `router.route` called, `notes` sent, legacy `runner.run` NOT called.
3. non-routable intent → legacy path, `router.route` NOT called.
4. routable intent + `router is None` → falls through to legacy path.
5. `RouterError` → `ACK_RUNNER_ERR_RU` sent, no crash.
Unit (`tests/unit/.../test_router_adapter.py` or extend existing `__main__` tests):
6. `_RouterAdapter.route` — `ensure_inbox_wiki` called; raw sidecar written to `raw/<ts>_<source>.md` with expected front-matter (text vs voice vs document); `run_wiki_session` invoked with `wiki_id=f"{tid}/Inbox-WIKI"`, `wiki_path=inbox_dir`, `overlay=prompts/inbox.md`; result parsed.
Integration (`tests/integration/test_e2e_pipeline.py`, gated `RUN_INTEGRATION=1`):
7. text message with a routable intent → real Claude runs with `cwd` inside `<wiki_root>/<tid>/Inbox-WIKI/`, emits a `router` block, bot replies with `notes`. (FakeAiogramBot; assert the transcript path is under `Inbox-WIKI/runs/`.)

## 9. Out of scope (carried to later phases)
Domain-WIKI lookup/create + file move + Stage-1b ingest → `aisw-zd9`. Inline-button confirm loop → `aisw-e45`. Router→cron bridge → `aisw-kcz`. `## Inbox hint` fast-path + per-user media `_staging` → `aisw-12t`. Retention sweep of `Inbox-WIKI/raw/` → later ops task (epic note).
