"""Unit tests for the Phase-C route confirm gate in DefaultPipeline (aisw-e45)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier.schema import ClassifierResult, Intent
from ai_steward_wiki.inbox.route import route_action_from_payload
from ai_steward_wiki.inbox.router import RouterDecision, RouterIntent
from ai_steward_wiki.tg.confirm import build_route_confirm_keyboard
from ai_steward_wiki.tg.pipeline import DefaultPipeline, IngestOutcome
from tests.unit.tg.conftest import FakeSender


def _classifier(intent: Intent = Intent.WIKI_INGEST) -> MagicMock:
    cls = MagicMock()
    cls.classify = AsyncMock(
        return_value=ClassifierResult(
            intent=intent,
            confidence=0.9,
            distilled_payload={"q": "x"},
            backend="fake",
            model="m",
            prompt_semver="1.1.0",
            prompt_sha256="a" * 64,
            latency_ms=1,
        )
    )
    return cls


def _idem() -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=True)
    idem.check_content = AsyncMock(return_value=("b" * 64, None))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


def _router(
    intent: RouterIntent = RouterIntent.ROUTE,
    target: str | None = "Travel-WIKI",
    notes: str = "Положу в Travel-WIKI.",
) -> MagicMock:
    rt = MagicMock()
    rt.route = AsyncMock(
        return_value=RouterDecision(
            intent=intent,
            target_wiki=target,
            notes=notes,
            raw="```router\n...\n```",
            parsed_ok=True,
        )
    )
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


def _runner() -> MagicMock:
    from ai_steward_wiki.tg.pipeline import WikiRunOutcome

    r = MagicMock()
    r.run = AsyncMock(return_value=WikiRunOutcome(run_id="run-x", text="legacy", latency_ms=1))
    return r


def _pipe(
    *,
    sender: FakeSender,
    confirmation: MagicMock | None = None,
    router: MagicMock | None = None,
    librarian: MagicMock | None = None,
    output: MagicMock | None = None,
    intent: Intent = Intent.WIKI_INGEST,
) -> DefaultPipeline:
    return DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=confirmation or _confirm(),
        classifier=_classifier(intent),
        runner=_runner(),
        output=output or _output(),
        router=router or _router(),
        librarian=librarian or _librarian(),
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
    assert draft.telegram_id == 42
    assert draft.chat_id == 10
    assert draft.category == "route_ingest"
    assert call.kwargs["keyboard_factory"] is build_route_confirm_keyboard
    assert "Travel-WIKI" in draft.recap_text
    assert "Положу в Travel-WIKI." in draft.recap_text
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
    notes = "Уточни?" if intent is RouterIntent.CLARIFY else "Не по адресу."
    router = _router(intent, None, notes)
    pipe = _pipe(sender=sender, confirmation=confirm, router=router, librarian=_librarian())

    await pipe.on_text(telegram_id=1, chat_id=2, update_id=3, text="?")

    confirm.request_explicit.assert_not_awaited()
    assert sender.sends[0]["text"] == notes


@pytest.mark.asyncio
async def test_no_librarian_still_notes_echo_no_confirm() -> None:
    sender = FakeSender()
    confirm = _confirm()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=confirm,
        classifier=_classifier(),
        runner=_runner(),
        output=_output(),
        router=_router(RouterIntent.ROUTE),
        librarian=None,
    )
    await pipe.on_text(telegram_id=1, chat_id=2, update_id=3, text="вот билет")
    confirm.request_explicit.assert_not_awaited()
    assert sender.sends[0]["text"] == "Положу в Travel-WIKI."


@pytest.mark.asyncio
async def test_route_confirm_requested_log_marker(capsys: pytest.CaptureFixture[str]) -> None:
    sender = FakeSender()
    pipe = _pipe(sender=sender, confirmation=_confirm(), router=_router(RouterIntent.ROUTE))

    await pipe.on_text(telegram_id=7, chat_id=10, update_id=2, text="вот билет")

    out = capsys.readouterr().out
    assert "tg.pipeline.route.confirm_requested" in out
