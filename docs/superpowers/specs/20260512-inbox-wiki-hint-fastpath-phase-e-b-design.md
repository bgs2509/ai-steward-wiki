---
feature: inbox-wiki-hint-fastpath
bd_id: aisw-5sd
epic: aisw-t2r
phase: "Inbox-WIKI Phase-E.b"
status: draft
created: 2026-05-12
discovery: docs/superpowers/specs/20260512-inbox-wiki-hint-fastpath-phase-e-b-discovery.md
decision: "OQ-A → Option A (deterministic keyword/token-overlap, single-unambiguous-match threshold + margin). Confirmed at Brainstorming gate 2026-05-12."
technology:
  decisions:
    - id: T-1
      text: "New pure module inbox/hint_match.py: score_catalog(text: str, catalog: Mapping[str, str]) -> HintMatch where HintMatch is a small frozen dataclass {ranked: list[tuple[str, float]] (stem, score, desc), top_stem: str | None, top_score: float, margin: float}. Scoring = normalised token-overlap: tokens(text) and tokens(hint) are lowercased word-runs (regex r'\\w+' over the casefolded string, drop tokens shorter than MIN_TOKEN_LEN=3 and a tiny ru/en stop-word set); score(stem) = |tokens(text) ∩ tokens(hint_stem)| / max(1, |tokens(hint_stem)|); the domain stem name itself (minus the '-WIKI' suffix, casefolded, split on non-word) is folded into that domain's token set so 'health'/'здоровье' in the message also counts. Pure, deterministic, no IO."
    - id: T-2
      text: "‘Confident’ predicate (in hint_match.py): is_confident(m: HintMatch) -> bool := m.top_stem is not None AND m.top_score >= HINT_FASTPATH_MIN_SCORE AND m.margin >= HINT_FASTPATH_MIN_MARGIN, where margin = top_score − second_score (second_score = 0.0 if only one domain scored > 0). Default constants live in inbox/hint_match.py as module-level Final[float]: MIN_SCORE = 0.34, MIN_MARGIN = 0.17 (tunable; chosen conservative per NFR-2 precision-over-recall — re-tuned offline from the tg.pipeline.hint_fastpath.* logs). NOT exposed via settings.py in MVP (D-032 / YAGNI — no env knob until logs justify one)."
    - id: T-3
      text: "Catalog build: a factory in __main__ — make_hint_catalog_resolver(*, hint_repo: InboxHintCacheRepo, owner_wikis_resolver: OwnerWikisResolver, surrogate_id_of: Callable[[int], Awaitable[int | None]]) -> Callable[[int], Awaitable[dict[str, str]]]. The returned async resolver: uid = await surrogate_id_of(telegram_id); if uid is None → return {}; for (stem, dir_path) in await owner_wikis_resolver(telegram_id): hint = await get_or_refresh_hint(hint_repo, uid, dir_path / 'CLAUDE.md'); if hint: catalog[stem] = hint; return catalog. Reuses the existing get_or_refresh_hint stat→cache-hit path verbatim (0 fs reads on a hot message). surrogate_id_of is the existing users-table telegram_id→user_id lookup that __main__ already builds (the same surrogate every other sessions.db row uses — D-042); if no such helper is exposed yet, add a one-liner read over the users table in storage/sessions next to the engine setup."
    - id: T-4
      text: "DefaultPipeline gains ONE new optional kwarg: hint_catalog_resolver: Callable[[int], Awaitable[Mapping[str, str]]] | None = None (stored as self._hint_catalog_resolver). None ⇒ the fast-path block is a no-op fall-through — keeps every existing pipeline unit test green without the new dep. No other constructor change; owner_wikis_resolver stays as-is (the catalog resolver wraps it internally — the pipeline does not re-enumerate WIKIs)."
    - id: T-5
      text: "New block START_BLOCK_HINT_FASTPATH … END_BLOCK_HINT_FASTPATH in tg/pipeline.py _run_text_pipeline, placed AFTER END_BLOCK_REMINDER_FASTPATH and BEFORE START_BLOCK_ROUTABLE_BRANCH. Guard: `if result.intent in _ROUTABLE_INTENTS and self._router is not None and self._hint_catalog_resolver is not None and self._librarian is not None and self._output is not None:` (the librarian/output guard mirrors START_BLOCK_ROUTABLE_BRANCH's confirm-path guard — no point bypassing the router if we cannot then run the confirm+ingest). Inside: catalog = await self._hint_catalog_resolver(telegram_id); if not catalog → log tg.pipeline.hint_fastpath.miss(reason='empty_catalog') and fall through. Else m = score_catalog(result.distilled_payload or text, catalog); log tg.pipeline.hint_fastpath.catalog(n_domains=len(catalog)). If not is_confident(m) → log tg.pipeline.hint_fastpath.miss(reason='ambiguous' if m.top_stem else 'no_match', top_stem, top_score, margin) and fall through. Else: synthesise decision = RouterDecision(intent=RouterIntent.ROUTE, target_wiki=m.top_stem, notes=<short ru recap>, parsed_ok=True, …) — exact field set mirrored from what _RouterAdapter returns for a ROUTE; payload = route_action_to_payload(decision, user_text=text, source=source, media_paths=media_paths, correlation_id=correlation_id); confirm_draft = PendingConfirmDraft(telegram_id, chat_id, category='route_ingest', draft=payload, recap_text=build_route_recap(decision)); rec = await self._confirm.request_explicit(confirm_draft, keyboard_factory=build_route_confirm_keyboard); log tg.pipeline.hint_fastpath.hit(target_wiki=m.top_stem, score=m.top_score, margin=m.margin, pending_id=rec.pending_id); return. (Falls through ⇒ START_BLOCK_ROUTABLE_BRANCH runs verbatim.)"
    - id: T-6
      text: "Constructing a stand-in RouterDecision: read RouterDecision's field set from inbox/router.py at execution time and fill the minimum for a ROUTE — intent, target_wiki, parsed_ok=True, notes=ru one-liner ('Похоже на запись для «{stem}». Подтвердите перенос.' — wording finalised against the existing build_route_recap output so the recap reads naturally), and any other required fields with their natural defaults (e.g. an empty/structured payload field if RouterDecision has one). If RouterDecision has a required field we cannot meaningfully synthesise (e.g. a raw-CLI-output blob) → that is a deviation: stop, re-evaluate (advisory gate). route_action_to_payload / on_confirm_callback / Stage-1b librarian consume it unchanged."
    - id: T-7
      text: "structlog anchors (tg/pipeline.py, all under correlation_id + telegram_id): tg.pipeline.hint_fastpath.catalog(n_domains: int); tg.pipeline.hint_fastpath.hit(target_wiki, score, margin, pending_id); tg.pipeline.hint_fastpath.miss(reason: 'empty_catalog'|'no_match'|'ambiguous', top_stem: str|None, top_score: float, margin: float); tg.pipeline.hint_fastpath.fallthrough(reason) — emitted whenever we leave the block without short-circuiting (covers the guard-not-satisfied case at block top and the miss cases, so 'every bypass attempt observable' per FR-6). hint_text content is NEVER logged (it can quote arbitrary user-authored CLAUDE.md prose) — only stems, counts, scores. PIIRedactor already wraps the logger; no new redact rule needed (scores/counts/stems are non-PII)."
    - id: T-8
      text: "No new third-party dependency, no new SQLite table, no new Alembic migration, no new user-facing string beyond the synthesised RouterDecision.notes ru one-liner (which is internal recap text, reusing the existing route-confirm copy family — D-032 ru-only honoured), no prompt change, no /wiki_* surface change (D-041), no change to _RouterAdapter / prompts/inbox.md / the heavy Router path. ops/retention and the media track are untouched."
    - id: T-9
      text: "Tests — all with fakes, no live Telegram/Claude/APScheduler/fs-outside-tmp_path: (a) tests/unit/inbox/test_hint_match.py — score_catalog on a hand-built catalog: single clear winner above thresholds → is_confident True; two domains both above MIN_SCORE within MIN_MARGIN → is_confident False (ambiguous); winner below MIN_SCORE → False (no_match); empty catalog → top_stem None; stem-name folding ('здоровье'/'health' in text matches Health-WIKI even if absent from the hint body); token normalisation (case, short-token drop, stop-words). (b) tests/unit/tg/test_pipeline_hint_fastpath.py — DefaultPipeline built with a fake hint_catalog_resolver returning a curated catalog + a fake confirm + a stub router whose .route is asserted NOT called on a confident hit and asserted called on miss/ambiguous/empty/disabled; assert a route_ingest PendingConfirmDraft was requested with category='route_ingest' on hit; assert the four log anchors fire on their respective paths (caplog/structlog capture). (c) existing tests/unit/tg/test_pipeline*.py untouched (new kwarg defaults to None). Coverage stays ≥80%."
    - id: T-10
      text: "grace-refresh + grace-refresh --verify after the change: knowledge-graph.xml — new module inbox/hint_match.py (M-INBOX-HINT-MATCH or folded into M-INBOX per the existing granularity; score_catalog/is_confident/HintMatch exports), DefaultPipeline DEPENDS += inbox.hint_match.score_catalog/is_confident + inbox.hint_cache.get_or_refresh_hint (transitively via the resolver dep), __main__ exports make_hint_catalog_resolver. verification-plan.xml — new test modules + the tg.pipeline.hint_fastpath.* log markers. Also clears the Phase-D-era 'hint fast-path' deferred note now that the cache is wired as a catalog."
