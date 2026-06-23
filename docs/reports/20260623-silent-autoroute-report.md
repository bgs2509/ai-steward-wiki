# Completion Report — Silent auto-route on confident hint fast-path

- **bd_id:** aisw-2ra (follow-up: aisw-05k — deferred loader label, FR-2)
- **module:** M-TG-PIPELINE
- **date:** 2026-06-23
- **decision origin:** `/best-approach` → Variant 1 (keep absolute-margin threshold; no 3× ratio)

## What changed

When the keyword hint fast-path is **confident** (`is_confident(hint_match)` — threshold
unchanged: `MIN_SCORE=2.0`, `MIN_MARGIN=1.0`), the pipeline now routes **silently** instead of
asking the user to confirm:

1. `START_BLOCK_HINT_FASTPATH` confident branch ingests immediately via a new shared helper
   `_ingest_and_deliver(decision, …)` (extracted from `_handle_route_confirm` — single Stage-1b
   ingest+deliver path, DRY) instead of calling `request_explicit` with a Confirm/Cancel keyboard.
2. On `status=="ok"`: sticky active-WIKI pointer set, reply delivered, then a `"✅ Записал в
   <Domain>-WIKI. Не туда? Перенесу:"` ack with a **one-tap redirect picker**
   (`build_route_redirect_keyboard` — picker-only, reuses the existing `wikipick` /
   `on_wikipick_callback` path). With no other WIKI to redirect into → a plain `auto_ack`.
3. On a non-ok ingest: the composed reply is sent, no redirect offered.
4. New log anchor `tg.pipeline.hint_fastpath.silent_route` replaces `…hit` on this branch.

Non-confident matches and the heavy Sonnet-router path are unchanged (still explicit confirm).

## Files

- `src/ai_steward_wiki/tg/pipeline.py` — silent branch, `_ingest_and_deliver` helper, RU acks, log anchor, header/MODULE_MAP.
- `src/ai_steward_wiki/tg/confirm.py` — `build_route_redirect_keyboard` (picker-only).
- `tests/unit/tg/test_pipeline_hint_fastpath.py` — silent-route + redirect + no-other-WIKI + failed-ingest tests.
- `tests/unit/tg/test_confirm.py` — redirect keyboard unit tests.
- `docs/verification-plan.xml` — marker + evidence updated.

## Verification (evidence)

- `uv run pytest tests/unit` → **1043 passed**, 1 pre-existing unrelated warning.
- `make lint` (ruff + ruff format + mypy --strict) → clean.
- `grace lint --failOn errors` → 0 issues.
- Independent code review (Explore agent): one flagged "missing error reply" was a **false
  positive** — the error reply is sent by `_ingest_and_deliver`'s `else` branch, proven by the
  passing `test_confident_hit_failed_ingest_sends_error_no_redirect`.

## Known limitations / deferred

- **No git-revert of a misroute** — `IngestOutcome` exposes no commit handle; "undo" is a redirect
  (re-ingest into the correct WIKI), the wrong-WIKI copy remains. Confident-predicate keeps
  misroutes rare. Possible future `wiki_lint` cleanup.
- **FR-2 loader label** ("🔍 Определяю проект…") deferred to **aisw-05k** — needs cross-cutting
  loader-relabel plumbing through `InboxAggregator` + handler placeholder + pipeline; the existing
  "⏳ Думаю…" loader and the ack already convey progress and outcome.
