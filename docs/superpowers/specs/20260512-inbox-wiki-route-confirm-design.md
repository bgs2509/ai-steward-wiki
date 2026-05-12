---
feature: inbox-wiki-route-confirm
bd_id: aisw-e45
epic: aisw-t2r
phase: "Inbox-WIKI Phase-C"
status: draft
created: 2026-05-12
approach: "Confirm-gate the Phase-B route+ingest: a ROUTE/CREATE_WIKI RouterDecision is persisted as a `route_ingest` pending_confirms row (10-min TTL, draft_json) and proposed via a 2-button inline keyboard; the move+ingest runs in on_confirm_callback on 'confirmed'. Reuses ConfirmationService, the existing confirm:<id>:<action> handler, the PendingConfirm model (no migration), and the Phase-B Librarian.ingest path."
technology:
  decisions:
    - id: TD-1
      text: "Reuse `ConfirmationService` (D-023) as-is plus one additive change: `request_explicit(draft, *, keyboard_factory: Callable[[int], object] = build_explicit_keyboard)`. Route confirms pass `keyboard_factory=build_route_confirm_keyboard`. The chunk-10 explicit-confirm path keeps the default 3-button keyboard. No new confirm service, no parallel persistence."
    - id: TD-2
      text: "Reuse the `pending_confirms` table and `PendingConfirm` ORM model unchanged — `category`, `draft_json`, `chat_id`, `recap_message_id` columns exist since chunk 10. Route confirms set `category='route_ingest'` and `draft_json=json.dumps(route_action_payload)`. NO Alembic migration."
    - id: TD-3
      text: "Route-action (de)serialisation lives in `inbox/route.py` (it already owns `RouterDecision`): `RouteAction` frozen dataclass (decision: RouterDecision, user_text: str, source: Literal['text','voice','document','photo'], media_paths: list[str], correlation_id: str), `route_action_to_payload(decision, *, user_text, source, media_paths, correlation_id) -> dict[str, object]`, `route_action_from_payload(payload: dict[str, object]) -> RouteAction`. RouterIntent ↔ str via `RouterIntent(value)`/`.value`; RouterDecision ↔ dict via `.model_dump()`/`RouterDecision(**d)`; Path ↔ POSIX str."
    - id: TD-4
      text: "Russian recap / ack strings + the `build_route_recap(decision) -> str` helper live in `tg/pipeline.py` next to the existing `*_RU` constants. `build_route_confirm_keyboard(pending_id) -> InlineKeyboardMarkup` (2 buttons: `confirm:<id>:confirm`, `confirm:<id>:cancel`) lives in `tg/confirm.py` next to `build_explicit_keyboard`."
    - id: TD-5
      text: "`handlers.py` is untouched — `parse_confirm_callback` already accepts `confirm`/`correct`/`cancel`, and `pipeline.on_confirm_callback(telegram_id, chat_id, pending_id, action)` already receives everything. The route-vs-other dispatch happens inside `DefaultPipeline.on_confirm_callback` by reading the pending row's `category`."
    - id: TD-6
      text: "On `confirm` the pipeline sends a short ru ack («📝 Записываю в вики…») via `TgSender.send_message` BEFORE the (≤vision/text-timeout) `Librarian.ingest` call so the user isn't left hanging; the aiogram callback itself is already `answer()`-ed in `handlers.py`. Keyboard removal on the recap message is NOT done in the MVP (no `edit_reply_markup` on `TgSender`; a repeat tap is harmless — `resolve` returns `None` → 'stale' reply)."
    - id: TD-7
      text: "Phase-B's immediate `librarian.ingest()` in `_run_text_pipeline`'s routable branch is REPLACED (not gated behind a flag). No `route_confirm_enabled` toggle. No new `proposed_actions` field on `RouterDecision`."
    - id: TD-8
      text: "No new third-party dependency. TTL expiry continues to rely on the existing `ConfirmationService.expire_due` sweeper job — no new scheduler entry. mypy --strict / ruff / ruff-format / grace lint clean; coverage ≥80%."
  stack_unchanged:
    - "aiogram 3.x (InlineKeyboardMarkup/InlineKeyboardButton), SQLAlchemy async (pending_confirms), pydantic v2 (RouterDecision), structlog — all already in use."
---

# Design — Inbox-WIKI Phase-C: confirmation loop (aisw-e45)

## Goal

Insert a one-tap user confirmation between the Stage-1a router decision and the Stage-1b move+ingest. A `ROUTE`/`CREATE_WIKI` `RouterDecision` is no longer executed on the spot (Phase-B); it is *proposed* — recap + «✅ Подтвердить»/«❌ Отмена» — and the WIKI mutation happens only on confirm. Closes R-3 from the Phase-B discovery.

## Architecture

