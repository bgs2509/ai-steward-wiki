# Implementation Plan — Inbox-WIKI Phase-D.b.2b: digest interactive surface (`aisw-269`)

> SSoT for execution. Discovery: `…-digest-interactive-discovery.md` (3 FR). Design: `…-digest-interactive-design.md`. ADR: `docs/adr/ADR-025-digest-interactive-surface.md`. GRACE: `development-plan.xml` Phase-D.b.2b, `verification-plan.xml` V-M-{STORAGE-JOBS,SCHEDULER-FIRING,TG-HANDLERS-WIRING,TG-PIPELINE-CLASSIFIER}.
> TDD throughout: RED (failing test) → verify it fails → GREEN (minimal code) → verify pass → REFACTOR. Commit after each validated step. Conventional Commits + GRACE MODULE_ID scope. `make lint` + `grace lint --failOn errors` + `uv run pytest tests/unit` must stay green.

## Pre-flight (once, before step 1)
- `bd update aisw-269 --claim` ; `bd update aisw-269 --status=in_progress`.
- Confirm baseline green: `make lint` ✅, `grace lint --failOn errors` → 0, `uv run pytest tests/unit -q` ✅.

---

## Step 1 — `DigestPayload.wiki_scope` widened to `'all' | list[str]` · MODULE `M-STORAGE-JOBS`

**Files:** `src/ai_steward_wiki/storage/jobs/payloads.py`, `tests/unit/storage/test_payloads.py`.

**RED** — add to `test_payloads.py`:
- `DigestPayload(wiki_scope="all", recurrence=<rec>)` → ok, `.wiki_scope == "all"`.
- `DigestPayload(wiki_scope=["Health"], recurrence=<rec>)` → ok, `.wiki_scope == ["Health"]`.
- `DigestPayload(wiki_scope=[], recurrence=<rec>)` → raises `ValidationError`.
- `parse_job_payload(DigestPayload(wiki_scope=["Health","Money"], recurrence=<rec>).model_dump(mode="json"))` → `DigestPayload` with the list intact.
- An existing-shape dict `{"kind":"digest","wiki_scope":"all","recurrence":<rec dict>,"window_hours":24}` still validates via `parse_job_payload`.

Run `uv run pytest tests/unit/storage/test_payloads.py -q` → the new cases fail (list rejected by `Literal["all"]`).

**GREEN** — in `payloads.py`:
```python
class DigestPayload(_PayloadBase):
    kind: Literal["digest"] = "digest"
    wiki_scope: Literal["all"] | Annotated[list[str], Field(min_length=1)] = "all"
    recurrence: Recurrence
    window_hours: int = Field(default=24, ge=1, le=24 * 7)
    prompt_hint: str | None = None
```
Bump header `VERSION: 0.0.4 → 0.0.5`; update `START_MODULE_MAP` line for `DigestPayload` (`wiki_scope:'all'|list[str]`); update `START_CHANGE_SUMMARY` (`LAST_CHANGE: v0.0.5 - aisw-269: widen DigestPayload.wiki_scope to 'all'|list[str] (named-subset digest); PREVIOUS: v0.0.4 - …`).

**Verify:** `uv run pytest tests/unit/storage -q` ✅; `make lint` ✅; `grace lint --failOn errors` → 0.
**Commit:** `feat(M-STORAGE-JOBS): widen DigestPayload.wiki_scope to 'all'|list[str] (aisw-269)`.

---

## Step 2 — `create_digest_job` accepts `wiki_scope: str | list[str]`; log it · MODULE `M-SCHEDULER-FIRING`

**Files:** `src/ai_steward_wiki/scheduler/firing.py`, `tests/unit/scheduler/test_firing.py`.

**RED** — add: `create_digest_job(session, scheduler, owner_telegram_id=…, chat_id=…, recurrence=<rec>, wiki_scope=["Health","Money"])` → the persisted `Job.payload` round-trips through `parse_job_payload` to a `DigestPayload` with `wiki_scope == ["Health","Money"]`; `scheduler.add_job` still called once with the cron trigger. Fails today (`wiki_scope: str` param + `"all" if wiki_scope == "all" else wiki_scope` would pass a `list` straight through — actually currently typed `str`, mypy/test mismatch).

