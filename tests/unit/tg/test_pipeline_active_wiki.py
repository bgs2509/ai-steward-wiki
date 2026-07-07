"""Unit tests for the active-WIKI sticky pointer wiring in DefaultPipeline (aisw-0ym)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier.schema import ClassifierResult, Intent
from ai_steward_wiki.inbox.route import route_action_to_payload
from ai_steward_wiki.inbox.router import RouterDecision, RouterIntent
from ai_steward_wiki.tg.pipeline import DefaultPipeline, IngestOutcome, WikiRunOutcome
from tests.helpers.classifier_factory import make_classifier_result
from tests.unit.tg.conftest import FakeSender


def _classifier_result(intent: Intent, *, action: str | None = None) -> ClassifierResult:
    return make_classifier_result(intent, action=action, confidence=0.9)


def _make_idem() -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=True)
    idem.check_content = AsyncMock(return_value=("b" * 64, None))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


def _make_classifier(intent: Intent, *, action: str | None = None) -> MagicMock:
    cls = MagicMock()
    cls.classify = AsyncMock(return_value=_classifier_result(intent, action=action))
    return cls


def _make_runner() -> MagicMock:
    r = MagicMock()
    r.run = AsyncMock(return_value=WikiRunOutcome(run_id="run-x", text="ok", latency_ms=1))
    return r


def _make_output() -> MagicMock:
    out = MagicMock()
    out.deliver = AsyncMock(return_value=None)
    return out


def _make_router(decision: RouterDecision) -> MagicMock:
    rt = MagicMock()
    rt.route = AsyncMock(return_value=decision)
    return rt


def _make_librarian(target: str = "Medical-WIKI") -> MagicMock:
    lib = MagicMock()
    lib.ingest = AsyncMock(
        return_value=IngestOutcome(
            status="ok",
            reply="готово",
            run_id="ing-1",
            target_wiki=target,
            created=False,
        )
    )
    return lib


def _make_active_wiki(pointer: str | None = None) -> MagicMock:
    aw = MagicMock()
    aw.set_active = AsyncMock(return_value=None)
    aw.get_active = AsyncMock(return_value=pointer)
    return aw


def _make_confirm() -> MagicMock:
    confirm = MagicMock()
    rec = MagicMock()
    rec.pending_id = 42
    confirm.request_explicit = AsyncMock(return_value=rec)
    confirm.resolve = AsyncMock(return_value="confirmed")
    return confirm


@pytest.mark.asyncio
async def test_clarify_with_fresh_pointer_routes_to_active_wiki() -> None:
    """A bare follow-up (router CLARIFY) with a fresh pointer → confirm into that WIKI."""
    sender = FakeSender()
    confirm = _make_confirm()
    decision = RouterDecision(
        intent=RouterIntent.CLARIFY,
        target_wiki=None,
        notes="не понял",
        raw="",
        parsed_ok=True,
    )
    router = _make_router(decision)
    active_wiki = _make_active_wiki("Medical-WIKI")
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=confirm,
        classifier=_make_classifier(Intent.UNKNOWN),
        runner=_make_runner(),
        output=_make_output(),
        router=router,
        librarian=_make_librarian(),
        active_wiki=active_wiki,
        owner_wikis_resolver=AsyncMock(return_value=[]),
    )

    await pipe.on_text(telegram_id=7, chat_id=70, update_id=5, text="повтори")

    active_wiki.get_active.assert_awaited_once_with(7)
    # a route_ingest confirm was requested (not a cold notes-echo)
    confirm.request_explicit.assert_awaited_once()
    # notes-echo must NOT be the reply
    assert all(s["text"] != "не понял" for s in sender.sends)


@pytest.mark.asyncio
async def test_clarify_without_pointer_keeps_cold_notes_echo() -> None:
    sender = FakeSender()
    confirm = _make_confirm()
    decision = RouterDecision(
        intent=RouterIntent.CLARIFY,
        target_wiki=None,
        notes="уточни тему",
        raw="",
        parsed_ok=True,
    )
    router = _make_router(decision)
    active_wiki = _make_active_wiki(None)  # no fresh pointer
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=confirm,
        classifier=_make_classifier(Intent.UNKNOWN),
        runner=_make_runner(),
        output=_make_output(),
        router=router,
        librarian=_make_librarian(),
        active_wiki=active_wiki,
    )

    await pipe.on_text(telegram_id=7, chat_id=70, update_id=5, text="м?")

    confirm.request_explicit.assert_not_awaited()
    assert sender.sends[-1]["text"] == "уточни тему"


@pytest.mark.asyncio
async def test_confident_route_does_not_override_with_pointer() -> None:
    """A confident ROUTE to a concrete WIKI is untouched by the pointer."""
    sender = FakeSender()
    confirm = _make_confirm()
    decision = RouterDecision(
        intent=RouterIntent.ROUTE,
        target_wiki="Travel-WIKI",
        notes="в Travel-WIKI",
        raw="",
        parsed_ok=True,
    )
    router = _make_router(decision)
    active_wiki = _make_active_wiki("Medical-WIKI")
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=confirm,
        classifier=_make_classifier(Intent.WIKI, action="ingest"),
        runner=_make_runner(),
        output=_make_output(),
        router=router,
        librarian=_make_librarian(),
        active_wiki=active_wiki,
        owner_wikis_resolver=AsyncMock(return_value=[]),
    )

    await pipe.on_text(telegram_id=7, chat_id=70, update_id=5, text="вот авиабилет")

    # confirm requested for the CONFIDENT target, pointer not consulted for override
    confirm.request_explicit.assert_awaited_once()
    draft = confirm.request_explicit.await_args.args[0]
    payload = json.loads(draft.draft) if isinstance(draft.draft, str) else draft.draft
    # the proposed target stays Travel-WIKI, not the Medical-WIKI pointer
    assert "Travel-WIKI" in json.dumps(payload)


@pytest.mark.asyncio
async def test_set_active_on_successful_route_confirm() -> None:
    sender = FakeSender()
    confirm = _make_confirm()
    active_wiki = _make_active_wiki()
    decision = RouterDecision(
        intent=RouterIntent.ROUTE,
        target_wiki="Medical-WIKI",
        notes="в Medical-WIKI",
        raw="",
        parsed_ok=True,
    )
    payload = route_action_to_payload(
        decision, user_text="давление 120", source="text", media_paths=None, correlation_id="c1"
    )
    pending = MagicMock()
    pending.category = "route_ingest"
    pending.draft_json = json.dumps(payload)
    confirm.get_pending = AsyncMock(return_value=pending)

    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=confirm,
        classifier=_make_classifier(Intent.WIKI, action="ingest"),
        runner=_make_runner(),
        output=_make_output(),
        librarian=_make_librarian("Medical-WIKI"),
        active_wiki=active_wiki,
    )

    await pipe.on_confirm_callback(telegram_id=7, chat_id=70, pending_id=42, action="confirm")

    active_wiki.set_active.assert_awaited_once()
    args = active_wiki.set_active.await_args
    assert args.args[0] == 7
    assert args.args[1] == "Medical-WIKI"


@pytest.mark.asyncio
async def test_no_active_wiki_port_keeps_pipeline_working() -> None:
    sender = FakeSender()
    output = _make_output()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=_make_confirm(),
        classifier=_make_classifier(Intent.WIKI, action="query"),
        runner=_make_runner(),
        output=output,
        active_wiki=None,
    )

    await pipe.on_text(telegram_id=7, chat_id=70, update_id=5, text="hi")

    output.deliver.assert_awaited_once()
