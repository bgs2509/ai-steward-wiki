"""Unit tests for the Phase-C route_ingest dispatch in on_confirm_callback (aisw-e45)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.inbox.route import route_action_to_payload
from ai_steward_wiki.inbox.router import RouterDecision, RouterIntent
from ai_steward_wiki.tg.pipeline import (
    ROUTE_CONFIRM_ACK_RU,
    ROUTE_CONFIRM_CANCELLED_RU,
    ROUTE_CONFIRM_STALE_RU,
    DefaultPipeline,
    IngestOutcome,
)
from tests.unit.tg.conftest import FakeSender


def _decision(
    intent: RouterIntent = RouterIntent.ROUTE, target: str | None = "Travel-WIKI"
) -> RouterDecision:
    return RouterDecision(
        intent=intent,
        target_wiki=target,
        notes="Положу в Travel-WIKI.",
        raw="```router\n...\n```",
        parsed_ok=True,
    )


def _pending_row(
    category: str = "route_ingest",
    *,
    decision: RouterDecision | None = None,
    media: list[str] | None = None,
) -> MagicMock:
    from pathlib import Path

    row = MagicMock()
    row.category = category
    row.draft_json = json.dumps(
        route_action_to_payload(
            decision or _decision(),
            user_text="вот билет",
            source="photo" if media else "text",
            media_paths=[Path(p) for p in media] if media else None,
            correlation_id="tg-5-42",
        )
    )
    return row


def _confirm(*, pending_row: MagicMock | None, resolve_status: str | None) -> MagicMock:
    c = MagicMock()
    c.get_pending = AsyncMock(return_value=pending_row)
    c.resolve = AsyncMock(return_value=resolve_status)
    return c


def _librarian(outcome: IngestOutcome | None = None) -> MagicMock:
    lib = MagicMock()
    lib.ingest = AsyncMock(
        return_value=outcome
        or IngestOutcome(
            status="ok",
            reply="Положу в Travel-WIKI.\n\nЗаписал.",
            run_id="ing-1",
            target_wiki="Travel-WIKI",
            created=False,
        )
    )
    return lib


def _output() -> MagicMock:
    out = MagicMock()
    out.deliver = AsyncMock(return_value=None)
    return out


def _pipe(
    *,
    sender: FakeSender,
    confirmation: MagicMock,
    librarian: MagicMock | None = None,
    output: MagicMock | None = None,
) -> DefaultPipeline:
    return DefaultPipeline(
        sender=sender,
        idempotency=MagicMock(),
        confirmation=confirmation,
        librarian=librarian or _librarian(),
        output=output or _output(),
    )


@pytest.mark.asyncio
async def test_confirm_executes_ingest_and_delivers() -> None:
    sender = FakeSender()
    row = _pending_row(decision=_decision(RouterIntent.CREATE_WIKI), media=["/tmp/raw/a.jpg"])
    confirm = _confirm(pending_row=row, resolve_status="confirmed")
    lib = _librarian()
    out = _output()
    pipe = _pipe(sender=sender, confirmation=confirm, librarian=lib, output=out)

    await pipe.on_confirm_callback(telegram_id=42, chat_id=10, pending_id=555, action="confirm")

    assert sender.sends[0]["text"] == ROUTE_CONFIRM_ACK_RU  # ack before the slow ingest
    lib.ingest.assert_awaited_once()
    kw = lib.ingest.await_args.kwargs
    assert kw["telegram_id"] == 42
    assert kw["user_text"] == "вот билет"
    assert kw["source"] == "photo"
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
    lib = _librarian(
        IngestOutcome(
            status="run_failed",
            reply="Положу в Travel-WIKI.\n\nПозже.",
            run_id="ing-2",
            target_wiki="Travel-WIKI",
            created=True,
        )
    )
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