---

# Phase-E.b — Inbox-WIKI `## Inbox hint` fast-path (router-bypass) — Design

> bd **aisw-5sd** · epic **aisw-t2r** · split from **aisw-12t** (Phase-E bit 1). Discovery: `docs/superpowers/specs/20260512-inbox-wiki-hint-fastpath-phase-e-b-discovery.md`.
> **OQ-A decided: Option A** — deterministic keyword/token-overlap matching with a narrow single-unambiguous-match threshold + margin. Precision over recall (NFR-2): a wrong guess costs one extra "Отмена" tap, never a wrong file move (the user-confirm step stays mandatory).

## 1. Goal

Add a third pre-router fast-path in `tg/pipeline.py._run_text_pipeline` (alongside the existing reminder `aisw-kcz` and digest `aisw-269` ones): for a routable Stage-0 intent, score the incoming content against the cached `## Inbox hint` catalog of the sender's domain WIKIs; on a *confident* single match, synthesise a `RouterDecision(ROUTE, target_wiki=<stem>)` and feed the **existing** Phase-C confirm loop — skipping the ~10–30 s + ~3500-token Sonnet Router-Claude run. On no/ambiguous match → fall through to today's heavy Router unchanged. (tech-spec §4, §8.3.3; D-004, D-016.)