**GREEN** — in `create_digest_job`:
```python
async def create_digest_job(
    session, scheduler, *, owner_telegram_id, chat_id, recurrence,
    wiki_scope: str | list[str] = "all", window_hours: int = 24, correlation_id: str = "",
) -> int:
    payload = DigestPayload(
        wiki_scope="all" if wiki_scope == "all" else list(wiki_scope),
        recurrence=recurrence, window_hours=window_hours,
    ).model_dump(mode="json")
    ...
    _log.info("scheduler.digest.scheduled", correlation_id=correlation_id, job_id=job_id,
              owner_telegram_id=owner_telegram_id, recurrence=recurrence.model_dump(mode="json"),
              wiki_scope=payload["wiki_scope"])
```
Update the `create_digest_job` `START_CONTRACT` INPUTS to note `wiki_scope: str | list[str]`.

**Verify:** `uv run pytest tests/unit/scheduler/test_firing.py -q` ✅; lint ✅; grace ✅.
**Commit:** `feat(M-SCHEDULER-FIRING): create_digest_job accepts wiki_scope list; log wiki_scope on scheduled (aisw-269)`.

---

## Step 3 — `DigestRunner` Protocol `+ section: str | None = None` (non-behavioral) · MODULE `M-SCHEDULER-FIRING`

**Files:** `src/ai_steward_wiki/scheduler/firing.py` (Protocol only).

