---
feature: inbox-wiki-route-confirm
bd_id: aisw-e45
epic: aisw-t2r
phase: "Inbox-WIKI Phase-C"
status: draft
created: 2026-05-12
requirements:
  functional:
    - id: FR-1
      text: "When the Stage-1a Router returns a RouterDecision with intent ∈ {ROUTE, CREATE_WIKI} AND a Librarian + OutputDelivery are wired, the pipeline NO LONGER executes the Phase-B move+ingest immediately; instead it builds a confirm draft and calls ConfirmationService.request_explicit(...) — an explicit-level (D-023) recap message with inline buttons is sent, and a pending row is persisted in sessions.pending_confirms with category='route_ingest', 10-minute TTL, and draft_json holding everything needed to replay the action (intent, target_wiki, notes, raw, parsed_ok, user_text, source, media_paths as strings, correlation_id)."
    - id: FR-2
      text: "intent ∈ {CLARIFY, REJECT}, or the no-Librarian / no-OutputDelivery case, keep the existing Phase-A notes-echo (reply decision.notes, no WIKI touched, no confirm row). The Stage-0→Stage-1a router dispatch and all Phase-A/B logging up to tg.pipeline.router.decided are unchanged."
    - id: FR-3
      text: "The recap message is Russian and states the proposed action derived from the decision: ROUTE → «Положу это в вики «<target_wiki>». Подтверждаешь?» plus decision.notes; CREATE_WIKI → «Заведу новую вики «<target_wiki>» и положу это туда. Подтверждаешь?» plus decision.notes. Buttons: «✅ Подтвердить» (confirm) and «❌ Отмена» (cancel) — a 2-button keyboard variant for route confirms (the 3-button correct/«Изменить» path is not meaningful for routing in the MVP and is omitted)."
    - id: FR-4
      text: "On the confirm callback (callback_data confirm:<pending_id>:confirm): ConfirmationService.resolve(...) flips the row to 'confirmed' race-safely; if it returned 'confirmed', the pipeline loads the pending row (get_pending), checks category=='route_ingest', reconstructs the RouterDecision from draft_json, and runs the Phase-B path — Librarian.ingest(decision, telegram_id=…, user_text=…, source=…, media_paths=…, correlation_id=…) — then delivers the reply exactly like Phase-B (status=='ok' → OutputDelivery.deliver(reply); else → send_message(reply)). If resolve returned None (already resolved / TTL-expired) → reply «Время на подтверждение истекло, пришли заново.» (or a 'already handled' line). The recap message's keyboard is removed after resolution (edit_reply_markup best-effort)."
    - id: FR-5
      text: "On the cancel callback (confirm:<pending_id>:cancel): resolve(...) flips to 'cancelled'; reply «Отменено. Файл остался в Inbox — можешь прислать заново с уточнением.» The staged raw (Inbox-WIKI/raw/<ts>_<source>.md sidecar + any media binary) is NOT deleted on cancel or TTL expiry (D-022 / Phase-B no-rollback philosophy — it's an audit artifact, content-addressed for media; a sweep job is a later ops task). expire_due is already run by the existing TTL sweeper (no new scheduler job)."
    - id: FR-6
      text: "Structlog anchors (all with correlation_id, telegram_id): tg.pipeline.route.confirm_requested (pending_id, intent, target_wiki); tg.pipeline.confirm.route_dispatched (pending_id, action) — emitted from on_confirm_callback when category=='route_ingest'; tg.pipeline.route.confirm_executed (pending_id, status, target_wiki, created, run_id) on confirm→ingest done; tg.pipeline.route.confirm_cancelled (pending_id); tg.pipeline.route.confirm_stale (pending_id) when resolve returns None. The existing tg.pipeline.confirm anchor in on_confirm_callback stays for non-route categories."
  non_functional:
    - id: NFR-1
      text: "Reuses ConfirmationService (request_explicit / resolve / get_pending / expire_due), the existing CONFIRM_CALLBACK_PREFIX handler in tg/handlers.py, the PendingConfirm ORM model, and the Phase-B Librarian.ingest path — no reimplementation of the confirm machinery or the ingest. NO new SQLite table, NO new Alembic migration (draft_json + category columns already exist on pending_confirms from chunk 10). NO new third-party dependency."
    - id: NFR-2
      text: "mypy --strict / ruff / ruff-format / grace lint clean; coverage stays ≥80%. New behaviour fully unit-tested with fakes (FakeConfirmationService recording request_explicit calls; FakeLibrarian; FakeOutputDelivery) — no live Telegram / Claude in unit tests."
    - id: NFR-3
      text: "Ru-only (D-032). All datetimes UTC (the confirm TTL already is). No bypass of pre-commit hooks. The recap-message keyboard removal on callback is best-effort (a failed edit_reply_markup must not break the ingest)."
    - id: NFR-4
      text: "media_paths in draft_json are serialised as POSIX path strings and re-hydrated to Path on replay; the staged files are expected to still exist (Phase-A staged them under Inbox-WIKI/raw/ + media_staging_root, which are not GC'd within the 10-min window). If a media path no longer exists at confirm time, Librarian.ingest handles it as Phase-B does (no special new handling here)."
  constraints:
    - "Phase-B's auto-execute is REPLACED, not kept behind a flag — confirm-before-mutate is strictly the desired behaviour (R-3 in the Phase-B discovery explicitly named the missing gate; this closes it). No route_confirm_enabled toggle in the MVP (KISS)."
    - "No new structured `proposed_actions` field on RouterDecision. The proposed action for Phase-C is fully derivable from (intent, target_wiki); a multi-action list (route + reminder + aggregator) only becomes necessary with the Phase-D cron bridge (aisw-kcz) and is out of scope here. The bd issue title's 'RouterDecision.proposed_actions' is interpreted as 'the action proposed by the RouterDecision', not a literal new field."
    - "The 3-button explicit keyboard (build_explicit_keyboard, with «Изменить») stays for the chunk-10 confirm category; route confirms use a new 2-button keyboard builder. parse_confirm_callback already accepts both 'confirm' and 'cancel' actions, so handlers.py needs no parser change — only the pipeline dispatch in on_confirm_callback branches on the pending row's category."
    - "ConfirmationService.request_explicit currently builds its keyboard internally via build_explicit_keyboard(pending_id). To send a 2-button keyboard for route confirms we either (a) add an optional keyboard-factory param to request_explicit, or (b) add a dedicated request_explicit_route(...) method, or (c) pass the recap text already and let the pipeline send its own keyboard via implicit_ack-style call but persist via request_explicit. Decided in Brainstorming — (a) is the lightest."
  risks:
    - id: R-1
      text: "Between request_explicit and the confirm callback (≤10 min) the user sends the SAME content again → Phase-A re-stages, Stage-1a re-runs, and request_explicit is called with a draft whose payload_hash collides with the pending row → request_explicit is idempotent on (telegram_id, payload_hash, status='pending') and returns the existing record without a new recap. Acceptable (no duplicate buttons). Mitigation: rely on the existing idempotency; verify with a unit test."
    - id: R-2
      text: "User taps Подтвердить twice (double-tap, or on two devices). resolve(...) is UPDATE … WHERE status='pending' → the second tap gets rowcount 0 → returns None → FR-4 'stale' reply, no second ingest. Race-safe by construction."
    - id: R-3
      text: "draft_json round-trip loses type info (Path → str, RouterIntent enum → str). Mitigation: explicit (de)serialiser with a unit test; reconstruct RouterIntent(decision_dict['intent']) and RouterDecision(**fields); Path(p) for each media path."
    - id: R-4
      text: "Latency: a routable turn now needs TWO user interactions (message → recap → tap → ingest) and the Stage-1b run happens on the callback, not the original message. The callback handler must answer the CallbackQuery promptly (aiogram answer()) before the (possibly ~30-300s) ingest, and ideally send a «Записываю…» line first. Mitigation: handlers.py already answers the callback; the pipeline sends a short ack before the ingest. Considered in Brainstorming."
    - id: R-5
      text: "expire_due flips rows to 'expired' but the user never gets a notice. Acceptable for the MVP (the file stays in Inbox, the user can resend). A 'твоё подтверждение устарело' nudge is a later UX refinement, not Phase-C."
  scope:
    in:
      - "Replace the Phase-B immediate librarian.ingest() call (in _run_text_pipeline's routable branch) with a confirm-request: build a route confirm draft + recap text + call ConfirmationService.request_explicit with a 2-button keyboard."
      - "Extend on_confirm_callback: after resolve, if the pending row's category=='route_ingest', dispatch to a new _execute_route_confirm path — reconstruct RouterDecision, run Librarian.ingest, deliver the reply, remove the keyboard; handle cancel and stale cases with ru messages."
      - "A draft (de)serialiser for the route action payload (RouterDecision + user_text + source + media_paths + correlation_id ↔ dict)."
      - "An optional keyboard-factory hook on ConfirmationService.request_explicit (or equivalent) + a build_route_confirm_keyboard(pending_id) 2-button builder."
      - "New structlog anchors per FR-6."
      - "Unit tests: routable ROUTE/CREATE_WIKI → request_explicit called with the right draft + keyboard, no immediate ingest; confirm callback → ingest + deliver; cancel callback → cancelled reply, no ingest; stale (resolve→None) → stale reply; CLARIFY/REJECT and no-librarian → unchanged notes-echo; draft round-trip; double-confirm idempotency. An e2e/integration scenario extending tests/integration: routable text → recap → simulate confirm callback → page written in the target <Domain>-WIKI."
      - "GRACE: update M-TG-PIPELINE-CLASSIFIER / M-TG-DOCUMENT-FULL (pipeline) + M-TG-TEXT (confirm) contracts + MODULE_MAP, knowledge-graph CrossLinks (pipeline → confirm now used for routing), verification-plan refs (new tests + log anchors), development-plan Phase-C entry, ADR if a notable decision (e.g. replace-vs-flag, keyboard variant) warrants one."
    out:
      - "Router → cron/job bridge (Phase-D / aisw-kcz) — the confirm path executes only the Phase-B move+ingest; cron creation hooks in later (the draft schema is forward-compatible but not used for cron here)."
      - "## Inbox hint fast-path + per-user media _staging migration (Phase-E / aisw-12t)."
      - "A literal `proposed_actions` list field on RouterDecision (deferred; not needed until Phase-D)."
      - "The «Изменить»/correct UX for route confirms (re-route flow) — omitted from the keyboard."
      - "TTL-expiry user notification, restore-from-trash on ROUTE to a soft-deleted name (lifecycle UX), per-WIKI git auto-commit on ingest."
      - "Any change to Stage-0 / Stage-1a / prompts/inbox.md / prompts/wiki.md."
