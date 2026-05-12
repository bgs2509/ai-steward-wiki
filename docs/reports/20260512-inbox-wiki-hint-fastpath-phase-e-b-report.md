# Inbox-WIKI Phase-E.b — `## Inbox hint` fast-path (router-bypass) — Completion Report

**Date:** 2026-05-12
**Issue:** `aisw-5sd` (epic `aisw-t2r`) — split from `aisw-12t` (Phase-E bit 1)
**Status:** Done
**Branch:** `feat/aisw-5sd-hint-fastpath`
**Commits:** `16dc587` (hint_match scorer + docs), `4f79a39` (pipeline fast-path + wiring + tests + GRACE refresh), `<this>` (report)

## Problem

Every routable Stage-0 intent (`WIKI_INGEST` / `WIKI_QUERY` / `UNKNOWN`) ran the heavy Inbox-WIKI Router-Claude (Sonnet, ~10–30 s + ~3500 tokens) to pick the target `<Domain>-WIKI/`, even when the target was obvious from the one-line `## Inbox hint` of a domain WIKI. The hint cache (`sessions.inbox_hint_cache` + `inbox/hint_cache.py`, chunk 6) was built but unwired — nothing read it as a routing catalog (tech-spec §4, §8.3.3 fast/heavy two-tier).

## Approach (OQ-A → Option A)

Decided at the Brainstorming gate: **deterministic keyword/token-overlap matching** with a conservative single-unambiguous-match predicate. Rejected: Option B (tiny Haiku call — still a CLI round-trip), Option B′ (feed the catalog into the Router prompt — no bypass, smaller win), Option C (drop). Precision over recall (NFR-2): a wrong auto-route the user then cancels is worse than a 20 s router run, so a wrong guess costs one extra "Отмена" tap, never a wrong file move — the user-confirm step stays mandatory.

## Changes

1. **New pure module `inbox/hint_match.py`** (`M-INBOX`):
   - `score_catalog(text, catalog) -> HintMatch` — count of distinct domain keywords the message hits; `tokens(s) = re.findall(r"\w+", s.casefold())` minus `<3`-char tokens and a small ru/en stop-word set; the WIKI stem name (minus `-WIKI`) is folded into that domain's token set.
   - `is_confident(m)` := `top_stem set ∧ top_score ≥ MIN_SCORE(2.0) ∧ margin ≥ MIN_MARGIN(1.0)`. Thresholds are module-level `Final` constants — tunable offline from the `tg.pipeline.hint_fastpath.*` logs, deliberately **not** env-exposed (YAGNI / D-032).
   - **Deviation from design T-1:** the design specified a *ratio* `|∩| / |hint|`; switched to a raw *count* because ratios over hints of wildly different length are not comparable across domains. Within Option A's spirit (deterministic token overlap, single unambiguous match) and the OQ-C scoring detail the design left to the plan.
2. **`tg/pipeline.py`** (`M-TG-PIPELINE-CLASSIFIER`, v0.11.0):
   - `DefaultPipeline` gains an optional `hint_catalog_resolver: Callable[[int], Awaitable[Mapping[str,str]]] | None` (None ⇒ fast-path disabled, every existing pipeline unit test stays green).
   - New `START_BLOCK_HINT_FASTPATH … END_BLOCK_HINT_FASTPATH` in `_run_text_pipeline`, between `END_BLOCK_REMINDER_FASTPATH` and `START_BLOCK_ROUTABLE_BRANCH`. Guard: `intent ∈ _ROUTABLE_INTENTS ∧ router ∧ hint_catalog_resolver ∧ librarian ∧ output`. On a confident hit → `RouterDecision(intent=ROUTE, target_wiki=<stem>, notes="Похоже по ключевым словам из подсказки этой вики.", raw="", parsed_ok=True)` → `route_action_to_payload` → `PendingConfirmDraft(category="route_ingest", recap=build_route_recap)` → `ConfirmationService.request_explicit(build_route_confirm_keyboard)` → `return`, **without** awaiting `router.route`. Miss / ambiguous / empty / disabled ⇒ falls through to the heavy router unchanged.
