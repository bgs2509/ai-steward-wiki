"""Unit tests for the '## Inbox hint' pre-router fast-path in DefaultPipeline (aisw-5sd, Phase-E.b).

A confident single-domain match against the sender's cached hint catalog
short-circuits the heavy Sonnet Router and goes straight to a route_ingest
confirm; everything else (ambiguous / no match / empty catalog / fast-path
disabled) falls through to the heavy router unchanged. The fast-path never
routes silently — the user still confirms via the route-confirm keyboard.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier.schema import ClassifierResult, Intent
from ai_steward_wiki.tg.pipeline import DefaultPipeline
from tests.unit.tg.conftest import FakeSender

# A hint catalog rich enough that a clearly-on-topic message clears
# MIN_SCORE (>=2 matched keywords) with a comfortable margin over the runner-up.
_HEALTH_HINT = "Ключевые слова: давление, пульс, анализы, лекарства, врач, симптомы, самочувствие."
_INVEST_HINT = "Ключевые слова: акции, облигации, дивиденды, портфель, брокер, доходность."
_CATALOG = {"Health-WIKI": _HEALTH_HINT, "Investment-WIKI": _INVEST_HINT}


def _classifier_result(intent: Intent = Intent.WIKI_INGEST) -> ClassifierResult:
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


def _idem() -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=True)
    idem.check_content = AsyncMock(return_value=("b" * 64, None))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


def _classifier(intent: Intent = Intent.WIKI_INGEST) -> MagicMock:
    cls = MagicMock()
    cls.classify = AsyncMock(return_value=_classifier_result(intent))
    return cls


def _confirm() -> MagicMock:
    c = MagicMock()
    rec = MagicMock()
    rec.pending_id = 777
    rec.recap_message_id = 4242
    c.request_explicit = AsyncMock(return_value=rec)
    return c


def _router() -> MagicMock:
    # If the fast-path fires we must never reach .route(); if it doesn't,
    # the heavy router runs — we only assert on call/no-call here, so a bare
    # AsyncMock return value is fine (the heavy-router path is covered elsewhere).
    rt = MagicMock()
    rt.route = AsyncMock()
    return rt


_UNSET = object()  # sentinel: "not passed" vs an explicit None


def _pipe(
    *,
    sender: FakeSender,
    confirm: MagicMock,
    router: MagicMock | None,
    hint_catalog_resolver: object | None,
    librarian: object = _UNSET,
    output: object = _UNSET,
    intent: Intent = Intent.WIKI_INGEST,
) -> DefaultPipeline:
    return DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=confirm,
        classifier=_classifier(intent),
        runner=MagicMock(),
        output=MagicMock() if output is _UNSET else output,  # type: ignore[arg-type]
        router=router,
        librarian=MagicMock() if librarian is _UNSET else librarian,  # type: ignore[arg-type]
        hint_catalog_resolver=hint_catalog_resolver,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_confident_hit_bypasses_router_and_requests_route_confirm(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sender = FakeSender()
    confirm = _confirm()
    router = _router()
    pipe = _pipe(
        sender=sender,
        confirm=confirm,
        router=router,
        hint_catalog_resolver=AsyncMock(return_value=_CATALOG),
    )

    await pipe.on_text(
        telegram_id=42,
        chat_id=10,
        update_id=5,
        text="давление 130 на 85, сдал анализы, выписали лекарства, был у врача",
    )

    router.route.assert_not_awaited()
    confirm.request_explicit.assert_awaited_once()
    draft = confirm.request_explicit.await_args.args[0]
    assert draft.category == "route_ingest"
    assert "Health-WIKI" in draft.recap_text
    events = capsys.readouterr().out
    assert "tg.pipeline.hint_fastpath.catalog" in events
    assert "tg.pipeline.hint_fastpath.hit" in events
    assert "Health-WIKI" in events


@pytest.mark.asyncio
async def test_ambiguous_match_falls_through_to_router(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sender = FakeSender()
    confirm = _confirm()
    router = _router()
    pipe = _pipe(
        sender=sender,
        confirm=confirm,
        router=router,
        hint_catalog_resolver=AsyncMock(return_value=_CATALOG),
    )

    # tokens drawn roughly equally from both hints → margin below MIN_MARGIN
    await pipe.on_text(
        telegram_id=42,
        chat_id=10,
        update_id=6,
        text="давление анализы лекарства акции дивиденды портфель",
    )

    router.route.assert_awaited_once()
    confirm.request_explicit.assert_not_awaited()
    events = capsys.readouterr().out
    assert "tg.pipeline.hint_fastpath.miss" in events
    assert "ambiguous" in events
    assert "tg.pipeline.hint_fastpath.fallthrough" in events


@pytest.mark.asyncio
async def test_no_match_falls_through_to_router(capsys: pytest.CaptureFixture[str]) -> None:
    sender = FakeSender()
    confirm = _confirm()
    router = _router()
    pipe = _pipe(
        sender=sender,
        confirm=confirm,
        router=router,
        hint_catalog_resolver=AsyncMock(return_value=_CATALOG),
    )

    await pipe.on_text(
        telegram_id=42, chat_id=10, update_id=7, text="сегодня был хороший солнечный день погуляли"
    )

    router.route.assert_awaited_once()
    confirm.request_explicit.assert_not_awaited()
    assert "no_match" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_empty_catalog_falls_through_to_router(capsys: pytest.CaptureFixture[str]) -> None:
    sender = FakeSender()
    confirm = _confirm()
    router = _router()
    pipe = _pipe(
        sender=sender,
        confirm=confirm,
        router=router,
        hint_catalog_resolver=AsyncMock(return_value={}),
    )

    await pipe.on_text(
        telegram_id=42, chat_id=10, update_id=8, text="давление 130 анализы лекарства"
    )

    router.route.assert_awaited_once()
    confirm.request_explicit.assert_not_awaited()
    events = capsys.readouterr().out
    assert "empty_catalog" in events
    # the scorer is never consulted on an empty catalog → no catalog event
    assert "tg.pipeline.hint_fastpath.catalog" not in events


@pytest.mark.asyncio
async def test_resolver_none_disables_fastpath() -> None:
    sender = FakeSender()
    confirm = _confirm()
    router = _router()
    pipe = _pipe(sender=sender, confirm=confirm, router=router, hint_catalog_resolver=None)

    await pipe.on_text(
        telegram_id=42, chat_id=10, update_id=9, text="давление 130 анализы лекарства"
    )

    router.route.assert_awaited_once()
    confirm.request_explicit.assert_not_awaited()


@pytest.mark.asyncio
async def test_librarian_none_disables_fastpath() -> None:
    # No point bypassing the router if the confirm→ingest path cannot run.
    sender = FakeSender()
    confirm = _confirm()
    router = _router()
    pipe = _pipe(
        sender=sender,
        confirm=confirm,
        router=router,
        hint_catalog_resolver=AsyncMock(return_value=_CATALOG),
        librarian=None,
    )

    await pipe.on_text(
        telegram_id=42,
        chat_id=10,
        update_id=11,
        text="давление 130 на 85, сдал анализы, выписали лекарства, был у врача",
    )

    router.route.assert_awaited_once()
    confirm.request_explicit.assert_not_awaited()