---

# Discovery — Inbox-WIKI Phase-C: confirmation loop (aisw-e45)

## Context

Phase-A (aisw-dsg) stages raw content into `Inbox-WIKI/raw/` and runs the Stage-1a Router-Claude there, returning a parsed `RouterDecision`. Phase-B (aisw-zd9) wired the routable branch in `tg/pipeline.py`: a `ROUTE`/`CREATE_WIKI` decision is **immediately** executed — resolve/create the target `<Domain>-WIKI`, move the raw payload in, run the Stage-1b "librarian" Claude there, reply with `notes + summary`. Phase-B's own discovery (R-3) flagged this as the deliberate gap: *"Auto-executing a WIKI mutation from a single user message (no confirm) until Phase-C lands."*

Phase-C closes that gap: the routable decision is **proposed** to the user via inline buttons; the move+ingest runs only after the user taps «Подтвердить».

## What already exists (building blocks — do not reinvent)

1. `ConfirmationService` (`tg/confirm.py`, D-023) — `request_explicit(draft)` persists a `pending_confirms` row (10-min TTL, `status`/`category`/`chat_id`/`recap_message_id`/`draft_json`) and sends a recap + inline keyboard; `resolve(telegram_id, pending_id, action)` is a race-safe `UPDATE … WHERE status='pending'`; `get_pending(id)`; `expire_due()` (already swept).
2. `PendingConfirm` ORM model (`storage/sessions/models.py`) — has `category`, `draft_json`, `chat_id`, `recap_message_id` columns since chunk 10. **No migration needed.**
3. `handlers.py` — `@router.callback_query(F.data.startswith("confirm:"))` → `parse_confirm_callback` → `pipeline.on_confirm_callback(telegram_id, chat_id, pending_id, action)`. `parse_confirm_callback` already accepts `confirm` / `correct` / `cancel`. The callback is `answer()`-ed.
4. `DefaultPipeline._run_text_pipeline` routable branch (Phase-B) — currently calls `self._librarian.ingest(decision, …)` directly. `DefaultPipeline.on_confirm_callback` — currently just `confirm.resolve(...)` + a log line.
5. `Librarian` Protocol + `IngestOutcome` dataclass (`tg/pipeline.py`) — `ingest(decision, *, telegram_id, user_text, source, media_paths, correlation_id) -> IngestOutcome(status, reply, run_id, target_wiki, created)`.