Three small additive pieces + two edits in `DefaultPipeline`:

```
tg/confirm.py
  + build_route_confirm_keyboard(pending_id)            # 2-button InlineKeyboardMarkup
  ~ ConfirmationService.request_explicit(draft, *, keyboard_factory=build_explicit_keyboard)

inbox/route.py
  + RouteAction (frozen dataclass)
  + route_action_to_payload(decision, *, user_text, source, media_paths, correlation_id) -> dict
  + route_action_from_payload(payload) -> RouteAction

tg/pipeline.py
  + ROUTE_CONFIRM_RECAP_ROUTE_RU / ROUTE_CONFIRM_RECAP_CREATE_RU
  + ROUTE_CONFIRM_ACK_RU / ROUTE_CONFIRM_CANCELLED_RU / ROUTE_CONFIRM_STALE_RU
  + build_route_recap(decision) -> str
  ~ DefaultPipeline._run_text_pipeline  (routable branch: ingest -> request_explicit)
  ~ DefaultPipeline.on_confirm_callback (category dispatch + route execute/cancel/stale)
```

`handlers.py` — unchanged.

## Data flow

### Inbound message (routable)

```
on_text/on_voice/on_photo/on_document
  → _run_text_pipeline
      → classify → intent ∈ _ROUTABLE_INTENTS and self._router → router.route() → RouterDecision
          (log tg.pipeline.router.decided  — unchanged)
      → if decision.intent ∈ {ROUTE, CREATE_WIKI} and self._librarian and self._output:
            payload = route_action_to_payload(decision, user_text=text, source=source,
                                               media_paths=media_paths, correlation_id=correlation_id)
            draft = PendingConfirmDraft(telegram_id, chat_id, category="route_ingest",
                                        draft=payload, recap_text=build_route_recap(decision))
            await self._confirm.request_explicit(draft, keyboard_factory=build_route_confirm_keyboard)
            log tg.pipeline.route.confirm_requested(pending_id, intent, target_wiki)
            return
      → else (CLARIFY/REJECT/no-librarian): send_message(decision.notes)   # Phase-A behaviour, unchanged
```

### Callback (`confirm:<pending_id>:<action>`)

```
handlers._on_confirm → pipeline.on_confirm_callback(telegram_id, chat_id, pending_id, action)
  pending = await self._confirm.get_pending(pending_id)        # read-only, may be None
  if pending is None or pending.category != "route_ingest":
      status = await self._confirm.resolve(telegram_id, pending_id, action)   # legacy path
      log tg.pipeline.confirm(...)                                            # unchanged
      return
  # route_ingest:
  status = await self._confirm.resolve(telegram_id, pending_id, action)
  log tg.pipeline.confirm.route_dispatched(pending_id, action)
  if status is None:
      send_message(chat_id, ROUTE_CONFIRM_STALE_RU);  log tg.pipeline.route.confirm_stale(pending_id);  return
  if status == "cancelled" (action=="cancel") or status == "corrected" (action=="correct"):
      send_message(chat_id, ROUTE_CONFIRM_CANCELLED_RU);  log tg.pipeline.route.confirm_cancelled(pending_id);  return
  # status == "confirmed"
  action_obj = route_action_from_payload(json.loads(pending.draft_json or "{}"))
  send_message(chat_id, ROUTE_CONFIRM_ACK_RU)                                 # short ack before slow ingest
  outcome = await self._librarian.ingest(action_obj.decision, telegram_id=telegram_id,
                                          user_text=action_obj.user_text, source=action_obj.source,
                                          media_paths=[Path(p) for p in action_obj.media_paths],
                                          correlation_id=action_obj.correlation_id)
  log tg.pipeline.route.confirm_executed(pending_id, status=outcome.status, target_wiki=outcome.target_wiki,
                                          created=outcome.created, run_id=outcome.run_id)
  if outcome.status == "ok":
      await self._output.deliver(chat_id=chat_id, telegram_id=telegram_id, run_id=outcome.run_id or "", text=outcome.reply)
  else:
      send_message(chat_id, outcome.reply)
```

All new log lines carry `correlation_id` (from the stored draft on the callback path) and `telegram_id`.

## Recap copy (ru, D-032)

- `ROUTE` →  `«Положу это в вики «{target_wiki}».»\n\n{notes}\n\nПодтверждаешь?`
- `CREATE_WIKI` →  `«Заведу новую вики «{target_wiki}» и положу это туда.»\n\n{notes}\n\nПодтверждаешь?`
- `ROUTE_CONFIRM_ACK_RU` = `«📝 Записываю в вики…»`
- `ROUTE_CONFIRM_CANCELLED_RU` = `«Отменено. Файл остался в Inbox — пришли заново с уточнением.»`
- `ROUTE_CONFIRM_STALE_RU` = `«Время на подтверждение истекло — пришли заново.»`

