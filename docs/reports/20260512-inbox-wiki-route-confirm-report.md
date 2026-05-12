---
feature: inbox-wiki-route-confirm
bd_id: aisw-e45
epic: aisw-t2r
date: 2026-05-12
type: feature
status: complete
commits: 125c895, 06ea9d5, dcdfc4c, 33da18c, 2f03f41, f3be708, a63aae2
adr: ADR-005
follows: aisw-zd9
---

# Completion report — Inbox-WIKI Phase-C: confirmation loop (router proposes actions → inline buttons → execute on confirm)

## Goal

Insert a one-tap user confirmation between the Stage-1a router decision and the Stage-1b move+ingest (epic `aisw-t2r`). Before (Phase-B): a `ROUTE`/`CREATE_WIKI` `RouterDecision` was executed immediately — resolve/create the target `<Domain>-WIKI`, move the raw in, run Stage-1b, reply. After (Phase-C): the decision is *proposed* via «✅ Подтвердить» / «❌ Отмена» inline buttons; the WIKI mutation runs in `on_confirm_callback` only on "confirmed". Closes the gate ADR-004 deliberately deferred (its consequence 3). Cron bridge and hint fast-path remain out of scope (`aisw-kcz` / `aisw-12t`).

## What changed

1. **`M-INBOX-ROUTE` (`src/ai_steward_wiki/inbox/route.py`)** — `RouteAction` frozen dataclass (`decision: RouterDecision`, `user_text`, `source`, `media_paths: list[str]`, `correlation_id`); `route_action_to_payload(decision, *, user_text, source, media_paths, correlation_id) -> dict` (JSON-able: `decision.model_dump(mode="json")`, media as POSIX strings) and `route_action_from_payload(payload) -> RouteAction` (reconstructs `RouterDecision`, `Path`s; `ValueError` on a missing `decision` or a bad `source`).
2. **`M-TG-TEXT` (`src/ai_steward_wiki/tg/confirm.py`)** — `build_route_confirm_keyboard(pending_id)` → 2-button `InlineKeyboardMarkup` (`confirm:<id>:confirm` / `confirm:<id>:cancel`); `ConfirmationService.request_explicit` gains an additive `keyboard_factory: Callable[[int], Any] = build_explicit_keyboard` param (the chunk-10 explicit-confirm path keeps the 3-button default).
3. **`M-TG-PIPELINE-CLASSIFIER` (`src/ai_steward_wiki/tg/pipeline.py`)** — in `_run_text_pipeline`'s routable branch a `ROUTE`/`CREATE_WIKI` decision (with a wired `librarian` + `output`) no longer calls `librarian.ingest()`; it builds a `route_ingest` confirm draft (`route_action_to_payload(...)`) and calls `confirmation.request_explicit(draft, keyboard_factory=build_route_confirm_keyboard)`, logging `tg.pipeline.route.confirm_requested`. `CLARIFY`/`REJECT` and the no-librarian case still notes-echo. `on_confirm_callback` now reads the pending row (`get_pending`) first; a `category != 'route_ingest'` (or missing) row → legacy `resolve` + `tg.pipeline.confirm`; a `route_ingest` row → `_handle_route_confirm`: `resolve` race-safely (`tg.pipeline.confirm.route_dispatched`), on `None` → `ROUTE_CONFIRM_STALE_RU` (`confirm_stale`), on `cancelled`/`corrected` → `ROUTE_CONFIRM_CANCELLED_RU` (`confirm_cancelled`, staged raw kept in Inbox per D-022), on `confirmed` → `ROUTE_CONFIRM_ACK_RU` then `route_action_from_payload(json.loads(draft_json))` → `librarian.ingest(decision, …, media_paths=[Path(p) …] or None, …)` → `output.deliver(reply)` on `ok` else `send_message(reply)` (`confirm_executed`). New constants `ROUTE_CONFIRM_RECAP_ROUTE_RU` / `..._CREATE_RU` / `..._ACK_RU` / `..._CANCELLED_RU` / `..._STALE_RU` + `build_route_recap(decision)`. Phase-B's `tg.pipeline.route.ingest_dispatched|delivered` anchors removed.
4. **`handlers.py`** — unchanged (`parse_confirm_callback` already accepts `confirm`/`cancel`; the route-vs-legacy dispatch is inside `DefaultPipeline.on_confirm_callback`).
5. **Storage** — reuses `pending_confirms` (`category` + `draft_json` columns exist since chunk 10); **no Alembic migration**.
6. **Tests** — `tests/unit/inbox/test_route.py` (+3: `route_action` round-trip ×4 (ROUTE/CREATE_WIKI × with/without media), missing-media tolerance, bad-input `ValueError`). `tests/unit/tg/test_confirm.py` (+2: `build_route_confirm_keyboard` 2-button shape; `request_explicit` honours a custom `keyboard_factory`; the 3-button-default regression test still passes). `tests/unit/tg/test_pipeline_route_confirm.py` (new, 6: routable ROUTE/CREATE_WIKI → `request_explicit` with `category='route_ingest'` + route keyboard + round-trippable draft, no `ingest`; CLARIFY/REJECT and no-librarian → notes-echo, no confirm; `confirm_requested` log marker). `tests/unit/tg/test_pipeline_confirm_callback.py` (new, 6: confirm → ack-then-ingest-then-deliver (incl. media Paths + reconstructed decision); run_failed → `send_message`; cancel → `CANCELLED`; stale (`resolve→None`) → `STALE`; non-route category → legacy `resolve`; missing row → legacy `resolve`). `tests/unit/tg/test_pipeline_route_ingest.py` trimmed to the still-valid cases (CLARIFY notes-echo, no-librarian notes-echo, router-dispatch log markers) + a docstring pointer to the new files. `tests/unit/tg/test_pipeline.py` `_make_confirm` gains a `get_pending=AsyncMock(return_value=None)` so the legacy callback test stays on the legacy branch. `tests/integration/test_e2e_pipeline.py` scenario 6 reworked (`test_routable_text_confirm_then_ingests`): routable text → recap + `route_ingest` pending row, **no domain WIKI yet**; `on_confirm_callback("confirm")` → real Stage-1b librarian writes into the target `<Domain>-WIKI/raw/`, `ROUTE_CONFIRM_ACK_RU` sent, reply via `output.deliver`; tolerant of CLARIFY/REJECT.
7. **GRACE** — `M-INBOX-ROUTE` / `M-TG-TEXT` / `M-TG-PIPELINE-CLASSIFIER` contracts (CHANGE_SUMMARY, MODULE_MAP, `__all__`, DEPENDS/LINKS) + knowledge-graph annotations & CrossLink relations + verification-plan (new test paths, new `tg.pipeline.route.confirm_*` / `confirm.route_dispatched` markers, updated evidence) + `Phase-C` node in `development-plan.xml`; version bumps (KG 0.0.8, VP 0.0.8, DP 0.0.7). ADR-005.

