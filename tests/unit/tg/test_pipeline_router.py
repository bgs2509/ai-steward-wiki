"""Unit tests for the Inbox-WIKI Stage-1a router branch in DefaultPipeline (aisw-dsg)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier.schema import ClassifierResult, Intent
from ai_steward_wiki.inbox.router import RouterDecision, RouterError, RouterIntent
from ai_steward_wiki.tg.pipeline import ACK_RUNNER_ERR_RU, DefaultPipeline, WikiRunOutcome
from tests.unit.tg.conftest import FakeSender


def _classifier_result(intent: Intent) -> ClassifierResult:
    return ClassifierResult(
        intent=intent,
        confidence=0.9,
        distilled_payload={"q": "x"},
        backend="fake",
        model="fake-m",
        prompt_semver="1.1.0",
        prompt_sha256="a" * 64,
        latency_ms=10,
    )


def _make_idem() -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=True)
    idem.check_content = AsyncMock(return_value=("b" * 64, None))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


def _make_classifier(intent: Intent) -> MagicMock:
    cls = MagicMock()
    cls.classify = AsyncMock(return_value=_classifier_result(intent))
    return cls


def _make_runner() -> MagicMock:
    r = MagicMock()
    r.run = AsyncMock(return_value=WikiRunOutcome(run_id="run-x", text="legacy", latency_ms=1))
    return r


def _make_output() -> MagicMock:
    out = MagicMock()
    out.deliver = AsyncMock(return_value=None)
    return out


def _make_router(
    decision: RouterDecision | None = None, *, raises: Exception | None = None
) -> MagicMock:
    rt = MagicMock()
    if raises is not None:
        rt.route = AsyncMock(side_effect=raises)
    else:
        rt.route = AsyncMock(
            return_value=decision
            or RouterDecision(
                intent=RouterIntent.ROUTE,
                target_wiki="Travel-WIKI",
                notes="Положу в Travel-WIKI.",
                raw="```router\n...\n```",
                parsed_ok=True,
            )
        )
    return rt


def _pipe(
    *,
    sender: FakeSender,
    intent: Intent,
    router: MagicMock | None,
    runner: MagicMock | None = None,
    output: MagicMock | None = None,
) -> tuple[DefaultPipeline, MagicMock, MagicMock]:
    runner = runner or _make_runner()
    output = output or _make_output()
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=MagicMock(),
        classifier=_make_classifier(intent),
        runner=runner,
        output=output,
        router=router,
    )
    return pipe, runner, output


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", [Intent.WIKI_INGEST, Intent.WIKI_QUERY, Intent.UNKNOWN])
async def test_routable_intent_goes_through_router_not_runner(intent: Intent) -> None:
    sender = FakeSender()
    router = _make_router()
    pipe, runner, output = _pipe(sender=sender, intent=intent, router=router)

    await pipe.on_text(telegram_id=42, chat_id=10, update_id=5, text="вот авиабилет")

    router.route.assert_awaited_once()
    kw = router.route.await_args.kwargs
    assert kw["text"] == "вот авиабилет"
    assert kw["telegram_id"] == 42
    assert kw["source"] == "text"
    assert kw["correlation_id"] == "tg-5-42"
    runner.run.assert_not_awaited()
    output.deliver.assert_not_awaited()
    assert sender.sends[0]["text"] == "Положу в Travel-WIKI."


@pytest.mark.asyncio
# Intent.DIGEST is no longer legacy — it has its own fast-path (#2, aisw-578),
# covered in test_pipeline_digest.py. Intent.ADMIN is no longer legacy either —
# aisw-aca tames it (see test_admin_intent_declined_safely below).
@pytest.mark.parametrize("intent", [Intent.REMINDER, Intent.WIKI_LINT])
async def test_non_routable_intent_uses_legacy_path(intent: Intent) -> None:
    sender = FakeSender()
    router = _make_router()
    pipe, runner, _ = _pipe(sender=sender, intent=intent, router=router)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="разбуди в 6")

    router.route.assert_not_awaited()
    runner.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_intent_declined_safely() -> None:
    """aisw-aca: intent=admin must NOT freelance-run Claude in the user root."""
    from ai_steward_wiki.tg.pipeline import ACK_ADMIN_RU

    sender = FakeSender()
    router = _make_router()
    pipe, runner, output = _pipe(sender=sender, intent=Intent.ADMIN, router=router)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="создай Russian-Coal-WIKI")

    runner.run.assert_not_awaited()  # no generic root run -> no freelance WIKI
    router.route.assert_not_awaited()
    output.deliver.assert_not_awaited()
    assert sender.sends[0]["text"] == ACK_ADMIN_RU


@pytest.mark.asyncio
async def test_routable_intent_without_router_falls_through_to_legacy() -> None:
    sender = FakeSender()
    pipe, runner, _ = _pipe(sender=sender, intent=Intent.WIKI_INGEST, router=None)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="x")

    runner.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_router_error_replies_safe_ack() -> None:
    sender = FakeSender()
    router = _make_router(raises=RouterError("cli blew up"))
    pipe, runner, output = _pipe(sender=sender, intent=Intent.WIKI_INGEST, router=router)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="x")

    assert sender.sends[0]["text"] == ACK_RUNNER_ERR_RU
    runner.run.assert_not_awaited()
    output.deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_branch_emits_log_markers(capsys: pytest.CaptureFixture[str]) -> None:
    sender = FakeSender()
    router = _make_router()
    pipe, _, _ = _pipe(sender=sender, intent=Intent.WIKI_INGEST, router=router)

    await pipe.on_text(telegram_id=7, chat_id=10, update_id=2, text="hi")

    out = capsys.readouterr().out
    for marker in ("tg.pipeline.router.dispatched", "tg.pipeline.router.decided"):
        assert marker in out, f"missing {marker} in:\n{out}"
    assert "tg.pipeline.runner.dispatched" not in out


@pytest.mark.asyncio
async def test_router_clarify_decision_delivers_notes() -> None:
    sender = FakeSender()
    decision = RouterDecision(
        intent=RouterIntent.CLARIFY,
        target_wiki=None,
        notes="Уточни, к какой теме это относится?",
        raw="```router\n...\n```",
        parsed_ok=True,
    )
    router = _make_router(decision)
    pipe, _, _ = _pipe(sender=sender, intent=Intent.UNKNOWN, router=router)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="м?")

    assert sender.sends[0]["text"] == "Уточни, к какой теме это относится?"