3. **`__main__.make_hint_catalog_resolver`** (`M-RUNTIME-WIRING`, v0.5.4) — `telegram_id → {wiki_stem: hint_text}` via `surrogate_id_of` + the existing `owner_wikis_resolver` + `get_or_refresh_hint` per domain `CLAUDE.md` (reuses the stat→cache-hit path, so a hot message does 0 filesystem reads); empty dict if the sender has no `users` row or no domain WIKIs. Wired into `DefaultPipeline(hint_catalog_resolver=…)`.
4. **`storage/sessions/users.resolve_user_id`** (`M-STORAGE-SESSIONS`) — `telegram_id → users.user_id` surrogate (the `inbox_hint_cache` FK key; D-042). OQ-B resolved: no reusable lookup existed, added this 5-line `select`. **No migration, no schema change.**
5. **structlog anchors** (`tg/pipeline.py`): `tg.pipeline.hint_fastpath.catalog` (`n_domains`), `…hit` (`target_wiki, score, margin, pending_id`), `…miss` (`reason ∈ {empty_catalog, ambiguous, no_match}, top_stem, top_score, margin`), `…fallthrough` (`reason`). Hint text content is never logged (stems / counts / scores only).

## Non-goals (this iteration)

Boot-time / periodic hint-cache warm-up job (lazy `get_or_refresh_hint` suffices for MVP); fs-watch invalidation (the `(size, mtime)` stat is enough); any change to the heavy Router (`prompts/inbox.md`, `_RouterAdapter`); an env knob for the thresholds; the unrelated media-track deferrals (vision timeout, captions, `video_note` ext, `ops/retention.purge_staging`).

## Verification

1. **630 unit tests pass** (`uv run pytest tests/unit`).
2. `ruff check` + `ruff format --check` + `mypy --strict` clean; `gitleaks` (pre-commit) clean.
3. `grace lint --failOn errors` exit 0 (72 governed files, 3 XML).
4. `make inv-lint` — all 14 invariant checks pass.
5. Coverage: overall 92%; `inbox/hint_match.py` 100%, `storage/sessions/users.py` 100%, `tg/pipeline.py` 92%.
6. New tests:
   - `tests/unit/inbox/test_hint_match.py` — single clear winner / ambiguous (gap < `MIN_MARGIN`) / weak winner (< `MIN_SCORE`) / empty catalog / stem-name folding / token normalisation / `is_confident` boundaries.
   - `tests/unit/tg/test_pipeline_hint_fastpath.py` — confident hit bypasses `router.route` and requests a `route_ingest` confirm whose recap names the target; ambiguous / no_match / empty_catalog / `hint_catalog_resolver=None` / `librarian=None` all fall through to `router.route`; `tg.pipeline.hint_fastpath.{catalog,hit,miss,fallthrough}` markers asserted via `capsys`.
   - `tests/unit/test_main_hint_catalog.py` — `make_hint_catalog_resolver` assembles + filters (skips domains with no hint; `{}` on unknown sender; `{}` without touching the cache when no domain WIKIs).
   - `tests/unit/inbox/test_hint_cache.py::test_resolve_user_id_known_and_unknown`.

## GRACE Artifacts

1. `docs/knowledge-graph.xml` — `M-INBOX`: `+hint_match.py` path, `+fn-score_catalog / fn-is_confident / type-HintMatch`, purpose updated. `M-STORAGE-SESSIONS`: `+fn-resolve_user_id`. `M-TG-PIPELINE-CLASSIFIER`: purpose mentions the hint fast-path.
2. `docs/verification-plan.xml` — `V-M-INBOX` `+test_hint_match.py` + evidence; `V-M-TG-PIPELINE-CLASSIFIER` `+test_pipeline_hint_fastpath.py` + 4 `hint_fastpath.*` markers + evidence; `V-M-RUNTIME-WIRING` `+test_main_hint_catalog.py` + evidence.
3. `MODULE_CONTRACT` headers: new `inbox/hint_match.py`, new `storage/sessions/users.py`; bumped `tg/pipeline.py` (v0.11.0) and `__main__.py` (v0.5.4); `storage/sessions/__init__.py` re-exports `resolve_user_id`.

## Follow-ups

1. Tune `MIN_SCORE` / `MIN_MARGIN` from production `tg.pipeline.hint_fastpath.*` logs once there is real traffic (hit/miss rates, false-positive rate). Currently conservative (`2.0` / `1.0`).
2. Optional boot-time hint-cache warm-up job if cold-catalog latency shows up in logs (each cold/changed domain `CLAUDE.md` is one read on first contact; steady state is N stats, 0 reads).
3. Epic `aisw-t2r` — Phase-E.b was the last hanging sub-phase; consider closing the epic (separate decision).
