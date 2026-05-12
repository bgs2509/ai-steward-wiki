# Inbox-WIKI Phase-C: confirmation loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert a one-tap user confirmation between the Stage-1a router decision and the Stage-1b move+ingest — a `ROUTE`/`CREATE_WIKI` `RouterDecision` is persisted as a `route_ingest` pending-confirm row and proposed via inline buttons; the WIKI mutation runs in `on_confirm_callback` only on "confirmed".

**Architecture:** Additive — `inbox/route.py` gains a `RouteAction` (de)serialiser, `tg/confirm.py` gains a 2-button keyboard builder + a `keyboard_factory` param on `request_explicit`, `tg/pipeline.py`'s routable branch swaps the immediate `librarian.ingest()` for `confirmation.request_explicit(...)` and `on_confirm_callback` grows a `route_ingest` dispatch (execute / cancel / stale). `handlers.py` is untouched. Reuses the `pending_confirms` table (no migration).

**Tech Stack:** Python 3.11, aiogram 3.x (`InlineKeyboardMarkup`/`InlineKeyboardButton`), SQLAlchemy async, pydantic v2 (`RouterDecision`), structlog, pytest/pytest-asyncio.

**bd:** aisw-e45 (epic aisw-t2r). Spec: `docs/superpowers/specs/20260512-inbox-wiki-route-confirm-{discovery,design}.md`. D-023, D-022, D-032.

---

## Task 1: `RouteAction` (de)serialiser in `inbox/route.py`

**Files:**
- Modify: `src/ai_steward_wiki/inbox/route.py` (add `RouteAction`, `route_action_to_payload`, `route_action_from_payload`; extend `__all__` + MODULE_MAP + CHANGE_SUMMARY)
- Test: `tests/unit/inbox/test_route.py` (extend)

- [ ] **Step 1: Write the failing test** — append to `tests/unit/inbox/test_route.py`:

```python
import pytest
from pathlib import Path
from ai_steward_wiki.inbox.router import RouterDecision, RouterIntent
from ai_steward_wiki.inbox.route import (
    RouteAction,
    route_action_to_payload,
    route_action_from_payload,
)


def _decision(intent=RouterIntent.ROUTE, target="Travel-WIKI"):
    return RouterDecision(
        intent=intent, target_wiki=target, notes="Положу в Travel-WIKI.",
        raw="```router\nintent: route\n```", parsed_ok=True,
    )


@pytest.mark.parametrize("intent", [RouterIntent.ROUTE, RouterIntent.CREATE_WIKI])
@pytest.mark.parametrize("media", [[], [Path("/tmp/raw/a.jpg"), Path("/tmp/raw/b.png")]])
def test_route_action_payload_roundtrip(intent, media):
    decision = _decision(intent)
    payload = route_action_to_payload(
        decision, user_text="вот билет", source="photo" if media else "text",
        media_paths=media, correlation_id="tg-5-42",
    )
    # payload must be plain JSON-able types
    import json
    assert json.loads(json.dumps(payload)) == payload
    back = route_action_from_payload(payload)
    assert isinstance(back, RouteAction)
    assert back.decision == decision
    assert back.user_text == "вот билет"
    assert back.source == ("photo" if media else "text")
    assert back.media_paths == [str(p) for p in media]
    assert back.correlation_id == "tg-5-42"


def test_route_action_from_payload_tolerates_missing_media():
    payload = route_action_to_payload(
        _decision(), user_text="x", source="text", media_paths=None, correlation_id="c",
    )
    assert payload["media_paths"] == []
    assert route_action_from_payload(payload).media_paths == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/inbox/test_route.py -k route_action -v`
Expected: FAIL — `ImportError: cannot import name 'RouteAction'`.

- [ ] **Step 3: Write minimal implementation** — in `src/ai_steward_wiki/inbox/route.py`, add after the existing dataclasses (and add the names to `__all__` and the MODULE_MAP header block):

```python
@dataclass(frozen=True, slots=True)
class RouteAction:
    """A staged route+ingest action awaiting user confirmation (Phase-C, aisw-e45).

    Persisted as JSON in pending_confirms.draft_json; replayed by the pipeline's
    on_confirm_callback to drive Librarian.ingest after the user taps Подтвердить.
    """

    decision: RouterDecision
    user_text: str
    source: _RawSource
    media_paths: list[str]  # POSIX path strings (re-hydrated to Path on replay)
    correlation_id: str


def route_action_to_payload(
    decision: RouterDecision,
    *,
    user_text: str,
    source: _RawSource,
    media_paths: list[Path] | None,
    correlation_id: str,
) -> dict[str, object]:
    """Serialise a route action to a plain JSON-able dict for draft_json."""
    return {
        "decision": decision.model_dump(mode="json"),
        "user_text": user_text,
        "source": source,
        "media_paths": [Path(p).as_posix() for p in (media_paths or [])],
        "correlation_id": correlation_id,
    }


def route_action_from_payload(payload: dict[str, object]) -> RouteAction:
    """Inverse of route_action_to_payload — reconstruct typed RouteAction."""
    raw_decision = payload.get("decision")
    if not isinstance(raw_decision, dict):
        raise ValueError("route action payload missing 'decision' object")
    decision = RouterDecision(**raw_decision)
    raw_media = payload.get("media_paths") or []
    media = [str(p) for p in raw_media] if isinstance(raw_media, list) else []
    source = payload.get("source")
    if source not in ("text", "voice", "document", "photo"):
        raise ValueError(f"route action payload bad source: {source!r}")
    return RouteAction(
        decision=decision,
        user_text=str(payload.get("user_text", "")),
        source=source,  # type: ignore[arg-type]
        media_paths=media,
        correlation_id=str(payload.get("correlation_id", "")),
    )
```

