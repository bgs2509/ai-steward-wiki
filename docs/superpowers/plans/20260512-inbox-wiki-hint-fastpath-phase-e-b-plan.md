# Implementation Plan — Inbox-WIKI Phase-E.b: `## Inbox hint` fast-path (router-bypass)

> bd **aisw-5sd** · epic **aisw-t2r** · `dev` project · GRACE + feature-workflow
> Discovery: `docs/superpowers/specs/20260512-inbox-wiki-hint-fastpath-phase-e-b-discovery.md`
> Design: `docs/superpowers/specs/20260512-inbox-wiki-hint-fastpath-phase-e-b-design.md` (OQ-A → **Option A**, deterministic token-overlap)
> TDD throughout: RED → verify fail → GREEN → verify pass → REFACTOR. Commit after each validated step. Conventional Commits + GRACE MODULE_ID scope.

## Context snapshot (verified this session)

- `tg/pipeline.py` (~2134 lines): `_run_text_pipeline` = `classify` → `START_BLOCK_REMINDER_FASTPATH` (`return` on REMINDER) → `START_BLOCK_ROUTABLE_BRANCH` (`if result.intent in _ROUTABLE_INTENTS and self._router is not None:` → `await self._router.route(...)` → on `RouterIntent.ROUTE`/`CREATE_WIKI` **and** `self._librarian is not None` **and** `self._output is not None` → `route_action_to_payload(decision, user_text=text, source=source, media_paths=media_paths, correlation_id=correlation_id)` → `PendingConfirmDraft(telegram_id, chat_id, category="route_ingest", draft=payload, recap_text=build_route_recap(decision))` → `self._confirm.request_explicit(confirm_draft, keyboard_factory=build_route_confirm_keyboard)` → `return`) → legacy runner branch.
- `inbox/hint_cache.py`: `InboxHintCacheRepo(session_maker)` (`.get/.upsert/.invalidate`) + `async get_or_refresh_hint(repo, user_id: int, claude_md_path: Path) -> str | None` (stat→cache-hit on `(size, mtime)`; else read+sha256+`extract_inbox_hint`+upsert; logs `inbox.hint_cache.{hit,refresh,invalidate_missing}`). `__all__ = ["InboxHintCacheRepo", "get_or_refresh_hint"]`. **Built, unwired.**
- `inbox/parser.py`: `extract_inbox_hint(text) -> str | None`.
- `storage/sessions/models.py`: `InboxHintCache(user_id FK→users.user_id, wiki_path, size_bytes, mtime_ns, ctime_ns, content_sha256, hint_text, refreshed_at_utc)`, `UniqueConstraint("user_id","wiki_path")`. `users(user_id PK autoincrement, telegram_id BigInteger unique index, …)` — "the ONLY table holding both" (D-042).
- `__main__.py`: `_resolve_owner_wikis_factory(wiki_root) -> Callable[[int], Awaitable[list[tuple[str, Path]]]]` (owner_telegram_id → `[(wiki_dir_name, path), …]` minus `Inbox-WIKI`); already injected into `DefaultPipeline` as `owner_wikis_resolver` (used by digest fast-path). `DefaultPipeline` does **not** currently get an `InboxHintCacheRepo`.
- `inbox/router.py`: `RouterDecision`, `RouterIntent` — exact field set to be read at Step 4.
- `inbox/route.py`: `route_action_to_payload`. `tg/...`: `PendingConfirmDraft`, `build_route_recap`, `build_route_confirm_keyboard`, `ConfirmationService.request_explicit` — all imported into `pipeline.py` already.
- Tests: `tests/unit/inbox/`, `tests/unit/tg/test_pipeline*.py`. Run: `uv run pytest tests/unit -q`. Gate: `make lint` (= `ruff-check ruff-format-check mypy`), `grace lint --failOn errors`, `make total-test`. Integration skipped under `CLAUDECODE=1`.

---

## Step 0 — Branch + claim

```bash
git checkout -b feat/aisw-5sd-hint-fastpath
bd update aisw-5sd --status=in_progress   # already done this session
```
*(Working on `master`; create the branch before any edit.)*

**Verify:** `git branch --show-current` → `feat/aisw-5sd-hint-fastpath`.

