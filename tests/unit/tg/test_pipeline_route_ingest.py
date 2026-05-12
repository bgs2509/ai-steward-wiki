"""Unit tests for the routable branch in DefaultPipeline — non-confirm cases (aisw-zd9).

Phase-C (aisw-e45) moved the actual Stage-1b ingest behind a user confirm: the
confirm-request behaviour is covered by ``test_pipeline_route_confirm.py`` and the
confirm-callback → ingest behaviour by ``test_pipeline_confirm_callback.py``. What
remains here: the routable branches that still reply inline (CLARIFY/REJECT) or fall
through to the Phase-A notes-echo (no librarian wired), plus the router-dispatch logs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier.schema import ClassifierResult, Intent
from ai_steward_wiki.inbox.router import RouterDecision, RouterIntent
from ai_steward_wiki.tg.pipeline import DefaultPipeline
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


def _librarian() -> MagicMock:
    lib = MagicMock()
    lib.ingest = AsyncMock(return_value=None)
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
async def test_router_dispatch_log_markers(capsys: pytest.CaptureFixture[str]) -> None:
    sender = FakeSender()
    # No librarian → notes-echo path, but the router-dispatch logs still fire.
    pipe, _, _ = _pipe(sender=sender, router=_router(RouterIntent.ROUTE), librarian=None)

    await pipe.on_text(telegram_id=7, chat_id=10, update_id=2, text="hi")

    out = capsys.readouterr().out
    for marker in ("tg.pipeline.router.dispatched", "tg.pipeline.router.decided"):
        assert marker in out, f"missing {marker} in:\n{out}"