Also: add `RouteAction`, `route_action_from_payload`, `route_action_to_payload` to `__all__` (keep it sorted), add three MODULE_MAP lines, and bump `VERSION` + add a CHANGE_SUMMARY line `v0.0.2 - aisw-e45 (Phase-C): RouteAction + route_action_to/from_payload (confirm-loop draft round-trip)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/inbox/test_route.py -k route_action -v`
Expected: PASS (all parametrizations).

- [ ] **Step 5: Commit**

```bash
git add src/ai_steward_wiki/inbox/route.py tests/unit/inbox/test_route.py
git commit -m "feat(M-INBOX-ROUTE): RouteAction (de)serialiser for the Phase-C confirm loop (aisw-e45)"
```

---

## Task 2: `build_route_confirm_keyboard` in `tg/confirm.py`

**Files:**
- Modify: `src/ai_steward_wiki/tg/confirm.py` (add `build_route_confirm_keyboard`; extend `__all__` + MODULE_MAP)
- Test: `tests/unit/tg/test_confirm.py` (extend)

- [ ] **Step 1: Write the failing test** — append to `tests/unit/tg/test_confirm.py`:

```python
def test_build_route_confirm_keyboard_has_two_buttons() -> None:
    from ai_steward_wiki.tg.confirm import build_route_confirm_keyboard, BTN_CONFIRM, BTN_CANCEL

    kb = build_route_confirm_keyboard(77)
    rows = kb.inline_keyboard
    assert len(rows) == 2
    assert rows[0][0].text == BTN_CONFIRM
    assert rows[0][0].callback_data == "confirm:77:confirm"
    assert rows[1][0].text == BTN_CANCEL
    assert rows[1][0].callback_data == "confirm:77:cancel"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/tg/test_confirm.py -k route_confirm_keyboard -v`
Expected: FAIL — `ImportError: cannot import name 'build_route_confirm_keyboard'`.

- [ ] **Step 3: Write minimal implementation** — in `src/ai_steward_wiki/tg/confirm.py`, add right after `build_explicit_keyboard`:

```python
def build_route_confirm_keyboard(pending_id: int) -> Any:
    """2-button keyboard for Inbox-WIKI route confirms (Phase-C, aisw-e45).

    Callback data: ``confirm:<pending_id>:confirm`` / ``confirm:<pending_id>:cancel``
    — reuses the existing ``confirm:`` callback prefix and parser (no «Изменить»).
    """
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_CONFIRM, callback_data=f"confirm:{pending_id}:confirm")],
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data=f"confirm:{pending_id}:cancel")],
        ]
    )
```

Add `"build_route_confirm_keyboard"` to `__all__` (sorted) and a MODULE_MAP line `build_route_confirm_keyboard - 2-button route-confirm keyboard (Phase-C)`. Add a CHANGE_SUMMARY line `v0.0.2 - aisw-e45 (Phase-C): build_route_confirm_keyboard + request_explicit keyboard_factory`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/tg/test_confirm.py -k route_confirm_keyboard -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_steward_wiki/tg/confirm.py tests/unit/tg/test_confirm.py
git commit -m "feat(M-TG-TEXT): 2-button route-confirm keyboard builder (aisw-e45)"
```

---

## Task 3: `keyboard_factory` param on `ConfirmationService.request_explicit`

**Files:**
- Modify: `src/ai_steward_wiki/tg/confirm.py` (`request_explicit` signature + body)
- Test: `tests/unit/tg/test_confirm.py` (extend)

- [ ] **Step 1: Write the failing test** — append to `tests/unit/tg/test_confirm.py`:

```python
@pytest.mark.asyncio
async def test_request_explicit_uses_custom_keyboard_factory(session_maker) -> None:
    from ai_steward_wiki.tg.confirm import (
        ConfirmationService, PendingConfirmDraft, build_route_confirm_keyboard,
    )

    sender = FakeSender()
    svc = ConfirmationService(sender, session_maker)
    draft = PendingConfirmDraft(
        telegram_id=1, chat_id=2, category="route_ingest",
        draft={"decision": {"intent": "route"}}, recap_text="Положу в Travel-WIKI. Подтверждаешь?",
    )
    rec = await svc.request_explicit(draft, keyboard_factory=build_route_confirm_keyboard)

    kb = sender.sends[0]["reply_markup"]
    assert len(kb.inline_keyboard) == 2  # route keyboard, not the 3-button default
    assert kb.inline_keyboard[0][0].callback_data == f"confirm:{rec.pending_id}:confirm"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/tg/test_confirm.py -k custom_keyboard_factory -v`
Expected: FAIL — `TypeError: request_explicit() got an unexpected keyword argument 'keyboard_factory'`.

- [ ] **Step 3: Write minimal implementation** — in `src/ai_steward_wiki/tg/confirm.py`:

1. Add a `Callable` import: change `from typing import TYPE_CHECKING, Any, Literal` and add `from collections.abc import Callable` near the other stdlib imports.
2. Change the method signature and the one keyboard line:

```python
    async def request_explicit(
        self,
        draft: PendingConfirmDraft,
        *,
        keyboard_factory: Callable[[int], Any] = build_explicit_keyboard,
    ) -> ConfirmRecord:
