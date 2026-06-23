---
feature: silent-autoroute
bd_id: aisw-2ra
module_id: M-TG-PIPELINE
status: stable
date: 2026-06-23
risk: medium
fr:
  - FR-1: When the hint fast-path is confident (is_confident(hint_match) == True — top_stem set AND top_score >= MIN_SCORE(2.0) AND margin >= MIN_MARGIN(1.0)), the pipeline MUST route the message into the matched <Domain>-WIKI WITHOUT the explicit Confirm/Cancel keyboard — replacing the request_explicit call at pipeline.py:1191-1193.
  - FR-2: During the silent route the user MUST see a progress loader stating the project is being determined ("🔍 Определяю проект…") instead of the generic "⏳ Думаю…" placeholder, for the duration of the librarian ingest.
  - FR-3: After a successful silent ingest the user MUST receive the normal ingest reply PLUS a short ack naming the target ("✅ <Domain>-WIKI") so the routing decision is never invisible.
  - FR-4: The ack MUST carry a cheap correction affordance ("↩️ Не туда") that lets the user redirect the just-routed item into a different existing WIKI — because a silent route gives no pre-confirmation chance to catch a misroute.
  - FR-5: The confidence threshold form is UNCHANGED — absolute margin (MIN_SCORE=2.0 / MIN_MARGIN=1.0 in inbox/hint_match.py). No ratio (3x) gate is introduced.
  - FR-6: Non-confident hint matches and the heavy Sonnet-router path are UNCHANGED — they keep the existing explicit route-confirm keyboard (request_explicit + build_route_confirm_keyboard).
  - FR-7: A successful silent ingest MUST update the active-WIKI sticky pointer (_active_wiki_set), exactly as the confirmed route path does (pipeline.py:2279).
nfr:
  - NFR-1: Precision over recall (inherits hint fast-path NFR-2) — silent routing only fires on the existing confident predicate; no loosening of the bar.
  - NFR-2: Observability — a new structlog anchor distinguishes silent routes from confirmed ones (e.g. tg.pipeline.hint_fastpath.silent_route) carrying target_wiki, score, margin, correlation_id; the existing hint_fastpath.hit anchor semantics must stay clear.
  - NFR-3: Safety — a silent misroute MUST be correctable by the user in one tap/reply; the limitation that content is not git-reverted from the wrong WIKI (no revert API on IngestOutcome) MUST be documented, not silently ignored.
  - NFR-4: mypy --strict + ruff + grace lint clean; all existing pipeline/confirm/handlers tests stay green.
  - NFR-5: Ru-only user-facing strings (D-032), no i18n.
constraints:
  - No revert/undo handle exists — IngestOutcome (pipeline.py:613-621) exposes only status/reply/run_id/target_wiki/created. "Cheap undo" therefore CANNOT mean git-revert; it must be a redirect (re-route) affordance, reusing the existing wikipick machinery.
  - The silent ingest MUST call the SAME execution path as a confirmed route — self._librarian.ingest(decision, ...) then _active_wiki_set + _output.deliver (mirroring _handle_route_confirm, pipeline.py:2252-2287) — to avoid forking ingest logic (DRY).
  - D-023 graduated confirmation (auto / implicit / explicit) is the governing model; silent route == "auto" level (ack line, no decision keyboard). ConfirmationService.auto_ack(chat_id, line) already exists (confirm.py:176).
  - Change is localized to- START_BLOCK_HINT_FASTPATH (pipeline.py:1125-1222), the loader string (handlers.py PLACEHOLDER_TEXT_RU / a scoped variant), and a redirect callback path reusing build_route_confirm_keyboard / on_wikipick_callback.
risks:
  - Silent misroute writes into the wrong git-backed WIKI before the user can object. Mitigation - the confident predicate already minimizes this (>=2 keyword overlap + >=1 lead); ack + "↩️ Не туда" redirect lets the user re-route immediately; mis-ingested copy in the wrong WIKI is a documented MVP limitation (follow-up bead for content relocation / wiki_lint cleanup).
  - Loader-string coupling — PLACEHOLDER_TEXT_RU is shared by all slow paths; blindly changing it would relabel every "thinking" placeholder as "determining project". Mitigation - introduce a scoped loader for the route path, do not repurpose the global placeholder.
  - Redirect affordance without a persisted pending row — the silent path skips request_explicit, so there is no route_ingest pending row for on_wikipick_callback to load. Mitigation (design decision, Step 4) - either persist a lightweight post-route record, or have the redirect button open a fresh explicit picker bound to the original payload.
scope_in:
  - src/ai_steward_wiki/tg/pipeline.py — START_BLOCK_HINT_FASTPATH confident branch- silent ingest + auto_ack + redirect affordance + active-WIKI set + structlog anchor
  - src/ai_steward_wiki/tg/handlers.py — scoped route loader text ("🔍 Определяю проект…")
  - src/ai_steward_wiki/tg/confirm.py — only if a redirect-after-silent keyboard helper is needed (reuse build_route_confirm_keyboard where possible)
  - tests/unit/tg/ — silent-route hit, loader text, ack+redirect, sticky-pointer set, and the unchanged-paths regression (non-confident + heavy router still confirm)
scope_out:
  - Git-revert / true content rollback from the wrong WIKI (no API; documented limitation + possible follow-up bead)
  - Changing the confidence threshold form (absolute margin stays; no 3x ratio — decided via /best-approach)
  - Touching the heavy Sonnet-router path, reminder fast-path, or digest fast-path confirmations
  - Per-candidate LLM confidence scores in ClassifierResult (rejected as Variant 3 in /best-approach)
---

# Discovery — Silent auto-route on confident hint fast-path

## Intent

Reduce friction: when the keyword-hint catalog points **unambiguously** at one WIKI, the user
should not have to tap a Confirm button — the bot routes silently, shows a "determining project"
loader, then acks where it landed. Chosen as **Variant 1** via `/best-approach` (keep the existing
**absolute margin** threshold; do **not** introduce a 3× ratio).

## Current behaviour (verified)

- Hint fast-path (`pipeline.py:1125-1222`) already computes per-candidate scores
  (`inbox/hint_match.py`: `score_catalog` → `HintMatch{ranked, top_stem, top_score, margin}`) and a
  confidence predicate `is_confident` (`MIN_SCORE=2.0`, `MIN_MARGIN=1.0`).
- On a confident hit it synthesises `RouterDecision(ROUTE)` but **still** calls
  `request_explicit(...)` with the Confirm/Cancel keyboard (`pipeline.py:1191-1193`). The fast-path
  only skips the **Sonnet router**, not the **user confirmation**.
- The actual ingest runs later in `_handle_route_confirm` (`pipeline.py:2252-2287`):
  `librarian.ingest(decision, ...)` → on `ok`: `_active_wiki_set` + `_output.deliver(reply)`.
- Precedent for silent routing already accepted: the **active-WIKI sticky pointer** (aisw-0ym)
  default-routes bare follow-ups without re-confirming.

## Key blind spot → design fork (for Step 4)

There is **no undo/revert API**: `IngestOutcome` carries no commit handle and the WIKI write is a
Claude run into git-backed storage. "Cheap undo" therefore must be a **redirect** (re-route into the
correct WIKI), not a rollback. The open question for Brainstorming: how the "↩️ Не туда" affordance
is wired given the silent path creates **no** `route_ingest` pending row that `on_wikipick_callback`
expects.

## Stakeholders

End users (less tapping on obvious routes), and the WIKI integrity story (a rare misroute must be
cheaply correctable). No multi-user / auth surface touched.