## The change in one paragraph

In `_run_text_pipeline`'s routable branch, where Phase-B does `await self._librarian.ingest(...)`, instead build a `route_ingest` confirm draft (the `RouterDecision` fields + `user_text` + `source` + `media_paths` + `correlation_id`) and a Russian recap with «Подтвердить»/«Отмена» buttons, then `await self._confirm.request_explicit(draft_with_2button_keyboard)`. In `on_confirm_callback`, after `resolve(...)`: if the pending row's `category == 'route_ingest'` and the new status is `'confirmed'` → reconstruct the `RouterDecision`, run `Librarian.ingest(...)`, deliver the reply like Phase-B; if `'cancelled'` → ru "Отменено" line; if `resolve` returned `None` → ru "stale" line; remove the recap keyboard best-effort. Non-`route_ingest` categories keep today's behaviour.

## Open questions for Brainstorming

1. How to make `ConfirmationService` send a **2-button** keyboard for route confirms — optional `keyboard_factory` param on `request_explicit` (lightest) vs a dedicated `request_explicit_route(...)` vs pipeline-builds-keyboard. → leaning: optional `keyboard: Callable[[int], InlineKeyboardMarkup] | None = None` param defaulting to `build_explicit_keyboard`.
2. Send a short «Записываю в вики…» ack on confirm **before** the (slow) Stage-1b ingest? → leaning: yes (R-4).
3. ADR-worthy? Probably one short ADR: "Phase-C — confirm-before-route replaces Phase-B auto-execute; no `proposed_actions` field; no toggle" — captures the deliberate non-introduction of a structured actions field and the replace-not-flag choice. → decide after Brainstorming.
4. Cancel/TTL → keep the staged raw in Inbox (no delete) — confirm this is acceptable (it is, per D-022 + Phase-B R-2). → leaning: yes, keep.
5. Where does the route-confirm draft (de)serialiser live — a small helper in `tg/pipeline.py`, or `inbox/route.py` (Phase-B's helper module, if it exists)? → decide in Brainstorming after re-reading the Phase-B layout.