```

and replace `keyboard = build_explicit_keyboard(pending_id)` with `keyboard = keyboard_factory(pending_id)`.

(The duplicate-detection early-return path doesn't send a keyboard, so it's unaffected.)

- [ ] **Step 4: Run tests to verify pass + no regression**

Run: `uv run pytest tests/unit/tg/test_confirm.py -v`
Expected: PASS — incl. `test_request_explicit_persists_row_and_sends_3_button_keyboard` (default factory still 3-button) and the new test.

- [ ] **Step 5: Commit**

```bash
git add src/ai_steward_wiki/tg/confirm.py tests/unit/tg/test_confirm.py
git commit -m "feat(M-TG-TEXT): request_explicit accepts a keyboard_factory (aisw-e45)"
```

---

## Task 4: pipeline routable branch → request a route confirm (replace Phase-B auto-ingest)

**Files:**
- Modify: `src/ai_steward_wiki/tg/pipeline.py` (recap constants, `build_route_recap`, the routable branch in `_run_text_pipeline`; MODULE_CONTRACT CHANGE_SUMMARY + MODULE_MAP + `__all__`)
- Test: `tests/unit/tg/test_pipeline_route_confirm.py` (new); `tests/unit/tg/test_pipeline_route_ingest.py` (update — Phase-B tests now go through the confirm gate)

- [ ] **Step 1: Write the failing test** — create `tests/unit/tg/test_pipeline_route_confirm.py`:

```python
"""Unit tests for the Phase-C route confirm gate in DefaultPipeline (aisw-e45)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier.schema import ClassifierResult, Intent
from ai_steward_wiki.inbox.route import route_action_from_payload
from ai_steward_wiki.inbox.router import RouterDecision, RouterIntent
from ai_steward_wiki.tg.pipeline import DefaultPipeline, IngestOutcome
from tests.unit.tg.conftest import FakeSender


def _classifier(intent: Intent = Intent.WIKI_INGEST) -> MagicMock:
    cls = MagicMock()
    cls.classify = AsyncMock(
        return_value=ClassifierResult(
            intent=intent, confidence=0.9, distilled_payload={"q": "x"},
            backend="fake", model="m", prompt_semver="1.1.0", prompt_sha256="a" * 64, latency_ms=1,
        )
    )
    return cls


def _idem() -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=True)
    idem.check_content = AsyncMock(return_value=("b" * 64, None))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


def _router(intent=RouterIntent.ROUTE, target="Travel-WIKI", notes="Положу в Travel-WIKI.") -> MagicMock:
    rt = MagicMock()
    rt.route = AsyncMock(return_value=RouterDecision(
        intent=intent, target_wiki=target, notes=notes, raw="```router\n...\n```", parsed_ok=True,
    ))
    return rt


def _confirm() -> MagicMock:
    c = MagicMock()
    rec = MagicMock()
    rec.pending_id = 555
    rec.recap_message_id = 1234
    c.request_explicit = AsyncMock(return_value=rec)
    return c


def _librarian(outcome: IngestOutcome | None = None) -> MagicMock:
    lib = MagicMock()
    lib.ingest = AsyncMock(return_value=outcome or IngestOutcome(
        status="ok", reply="Положу в Travel-WIKI.\n\nЗаписал.", run_id="ing-1",
        target_wiki="Travel-WIKI", created=False,
    ))
    return lib


def _output() -> MagicMock:
    out = MagicMock()
    out.deliver = AsyncMock(return_value=None)
    return out


def _runner() -> MagicMock:
    from ai_steward_wiki.tg.pipeline import WikiRunOutcome
    r = MagicMock()
    r.run = AsyncMock(return_value=WikiRunOutcome(run_id="run-x", text="legacy", latency_ms=1))
    return r


def _pipe(*, sender, confirmation=None, router=None, librarian=None, output=None, intent=Intent.WIKI_INGEST):
    return DefaultPipeline(
        sender=sender, idempotency=_idem(), confirmation=confirmation or _confirm(),
        classifier=_classifier(intent), runner=_runner(), output=output or _output(),
        router=router or _router(), librarian=librarian or _librarian(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", [RouterIntent.ROUTE, RouterIntent.CREATE_WIKI])
async def test_routable_decision_requests_confirm_not_ingest(intent: RouterIntent) -> None:
    sender = FakeSender()
    confirm = _confirm()
    lib = _librarian()
    pipe = _pipe(sender=sender, confirmation=confirm, router=_router(intent), librarian=lib)

    await pipe.on_text(telegram_id=42, chat_id=10, update_id=5, text="вот авиабилет")

    lib.ingest.assert_not_awaited()
    confirm.request_explicit.assert_awaited_once()
    call = confirm.request_explicit.await_args
    draft = call.args[0]
    assert draft.telegram_id == 42 and draft.chat_id == 10
    assert draft.category == "route_ingest"
    # keyboard_factory must be the 2-button route builder
    from ai_steward_wiki.tg.confirm import build_route_confirm_keyboard
    assert call.kwargs["keyboard_factory"] is build_route_confirm_keyboard
    # recap mentions the target + the decision notes
    assert "Travel-WIKI" in draft.recap_text and "Положу в Travel-WIKI." in draft.recap_text
    # draft payload round-trips back to the same decision + context
    action = route_action_from_payload(draft.draft)
    assert action.decision.intent is intent
    assert action.user_text == "вот авиабилет"
    assert action.source == "text"
    assert action.correlation_id == "tg-5-42"
    assert action.media_paths == []


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", [RouterIntent.CLARIFY, RouterIntent.REJECT])
async def test_clarify_reject_still_notes_echo_no_confirm(intent: RouterIntent) -> None:
    sender = FakeSender()
    confirm = _confirm()
    router = _router(intent, None, "Уточни?" if intent is RouterIntent.CLARIFY else "Не по адресу.")
    pipe = _pipe(sender=sender, confirmation=confirm, router=router, librarian=_librarian())

    await pipe.on_text(telegram_id=1, chat_id=2, update_id=3, text="?")

    confirm.request_explicit.assert_not_awaited()
    assert sender.sends[0]["text"] in ("Уточни?", "Не по адресу.")


@pytest.mark.asyncio
async def test_no_librarian_still_notes_echo_no_confirm() -> None:
    sender = FakeSender()
    confirm = _confirm()
    pipe = DefaultPipeline(
        sender=sender, idempotency=_idem(), confirmation=confirm, classifier=_classifier(),
        runner=_runner(), output=_output(), router=_router(RouterIntent.ROUTE), librarian=None,
    )
    await pipe.on_text(telegram_id=1, chat_id=2, update_id=3, text="вот билет")
    confirm.request_explicit.assert_not_awaited()
    assert sender.sends[0]["text"] == "Положу в Travel-WIKI."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/tg/test_pipeline_route_confirm.py -v`
Expected: FAIL — `test_routable_decision_requests_confirm_not_ingest` fails because the pipeline still calls `librarian.ingest`.

- [ ] **Step 3: Write minimal implementation** in `src/ai_steward_wiki/tg/pipeline.py`:

(a) Add imports near the existing ones:

```python
from ai_steward_wiki.inbox.route import route_action_from_payload, route_action_to_payload
from ai_steward_wiki.tg.confirm import ConfirmationService, PendingConfirmDraft, build_route_confirm_keyboard
```

(replace the existing `from ai_steward_wiki.tg.confirm import ConfirmationService` line).

(b) Add constants next to the other `*_RU` constants:

```python
ROUTE_CONFIRM_RECAP_ROUTE_RU = "Положу это в вики «{target}».\n\n{notes}\n\nПодтверждаешь?"
ROUTE_CONFIRM_RECAP_CREATE_RU = "Заведу новую вики «{target}» и положу это туда.\n\n{notes}\n\nПодтверждаешь?"
ROUTE_CONFIRM_ACK_RU = "\U0001f4dd Записываю в вики…"
ROUTE_CONFIRM_CANCELLED_RU = "Отменено. Файл остался в Inbox — пришли заново с уточнением."  # noqa: RUF001
ROUTE_CONFIRM_STALE_RU = "Время на подтверждение истекло — пришли заново."  # noqa: RUF001
```

Add these five names to `__all__` (sorted) and a MODULE_MAP line each (one-liners).

(c) Add a module-level helper:

```python
def build_route_recap(decision: RouterDecision) -> str:
    """Russian recap text for a route confirm (Phase-C)."""
    target = decision.target_wiki or "?"
    tmpl = (
        ROUTE_CONFIRM_RECAP_CREATE_RU
        if decision.intent is RouterIntent.CREATE_WIKI
        else ROUTE_CONFIRM_RECAP_ROUTE_RU
    )
    return tmpl.format(target=target, notes=decision.notes)
```

(d) In `_run_text_pipeline`, replace the Phase-B block (the `if decision.intent in (RouterIntent.ROUTE, RouterIntent.CREATE_WIKI) and self._librarian is not None and self._output is not None:` body that currently logs `tg.pipeline.route.ingest_dispatched`, calls `self._librarian.ingest(...)`, logs `tg.pipeline.route.delivered`, and delivers) with:

```python
            if (
                decision.intent in (RouterIntent.ROUTE, RouterIntent.CREATE_WIKI)
                and self._librarian is not None
                and self._output is not None
            ):
                payload = route_action_to_payload(
                    decision,
                    user_text=text,
                    source=source,
                    media_paths=media_paths,
                    correlation_id=correlation_id,
                )
                draft = PendingConfirmDraft(
                    telegram_id=telegram_id,
                    chat_id=chat_id,
                    category="route_ingest",
                    draft=payload,
                    recap_text=build_route_recap(decision),
                )
                rec = await self._confirm.request_explicit(
                    draft, keyboard_factory=build_route_confirm_keyboard
                )
                _log.info(
                    "tg.pipeline.route.confirm_requested",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                    pending_id=rec.pending_id,
                    intent=decision.intent.value,
                    target_wiki=decision.target_wiki,
                    source=source,
                )
                return
            await self._sender.send_message(chat_id, decision.notes)
            return
```

Bump `pipeline.py` `VERSION` and add a CHANGE_SUMMARY line: `v0.6.0 - aisw-e45 (Phase-C): routable ROUTE/CREATE_WIKI no longer ingests immediately — builds a route_ingest confirm draft + 2-button keyboard via ConfirmationService.request_explicit; the move+ingest moves to on_confirm_callback. New anchors tg.pipeline.route.confirm_requested|confirm_executed|confirm_cancelled|confirm_stale; tg.pipeline.confirm.route_dispatched.` Also update the MODULE_CONTRACT `DEPENDS` to add `ai_steward_wiki.inbox.route` and the `LINKS` to add `aisw-e45`, and the `Librarian`/route notes in the docstring as needed.

- [ ] **Step 4: Update the existing Phase-B test file** `tests/unit/tg/test_pipeline_route_ingest.py` — its tests asserted `on_text` ingests immediately; that path is now the confirm callback. Two options: (i) move those assertions into a new "via confirm callback" helper, or (ii) since Task 5 adds a dedicated callback test file, simply **delete** the now-obsolete `test_routable_decision_invokes_librarian_and_delivers_on_ok`, `test_rejected_outcome_sends_message_not_deliver`, `test_run_failed_outcome_sends_message` from `test_pipeline_route_ingest.py` and keep only the ones still valid (`test_clarify_decision_does_not_invoke_librarian`, `test_no_librarian_falls_through_to_notes_echo` — these still pass). Pick (ii) (the ingest-on-confirm behaviour is covered by Task 5). Update the module docstring to note the dispatch moved to `on_confirm_callback` (aisw-e45) and that `test_pipeline_route_confirm.py` + `test_pipeline_confirm_callback.py` own the new behaviour. Also fix the `_pipe` helper if it referenced the removed tests — leave the helper, just drop the dead tests.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/tg/test_pipeline_route_confirm.py tests/unit/tg/test_pipeline_route_ingest.py tests/unit/tg/test_pipeline_router.py -v`
Expected: PASS — new confirm tests pass; the trimmed route_ingest file passes; router-dispatch tests unaffected.

- [ ] **Step 6: Commit**

```bash
git add src/ai_steward_wiki/tg/pipeline.py tests/unit/tg/test_pipeline_route_confirm.py tests/unit/tg/test_pipeline_route_ingest.py
git commit -m "feat(M-TG-PIPELINE-CLASSIFIER): route confirm gate — propose ROUTE/CREATE_WIKI via inline buttons instead of auto-ingest (aisw-e45)"
```

---

## Task 5: `on_confirm_callback` — `route_ingest` dispatch (execute / cancel / stale)

**Files:**
- Modify: `src/ai_steward_wiki/tg/pipeline.py` (`DefaultPipeline.on_confirm_callback` + a private `_execute_route_confirm` helper; add `import json` and `from pathlib import Path` if not present — `Path` is already imported)
- Test: `tests/unit/tg/test_pipeline_confirm_callback.py` (new)

- [ ] **Step 1: Write the failing test** — create `tests/unit/tg/test_pipeline_confirm_callback.py`:

```python
"""Unit tests for the Phase-C route_ingest dispatch in on_confirm_callback (aisw-e45)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.inbox.route import route_action_to_payload
from ai_steward_wiki.inbox.router import RouterDecision, RouterIntent
from ai_steward_wiki.tg.pipeline import (
    DefaultPipeline,
    IngestOutcome,
    ROUTE_CONFIRM_ACK_RU,
    ROUTE_CONFIRM_CANCELLED_RU,
    ROUTE_CONFIRM_STALE_RU,
)
from tests.unit.tg.conftest import FakeSender


