# Implementation Plan — Silent auto-route on confident hint fast-path

- **bd_id:** aisw-2ra  (follow-up: aisw-05k = deferred loader label, FR-2)
- **module_id:** M-TG-PIPELINE
- **TDD:** RED → GREEN → REFACTOR per step. Ru-only strings. mypy --strict + ruff + grace lint clean.

## Scope recap

Flip the **confident** hint-fast-path branch (`pipeline.py` START_BLOCK_HINT_FASTPATH) from
"synthesise ROUTE → `request_explicit` (Confirm/Cancel)" to "synthesise ROUTE → **ingest now** →
ack + redirect picker". Threshold unchanged. Non-confident + heavy-router paths unchanged.
Loader label (FR-2) deferred to aisw-05k.

---

## Step 1 — RU strings + redirect keyboard (confirm.py)

**RED:** `tests/unit/tg/test_confirm.py` (or test_callbacks.py) — `build_route_redirect_keyboard(pid, ["Health-WIKI","Investment-WIKI"])` returns an InlineKeyboardMarkup whose ONLY rows are 2-column `wikipick:<pid>:<idx>` buttons (NO `confirm:` buttons). Empty list → no rows.

**GREEN:**
- In `pipeline.py` near line 356 add:
  - `ROUTE_SILENT_ACK_RU = "✅ Записал в {wiki}. Не туда? Перенесу:"`
  - `ROUTE_SILENT_ACK_NOREDIR_RU = "✅ Записал в {wiki}."`
  - export both in `__all__`.
- In `confirm.py` add `build_route_redirect_keyboard(pending_id, other_wikis=())` = copy of `build_route_confirm_keyboard` MINUS the `[BTN_CONFIRM][BTN_CANCEL]` row (picker rows only); export in `__all__`.

## Step 2 — Extract shared ingest helper `_ingest_and_deliver` (pipeline.py)

**RED:** test that calling the helper with a stub librarian returning `IngestOutcome(status="ok", reply="R", run_id="r1", target_wiki="Health-WIKI", created=False)` calls `_active_wiki_set("Health-WIKI")` and `_output.deliver(text="R")`; on non-ok calls `sender.send_message(reply)` and NOT `_active_wiki_set`.

**GREEN:** extract from `_handle_route_confirm` (pipeline.py:2256-2287) a method:
```
async def _ingest_and_deliver(self, decision, *, telegram_id, chat_id, user_text,
                              source, media_paths, correlation_id) -> IngestOutcome:
    outcome = await self._librarian.ingest(decision, telegram_id=..., user_text=...,
                source=..., media_paths=media or None, correlation_id=...)
    if outcome.status == "ok":
        await self._active_wiki_set(telegram_id, outcome.target_wiki)
        await self._output.deliver(chat_id=chat_id, telegram_id=telegram_id,
                                   run_id=outcome.run_id or "", text=outcome.reply)
    else:
        await self._sender.send_message(chat_id, outcome.reply)
    return outcome
```
**REFACTOR:** `_handle_route_confirm` calls `_ingest_and_deliver(...)` after `status=="confirmed"` (keeps its own `ROUTE_CONFIRM_ACK_RU` send + `confirm_executed` log). Behaviour identical → existing route-confirm tests stay green (objective check).

## Step 3 — Silent branch in START_BLOCK_HINT_FASTPATH (pipeline.py)

**RED:** rewrite `tests/unit/tg/test_pipeline_hint_fastpath.py::test_confident_hit_*`:
- librarian = AsyncMock; `.ingest` returns ok IngestOutcome (target Health-WIKI).
- `_owner_wikis_resolver` (or `_list_owner_wiki_names`) yields `["Health-WIKI","Investment-WIKI"]`.
- After `on_text(confident health text)`:
  - `router.route` NOT awaited.
  - `librarian.ingest` awaited once with a ROUTE decision target=Health-WIKI.
  - `output.deliver` awaited (reply delivered) and `_active_wiki_set`/active pointer set.
  - `confirm.request_explicit` awaited once with `keyboard_factory` producing a redirect (picker-only) keyboard and `recap_text` == ROUTE_SILENT_ACK_RU.format(wiki="Health-WIKI").
  - log contains `tg.pipeline.hint_fastpath.silent_route` (NOT `...hit`).
- New test: owner has ONLY Health-WIKI → `confirm.request_explicit` NOT awaited; `confirm.auto_ack` awaited with ROUTE_SILENT_ACK_NOREDIR_RU.
- New test: non-ok ingest (status="rejected") → `sender.send_message(reply)`, no redirect row, no active-pointer set.
- Keep/confirm: ambiguous + long-text + empty-catalog still fall through to router (unchanged).

**GREEN:** replace lines 1170-1204 (the `if is_confident` body up to `return`) with:
```
decision = RouterDecision(intent=ROUTE, target_wiki=top_stem, notes=..., raw="", parsed_ok=True)
outcome = await self._ingest_and_deliver(decision, telegram_id=..., chat_id=...,
            user_text=text, source=source, media_paths=media_paths, correlation_id=...)
_log.info("tg.pipeline.hint_fastpath.silent_route", target_wiki=top_stem,
          score=hint_match.top_score, margin=hint_match.margin,
          run_id=outcome.run_id, status=outcome.status, correlation_id=...)
if outcome.status == "ok":
    others = [w for w in await self._list_owner_wiki_names(telegram_id) if w != top_stem]
    if others:
        payload = route_action_to_payload(decision, user_text=text, source=source,
                    media_paths=media_paths, correlation_id=correlation_id)
        draft = PendingConfirmDraft(telegram_id=..., chat_id=..., category="route_ingest",
                    draft=payload, recap_text=ROUTE_SILENT_ACK_RU.format(wiki=top_stem))
        await self._confirm.request_explicit(draft,
                    keyboard_factory=lambda pid: build_route_redirect_keyboard(pid, others))
    else:
        await self._confirm.auto_ack(chat_id, ROUTE_SILENT_ACK_NOREDIR_RU.format(wiki=top_stem))
return
```
Import `build_route_redirect_keyboard`. The mis-routed copy is not removed on redirect (D-local-3) — no code, documented only.

## Step 4 — MODULE_MAP + header bump

Update pipeline.py MODULE_MAP (add `_ingest_and_deliver`), confirm.py MODULE_MAP (add `build_route_redirect_keyboard`), bump VERSION + CHANGE_SUMMARY. Update the START_BLOCK_HINT_FASTPATH comment (it currently says "Never routes silently" — now it DOES on confident matches).

## Step 5 — Quality gate

`make lint` (ruff + format + mypy + grace lint) + `uv run pytest tests/unit/tg tests/unit/inbox` green. Fix any drift. Then full `make total-test` if time permits (≥80% coverage).

## Verification checklist (FR/NFR coverage)

- [ ] FR-1 silent route on confident hit (Step 3 test: no request_explicit-confirm; ingest now)
- [ ] FR-3 ack names target WIKI (ROUTE_SILENT_ACK_RU/NOREDIR_RU)
- [ ] FR-4 redirect picker reuses wikipick (Step 1 + Step 3 tests)
- [ ] FR-5 threshold unchanged (is_confident untouched — grep assert)
- [ ] FR-6 non-confident + heavy router unchanged (existing tests green)
- [ ] FR-7 active-WIKI set on ok (Step 2 + Step 3 tests)
- [ ] NFR-2 silent_route log anchor (Step 3 test asserts it)
- [ ] NFR-4 lint/mypy/grace clean (Step 5)
- [ ] FR-2 loader label → deferred aisw-05k (out of scope)