No test of its own — covered by Step 4 (`fire_digest_job` still works) and Step 6 (`_DigestRunnerAdapter` honours `section`). Change the `DigestRunner.__call__` signature:
```python
class DigestRunner(Protocol):
    async def __call__(
        self, *, wiki_id: str, wiki_path: Path, extra_add_dirs: Sequence[Path],
        planner_context: str, correlation_id: str, section: str | None = None,
    ) -> str: ...
```
`fire_digest_job` keeps calling `runner(wiki_id=…, wiki_path=…, extra_add_dirs=…, planner_context=…, correlation_id=…)` — `section` defaults, behaviour byte-identical. Note in `START_CHANGE_SUMMARY` (folded into Step 4's commit).

**Verify:** `uv run pytest tests/unit/scheduler -q` ✅ (still green); `make lint` (mypy) ✅.
*(No separate commit — bundled into Step 4.)*

---

## Step 4 — `fire_digest_job` scope filter on `payload.wiki_scope` · MODULE `M-SCHEDULER-FIRING`

**Files:** `src/ai_steward_wiki/scheduler/firing.py`, `tests/unit/scheduler/test_firing.py`.

**RED** — add to `test_firing.py` (fake `resolve_owner_wikis`, fake `runner`, fake `sender`):
- digest `Job` with `payload.wiki_scope == ["health"]`, owner resolver returns `[("health", p1), ("money", p2)]` → `fire_digest_job(job_id)` calls `runner` once with `wiki_id="health"` and `extra_add_dirs == []` (no `money`); a `scheduler.digest.scope_filter` log line with `requested=["health"]`, `kept=["health"]`, `vanished=[]` (assert via `capsys`/structlog capture as the existing digest tests do); `deliver_output` called; row stays `scheduled`, `retry_count == 0`.
- digest `Job` with `payload.wiki_scope == ["gone"]`, resolver returns `[("health", p1)]` → no `runner` call, no `deliver_output`; `sender.send_message(chat_id, <ru notice>)` called once; `scheduler.digest.scope_filter` with `vanished=["gone"]`; `scheduler.digest.delivered` with `empty == "scope_vanished"`; row stays `scheduled`, `retry_count == 0`, `finished_at_utc` set; **no DLQ row, no `remove_job`**.
- digest `Job` with `payload.wiki_scope == "all"` → unchanged from today's behaviour (regression — keep one existing-style assertion).

Run → fails (no filter today).

**GREEN** — in `fire_digest_job`, right after `wikis = list(await resolve_owner_wikis(owner_id))` and the `if not wikis:` empty branch:
```python
        if isinstance(payload.wiki_scope, list):
            wanted = {name.lower() for name in payload.wiki_scope}
            kept = [(wid, p) for (wid, p) in wikis if wid.lower() in wanted]
            vanished = sorted(wanted - {wid.lower() for wid, _ in kept})
            _log.info("scheduler.digest.scope_filter", job_id=job_id,
                      requested=list(payload.wiki_scope), kept=[w for w, _ in kept], vanished=vanished)
            if not kept:
                await sender.send_message(chat_id, _DIGEST_SCOPE_VANISHED_RU)
                job.finished_at_utc = _now_naive_utc()
                await session.commit()
                _log.info("scheduler.digest.delivered", job_id=job_id, empty="scope_vanished")
                return
            wikis = kept
```
Add the constant near the other digest ru strings in `firing.py`:
```python
_DIGEST_SCOPE_VANISHED_RU = "Сводка настроена по WIKI, которых сейчас нет. Создайте их заново или настройте сводку ещё раз."  # noqa: RUF001
```
Update `fire_digest_job` `START_CONTRACT`/`START_BLOCK_FIRE_DIGEST_JOB` comments + `START_CHANGE_SUMMARY` (`LAST_CHANGE: v0.4.0 - aisw-269: DigestRunner +section; fire_digest_job wiki_scope intersect-and-filter (scheduler.digest.scope_filter; empty kept → ru notice, empty='scope_vanished', no strike); create_digest_job wiki_scope list. PREVIOUS: v0.3.0 - …`). Bump file `VERSION: 0.3.0 → 0.4.0`.

**Verify:** `uv run pytest tests/unit/scheduler -q` ✅; `make lint` ✅; `grace lint --failOn errors` → 0.
**Commit:** `feat(M-SCHEDULER-FIRING): DigestRunner +section; fire_digest_job wiki_scope intersect-and-filter (aisw-269)`.

---

## Step 5 — `prompts/digest_expand.md` (new) · artifact for `M-RUNTIME-WIRING`

**File:** `prompts/digest_expand.md` (new).

Ru overlay prompt, `semver 0.1.0` line required (same convention as `digest.md`). Content (sketch):
- Role: read-only; you are given the name of ONE digest section (`today` | `meds` | `trackers` | `wiki`) in the user message; produce a detailed breakdown of **only that section** for the relevant period over the WIKIs you can read.
- Output: HTML whitelist `<b><i><u><s><code><pre><a><blockquote>`, escape `< > &` in literal text, never MarkdownV2; one short line per item; no tables.
- Empty: if there is nothing for this section over the period, reply with exactly one line: «По этому разделу за период ничего нет.».
- Never edit files.

No test (it's a prompt asset; exercised by integration). `grace lint` will register it once it's referenced in the knowledge graph (Step 8).

**Commit:** *(bundled with Step 6 — the prompt + its wiring.)*

---

## Step 6 — `_DigestRunnerAdapter.run(section=None)` swaps overlay prompt + user_input · MODULE `M-RUNTIME-WIRING`

**Files:** `src/ai_steward_wiki/__main__.py`, `tests/unit/...` (new `tests/unit/test_main_digest_adapter.py` if no existing adapter test — check `tests/unit/test_main*.py` first; reuse if present).

**RED** — test against `_DigestRunnerAdapter`:
- construct with a stub `run_wiki_session` (monkeypatched) + `digest_prompt_path=<a>` + `digest_expand_prompt_path=<b>`;
- `await adapter.run(wiki_id="health", wiki_path=p, extra_add_dirs=[], planner_context="PLAN", correlation_id="c")` → `run_wiki_session` got `overlay_prompt_path == <a>`, `user_input == "PLAN"`.
- `await adapter.run(wiki_id="health", wiki_path=p, extra_add_dirs=[], planner_context="", correlation_id="c", section="trackers")` → `overlay_prompt_path == <b>`, `user_input` contains `"trackers"`.

Run → fails (`run` has no `section` kwarg; ctor has no expand path).

**GREEN** — in `__main__.py`:
- `_DigestRunnerAdapter.__init__(..., digest_prompt_path, digest_expand_prompt_path)`.
- ```python
  async def run(self, *, wiki_id, wiki_path, extra_add_dirs, planner_context, correlation_id, section=None) -> str:
      if section is None:
          overlay = self._digest_prompt_path; user_input = planner_context
      else:
          overlay = self._digest_expand_prompt_path; user_input = f"Детализируй раздел сводки: {section}"
      result = await run_wiki_session(... overlay_prompt_path=overlay, user_input=user_input, extra_add_dirs=extra_add_dirs, ...)
      ...
  ```
- At construction: `digest_runner_adapter = _DigestRunnerAdapter(..., digest_prompt_path=settings.prompts_dir / "digest.md", digest_expand_prompt_path=settings.prompts_dir / "digest_expand.md")`.
- Update the `__main__.py` MODULE_CONTRACT change-summary (`v0.5.1 → v0.5.2 - aisw-269: _DigestRunnerAdapter.run(section=) → swaps overlay to prompts/digest_expand.md; commands wiring`).

**Verify:** `uv run pytest tests/unit -q` ✅; `python -c "import ai_steward_wiki.__main__"` ✅; `make lint` ✅.
**Commit:** `feat(M-RUNTIME-WIRING): _DigestRunnerAdapter.run(section=) + prompts/digest_expand.md (aisw-269)`.

---

## Step 7 — `firing.run_owner_digests` + `firing.run_section_expand` (use `_digest_ctx`) · MODULE `M-SCHEDULER-FIRING`

**Files:** `src/ai_steward_wiki/scheduler/firing.py`, `tests/unit/scheduler/test_firing.py`.

**RED** — add tests:
- `run_owner_digests(owner_telegram_id)` — with two `digest_job` rows (`status=='scheduled'`) for the owner + one `disabled` + one for another owner → `fire_digest_job` invoked exactly for the two scheduled-owner ids (monkeypatch `fire_digest_job`), returns `2`; one of the two `fire_digest_job` calls raising → the other still invoked, return value still `2` (counts attempted) and a `tg.command.digest_now.job_failed`-style log? — *no*: keep the per-job catch in the **handler** (Step 8), `run_owner_digests` just iterates and lets the handler decide; **decision: `run_owner_digests` does NOT swallow — the handler wraps each.** Actually simpler: `run_owner_digests` returns the list of ids; the handler loops + catches. → Re-spec: `firing.list_owner_digest_job_ids(owner_telegram_id) -> list[int]` (read-only, uses `_digest_ctx`'s jobs maker). Test that.
- `run_section_expand(owner_telegram_id, section)` — with a stub runner in `_digest_ctx` returning `"DETAIL"` and `resolve_owner_wikis` returning `[("health", p1), ("money", p2)]` → calls the ctx runner once with `wiki_id="health"`, `extra_add_dirs==[p2]`, `section==section`, returns `"DETAIL"`; owner with no WIKIs → returns `None` (handler renders the ru "no WIKI" notice).
- both raise `DigestNotInitialisedError` if `_digest_ctx is None`.

**GREEN** — in `firing.py`:
```python
async def list_owner_digest_job_ids(owner_telegram_id: int) -> list[int]:
    if _digest_ctx is None: raise DigestNotInitialisedError(...)
    _, _, _, maker, _, _ = _digest_ctx
    async with maker() as session:
        rows = (await session.execute(
            select(Job.id).where(Job.owner_telegram_id == owner_telegram_id,
                                 Job.kind == "digest_job", Job.status == "scheduled")
        )).scalars().all()
    return list(rows)

async def run_section_expand(owner_telegram_id: int, section: str) -> str | None:
    if _digest_ctx is None: raise DigestNotInitialisedError(...)
    _, runner, resolve_owner_wikis, _, _, _ = _digest_ctx
    wikis = list(await resolve_owner_wikis(owner_telegram_id))
    if not wikis: return None
    (primary_id, primary_path), *rest = wikis
    return await runner(wiki_id=primary_id, wiki_path=primary_path,
                        extra_add_dirs=[p for _, p in rest], planner_context="",
                        correlation_id=f"expand:{owner_telegram_id}:{section}", section=section)
```
Add both to `__all__` + `START_MODULE_MAP`. Fold the contract/version note into Step 4's bump or a tiny follow-up (`v0.4.0 → v0.4.1 - aisw-269: list_owner_digest_job_ids + run_section_expand (slash-command accessors)`).

**Verify:** `uv run pytest tests/unit/scheduler -q` ✅; lint ✅; grace ✅.
**Commit:** `feat(M-SCHEDULER-FIRING): list_owner_digest_job_ids + run_section_expand accessors (aisw-269)`.

---

## Step 8 — `/digest_now` + `/expand` command handlers · MODULE `M-TG-HANDLERS-WIRING`

**Files:** `src/ai_steward_wiki/tg/handlers.py`, `tests/unit/tg/test_commands.py` (new), GRACE: add `prompts/digest_expand.md` node + the `M-TG-HANDLERS-WIRING → M-SCHEDULER-FIRING` CrossLink to `docs/knowledge-graph.xml` (or defer to `grace-refresh` in Finish — but at minimum the new dep must be reflected).

**RED** — `tests/unit/tg/test_commands.py` (fake aiogram `Message` with `.from_user.id`, `.chat.id`, `.text`, `.answer` async-mock; monkeypatch `scheduler.firing`):
- `/digest_now`, `firing.list_owner_digest_job_ids` → `[11, 22]`, `firing.fire_digest_job` monkeypatched → called with `11` then `22`; `tg.command.digest_now.done` logged `n_jobs=2`.
- `/digest_now`, `list_owner_digest_job_ids` → `[]` → `message.answer(<ru hint>)` once; `tg.command.digest_now.empty` logged; `fire_digest_job` not called.
- `/digest_now`, ids `[11, 22]`, `fire_digest_job(11)` raises → `fire_digest_job(22)` still called; `tg.command.digest_now.job_failed` logged with `job_id=11`; handler does not raise.
- `/digest_now`, `firing.list_owner_digest_job_ids` raises `DigestNotInitialisedError` → handler catches → `message.answer(<ru unavailable>)`, no raise.
- `/expand trackers` → `firing.run_section_expand(owner, "trackers")` → `"DETAIL"` → `message.answer("DETAIL")`; `tg.command.expand.delivered` logged `section="trackers"`.
- `/expand` (no arg) / `/expand wat` → `message.answer(<ru usage listing today|meds|trackers|wiki>)`; `tg.command.expand.bad_section` logged; `run_section_expand` not called.
- `/expand trackers`, `run_section_expand` → `None` → `message.answer(<ru no-WIKI notice>)`.
- `/expand trackers`, `run_section_expand` → `""` (model produced nothing) → `message.answer(<ru "nothing for this section">)`. *(or: trust the prompt's own one-liner — then `""`/whitespace → the ru fallback constant; pick the constant path, deterministic.)*
- `/expand trackers`, `run_section_expand` raises → `message.answer(<generic ru error>)`; `tg.command.expand.failed` logged; no raise.
- Router-order regression: a plain text message `"привет"` reaches `pipeline.on_text` (the `~F.text.startswith("/")` filter already excludes commands — assert the command handlers are not invoked for it and `on_text` is).

**GREEN** — in `build_router`, after imports add `from aiogram.filters import Command` and:
```python
    SECTION_KEYS = ("today", "meds", "trackers", "wiki")

    @router.message(Command("digest_now"))
    async def _on_digest_now(message: Message) -> None:
        if message.from_user is None or message.chat is None:
            return
        owner = message.from_user.id
        cid = f"digest_now:{owner}"
        try:
            from ai_steward_wiki.scheduler import firing
            ids = await firing.list_owner_digest_job_ids(owner)
        except firing.DigestNotInitialisedError:
            _log.warning("tg.command.digest_now.unavailable", owner_telegram_id=owner)
            await message.answer(_DIGEST_NOW_UNAVAILABLE_RU); return
        except Exception as exc:  # noqa: BLE001
            _log.warning("tg.command.digest_now.failed", owner_telegram_id=owner, error_class=type(exc).__name__)
            await message.answer(_GENERIC_ERR_RU); return
        if not ids:
            _log.info("tg.command.digest_now.empty", owner_telegram_id=owner)
            await message.answer(_DIGEST_NOW_NONE_RU); return
        _log.info("tg.command.digest_now", owner_telegram_id=owner, correlation_id=cid, n_jobs=len(ids))
        for jid in ids:
            try:
                await firing.fire_digest_job(jid)
            except Exception as exc:  # noqa: BLE001
                _log.warning("tg.command.digest_now.job_failed", job_id=jid, error_class=type(exc).__name__)
        _log.info("tg.command.digest_now.done", owner_telegram_id=owner, n_jobs=len(ids))

    @router.message(Command("expand"))
    async def _on_expand(message: Message) -> None:
        if message.from_user is None or message.chat is None or message.text is None:
            return
        owner = message.from_user.id
        parts = message.text.split(maxsplit=1)
        section = parts[1].strip().lower() if len(parts) > 1 else ""
        if section not in SECTION_KEYS:
            _log.info("tg.command.expand.bad_section", owner_telegram_id=owner, got=section)
            await message.answer(_EXPAND_USAGE_RU); return
        try:
            from ai_steward_wiki.scheduler import firing
            text = await firing.run_section_expand(owner, section)
        except firing.DigestNotInitialisedError:
            await message.answer(_DIGEST_NOW_UNAVAILABLE_RU); return
        except Exception as exc:  # noqa: BLE001
            _log.warning("tg.command.expand.failed", owner_telegram_id=owner, section=section, error_class=type(exc).__name__)
            await message.answer(_GENERIC_ERR_RU); return
        if text is None:
            await message.answer(_EXPAND_NO_WIKI_RU); return
        body = text.strip() or _EXPAND_EMPTY_RU
        await message.answer(body)
        _log.info("tg.command.expand.delivered", owner_telegram_id=owner, section=section, chars=len(body))
```
Add the ru constants at module level in `handlers.py` (`_DIGEST_NOW_NONE_RU`, `_DIGEST_NOW_UNAVAILABLE_RU`, `_EXPAND_USAGE_RU = "Используй: /expand <раздел> — today | meds | trackers | wiki"`, `_EXPAND_NO_WIKI_RU`, `_EXPAND_EMPTY_RU = "По этому разделу за период ничего нет."`, `_GENERIC_ERR_RU`). Update the `handlers.py` MODULE_CONTRACT (`MODULE_MAP`: `+_on_digest_now`/`+_on_expand`; DEPENDS `+ai_steward_wiki.scheduler.firing`; change-summary `aisw-269: first slash commands /digest_now + /expand`). Update `docs/knowledge-graph.xml`: add `prompts/digest_expand.md` as a node (or under `M-RUNTIME-WIRING` assets) and the `CrossLink from="M-TG-HANDLERS-WIRING" to="M-SCHEDULER-FIRING" relation="…/digest_now+/expand via firing.list_owner_digest_job_ids / run_section_expand / fire_digest_job"`.

**Verify:** `uv run pytest tests/unit/tg -q` ✅ (incl. the new `test_commands.py` + the unchanged `test_handlers.py`); `make lint` ✅; `grace lint --failOn errors` → 0; `python -c "import ai_steward_wiki.__main__"` ✅.
**Commit:** `feat(M-TG-HANDLERS-WIRING): /digest_now + /expand slash commands (aisw-269)`.

---

## Step 9 — digest fast-path: heuristic `_extract_wiki_names`; carry `wiki_scope` into the confirm draft; recap names the WIKIs · MODULE `M-TG-PIPELINE-CLASSIFIER`

**Files:** `src/ai_steward_wiki/tg/pipeline.py`, `tests/unit/tg/test_pipeline_digest.py` (or wherever `_handle_digest_intent` is tested today — check; the existing digest fast-path tests live near `tg.pipeline.digest.*`).

**RED** — add cases (the owner's WIKI dir-stems supplied via whatever the pipeline already uses — `resolve_owner_wikis`/wiki-lifecycle listing already imported in `pipeline.py`; the test stubs it):
- «делай сводку по Health каждый день в 9», owner has `Health-WIKI/` → the `category='digest'` `PendingConfirmDraft` carries `wiki_scope == ["Health"]`; `build_digest_recap` text mentions «Health»; on the confirm callback → `create_digest_job` called with `wiki_scope == ["Health"]` (extend `test_pipeline_digest.py`'s confirm-callback assertions).
- «сводку по Money каждый день в 9», owner has only `Health-WIKI/` → `message`/`send_message` carries `_DIGEST_WIKI_UNKNOWN_RU` listing «Health»; **no** `request_explicit`, **no** confirm draft; `tg.pipeline.digest.wiki_unknown` logged with `token` ≈ `"money"`.
- «делай сводку каждый день в 9» (no WIKI name) → `wiki_scope == "all"` (today's behaviour, regression).

**GREEN** — in `pipeline.py` near `humanize_recurrence`/`build_digest_recap`:
```python
def _extract_wiki_names(text: str, owner_wiki_stems: Sequence[str]) -> list[str] | Literal["all"] | None:
    """Whole-token, case-insensitive ∩ with the owner's *-WIKI/ dir-stems.
    All mentioned names resolve → list[str]; none mentioned → 'all';
    a name-shaped token after 'по'/'сводк* по' that does not resolve → None (caller clarifies)."""
    stems = {s.lower(): s for s in owner_wiki_stems}
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё][\w-]*", text)
    hits = [stems[t.lower()] for t in tokens if t.lower() in stems]
    if hits:
        # de-dup, preserve first-seen order
        seen: dict[str, None] = {}
        for h in hits: seen.setdefault(h, None)
        return list(seen)
    # nothing resolved — is there a name-shaped token right after «по»? then it's an unresolved name
    if re.search(r"\bпо\s+([A-Za-zА-Яа-яЁё][\w-]+)", text) and " по " in f" {text.lower()} ":
        # heuristic: a «по X» where X is not a known stem AND looks like a proper noun → unresolved
        m = re.search(r"\bпо\s+([A-Za-zА-Яа-яЁё][\w-]+)", text)
        cand = m.group(1) if m else ""
        if cand and cand.lower() not in stems and cand[:1].isupper():
            return None
    return "all"
```
*(The exact "name-shaped unresolved" trigger is the one residual ambiguity flagged in the design self-review — this regex is the agreed concrete rule: a capitalised token after «по» that is not a known stem ⇒ clarify; everything else ⇒ `'all'`. Keep it small; do not try to be clever.)*

In `_handle_digest_intent`, after the recurrence parse and before building the confirm draft:
```python
        owner_stems = await _list_owner_wiki_stems(owner_telegram_id)  # via the resolver already wired
        scope = _extract_wiki_names(text, owner_stems)
        if scope is None:
            _log.info("tg.pipeline.digest.wiki_unknown", owner_telegram_id=owner_telegram_id)
            await sender.send_message(chat_id, _DIGEST_WIKI_UNKNOWN_RU.format(known=", ".join(owner_stems) or "—"))
            return
        # ... build draft as today, but draft.draft["wiki_scope"] = scope ; recap names the WIKIs when scope != "all"
```
On the confirm callback path (`_handle_digest_confirm`): pass `wiki_scope=draft["wiki_scope"]` into `create_digest_job(...)`. Extend `build_digest_recap` to append «по WIKI: <list>» when scoped. Add `_DIGEST_WIKI_UNKNOWN_RU = "Не нашёл такие WIKI. У тебя есть: {known}. Уточни, по каким делать сводку — или скажи «по всем»."`. Update `pipeline.py` MODULE_CONTRACT MAP/CHANGE (`aisw-269: digest fast-path _extract_wiki_names → wiki_scope in the confirm draft + recap; tg.pipeline.digest.wiki_unknown`).

**Verify:** `uv run pytest tests/unit/tg -q` ✅; `make lint` ✅; `grace lint --failOn errors` → 0.
**Commit:** `feat(M-TG-PIPELINE-CLASSIFIER): named-subset WIKI in the digest fast-path (aisw-269)`.

---

## Step 10 — Review + GRACE refresh + close

1. `make total-test` (lint + grace + inv-lint + coverage ≥80% + integration if wired) — or at minimum `make lint` ✅, `grace lint --failOn errors` → 0, `uv run pytest tests/unit -q` ✅ with coverage ≥80%, `python -c "import ai_steward_wiki.__main__"` ✅.
2. `grace-refresh` (full) + `grace-refresh --verify` — re-derive `knowledge-graph.xml` from the updated MODULE_CONTRACTs and `verification-plan.xml` from the new tests/anchors; reconcile with the hand-edits made in this plan.
3. `grace-reviewer` full-integrity + `code-reviewer` agent on the diff.
4. Completion report → `docs/reports/20260512-inbox-wiki-digest-interactive-report.md` (FR coverage table FR-1..4, verification evidence, the cards+toggles deferral note, commits).
5. File the two deferral bds: `bd create --type=feature` "Inbox-WIKI digest: actionable inline cards (medication/event/pending) — needs a job-category model or a long-lived 'needs your answer' queue" (depends aisw-269) ; "Inbox-WIKI digest: per-user section toggles (tracker/wiki on/off) + user_digest_prefs + alembic/sessions/0002" (depends aisw-269).
6. `smart-commit` the meta files (ADR-025, report, knowledge-graph/verification-plan/development-plan refresh, the discovery+design+plan docs if not yet committed).
7. `bd close aisw-269 --reason="digest interactive surface (FR-1..4) — /digest_now, /expand, named-subset WIKI; cards + toggles deferred to new bds"` ; `bd close --suggest-next` (unblocks `aisw-19o` once its other deps are done).

## Self-review checklist (writing-plans)
- [x] Every MODULE_CONTRACT touched → has step(s): M-STORAGE-JOBS (S1), M-SCHEDULER-FIRING (S2-4,7), M-RUNTIME-WIRING (S6), M-TG-HANDLERS-WIRING (S8), M-TG-PIPELINE-CLASSIFIER (S9).
- [x] Every FR covered: FR-1 (/digest_now) → S7+S8; FR-2 (/expand) → S3,S5,S6,S7,S8; FR-3 (named-subset WIKI) → S1,S2,S4,S9; FR-4 (ADR-025 + GRACE) → ADR written, GRACE edits in S1-9 + S10 refresh.
- [x] Every NFR has a check: NFR-1 (no dep/migration) — none added; NFR-4 (types/Pydantic/structlog) — mypy in `make lint` every step + the new anchors; NFR-5 (catch-all) — S8 handler try/except, S4 no-strike branch; NFR-6 (TDD ≥80%) — RED→GREEN each step + coverage gate S10; NFR-7 (one window) — phase est. ~35-45%.
- [x] Verification plan reflected: new anchors `scheduler.digest.scope_filter`, `tg.command.digest_now*`, `tg.command.expand*`, `tg.pipeline.digest.wiki_unknown` are in `verification-plan.xml` and asserted by the step tests.
- [x] ADR decisions implemented: slash-router-before-catch-all (the `~F.text.startswith("/")` filter already does this; S8 adds the Command handlers), reuse `fire_digest_job` not `_run_digest` (S7), scoped re-run via `DigestRunner(section=)` (S3,S6), `wiki_scope` union no-migration (S1).
- [x] Task order respects DEPENDS: payload (S1) → create_digest_job (S2) → DigestRunner Protocol (S3) → fire_digest_job filter (S4) → prompt (S5) → adapter (S6) → firing accessors (S7) → handlers (S8) → fast-path (S9) → review (S10).
- [x] No placeholders — the one design-flagged ambiguity (`_extract_wiki_names` trigger) is pinned to the concrete regex in S9.