def _decision(intent=RouterIntent.ROUTE, target="Travel-WIKI"):
    return RouterDecision(intent=intent, target_wiki=target, notes="Положу в Travel-WIKI.",
                          raw="```router\n...\n```", parsed_ok=True)


def _pending_row(category="route_ingest", *, decision=None, media=None):
    row = MagicMock()
    row.category = category
    row.draft_json = json.dumps(route_action_to_payload(
        decision or _decision(), user_text="вот билет", source="photo" if media else "text",
        media_paths=media, correlation_id="tg-5-42",
    ))
    return row


def _confirm(*, pending_row, resolve_status):
    c = MagicMock()
    c.get_pending = AsyncMock(return_value=pending_row)
    c.resolve = AsyncMock(return_value=resolve_status)
    return c


def _librarian(outcome: IngestOutcome | None = None) -> MagicMock:
    lib = MagicMock()
    lib.ingest = AsyncMock(return_value=outcome or IngestOutcome(
        status="ok", reply="Положу в Travel-WIKI.\n\nЗаписал.", run_id="ing-1",
        target_wiki="Travel-WIKI", created=False))
    return lib


def _output() -> MagicMock:
    out = MagicMock()
    out.deliver = AsyncMock(return_value=None)
    return out


def _pipe(*, sender, confirmation, librarian=None, output=None):
    return DefaultPipeline(sender=sender, idempotency=MagicMock(), confirmation=confirmation,
                           librarian=librarian or _librarian(), output=output or _output())