## Design decisions (auto-approved gates; see ADR-005)

Reuse `pending_confirms` (no migration) over a new table · **no** `proposed_actions` field on `RouterDecision` (single action derivable from `(intent, target_wiki)`; structured list is Phase-D) · **replace** Phase-B auto-execute, **no** `route_confirm_enabled` toggle · 2-button route keyboard (no «Изменить»), `request_explicit` keyboard_factory · short ru ack before the slow ingest, no recap-keyboard removal in the MVP · cancel/TTL keeps the staged raw in Inbox · (de)serialiser in `inbox/route.py`, ru strings in `tg/pipeline.py`.

## Verification

- `make lint` — `ruff check` ✅, `ruff format --check` ✅ (182 files), `mypy src` ✅ (68 files).
- `grace lint --failOn errors` — Issues: 0 (errors 0, warnings 0); 68 governed files, 3 XML.
- `uv run pytest tests/unit` — **516 passed**, 1 warning.
- Coverage — TOTAL 92%; `inbox/route.py` 98%, `tg/confirm.py` 95%, `tg/pipeline.py` 92%.
- Integration (`RUN_INTEGRATION=1`, real Claude CLI) — scenario 6 updated; not run in this session (nightly gate; needs the `claude` binary outside Claude Code).

## Follow-ups (not Phase-C)

Phase-D cron bridge (`aisw-kcz`) — hooks into `_handle_route_confirm` with an extended `route_action` payload · Phase-E hint fast-path + per-user media staging (`aisw-12t`) · structured `proposed_actions` list · «Изменить»/re-route UX · TTL-expiry user notification · removing the recap keyboard on resolution (needs `edit_reply_markup` on `TgSender`) · restore-from-trash on a `ROUTE` to a soft-deleted name.