## 2. Flow (after)

```
_run_text_pipeline (tg/pipeline.py):
  classify (Stage-0 Haiku)
   └─ START_BLOCK_REMINDER_FASTPATH   (intent=REMINDER → handled, return)        [unchanged]
   └─ START_BLOCK_HINT_FASTPATH (NEW):
        guard: intent ∈ _ROUTABLE_INTENTS ∧ router ∧ hint_catalog_resolver ∧ librarian ∧ output
        catalog = await hint_catalog_resolver(telegram_id)        # __main__ factory:
                                                                  #   uid = surrogate_id_of(tid)
                                                                  #   for (stem, dir) in owner_wikis_resolver(tid):
                                                                  #     hint = get_or_refresh_hint(repo, uid, dir/CLAUDE.md)   ← cached, 0 fs reads on hit
        if not catalog: log .miss(empty_catalog); fall through
        m = score_catalog(distilled_payload or text, catalog)     # inbox/hint_match.py — pure
        log .catalog(n_domains)
        if not is_confident(m): log .miss(no_match|ambiguous, …); fall through
        decision = RouterDecision(ROUTE, target_wiki=m.top_stem, notes=ru-recap, parsed_ok=True, …)
        payload  = route_action_to_payload(decision, user_text=text, source, media_paths, correlation_id)   [reused verbatim]
        draft    = PendingConfirmDraft(tid, chat, category="route_ingest", draft=payload, recap_text=build_route_recap(decision))   [reused]
        rec = await confirm.request_explicit(draft, keyboard_factory=build_route_confirm_keyboard)            [reused]
        log .hit(target_wiki, score, margin, pending_id); return
   └─ START_BLOCK_ROUTABLE_BRANCH     (heavy Sonnet Router → same route_action_to_payload → confirm)         [unchanged; runs on fall-through]
   └─ legacy runner branch

(on user confirm) on_confirm_callback → Stage-1b librarian → move+ingest                                     [unchanged]
```

## 3. Module deltas

| Module | Change | Contract impact |
|---|---|---|
| `inbox/hint_match.py` *(new)* | `score_catalog(text, catalog) -> HintMatch`; `is_confident(m) -> bool`; `HintMatch` frozen dataclass; `MIN_TOKEN_LEN`, `MIN_SCORE`, `MIN_MARGIN` `Final`. Pure, no IO. | new MODULE_CONTRACT (PURPOSE: deterministic hint-catalog token-overlap scoring; SCOPE: scoring + confidence predicate; DEPENDS: stdlib only; ROLE: RUNTIME); MODULE_MAP; `__all__` |
| `inbox/hint_cache.py` | unchanged (the catalog resolver calls `get_or_refresh_hint` verbatim) | — |
| `tg/pipeline.py` | `DefaultPipeline(..., hint_catalog_resolver: Callable[[int], Awaitable[Mapping[str,str]]] | None = None)`; new `START_BLOCK_HINT_FASTPATH` between reminder fast-path and routable branch | MODULE_CONTRACT DEPENDS += `inbox.hint_match.score_catalog/is_confident`; CHANGE_SUMMARY bump; new optional kwarg only (no public-API break) |
| `__main__.py` | `make_hint_catalog_resolver(*, hint_repo, owner_wikis_resolver, surrogate_id_of)`; construct `InboxHintCacheRepo(sessions_session_maker)`; resolve `surrogate_id_of` (existing users-table `telegram_id→user_id` lookup, or add a 1-liner); pass `hint_catalog_resolver=…` into `DefaultPipeline` | MODULE_MAP += `make_hint_catalog_resolver`; CHANGE_SUMMARY bump |
| `settings.py` | none (thresholds are module constants, not env — YAGNI/D-032) | — |
| `prompts/inbox.md`, `inbox/router.py` (`_RouterAdapter`) | none — heavy Router path untouched | — |