@pytest.mark.asyncio
async def test_confirm_executes_ingest_and_delivers() -> None:
    sender = FakeSender()
    row = _pending_row(decision=_decision(RouterIntent.CREATE_WIKI), media=["/tmp/raw/a.jpg"])
    confirm = _confirm(pending_row=row, resolve_status="confirmed")
    lib = _librarian()
    out = _output()
    pipe = _pipe(sender=sender, confirmation=confirm, librarian=lib, output=out)

    await pipe.on_confirm_callback(telegram_id=42, chat_id=10, pending_id=555, action="confirm")

    # short ack sent before the ingest
    assert sender.sends[0]["text"] == ROUTE_CONFIRM_ACK_RU
    lib.ingest.assert_awaited_once()
    kw = lib.ingest.await_args.kwargs
    assert kw["telegram_id"] == 42 and kw["user_text"] == "вот билет" and kw["source"] == "photo"
    assert kw["correlation_id"] == "tg-5-42"
    assert [str(p) for p in kw["media_paths"]] == ["/tmp/raw/a.jpg"]
    assert lib.ingest.await_args.args[0].intent is RouterIntent.CREATE_WIKI
    out.deliver.assert_awaited_once()
    assert out.deliver.await_args.kwargs["text"] == "Положу в Travel-WIKI.\n\nЗаписал."
    assert out.deliver.await_args.kwargs["run_id"] == "ing-1"