---

## Step 1 — `inbox/hint_match.py` — pure scorer + confidence predicate (TDD)

### 1a RED — `tests/unit/inbox/test_hint_match.py`

Create the test file. Cases (use `pytest.mark.parametrize` where natural):

- `test_score_catalog_single_clear_winner` — catalog `{"Health-WIKI": "давление, анализы, лекарства, врач, симптомы", "Investment-WIKI": "акции, дивиденды, портфель, брокер"}`, text `"давление 130 на 85 утром, выпил лекарство"` → `m.top_stem == "Health-WIKI"`, `m.top_score >= MIN_SCORE`, `m.margin >= MIN_MARGIN`, `is_confident(m) is True`.
- `test_score_catalog_ambiguous` — text containing tokens from two domains roughly equally so both score `> MIN_SCORE` and `margin < MIN_MARGIN` → `is_confident(m) is False`; `m.top_stem` is whichever ranked first (non-None).
- `test_score_catalog_weak_winner` — text barely overlapping → `m.top_score < MIN_SCORE` → `is_confident(m) is False`.
- `test_score_catalog_empty_catalog` — `score_catalog("давление", {})` → `m.top_stem is None`, `m.ranked == []`, `is_confident(m) is False`.
- `test_score_catalog_stem_name_folding` — catalog `{"Health-WIKI": "записи о самочувствии"}` (no "health"/"здоровье" in body), text `"здоровье: спал плохо"` → `Health-WIKI` still scores (stem "Health" folded; and the ru gloss is *not* auto-added — stem folding only covers the English stem token, so this case asserts `"health"` in text matches; add a sibling case where the text says `"health checkup today"` to keep it deterministic without a translation table). *(If a ru↔en gloss is wanted later it's a separate enhancement — out of scope.)*
- `test_score_catalog_token_normalisation` — uppercase / 1–2-char tokens / stop-words (`и`, `в`, `the`, `a`) are dropped: `score_catalog("И В THE Анализы", {"Health-WIKI": "анализы"})` scores as if text were just `"анализы"`.
- `test_is_confident_boundary` — construct `HintMatch` instances straddling `MIN_SCORE` and `MIN_MARGIN` exactly (≥ passes, just-below fails).

Run: `uv run pytest tests/unit/inbox/test_hint_match.py -q` → **fails** (module missing). Record the failure.

### 1b GREEN — `src/ai_steward_wiki/inbox/hint_match.py`

```
# FILE: src/ai_steward_wiki/inbox/hint_match.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Deterministic '## Inbox hint' catalog token-overlap scoring + a conservative 'confident-single-match' predicate for the pre-router fast-path (D-016, tech-spec §4/§8.3.3).
#   SCOPE: tokenisation/normalisation; score_catalog (text vs {stem: hint_text}); is_confident; HintMatch; threshold constants.
#   DEPENDS: stdlib only (re, dataclasses, typing)
#   LINKS: D-004, D-016, tech-spec §4, §8.3.3, M-INBOX, M-TG-PIPELINE-CLASSIFIER
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   HintMatch - frozen result: ranked [(stem, score)], top_stem, top_score, margin
#   score_catalog - text vs {stem: hint_text} → HintMatch (normalised token-overlap; stem name folded in)
#   is_confident - True iff top_stem and top_score >= MIN_SCORE and margin >= MIN_MARGIN
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - initial deterministic hint-match scorer (aisw-5sd, Phase-E.b)
# END_CHANGE_SUMMARY
```

Implementation:

- `MIN_TOKEN_LEN: Final[int] = 3`
- `_STOP_WORDS: Final[frozenset[str]] = frozenset({"и","в","на","для","с","по","от","до","за","the","a","an","to","of","for","and","or","in","on"})`
- `MIN_SCORE: Final[float] = 0.34`
- `MIN_MARGIN: Final[float] = 0.17`
- `def _tokens(s: str) -> frozenset[str]:` → `frozenset(t for t in re.findall(r"\w+", s.casefold()) if len(t) >= MIN_TOKEN_LEN and t not in _STOP_WORDS)`
- `def _stem_tokens(stem: str) -> frozenset[str]:` → drop a trailing `-WIKI`/`-wiki`, then `_tokens(<rest>)` (so `Health-WIKI` → `{"health"}`).
- `@dataclass(frozen=True, slots=True) class HintMatch:` fields `ranked: tuple[tuple[str, float], ...]`, `top_stem: str | None`, `top_score: float`, `margin: float`. `__post_init__` not needed.
- `def score_catalog(text: str, catalog: Mapping[str, str]) -> HintMatch:`
  - `txt = _tokens(text)`; if `not catalog` → `HintMatch((), None, 0.0, 0.0)`.
  - per stem: `dom = _tokens(hint_text) | _stem_tokens(stem)`; `score = (len(txt & dom) / len(dom)) if dom else 0.0`.
  - `ranked = tuple(sorted(((stem, sc) for stem, sc in scores.items()), key=lambda kv: (-kv[1], kv[0])))` (stable: score desc, then stem asc).
  - `top_stem, top_score = ranked[0]` (ranked is non-empty since catalog non-empty); `second = ranked[1][1] if len(ranked) > 1 else 0.0`; `margin = top_score - second`.
  - return `HintMatch(ranked, top_stem, top_score, margin)`.
- `def is_confident(m: HintMatch) -> bool:` → `m.top_stem is not None and m.top_score >= MIN_SCORE and m.margin >= MIN_MARGIN`.
- `__all__ = ["HintMatch", "MIN_MARGIN", "MIN_SCORE", "MIN_TOKEN_LEN", "is_confident", "score_catalog"]`

*If the RED test thresholds don't fit `0.34/0.17` cleanly for the hand-built catalogs, adjust the **test fixtures** (richer hint strings) before touching the constants — the constants are the conservative product choice, not a test artefact.*

Run: `uv run pytest tests/unit/inbox/test_hint_match.py -q` → **passes**.

### 1c REFACTOR + gate

`uv run ruff check src/ai_steward_wiki/inbox/hint_match.py tests/unit/inbox/test_hint_match.py && uv run ruff format --check . && uv run mypy src && grace lint --failOn errors`

### 1d Commit

```
test(M-INBOX): RED — hint_match scorer + confidence predicate (aisw-5sd)
feat(M-INBOX): hint_match.py — deterministic '## Inbox hint' token-overlap scorer (aisw-5sd)
```
*(Two commits, or one `feat` with the test included — match the repo's prevailing TDD-commit habit; check `git log` for which.)*

---

## Step 2 — `__main__.make_hint_catalog_resolver` + wiring (TDD where it has logic)

### 2a Locate the surrogate-id lookup (OQ-B)

`grep -rn "telegram_id" src/ai_steward_wiki/__main__.py src/ai_steward_wiki/auth/ src/ai_steward_wiki/storage/sessions/` — find how `__main__` already turns a `telegram_id` into the surrogate `users.user_id` for other sessions.db rows (pending_confirms etc. use `telegram_id` directly; `inbox_hint_cache` is the one that needs `user_id`). Two acceptable outcomes:
  1. A reusable async helper exists → pass it as `surrogate_id_of`.
  2. None exists → add a minimal `async def surrogate_user_id(session_maker, telegram_id) -> int | None:` doing `select(User.user_id).where(User.telegram_id == telegram_id)` in `storage/sessions/` (next to `engine.py`), with a tiny MODULE_MAP line. **No migration, no schema change.** Record which path was taken in the bd notes (advisory-gate-worthy only if it turns into more than a one-liner).

### 2b `make_hint_catalog_resolver` in `__main__.py`

```python
def make_hint_catalog_resolver(
    *,
    hint_repo: InboxHintCacheRepo,
    owner_wikis_resolver: Callable[[int], Awaitable[Sequence[tuple[str, Path]]]],
    surrogate_id_of: Callable[[int], Awaitable[int | None]],
) -> Callable[[int], Awaitable[dict[str, str]]]:
    """telegram_id → {wiki_stem: hint_text} from the cached '## Inbox hint' of each domain WIKI (aisw-5sd)."""
    async def _resolve(telegram_id: int) -> dict[str, str]:
        uid = await surrogate_id_of(telegram_id)
        if uid is None:
            return {}
        out: dict[str, str] = {}
        for stem, dir_path in await owner_wikis_resolver(telegram_id):
            hint = await get_or_refresh_hint(hint_repo, uid, dir_path / "CLAUDE.md")
            if hint:
                out[stem] = hint
        return out
    return _resolve
```
Add `make_hint_catalog_resolver` to `__main__`'s MODULE_MAP; bump its CHANGE_SUMMARY.

### 2c Wire into `DefaultPipeline`

In `__main__` where `DefaultPipeline(...)` is constructed (near `owner_wikis_resolver = _resolve_owner_wikis_factory(settings.wiki_root)`):
```python
hint_repo = InboxHintCacheRepo(sessions_session_maker)   # use the existing sessions async_sessionmaker var name
hint_catalog_resolver = make_hint_catalog_resolver(
    hint_repo=hint_repo,
    owner_wikis_resolver=owner_wikis_resolver,
    surrogate_id_of=<the resolver from 2a>,
)
# ... DefaultPipeline(..., owner_wikis_resolver=owner_wikis_resolver, hint_catalog_resolver=hint_catalog_resolver, ...)
```
Update `__main__`'s MODULE_CONTRACT DEPENDS if it enumerates deps (it lists `inbox.*` already; add `inbox.hint_match` is **not** needed here — only `pipeline.py` depends on it; `__main__` depends on `inbox.hint_cache.get_or_refresh_hint` + `InboxHintCacheRepo`, likely already listed via `M-INBOX`).

### 2d Test

Add `tests/unit/test_main_hint_catalog.py` (or extend an existing `__main__` test if one is there): a fake `hint_repo` (with `get_or_refresh_hint` monkeypatched or a real `InboxHintCacheRepo` over an in-memory sessions DB), a fake `owner_wikis_resolver` returning `[("Health-WIKI", tmp/"Health-WIKI"), ("Investment-WIKI", tmp/"Investment-WIKI")]` with `CLAUDE.md` files containing `## Inbox hint` sections, a `surrogate_id_of` returning `1` → resolver returns `{"Health-WIKI": "...", "Investment-WIKI": "..."}`; `surrogate_id_of` returning `None` → `{}`; a domain whose `CLAUDE.md` lacks the section → omitted from the dict.

If `__main__` is hard to unit-test in isolation (it usually is — it's the composition root), it's acceptable to test `make_hint_catalog_resolver` as the unit (it's a pure factory taking injectables) and leave the `DefaultPipeline(...)` call-site to the integration suite. Prefer the former.

Run: `uv run pytest tests/unit/test_main_hint_catalog.py -q` → passes.

### 2e gate + commit

`make lint && grace lint --failOn errors`
```
feat(M-RUNTIME-WIRING): make_hint_catalog_resolver + wire hint cache into DefaultPipeline (aisw-5sd)
```
*(Use the actual MODULE_ID of `__main__` from `knowledge-graph.xml` — looks like the runtime-wiring node; confirm.)*

---

## Step 3 — `DefaultPipeline` gains `hint_catalog_resolver` kwarg (no behaviour yet)

### 3a Edit `tg/pipeline.py` constructor

- Add param `hint_catalog_resolver: Callable[[int], Awaitable[Mapping[str, str]]] | None = None` to `DefaultPipeline.__init__` (place it next to `owner_wikis_resolver`); store `self._hint_catalog_resolver = hint_catalog_resolver`.
- Add `from collections.abc import Mapping` if not already imported; add `from ai_steward_wiki.inbox.hint_match import is_confident, score_catalog` (used in Step 4 — add now to keep one import edit).
- Update `pipeline.py` MODULE_CONTRACT DEPENDS: `+ ai_steward_wiki.inbox.hint_match (score_catalog, is_confident)`. Bump CHANGE_SUMMARY: `v?.?.? - aisw-5sd (Phase-E.b): '## Inbox hint' pre-router fast-path`.
- Update the `# START_CONTRACT: DefaultPipeline.__init__` doc (or wherever the ctor INPUTS are documented) with the new optional kwarg.

### 3b Verify nothing broke

`uv run pytest tests/unit/tg -q` → all green (new kwarg defaults to `None`, no behaviour change). `make lint && grace lint --failOn errors`.

### 3c Commit

```
refactor(M-TG-PIPELINE-CLASSIFIER): DefaultPipeline +hint_catalog_resolver kwarg (aisw-5sd)
```

---

## Step 4 — `START_BLOCK_HINT_FASTPATH` in `_run_text_pipeline` (TDD)

### 4a Read `RouterDecision`'s field set

`sed -n` over `inbox/router.py` for the `RouterDecision` definition. Note every **required** field. For a synthesised ROUTE we set: `intent=RouterIntent.ROUTE`, `target_wiki=m.top_stem`, `notes="Похоже на запись для «{stem}». Подтвердите перенос."` (final wording validated against `build_route_recap` output so the user-visible recap reads cleanly), `parsed_ok=True` (the field guarding "the router reply parsed" — a synthesised decision is trivially well-formed), and any other required field with its natural neutral default. **Deviation guard:** if `RouterDecision` has a required field that genuinely needs the raw Claude reply (e.g. a raw-output blob) and no sane default exists → STOP, surface "deviation: RouterDecision shape forces X — approve workaround Y?" before proceeding.

### 4b RED — `tests/unit/tg/test_pipeline_hint_fastpath.py`

Build a `DefaultPipeline` with: a stub `classifier` returning a `WIKI_INGEST` (routable) result with a `distilled_payload`; a stub `router` whose `.route` is an `AsyncMock` (asserted on); a fake `confirm` capturing `request_explicit` calls; `librarian` and `output` set to truthy stubs; `hint_catalog_resolver` an `AsyncMock` returning a curated catalog; a fake `sender`. Cases:

1. **confident hit** — catalog `{"Health-WIKI": "<rich health hint>", "Investment-WIKI": "<rich invest hint>"}`, payload clearly health → `router.route.assert_not_awaited()`; `confirm.request_explicit` called once with a `PendingConfirmDraft` whose `category == "route_ingest"` and `recap_text` truthy; the pipeline returned (no runner dispatch); logs include `tg.pipeline.hint_fastpath.catalog` and `tg.pipeline.hint_fastpath.hit` with `target_wiki="Health-WIKI"`.
2. **ambiguous** — payload overlapping both → `router.route.assert_awaited_once()`; logs include `tg.pipeline.hint_fastpath.miss` (`reason="ambiguous"`) + `…fallthrough`.
3. **no_match** — payload overlapping neither → `router.route.assert_awaited_once()`; `…miss` (`reason="no_match"`).
4. **empty catalog** — `hint_catalog_resolver` returns `{}` → `router.route.assert_awaited_once()`; `…miss` (`reason="empty_catalog"`); `score_catalog` not even consulted.
5. **disabled** — `hint_catalog_resolver=None` (or `librarian=None`) → `router.route.assert_awaited_once()`; `…fallthrough` logged (or block skipped silently — assert the router path, log assertion optional for the guard-skip case).

Log capture: use the project's existing structlog-capture fixture (grep `tests/` for `caplog`/`capture_logs`/a `structlog` fixture — the reminder/digest fast-path tests already assert on log events; mirror that).

Run → **fails** (block not implemented).

### 4c GREEN — insert the block

In `tg/pipeline.py._run_text_pipeline`, immediately after `# END_BLOCK_REMINDER_FASTPATH` and before `# START_BLOCK_ROUTABLE_BRANCH`:

```python
# START_BLOCK_HINT_FASTPATH (aisw-5sd, Inbox-WIKI Phase-E.b)
# Before the ~10-30s Sonnet Router-Claude run: if the sender's cached '## Inbox
# hint' catalog yields ONE unambiguous domain for this content, synthesise a
# ROUTE decision and feed the existing Phase-C confirm loop (tech-spec §8.3.3
# fast/heavy two-tier). Conservative: precision over recall — anything not a
# single confident match falls through to the heavy router unchanged. Never
# routes silently; the user still confirms via the route-confirm keyboard.
if (
    result.intent in _ROUTABLE_INTENTS
    and self._router is not None
    and self._hint_catalog_resolver is not None
    and self._librarian is not None
    and self._output is not None
):
    catalog = await self._hint_catalog_resolver(telegram_id)
    if not catalog:
        _log.info(
            "tg.pipeline.hint_fastpath.miss",
            correlation_id=correlation_id, telegram_id=telegram_id,
            reason="empty_catalog", top_stem=None, top_score=0.0, margin=0.0,
        )
        _log.info("tg.pipeline.hint_fastpath.fallthrough", correlation_id=correlation_id, telegram_id=telegram_id, reason="empty_catalog")
    else:
        match_text = result.distilled_payload or text
        m = score_catalog(match_text, catalog)
        _log.info("tg.pipeline.hint_fastpath.catalog", correlation_id=correlation_id, telegram_id=telegram_id, n_domains=len(catalog))
        if is_confident(m):
            decision = RouterDecision(
                intent=RouterIntent.ROUTE,
                target_wiki=m.top_stem,
                notes=f"Похоже на запись для «{m.top_stem}». Подтвердите перенос.",
                parsed_ok=True,
                # + any other required RouterDecision fields with neutral defaults (Step 4a)
            )
            payload = route_action_to_payload(
                decision, user_text=text, source=source,
                media_paths=media_paths, correlation_id=correlation_id,
            )
            confirm_draft = PendingConfirmDraft(
                telegram_id=telegram_id, chat_id=chat_id, category="route_ingest",
                draft=payload, recap_text=build_route_recap(decision),
            )
            rec = await self._confirm.request_explicit(confirm_draft, keyboard_factory=build_route_confirm_keyboard)
            _log.info(
                "tg.pipeline.hint_fastpath.hit",
                correlation_id=correlation_id, telegram_id=telegram_id,
                target_wiki=m.top_stem, score=m.top_score, margin=m.margin,
                pending_id=rec.pending_id, source=source,
            )
            return
        _log.info(
            "tg.pipeline.hint_fastpath.miss",
            correlation_id=correlation_id, telegram_id=telegram_id,
            reason="ambiguous" if m.top_stem is not None else "no_match",
            top_stem=m.top_stem, top_score=m.top_score, margin=m.margin,
        )
        _log.info("tg.pipeline.hint_fastpath.fallthrough", correlation_id=correlation_id, telegram_id=telegram_id, reason="not_confident")
# END_BLOCK_HINT_FASTPATH
```
*(Exact `RouterDecision(...)` kwargs per Step 4a. If `build_route_recap(decision)` needs fields beyond what we set — fill them; that's the same advisory-gate consideration.)*

Run `uv run pytest tests/unit/tg/test_pipeline_hint_fastpath.py -q` → **passes**. Then `uv run pytest tests/unit/tg -q` → all green.

### 4d REFACTOR

If the block is long, extract a private `async def _try_hint_fastpath(self, *, telegram_id, chat_id, text, source, media_paths, distilled_payload, correlation_id) -> bool` returning `True` if it short-circuited (caller does `if await self._try_hint_fastpath(...): return`). Mirrors `_handle_reminder_intent` / `_handle_digest_intent` style. Re-run tests.

### 4e gate + commit

`make lint && grace lint --failOn errors && uv run pytest tests/unit -q`
```
test(M-TG-PIPELINE-CLASSIFIER): RED — hint fast-path block (aisw-5sd)
feat(M-TG-PIPELINE-CLASSIFIER): '## Inbox hint' pre-router fast-path — confident-match router-bypass (aisw-5sd)
```

---

## Step 5 — Full quality gate + coverage

```bash
make lint                                  # ruff check + ruff format --check + mypy --strict
grace lint --failOn errors
uv run pytest tests/unit -q --cov=src/ai_steward_wiki --cov-report=term-missing   # ≥80% overall; new modules well-covered
# make total-test  (lint + grace + inv-lint + coverage + integration; integration auto-skips under CLAUDECODE=1)
```
Fix any drift (formatting from edits, mypy on the new kwarg, grace-lint on the new module/markers) **in this PR** — no `--no-verify`, no skipping.

---

## Step 6 — grace-refresh

```bash
grace lint   # confirm clean first
```
Then run the `grace-refresh` skill (full): regenerates `docs/knowledge-graph.xml` (new `score_catalog`/`is_confident`/`HintMatch` exports under M-INBOX; `M-TG-PIPELINE-CLASSIFIER` DEPENDS += `inbox.hint_match`; `M-RUNTIME-WIRING` MODULE_MAP += `make_hint_catalog_resolver`) and `docs/verification-plan.xml` (`--verify`: new test modules `tests/unit/inbox/test_hint_match.py`, `tests/unit/tg/test_pipeline_hint_fastpath.py`; new log markers `tg.pipeline.hint_fastpath.{catalog,hit,miss,fallthrough}`). Commit:
```
chore(knowledge-graph): refresh for aisw-5sd — hint_match scorer + pipeline fast-path + hint-catalog wiring
```

---

## Step 7 — Review (feature-workflow Step 12)

1. `grace-reviewer` full-integrity — contracts match code, graph synced, verification plan fulfilled.
2. `superpowers:code-reviewer` agent — quality / architecture / test coverage / requirements coverage (FR-1..FR-7, NFR-1..NFR-4).
3. `superpowers:verification-before-completion` — re-run `make lint`, `grace lint --failOn errors`, `uv run pytest tests/unit -q`, paste the actual output. No success claim without the output.
4. (No Sentrux — `.sentrux/rules.toml` absent.)

---

## Step 8 — Finish (feature-workflow Step 13)

1. `grace-refresh` (full) — final sync (re-run if Step 7 changed anything).
2. No ADR (no architectural deviation; OQ-A is within tech-spec §4/§8.3.3).
3. `_report` — `docs/reports/20260512-inbox-wiki-hint-fastpath-phase-e-b-report.md` (this is a meaningful feature: new fast-path on the hot path) — review results, test/coverage output, the chosen thresholds and the "tune from `hint_fastpath.*` logs" note.
4. Changelog: append to `docs/20260408_changelog.md` if that file is the project changelog (verify) — one line under a Phase-E.b heading.
5. `smart-commit` — commit remaining meta (report, changelog, any leftover graph bits): `docs(report): aisw-5sd Phase-E.b hint fast-path completion report`, `docs(changelog): hint fast-path (aisw-5sd)`.
6. `bd close aisw-5sd --reason="Phase-E.b hint fast-path shipped — deterministic token-overlap router-bypass with mandatory user confirm; thresholds MIN_SCORE=0.34/MIN_MARGIN=0.17 tunable from logs"` — then `bd show aisw-t2r` and consider `bd close aisw-t2r` if Phase-E.b was the last hanging sub-phase of the epic (separate decision, ask the user).
7. **Do not `git push`** — wait for explicit user request. `bd dolt push` is fine as part of session close.

---

## Self-review checklist (writing-plans)

- [x] Every MODULE_CONTRACT touched → has task(s): `hint_match.py` (Step 1), `pipeline.py` (Steps 3–4), `__main__` (Step 2).
- [x] Every FR covered: FR-1 (Step 4c block + Step 2 catalog), FR-2 (Option A — Step 1), FR-3 (Step 4c reuses confirm loop verbatim; `return` only after `request_explicit`), FR-4 (Step 4c placement between reminder fast-path and routable branch), FR-5 (Steps 2–3 dep + optional kwarg), FR-6 (Step 4c log anchors), FR-7 (Steps 1a, 4b tests + Step 5 coverage).
- [x] Every NFR has a verification step: NFR-1 reuse-only — no migration/table/dep added (Steps 1–4 add only stdlib + a pure module + a kwarg); NFR-2 precision-over-recall encoded in `is_confident` thresholds (Step 1) + tested ambiguous→fallthrough (Step 4b); NFR-3 `make lint`/`grace lint`/ru-only/UTC/no-bypass/isolation (Step 5); NFR-4 grace-refresh (Step 6).
- [x] Verification plan: new tests + log markers reflected (Step 6 `--verify`).
- [x] Log anchors from design (`tg.pipeline.hint_fastpath.*`) included (Step 4c).
- [x] ADR decisions: none required (noted Step 8.2).
- [x] Task order respects DEPENDS: scorer (1) → wiring (2) → kwarg (3) → block uses scorer+kwarg (4) → gate (5) → refresh (6) → review (7) → finish (8).
- [x] No placeholders — every step has concrete file paths, signatures, commands.
