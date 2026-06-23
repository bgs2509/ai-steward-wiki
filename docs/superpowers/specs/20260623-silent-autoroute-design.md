---
feature: silent-autoroute
bd_id: aisw-2ra
module_id: M-TG-PIPELINE
status: stable
date: 2026-06-23
stack:
  - library: aiogram
    version: 3.15.0 (uv.lock)
    used_for: InlineKeyboardMarkup for the redirect picker; existing wikipick callback reused
  - library: structlog
    version: pinned (uv.lock)
    used_for: tg.pipeline.hint_fastpath.silent_route anchor
decisions:
  - D-local-1: Silent route == D-023 "auto" level. On a confident hint hit, REPLACE request_explicit (Confirm/Cancel keyboard) with direct ingest. The branch calls the SAME execution as a confirmed route — librarian.ingest → on ok _active_wiki_set + _output.deliver(reply) — extracted into a shared helper _ingest_and_deliver(decision, user_text, source, media, chat_id, telegram_id, correlation_id) reused by both the silent branch and _handle_route_confirm (DRY, single ingest path).
  - D-local-2: Redirect (cheap undo) == direct WIKI picker, NOT git-revert (no revert API). After a successful silent ingest, persist a route_ingest pending row via request_explicit whose recap_text is the ack ("✅ Записал в <Domain>-WIKI. Не туда? Перенесу:") and whose keyboard is a NEW picker-only keyboard build_route_redirect_keyboard(pending_id, other_wikis) = build_route_confirm_keyboard MINUS the Confirm/Cancel row. Tapping a WIKI reuses the EXISTING wikipick callback → on_wikipick_callback re-ingests into the chosen WIKI. Zero new callback prefixes / handlers.
  - D-local-3: The mis-ingested copy is NOT removed from the wrong WIKI on redirect (no relocation API). Documented MVP limitation; the redirect re-ingests into the correct WIKI. Follow-up bead may add wiki_lint cleanup / content relocation. The confident predicate (>=2 overlap, >=1 lead) keeps misroutes rare.
  - D-local-4: Loader text (FR-2) DEFERRED to follow-up bead aisw-05k (user decision 2026-06-23). In production text flows through InboxAggregator (tg/aggregator.py), which posts the generic "⏳ Думаю…" loader BEFORE classification and only deletes it after _process; relabeling it requires a loader-relabel hook threaded through the aggregator + handler placeholder + pipeline dispatch — disproportionate plumbing on tested code for a cosmetic string. The core silent-route ships WITHOUT the custom label: the existing "⏳ Думаю…" loader stays, and the "✅ Записал в <Domain>-WIKI" ack already tells the user which project was determined.
  - D-local-5: No threshold change. is_confident / MIN_SCORE=2.0 / MIN_MARGIN=1.0 untouched (inbox/hint_match.py). Only the confident branch's tail (confirm → silent) changes. Non-confident fallthrough and the heavy Sonnet-router path keep request_explicit + build_route_confirm_keyboard unchanged.
  - D-local-6: Edge case — owner has no OTHER WIKI than the target: build_route_redirect_keyboard yields no picker rows; send the ack line WITHOUT a keyboard (nothing to redirect into). Logged. No pending row needed in that sub-case (auto_ack only).
  - D-local-7: New structlog anchor tg.pipeline.hint_fastpath.silent_route {target_wiki, score, margin, run_id, pending_id|None, correlation_id}. The old tg.pipeline.hint_fastpath.hit anchor is retired from this branch (it implied a confirm) — replaced by silent_route. Existing miss/fallthrough anchors unchanged.
---

# Design — Silent auto-route on confident hint fast-path

## Chosen approach (Variant 1, /best-approach)

Flip the **confident** hint-fast-path branch from "synthesise ROUTE → ask via Confirm/Cancel" to
"synthesise ROUTE → **ingest now** → ack + redirect picker". Everything else (threshold, heavy
router, non-confident fallthrough) is untouched.

## Control flow (new confident branch)

```
hint_match = score_catalog(text, catalog)
if len(text) <= HINT_FASTPATH_MAX_CHARS and is_confident(hint_match):
    loader_holder.text = "🔍 Определяю проект…"          # FR-2
    decision = RouterDecision(ROUTE, target_wiki=top_stem, ...)
    outcome  = await self._ingest_and_deliver(decision, ...)   # shared helper (D-local-1)
    log "tg.pipeline.hint_fastpath.silent_route" {target_wiki, score, margin, run_id}
    if outcome.status == "ok":
        others = [w for w in owner_wikis if w != decision.target_wiki]
        if others:
            kb = lambda pid: build_route_redirect_keyboard(pid, others)
            draft = PendingConfirmDraft(category="route_ingest",
                       draft=route_action_to_payload(decision, ...),
                       recap_text=ROUTE_SILENT_ACK_RU.format(wiki=target_wiki))
            await self._confirm.request_explicit(draft, keyboard_factory=kb)  # persists + sends ack
        else:
            await self._confirm.auto_ack(chat_id, ROUTE_SILENT_ACK_NOREDIR_RU.format(wiki=target_wiki))
    return
# (non-confident path unchanged — falls through to heavy router / existing confirm)
```

## Shared ingest helper (D-local-1)

`_handle_route_confirm` (pipeline.py:2252-2287) already does: `librarian.ingest` → on ok
`_active_wiki_set` + `_output.deliver(reply)`; else `sender.send_message(reply)`. Extract that tail
into `_ingest_and_deliver(...) -> IngestOutcome` and call it from both `_handle_route_confirm`
(after status=="confirmed") and the new silent branch. One ingest path, no fork.

## Redirect keyboard (D-local-2)

`build_route_redirect_keyboard(pending_id, other_wikis)` — identical to
`build_route_confirm_keyboard` but **without** the `[✅ Подтвердить] [❌ Отмена]` top row: only the
2-column `wikipick:<pending_id>:<idx>` picker. Reuses the existing `on_wikipick_callback` verbatim
(it already resolves the pending row 'correct' → 'corrected' and re-ingests into the picked WIKI,
excluding the proposed target). No new callback prefix, no new handler.

## Strings (Ru-only, D-032)

- `ROUTE_SILENT_ACK_RU = "✅ Записал в {wiki}. Не туда? Перенесу:"`
- `ROUTE_SILENT_ACK_NOREDIR_RU = "✅ Записал в {wiki}."`
- Loader: `"🔍 Определяю проект…"`

## Alternatives considered

1. **Single "↩️ Не туда" button → expands to picker.** Lower keyboard noise, but needs a new
   `redirect:` callback prefix + handler + a second message/edit. Rejected: more surface than the
   direct picker for marginal noise reduction; the picker is still strictly less noisy than today's
   mandatory Confirm keyboard.
2. **Free-text "не туда"/"нет" correction.** No keyboard, but needs last-route memory + NLU and
   risks false positives on ordinary messages. Rejected for MVP (non-deterministic).
3. **Git-revert undo.** No API on IngestOutcome; would require a relocation/revert subsystem.
   Rejected — out of scope (documented limitation, possible follow-up bead).
4. **Auto-route only on a stricter "very-confident" bar.** Considered, but /best-approach fixed the
   threshold as the existing absolute margin — no change.