@pytest.mark.asyncio
async def test_confirm_run_failed_sends_message_not_deliver() -> None:
    sender = FakeSender()
    confirm = _confirm(pending_row=_pending_row(), resolve_status="confirmed")
    lib = _librarian(IngestOutcome(status="run_failed", reply="Положу в Travel-WIKI.\n\nПозже.",
                                   run_id="ing-2", target_wiki="Travel-WIKI", created=True))
    out = _output()
    pipe = _pipe(sender=sender, confirmation=confirm, librarian=lib, output=out)

    await pipe.on_confirm_callback(telegram_id=1, chat_id=2, pending_id=9, action="confirm")

    out.deliver.assert_not_awaited()
    assert sender.sends[-1]["text"] == "Положу в Travel-WIKI.\n\nПозже."


@pytest.mark.asyncio
async def test_cancel_sends_cancelled_no_ingest() -> None:
    sender = FakeSender()
    confirm = _confirm(pending_row=_pending_row(), resolve_status="cancelled")
    lib = _librarian()
    pipe = _pipe(sender=sender, confirmation=confirm, librarian=lib)

    await pipe.on_confirm_callback(telegram_id=1, chat_id=2, pending_id=9, action="cancel")

    lib.ingest.assert_not_awaited()
    assert sender.sends[0]["text"] == ROUTE_CONFIRM_CANCELLED_RU


@pytest.mark.asyncio
async def test_stale_resolve_none_sends_stale_no_ingest() -> None:
    sender = FakeSender()
    confirm = _confirm(pending_row=_pending_row(), resolve_status=None)
    lib = _librarian()
    pipe = _pipe(sender=sender, confirmation=confirm, librarian=lib)

    await pipe.on_confirm_callback(telegram_id=1, chat_id=2, pending_id=9, action="confirm")

    lib.ingest.assert_not_awaited()
    assert sender.sends[0]["text"] == ROUTE_CONFIRM_STALE_RU


@pytest.mark.asyncio
async def test_non_route_category_uses_legacy_resolve() -> None:
    sender = FakeSender()
    row = _pending_row(category="reminder.create")
    confirm = _confirm(pending_row=row, resolve_status="confirmed")
    lib = _librarian()
    pipe = _pipe(sender=sender, confirmation=confirm, librarian=lib)

    await pipe.on_confirm_callback(telegram_id=1, chat_id=2, pending_id=9, action="confirm")

    confirm.resolve.assert_awaited_once_with(1, 9, "confirm")
    lib.ingest.assert_not_awaited()
    assert sender.sends == []  # legacy path: no extra message in the MVP


@pytest.mark.asyncio
async def test_missing_pending_row_uses_legacy_resolve() -> None:
    sender = FakeSender()
    confirm = MagicMock()
    confirm.get_pending = AsyncMock(return_value=None)
    confirm.resolve = AsyncMock(return_value=None)
    pipe = _pipe(sender=sender, confirmation=confirm, librarian=_librarian())

    await pipe.on_confirm_callback(telegram_id=1, chat_id=2, pending_id=9, action="confirm")

    confirm.resolve.assert_awaited_once_with(1, 9, "confirm")
    assert sender.sends == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/tg/test_pipeline_confirm_callback.py -v`
Expected: FAIL — current `on_confirm_callback` ignores `category` and never calls `get_pending`/`librarian.ingest`.

- [ ] **Step 3: Write minimal implementation** — replace `DefaultPipeline.on_confirm_callback` in `src/ai_steward_wiki/tg/pipeline.py` with:

```python
    async def on_confirm_callback(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        pending_id: int,
        action: ConfirmKeyboardAction,
    ) -> None:
        pending = await self._confirm.get_pending(pending_id)
        if pending is None or getattr(pending, "category", None) != "route_ingest":
            status = await self._confirm.resolve(telegram_id, pending_id, action)
            _log.info(
                "tg.pipeline.confirm",
                telegram_id=telegram_id,
                chat_id=chat_id,
                pending_id=pending_id,
                action=action,
                status=status,
            )
            return
        await self._handle_route_confirm(
            telegram_id=telegram_id,
            chat_id=chat_id,
            pending_id=pending_id,
            action=action,
            draft_json=pending.draft_json,
        )

    async def _handle_route_confirm(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        pending_id: int,
        action: ConfirmKeyboardAction,
        draft_json: str | None,
    ) -> None:
        """Resolve a route_ingest pending row and execute / cancel / report-stale."""
        import json as _json

        status = await self._confirm.resolve(telegram_id, pending_id, action)
        _log.info(
            "tg.pipeline.confirm.route_dispatched",
            telegram_id=telegram_id,
            chat_id=chat_id,
            pending_id=pending_id,
            action=action,
            status=status,
        )
        if status is None:
            await self._sender.send_message(chat_id, ROUTE_CONFIRM_STALE_RU)
            _log.info(
                "tg.pipeline.route.confirm_stale",
                telegram_id=telegram_id,
                pending_id=pending_id,
            )
            return
        if status != "confirmed":  # cancelled / corrected
            await self._sender.send_message(chat_id, ROUTE_CONFIRM_CANCELLED_RU)
            _log.info(
                "tg.pipeline.route.confirm_cancelled",
                telegram_id=telegram_id,
                pending_id=pending_id,
                status=status,
            )
            return

        action_obj = route_action_from_payload(_json.loads(draft_json or "{}"))
        correlation_id = action_obj.correlation_id or f"confirm-{pending_id}-{telegram_id}"
        await self._sender.send_message(chat_id, ROUTE_CONFIRM_ACK_RU)
        assert self._librarian is not None  # route_ingest rows are only created when wired
        assert self._output is not None
        outcome = await self._librarian.ingest(
            action_obj.decision,
            telegram_id=telegram_id,
            user_text=action_obj.user_text,
            source=action_obj.source,
            media_paths=[Path(p) for p in action_obj.media_paths] or None,
            correlation_id=correlation_id,
        )
        _log.info(
            "tg.pipeline.route.confirm_executed",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            pending_id=pending_id,
            status=outcome.status,
            target_wiki=outcome.target_wiki,
            created=outcome.created,
            run_id=outcome.run_id,
        )
        if outcome.status == "ok":
            await self._output.deliver(
                chat_id=chat_id,
                telegram_id=telegram_id,
                run_id=outcome.run_id or "",
                text=outcome.reply,
            )
        else:
            await self._sender.send_message(chat_id, outcome.reply)