## 4. Tests (TDD order)

1. `tests/unit/inbox/test_hint_match.py::test_score_catalog_*` — RED first: single clear winner → confident; ambiguous (two over `MIN_SCORE`, gap < `MIN_MARGIN`) → not confident; weak winner (< `MIN_SCORE`) → not confident; empty catalog → `top_stem is None`; stem-name folding; token normalisation (case / short-token drop / stop-words).
2. `tests/unit/inbox/test_hint_match.py::test_is_confident_thresholds` — boundary values around `MIN_SCORE` / `MIN_MARGIN`.
3. `tests/unit/tg/test_pipeline_hint_fastpath.py` — `DefaultPipeline` with a fake `hint_catalog_resolver` + fake `confirm` + stub `router`:
   - confident hit → `router.route` NOT awaited; a `category="route_ingest"` `PendingConfirmDraft` requested; `tg.pipeline.hint_fastpath.{catalog,hit}` logged; returns.
   - ambiguous / no_match / empty_catalog → `router.route` IS awaited (fall-through); `…hint_fastpath.{miss,fallthrough}` logged.
   - `hint_catalog_resolver=None` (or librarian/output `None`) → fall-through, `router.route` awaited.
4. Existing `tests/unit/tg/test_pipeline*.py` — untouched (new kwarg defaults to `None`); spot-run to confirm green.
5. Full gate: `make lint` (ruff / ruff-format / mypy --strict) + grace lint + `make total-test` semantics (inv-lint + coverage ≥ 80% + integration where applicable; integration skipped under `CLAUDECODE=1`).
6. `grace-refresh` + `grace-refresh --verify` → knowledge-graph.xml + verification-plan.xml.

## 5. Non-goals (this iteration)

Boot-time / periodic hint-cache warm-up job (lazy `get_or_refresh_hint` suffices for MVP — revisit if cold-catalog latency shows in logs); fs-watch invalidation (the `(size, mtime)` stat in `get_or_refresh_hint` is enough); any change to the heavy Inbox Router (`prompts/inbox.md`, `_RouterAdapter`); an env knob for the thresholds; the unrelated media-track deferrals (vision timeout, captions, `video_note` ext, `ops/retention.purge_staging`).

## 6. Open items carried to Plan / Execution

- **OQ-B (surrogate `user_id`)**: `make_hint_catalog_resolver` takes `surrogate_id_of: Callable[[int], Awaitable[int|None]]`. At plan time, locate the existing `telegram_id→user_id` lookup `__main__` uses for other sessions.db rows (D-042 says `users` is "the ONLY table holding both"); if none is exposed as a reusable callable, add a minimal async read over `users` in `storage/sessions/`. No migration, no schema change.
- **OQ-C (scoring details)**: tokenisation = `re.findall(r"\w+", s.casefold())`, drop tokens with `len < MIN_TOKEN_LEN(=3)` and a small ru/en stop-word frozenset (`{"и","в","на","для","с","по","the","a","to","of","for","and"}` — extend in PR if needed); stem-name folding as in T-1; `MIN_SCORE=0.34`, `MIN_MARGIN=0.17` — finalised in the plan, all tunable, logged for offline re-tuning.
- **T-6 deviation guard**: if `RouterDecision` turns out to have a required field that cannot be meaningfully synthesised for a stand-in ROUTE → advisory gate (stop, re-evaluate) rather than faking a value.

Spec refs: `docs/Spec-WIKI/research/tech-spec-draft.md` §4, §8.3.3; `docs/Spec-WIKI/concepts/smart-inbox-routing.md`; `docs/Spec-WIKI/entities/{inbox-wiki,router-agent}.md`; `D-004`, `D-016`, `D-032`, `D-041`, `D-042`.
