"""Unit tests for the Stage-1b librarian dispatch in DefaultPipeline (aisw-zd9)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier.schema import ClassifierResult, Intent
from ai_steward_wiki.inbox.router import RouterDecision, RouterIntent
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
            model="fake-m",
            prompt_semver="1.1.0",
            prompt_sha256="a" * 64,
            latency_ms=10,
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
    intent: RouterIntent = RouterIntent.ROUTE, target: str | None = "Travel-WIKI"
) -> MagicMock:
    rt = MagicMock()
    rt.route = AsyncMock(
        return_value=RouterDecision(
            intent=intent,
            target_wiki=target,
            notes="Положу в Travel-WIKI.",
            raw="```router\n...\n```",
            parsed_ok=True,
        )
    )
    return rt


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
    router: MagicMock | None,
    librarian: MagicMock | None,
    output: MagicMock | None = None,
    runner: MagicMock | None = None,
    classifier_intent: Intent = Intent.WIKI_INGEST,
) -> tuple[DefaultPipeline, MagicMock, MagicMock]:
    output = output or _output()
    runner = runner or _runner()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=MagicMock(),
        classifier=_classifier(classifier_intent),
        runner=runner,
        output=output,
        router=router,
        librarian=librarian,
    )
    return pipe, output, runner


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", [RouterIntent.ROUTE, RouterIntent.CREATE_WIKI])
async def test_routable_decision_invokes_librarian_and_delivers_on_ok(intent: RouterIntent) -> None:
    sender = FakeSender()
    router = _router(intent)
    lib = _librarian()
    pipe, output, runner = _pipe(sender=sender, router=router, librarian=lib)

    await pipe.on_text(telegram_id=42, chat_id=10, update_id=5, text="вот авиабилет")

    lib.ingest.assert_awaited_once()
    kw = lib.ingest.await_args.kwargs
    assert kw["telegram_id"] == 42
    assert kw["user_text"] == "вот авиабилет"
    assert kw["source"] == "text"
    assert kw["correlation_id"] == "tg-5-42"
    assert lib.ingest.await_args.args[0].intent is intent
    output.deliver.assert_awaited_once()
    assert output.deliver.await_args.kwargs["text"] == "Положу в Travel-WIKI.\n\nЗаписал."
    assert output.deliver.await_args.kwargs["run_id"] == "ing-1"
    runner.run.assert_not_awaited()
    assert sender.sends == []  # delivery via output adapter


@pytest.mark.asyncio
async def test_rejected_outcome_sends_message_not_deliver() -> None:
    sender = FakeSender()
    lib = _librarian(
        IngestOutcome(
            status="rejected",
            reply="Положу в Travel-WIKI.\n\nЛимит.",
            run_id=None,
            target_wiki=None,
            created=False,
        )
    )
    pipe, output, _ = _pipe(sender=sender, router=_router(), librarian=lib)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="x")

    output.deliver.assert_not_awaited()
    assert sender.sends[0]["text"] == "Положу в Travel-WIKI.\n\nЛимит."


@pytest.mark.asyncio
async def test_run_failed_outcome_sends_message() -> None:
    sender = FakeSender()
    lib = _librarian(
        IngestOutcome(
            status="run_failed",
            reply="Положу в Travel-WIKI.\n\nПозже.",
            run_id="ing-2",
            target_wiki="Travel-WIKI",
            created=True,
        )
    )
    pipe, output, _ = _pipe(sender=sender, router=_router(), librarian=lib)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="x")

    output.deliver.assert_not_awaited()
    assert sender.sends[0]["text"] == "Положу в Travel-WIKI.\n\nПозже."


@pytest.mark.asyncio
async def test_clarify_decision_does_not_invoke_librarian() -> None:
    sender = FakeSender()
    router = _router(RouterIntent.CLARIFY, None)
    router.route = AsyncMock(
        return_value=RouterDecision(
            intent=RouterIntent.CLARIFY,
            target_wiki=None,
            notes="Уточни?",
            raw="```router\n...\n```",
            parsed_ok=True,
        )
    )
    lib = _librarian()
    pipe, output, _ = _pipe(sender=sender, router=router, librarian=lib)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="?")

    lib.ingest.assert_not_awaited()
    output.deliver.assert_not_awaited()
    assert sender.sends[0]["text"] == "Уточни?"


@pytest.mark.asyncio
async def test_no_librarian_falls_through_to_notes_echo() -> None:
    sender = FakeSender()
    pipe, output, _ = _pipe(sender=sender, router=_router(RouterIntent.ROUTE), librarian=None)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="x")

    output.deliver.assert_not_awaited()
    assert sender.sends[0]["text"] == "Положу в Travel-WIKI."


@pytest.mark.asyncio
async def test_route_ingest_log_markers(capsys: pytest.CaptureFixture[str]) -> None:
    sender = FakeSender()
    pipe, _, _ = _pipe(sender=sender, router=_router(RouterIntent.ROUTE), librarian=_librarian())

    await pipe.on_text(telegram_id=7, chat_id=10, update_id=2, text="hi")

    out = capsys.readouterr().out
    for marker in (
        "tg.pipeline.router.dispatched",
        "tg.pipeline.router.decided",
        "tg.pipeline.route.ingest_dispatched",
        "tg.pipeline.route.delivered",
    ):
        assert marker in out, f"missing {marker} in:\n{out}"