```

(Note: `media_paths=[Path(p) for p in action_obj.media_paths] or None` — an empty list becomes `None`, matching how Phase-B's `_run_text_pipeline` passes `media_paths`. If `mypy` flags the `or None` on a `list`, write it explicitly: `media = [Path(p) for p in action_obj.media_paths]; ... media_paths=media or None`.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/tg/test_pipeline_confirm_callback.py tests/unit/tg/test_pipeline.py tests/unit/tg/test_handlers.py -v`
Expected: PASS — new callback tests pass; the existing `test_pipeline.py` confirm test (if any asserted the old log line) may need a tweak — if `test_pipeline.py` has a test like `test_on_confirm_callback_resolves` that mocks `confirmation.resolve` but NOT `confirmation.get_pending`, add `confirmation.get_pending = AsyncMock(return_value=None)` to that test's fake so it takes the legacy branch. Apply that minimal fix if needed.

- [ ] **Step 5: Commit**

```bash
git add src/ai_steward_wiki/tg/pipeline.py tests/unit/tg/test_pipeline_confirm_callback.py tests/unit/tg/test_pipeline.py
git commit -m "feat(M-TG-PIPELINE-CLASSIFIER): on_confirm_callback executes route_ingest on confirm, reports cancel/stale (aisw-e45)"
```

---

## Task 6: Full quality gate + GRACE artifacts + ADR

**Files:**
- Modify: `docs/knowledge-graph.xml`, `docs/verification-plan.xml`, `docs/development-plan.xml` (via `grace-refresh` / `grace-refresh --verify`, then hand-edit the dev-plan Phase-C entry if the tool doesn't add it)
- Create: `docs/adr/ADR-005-inbox-wiki-route-confirm.md`
- Verify: `src/ai_steward_wiki/tg/pipeline.py`, `src/ai_steward_wiki/tg/confirm.py`, `src/ai_steward_wiki/inbox/route.py` MODULE_CONTRACT headers were updated in Tasks 1–5 (CHANGE_SUMMARY, MODULE_MAP, `__all__`, DEPENDS/LINKS).

- [ ] **Step 1: Run the full lint + type gate**

Run: `make lint`
Expected: `ruff check` clean, `ruff format --check` clean, `mypy src` Success. Fix any drift (e.g. `__all__` sort order — run `uv run ruff check --fix .`).

- [ ] **Step 2: Run the full unit suite + coverage**

Run: `uv run pytest tests/unit -q`
Expected: all pass. Then `uv run pytest tests/unit --cov=src/ai_steward_wiki --cov-report=term-missing -q` and confirm total coverage ≥80%.

- [ ] **Step 3: Refresh GRACE artifacts**

Invoke the `grace-refresh` skill (full) to resync `docs/knowledge-graph.xml` (new exports in `tg/pipeline.py`, `tg/confirm.py`, `inbox/route.py`; new CrossLink `M-TG-PIPELINE-CLASSIFIER → M-TG-TEXT` for the confirm-loop usage), then `grace-refresh --verify` to register the new test files (`test_pipeline_route_confirm.py`, `test_pipeline_confirm_callback.py`, the `test_route.py` / `test_confirm.py` additions) and the new structlog anchors (`tg.pipeline.route.confirm_requested|confirm_executed|confirm_cancelled|confirm_stale`, `tg.pipeline.confirm.route_dispatched`) in `docs/verification-plan.xml`. If `docs/development-plan.xml` doesn't auto-gain a Phase-C node, hand-add one under epic `aisw-t2r` with `bd_id=aisw-e45`, status `in_progress`, listing the touched modules.

Run: `grace lint --failOn errors`
Expected: `Issues: 0 (errors: 0, warnings: 0)`.

- [ ] **Step 4: Write ADR-005** — create `docs/adr/ADR-005-inbox-wiki-route-confirm.md` (follow the `_adr` template / the style of `ADR-004`): Context — Phase-B auto-executed route+ingest from a single message (its R-3 named the gap); Decision — Phase-C confirm-before-route: a `ROUTE`/`CREATE_WIKI` decision becomes a `route_ingest` pending-confirm row (reuse `pending_confirms`, no migration) proposed via a 2-button keyboard; the move+ingest runs in `on_confirm_callback` on "confirmed"; **no** `proposed_actions` field on `RouterDecision` (single-action is derivable from `(intent, target_wiki)`; a structured list is deferred to Phase-D / aisw-kcz); **no** `route_confirm_enabled` toggle (replace, not flag); cancel/TTL keeps the staged raw in Inbox (D-022). Alternatives considered — new `pending_router_action` table (rejected: `pending_confirms` already has `category`/`draft_json`); structured `proposed_actions` now (rejected: YAGNI until Phase-D); keep auto-execute behind a flag (rejected: confirm-before-mutate is strictly the desired behaviour). Consequences — two user interactions per routable turn; Stage-1b runs on the callback (a short ack is sent first); the draft schema is forward-compatible with Phase-D's cron actions.

- [ ] **Step 5: Commit the docs/GRACE batch**

```bash
git add docs/adr/ADR-005-inbox-wiki-route-confirm.md docs/knowledge-graph.xml docs/verification-plan.xml docs/development-plan.xml
git commit -m "docs(adr): ADR-005 Phase-C route confirm + refresh graph/verification/dev plan (aisw-e45)"
```

---

## Task 7: Integration scenario (nightly)

**Files:**
- Modify: `tests/integration/` — add a `route → confirm → ingest` scenario alongside the Phase-B `test_e2e_pipeline.py` route case (the Phase-B test currently asserts a page is written directly from `on_text`; under Phase-C it must instead assert `on_text` returns only a recap (no page yet), then simulate `on_confirm_callback(action="confirm")` and assert the page exists in the target `<Domain>-WIKI`).
- Test: `tests/integration/test_e2e_pipeline.py` (extend / adjust)

- [ ] **Step 1: Read the existing route e2e case**

Run: `grep -n "route\|Router\|librarian\|Domain-WIKI\|raw/" tests/integration/test_e2e_pipeline.py`
Identify the Phase-B route scenario (added by aisw-zd9, commit 744834c).

- [ ] **Step 2: Adjust it to the confirm flow** — after the routable `on_text` call, assert: no `*-WIKI/raw/` page yet for the target, and a `pending_confirms` row with `category='route_ingest'` exists; then build the callback args from that row's `id` and call `pipeline.on_confirm_callback(telegram_id=…, chat_id=…, pending_id=<id>, action="confirm")`; then assert the page now exists under `<wiki_root>/<owner>/<Domain>-WIKI/` (or wherever Phase-B's `stage_raw_into_wiki` writes) and the reply chain contained `ROUTE_CONFIRM_ACK_RU`. Keep it guarded by the existing `RUN_INTEGRATION` marker/skip.

