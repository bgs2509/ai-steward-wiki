"""RED-first coverage for the DEC-3 routable predicate (replaces the old
_ROUTABLE_INTENTS frozenset) — a function over (intent, action), not a static
membership test. Also covers wiki/catalog reaching the Stage-1a list_wikis path
(FR-11 — closes the measured 99/100 "Покажи мои вики" miss)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier.schema import Intent
from ai_steward_wiki.inbox.router import RouterDecision, RouterIntent
from ai_steward_wiki.tg.pipeline import DefaultPipeline, _is_routable
from tests.helpers.classifier_factory import make_classifier_result
from tests.unit.tg.conftest import FakeSender


@pytest.mark.parametrize(
    ("intent", "action", "expected"),
    [
        (Intent.UNKNOWN, None, True),
        (Intent.WIKI, "ingest", True),
        (Intent.WIKI, "catalog", True),
        (Intent.WIKI, None, True),
        (Intent.WIKI, "query", False),
        (Intent.WIKI, "lint", False),
        (Intent.JOB, None, False),
        (Intent.WEB, None, False),
        (Intent.CHAT, None, False),
        (Intent.ADMIN, None, False),
    ],
)
def test_is_routable_predicate(intent: Intent, action: str | None, expected: bool) -> None:
    assert _is_routable(intent, action) is expected


def _make_idem() -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=True)
    idem.check_content = AsyncMock(return_value=("b" * 64, None))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


@pytest.mark.asyncio
async def test_wiki_catalog_reaches_router_list_wikis_path() -> None:
    """FR-11: wiki/catalog (action=None, the measured miss, OR action="catalog")
    reuses the EXISTING Stage-1a RouterIntent.LIST_WIKIS path — zero router changes."""
    sender = FakeSender()
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=make_classifier_result(Intent.WIKI, action="catalog", confidence=0.95)
    )
    router = MagicMock()
    router.route = AsyncMock(
        return_value=RouterDecision(
            intent=RouterIntent.LIST_WIKIS,
            target_wiki=None,
            notes="У тебя 2 вики: Health-WIKI, Budget-WIKI.",
            raw="",
            parsed_ok=True,
        )
    )
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=MagicMock(),
        classifier=classifier,
        runner=MagicMock(),
        output=MagicMock(),
        router=router,
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="Покажи мои вики")
    router.route.assert_awaited_once()
    assert "Health-WIKI" in sender.sends[0]["text"]


@pytest.mark.asyncio
async def test_wiki_catalog_with_action_none_also_routes() -> None:
    """The measured 99/100 miss — an EMPTY action must still route correctly."""
    sender = FakeSender()
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=make_classifier_result(Intent.WIKI, action=None, confidence=0.95)
    )
    router = MagicMock()
    router.route = AsyncMock(
        return_value=RouterDecision(
            intent=RouterIntent.LIST_WIKIS,
            target_wiki=None,
            notes="список вики",
            raw="",
            parsed_ok=True,
        )
    )
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=MagicMock(),
        classifier=classifier,
        runner=MagicMock(),
        output=MagicMock(),
        router=router,
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="Покажи мои вики")
    router.route.assert_awaited_once()


@pytest.mark.asyncio
async def test_wiki_query_never_reaches_router() -> None:
    sender = FakeSender()
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=make_classifier_result(Intent.WIKI, action="query", confidence=0.95)
    )
    router = MagicMock()
    router.route = AsyncMock(side_effect=AssertionError("must not be called"))
    runner = MagicMock()
    from ai_steward_wiki.tg.pipeline import WikiRunOutcome

    runner.run = AsyncMock(return_value=WikiRunOutcome(run_id="r", text="42", latency_ms=1))
    output = MagicMock()
    output.deliver = AsyncMock(return_value=None)
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=MagicMock(),
        classifier=classifier,
        runner=runner,
        output=output,
        router=router,
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="сколько у меня вики")
    router.route.assert_not_awaited()
    runner.run.assert_awaited_once()
    assert runner.run.await_args.kwargs["action"] == "query"