(`build_route_recap` returns the recap for the decision; the `notes` block is `decision.notes`.)

## Error handling

1. **Double-tap / two devices** — `resolve` is `UPDATE … WHERE status='pending'`; second tap → `rowcount 0` → `None` → stale reply. No second ingest. (R-2)
2. **Re-send of the same content within the TTL** — `request_explicit` is idempotent on `(telegram_id, payload_hash, status='pending')` → returns the existing record, no second recap. (R-1)
3. **`draft_json` round-trip** — explicit (de)serialiser in `inbox/route.py` with a dedicated unit test; `RouterIntent`/`RouterDecision`/`Path` reconstructed by type. (R-3)
4. **Stage-1b failure on confirm** — `Librarian.ingest` already maps to `IngestOutcome(status='run_failed'|'rejected', reply=…)` → `send_message(outcome.reply)`; the moved raw stays in the target WIKI (Phase-B behaviour, no rollback). The pending row is already `confirmed`.
5. **Missing staged media path at confirm time** — passed through to `Librarian.ingest` which handles it as Phase-B does (no new special-casing here).
6. **Cancel / TTL expiry** — staged raw stays in `Inbox-WIKI/raw/` (D-022); `expire_due` flips the row; no user notification (acceptable MVP, R-5).
7. **`get_pending` returns `None`** (row never existed / pruned) — treated as legacy/no-op: `resolve` will also return `None`; reply stale.

## Testing

Unit (`tests/unit/tg/test_pipeline_route_confirm.py`, `tests/unit/inbox/test_route.py` extension, `tests/unit/tg/test_confirm.py` extension):
- routable `ROUTE` → `confirmation.request_explicit` called once with `category='route_ingest'`, `keyboard_factory=build_route_confirm_keyboard`, draft payload round-trippable; `librarian.ingest` NOT called.
- routable `CREATE_WIKI` → same, recap uses the create copy.
- `on_confirm_callback(action='confirm')` on a `route_ingest` row → `librarian.ingest` called with the reconstructed decision + media Paths; `output.deliver` on `ok`, `send_message(reply)` on `run_failed`; ack sent before ingest; `confirm_executed` log.
- `on_confirm_callback(action='cancel')` → `ROUTE_CONFIRM_CANCELLED_RU`, no ingest, `confirm_cancelled` log.
- `resolve` returns `None` (already resolved / expired) → `ROUTE_CONFIRM_STALE_RU`, `confirm_stale` log, no ingest.
- `on_confirm_callback` on a non-`route_ingest` (or missing) row → legacy `tg.pipeline.confirm` behaviour unchanged.
- `CLARIFY`/`REJECT` and the no-librarian case → still notes-echo, no confirm row.
- `route_action_to_payload` ∘ `route_action_from_payload` == identity for ROUTE and CREATE_WIKI decisions with/without media; `build_route_confirm_keyboard` callback_data strings.
- double-`confirm` idempotency (second call → stale).
- `request_explicit` with the default `keyboard_factory` still produces the 3-button keyboard (regression).

Integration (`tests/integration/` extension, `RUN_INTEGRATION=1`):
- routable text → real Stage-1a router (Inbox-WIKI) → recap returned (assert no page written yet) → simulate `on_confirm_callback(confirm)` → real Stage-1b librarian → a page exists under the target `<Domain>-WIKI`.

## GRACE artifacts

- Update `tg/pipeline.py` MODULE_CONTRACT (CHANGE_SUMMARY, MODULE_MAP: new constants + `build_route_recap`), `tg/confirm.py` (new `build_route_confirm_keyboard`, `request_explicit` keyboard_factory), `inbox/route.py` (new `RouteAction` + (de)serialisers).
- `docs/knowledge-graph.xml` — add CrossLink `tg.pipeline → tg.confirm` (routing now uses the confirm loop) and `tg.pipeline → inbox.route` already exists (Phase-B) — extend if the new symbols change the surface; bump module versions.
- `docs/verification-plan.xml` — register the new test files + the new `tg.pipeline.route.confirm_*` log anchors.
- `docs/development-plan.xml` — add the Phase-C entry under epic aisw-t2r (status in_progress → done at Finish), `bd_id=aisw-e45`.
- `docs/adr/ADR-005-inbox-wiki-route-confirm.md` — "Phase-C confirm-before-route replaces Phase-B auto-execute; reuse pending_confirms (no migration); no `proposed_actions` field; no toggle; cancel/TTL keeps the staged raw."

## Out of scope (see discovery)

Phase-D cron bridge (aisw-kcz); the `proposed_actions` list field; the «Изменить» re-route UX; TTL-expiry notification; restore-from-trash; per-WIKI git auto-commit; any prompt/Stage-0/Stage-1a change.