- [ ] **Step 3: Run it (opt-in)**

Run: `RUN_INTEGRATION=1 uv run pytest tests/integration/test_e2e_pipeline.py -k route -v`
Expected: PASS (requires a real Claude CLI; if the environment lacks it, this is a nightly-only gate — note it in the PR/report rather than blocking).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_e2e_pipeline.py
git commit -m "test(M-TG-PIPELINE-CLASSIFIER): e2e — routable text → confirm callback → page written in target WIKI (aisw-e45)"
```

---

## Task 8: Finish

- [ ] **Step 1: Final full gate**

Run: `make lint && uv run pytest tests/unit -q && grace lint --failOn errors`
Expected: all green.

- [ ] **Step 2: `bd close`**

```bash
bd close aisw-e45 --reason="Phase-C confirm loop: route/create decisions proposed via inline buttons, executed on confirm; reuses pending_confirms; ADR-005."
```

- [ ] **Step 3: Completion report (optional, this is a notable phase)** — `docs/reports/20260512-inbox-wiki-route-confirm-report.md` via the `_report` skill (review results + test results + the Phase-C scope). Commit `docs(report): aisw-e45 Phase-C completion report`.

- [ ] **Step 4: `bd dolt push`** (session-close persistence — allowed automatically). Leave `git push` of code for the user to trigger.

---

## Self-Review

- **Spec coverage:** FR-1 → Task 4. FR-2 → Task 4 (CLARIFY/REJECT/no-librarian tests). FR-3 (recap copy + 2-button keyboard) → Tasks 2 + 4. FR-4 (confirm → execute, stale) → Task 5. FR-5 (cancel → keep raw) → Task 5 (no delete) + ADR-005. FR-6 (log anchors) → Tasks 4 + 5 + verification-plan in Task 6. NFR-1 (reuse, no migration) → all tasks (no Alembic touched). NFR-2 (gates, fakes) → Task 6. NFR-3 (ru, UTC, no bypass, best-effort) → constants in Task 4, no keyboard-removal per TD-6. NFR-4 (media path round-trip) → Task 1 + Task 5. TD-1..TD-8 → Tasks 1–5. GRACE artifacts → Task 6. ADR-005 → Task 6.
- **Placeholder scan:** none — every code step has full code; the integration task (Task 7) describes exact assertions to add to an existing file rather than reprinting an unknown file verbatim, which is acceptable since the file content must be read first (Step 1 of the task).
- **Type consistency:** `route_action_to_payload` / `route_action_from_payload` / `RouteAction` (Task 1) used identically in Tasks 4 & 5; `build_route_confirm_keyboard` (Task 2) referenced in Tasks 3 & 4; `request_explicit(draft, *, keyboard_factory=...)` (Task 3) called in Task 4; `ROUTE_CONFIRM_*_RU` constants (Task 4) imported in Task 5's tests; `_handle_route_confirm` is private and only called from `on_confirm_callback` (Task 5).
