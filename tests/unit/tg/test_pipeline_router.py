"""Unit tests for the Inbox-WIKI Stage-1a router branch in DefaultPipeline (aisw-dsg).

aisw-xi8 (Phase-C.1): migrated to the v2 taxonomy per the DEC-14 mapping table —
wiki_ingest -> (wiki, ingest), wiki_query -> (wiki, query), web_task -> (web,-),
admin unchanged, unknown unchanged. _classifier_result now builds via the shared
make_classifier_result factory (DEC-14).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier.schema import Intent
from ai_steward_wiki.inbox.router import RouterDecision, RouterError, RouterIntent
from ai_steward_wiki.tg.pipeline import ACK_RUNNER_ERR_RU, DefaultPipeline, WikiRunOutcome
from tests.helpers.classifier_factory import make_classifier_result
from tests.unit.tg.conftest import FakeSender


def _make_idem() -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=True)
    idem.check_content = AsyncMock(return_value=("b" * 64, None))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


def _make_classifier(intent: Intent, action: str | None = None) -> MagicMock:
    cls = MagicMock()
    cls.classify = AsyncMock(
        return_value=make_classifier_result(intent, action=action, confidence=0.9)
    )
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
    action: str | None,
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
        classifier=_make_classifier(intent, action),
        runner=runner,
        output=output,
        router=router,
    )
    return pipe, runner, output


@pytest.mark.asyncio
# aisw-50z (v1)/aisw-xi8 (v2): wiki/query is not routable — it must be ANSWERED
# by the generic runner, not filed (see test_wiki_query_answers_via_runner_not_router).
@pytest.mark.parametrize(("intent", "action"), [(Intent.WIKI, "ingest"), (Intent.UNKNOWN, None)])
async def test_routable_intent_goes_through_router_not_runner(
    intent: Intent, action: str | None
) -> None:
    sender = FakeSender()
    router = _make_router()
    pipe, runner, output = _pipe(sender=sender, intent=intent, action=action, router=router)

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
async def test_wiki_query_answers_via_runner_not_router() -> None:
    """wiki/query must ANSWER via the generic runner (cross-WIKI read), NOT be
    filed through the Stage-1a ingest router — even when a router is wired."""
    sender = FakeSender()
    router = _make_router()
    pipe, runner, output = _pipe(sender=sender, intent=Intent.WIKI, action="query", router=router)

    await pipe.on_text(telegram_id=42, chat_id=10, update_id=5, text="какое у меня было давление?")

    router.route.assert_not_awaited()  # no filing
    runner.run.assert_awaited_once()  # answer path
    output.deliver.assert_awaited_once()
    assert runner.run.await_args.kwargs["intent"] is Intent.WIKI
    assert runner.run.await_args.kwargs["action"] == "query"


@pytest.mark.asyncio
async def test_wiki_lint_uses_legacy_generic_runner_path() -> None:
    sender = FakeSender()
    router = _make_router()
    pipe, runner, _ = _pipe(sender=sender, intent=Intent.WIKI, action="lint", router=router)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="проверь на дубли")

    router.route.assert_not_awaited()
    runner.run.assert_awaited_once()
    assert runner.run.await_args.kwargs["action"] == "lint"


@pytest.mark.asyncio
async def test_web_answers_via_runner_not_filed() -> None:
    """intent=web must ANSWER via the generic runner (intent threaded through),
    never the Inbox router/filing path."""
    sender = FakeSender()
    router = _make_router()
    pipe, runner, output = _pipe(sender=sender, intent=Intent.WEB, action=None, router=router)

    await pipe.on_text(
        telegram_id=1, chat_id=10, update_id=2, text="найди в интернете рецепт борща"
    )

    router.route.assert_not_awaited()  # not filed
    runner.run.assert_awaited_once()
    assert runner.run.await_args.kwargs["intent"] is Intent.WEB


@pytest.mark.asyncio
async def test_admin_intent_declined_safely() -> None:
    """intent=admin must NOT freelance-run Claude in the user root."""
    from ai_steward_wiki.tg.pipeline import ACK_ADMIN_RU

    sender = FakeSender()
    router = _make_router()
    pipe, runner, output = _pipe(sender=sender, intent=Intent.ADMIN, action=None, router=router)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="создай Russian-Coal-WIKI")

    runner.run.assert_not_awaited()  # no generic root run -> no freelance WIKI
    router.route.assert_not_awaited()
    output.deliver.assert_not_awaited()
    assert sender.sends[0]["text"] == ACK_ADMIN_RU


@pytest.mark.asyncio
async def test_routable_intent_without_router_falls_through_to_legacy() -> None:
    sender = FakeSender()
    pipe, runner, _ = _pipe(sender=sender, intent=Intent.WIKI, action="ingest", router=None)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="x")

    runner.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_router_error_replies_safe_ack() -> None:
    sender = FakeSender()
    router = _make_router(raises=RouterError("cli blew up"))
    pipe, runner, output = _pipe(sender=sender, intent=Intent.WIKI, action="ingest", router=router)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="x")

    assert sender.sends[0]["text"] == ACK_RUNNER_ERR_RU
    runner.run.assert_not_awaited()
    output.deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_branch_emits_log_markers(capsys: pytest.CaptureFixture[str]) -> None:
    sender = FakeSender()
    router = _make_router()
    pipe, _, _ = _pipe(sender=sender, intent=Intent.WIKI, action="ingest", router=router)

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
    pipe, _, _ = _pipe(sender=sender, intent=Intent.UNKNOWN, action=None, router=router)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="м?")

    assert sender.sends[0]["text"] == "Уточни, к какой теме это относится?"
