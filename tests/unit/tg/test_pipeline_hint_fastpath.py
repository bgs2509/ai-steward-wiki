"""Unit tests for the '## Inbox hint' pre-router fast-path in DefaultPipeline (aisw-5sd, Phase-E.b).

A confident single-domain match against the sender's cached hint catalog
short-circuits the heavy Sonnet Router; everything else (ambiguous / no match /
empty catalog / fast-path disabled) falls through to the heavy router unchanged.

aisw-2ra: a confident match now routes SILENTLY (D-023 'auto') — the item is
ingested via _ingest_and_deliver (no Confirm/Cancel keyboard) and the user gets a
"✅ Записал в <WIKI>" ack carrying a one-tap redirect picker
(build_route_redirect_keyboard); with no other WIKI it degrades to a plain
auto_ack. A non-ok ingest sends the composed error reply and does NOT offer a
redirect.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier.schema import ClassifierResult, Intent
from ai_steward_wiki.inbox.router import RouterIntent
from ai_steward_wiki.tg.pipeline import DefaultPipeline, IngestOutcome
from tests.helpers.classifier_factory import make_classifier_result
from tests.unit.tg.conftest import FakeSender

# A hint catalog rich enough that a clearly-on-topic message clears
# MIN_SCORE (>=2 matched keywords) with a comfortable margin over the runner-up.
_HEALTH_HINT = "Ключевые слова: давление, пульс, анализы, лекарства, врач, симптомы, самочувствие."
_INVEST_HINT = "Ключевые слова: акции, облигации, дивиденды, портфель, брокер, доходность."
_CATALOG = {"Health-WIKI": _HEALTH_HINT, "Investment-WIKI": _INVEST_HINT}


def _classifier_result(intent: Intent = Intent.WIKI) -> ClassifierResult:
    return make_classifier_result(intent, action="ingest", confidence=0.9)


def _idem() -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=True)
    idem.check_content = AsyncMock(return_value=("b" * 64, None))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


def _classifier(intent: Intent = Intent.WIKI) -> MagicMock:
    cls = MagicMock()
    cls.classify = AsyncMock(return_value=_classifier_result(intent))
    return cls


def _confirm() -> MagicMock:
    c = MagicMock()
    rec = MagicMock()
    rec.pending_id = 777
    rec.recap_message_id = 4242
    c.request_explicit = AsyncMock(return_value=rec)
    c.auto_ack = AsyncMock(return_value=999)
    return c


def _router() -> MagicMock:
    # If the fast-path fires we must never reach .route(); if it doesn't,
    # the heavy router runs — we only assert on call/no-call here, so a bare
    # AsyncMock return value is fine (the heavy-router path is covered elsewhere).
    rt = MagicMock()
    rt.route = AsyncMock()
    return rt


def _librarian(status: str = "ok", target: str = "Health-WIKI") -> MagicMock:
    lib = MagicMock()
    lib.ingest = AsyncMock(
        return_value=IngestOutcome(
            status=status,  # type: ignore[arg-type]
            reply="Записал в Health-WIKI." if status == "ok" else "Не получилось — попробуй позже.",
            run_id="ing-1" if status == "ok" else None,
            target_wiki=target if status == "ok" else None,
            created=False,
        )
    )
    return lib


def _output() -> MagicMock:
    out = MagicMock()
    out.deliver = AsyncMock(return_value=None)
    return out


def _active_wiki() -> MagicMock:
    aw = MagicMock()
    aw.set_active = AsyncMock(return_value=None)
    aw.get_active = AsyncMock(return_value=None)
    return aw


def _owner_wikis(*names: str):
    async def _resolver(_owner: int) -> list[tuple[str, Path]]:
        return [(n, Path(f"/x/{n}")) for n in names]

    return _resolver


_UNSET = object()  # sentinel: "not passed" vs an explicit None


def _pipe(
    *,
    sender: FakeSender,
    confirm: MagicMock,
    router: MagicMock | None,
    hint_catalog_resolver: object | None,
    librarian: object = _UNSET,
    output: object = _UNSET,
    owner_wikis_resolver: object | None = None,
    active_wiki: object | None = None,
    intent: Intent = Intent.WIKI,
) -> DefaultPipeline:
    return DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=confirm,
        classifier=_classifier(intent),
        runner=MagicMock(),
        output=_output() if output is _UNSET else output,  # type: ignore[arg-type]
        router=router,
        librarian=_librarian() if librarian is _UNSET else librarian,  # type: ignore[arg-type]
        hint_catalog_resolver=hint_catalog_resolver,  # type: ignore[arg-type]
        owner_wikis_resolver=owner_wikis_resolver,  # type: ignore[arg-type]
        active_wiki=active_wiki,  # type: ignore[arg-type]
    )


_CONFIDENT_HEALTH = "давление 130 на 85, сдал анализы, выписали лекарства, был у врача"


@pytest.mark.asyncio
async def test_confident_hit_routes_silently_with_redirect_picker(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """aisw-2ra: a confident match ingests SILENTLY then acks with a redirect picker."""
    sender = FakeSender()
    confirm = _confirm()
    router = _router()
    lib = _librarian()
    out = _output()
    aw = _active_wiki()
    pipe = _pipe(
        sender=sender,
        confirm=confirm,
        router=router,
        hint_catalog_resolver=AsyncMock(return_value=_CATALOG),
        librarian=lib,
        output=out,
        owner_wikis_resolver=_owner_wikis("Health-WIKI", "Investment-WIKI"),
        active_wiki=aw,
    )

    await pipe.on_text(telegram_id=42, chat_id=10, update_id=5, text=_CONFIDENT_HEALTH)

    # heavy router skipped; ingest ran silently and reply delivered.
    router.route.assert_not_awaited()
    lib.ingest.assert_awaited_once()
    routed = lib.ingest.await_args.args[0]
    assert routed.intent is RouterIntent.ROUTE
    assert routed.target_wiki == "Health-WIKI"
    out.deliver.assert_awaited_once()
    aw.set_active.assert_awaited_once_with(42, "Health-WIKI")

    # ack carries a redirect picker (no Confirm/Cancel), excluding the target WIKI.
    confirm.request_explicit.assert_awaited_once()
    draft = confirm.request_explicit.await_args.args[0]
    assert draft.category == "route_ingest"
    assert draft.recap_text == "✅ Записал в Health-WIKI. Не туда? Перенесу:"
    kb = confirm.request_explicit.await_args.kwargs["keyboard_factory"](555)
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert cbs == ["wikipick:555:0"]  # only Investment-WIKI (Health-WIKI excluded)
    assert all(not c.startswith("confirm:") for c in cbs)

    events = capsys.readouterr().out
    assert "tg.pipeline.hint_fastpath.silent_route" in events
    assert "tg.pipeline.hint_fastpath.hit" not in events


@pytest.mark.asyncio
async def test_confident_hit_no_other_wiki_plain_ack() -> None:
    """aisw-2ra: with no OTHER WIKI to redirect into, the ack is plain (no keyboard)."""
    confirm = _confirm()
    lib = _librarian()
    pipe = _pipe(
        sender=FakeSender(),
        confirm=confirm,
        router=_router(),
        hint_catalog_resolver=AsyncMock(return_value=_CATALOG),
        librarian=lib,
        owner_wikis_resolver=_owner_wikis("Health-WIKI"),  # only the target
    )

    await pipe.on_text(telegram_id=42, chat_id=10, update_id=5, text=_CONFIDENT_HEALTH)

    lib.ingest.assert_awaited_once()
    confirm.request_explicit.assert_not_awaited()  # no redirect row
    confirm.auto_ack.assert_awaited_once_with(10, "✅ Записал в Health-WIKI.")


@pytest.mark.asyncio
async def test_confident_hit_failed_ingest_sends_error_no_redirect() -> None:
    """aisw-2ra: a non-ok silent ingest sends the composed reply, offers no redirect."""
    sender = FakeSender()
    confirm = _confirm()
    lib = _librarian(status="rejected")
    out = _output()
    aw = _active_wiki()
    pipe = _pipe(
        sender=sender,
        confirm=confirm,
        router=_router(),
        hint_catalog_resolver=AsyncMock(return_value=_CATALOG),
        librarian=lib,
        output=out,
        owner_wikis_resolver=_owner_wikis("Health-WIKI", "Investment-WIKI"),
        active_wiki=aw,
    )

    await pipe.on_text(telegram_id=42, chat_id=10, update_id=5, text=_CONFIDENT_HEALTH)

    lib.ingest.assert_awaited_once()
    out.deliver.assert_not_awaited()  # error path uses sender, not OutputDelivery
    aw.set_active.assert_not_awaited()  # no sticky pointer on failure
    confirm.request_explicit.assert_not_awaited()
    confirm.auto_ack.assert_not_awaited()
    assert any("Не получилось" in s["text"] for s in sender.sends)


async def test_long_text_skips_fastpath_even_when_confident() -> None:
    """aisw-378: a long document must NOT auto-route on incidental keyword overlap.

    Same confident Medical keywords, but padded past MAX_FASTPATH_CHARS — the
    keyword fast-path is unreliable on long text (the coal-report → Medical bug),
    so it falls through to the context-aware Sonnet router.
    """
    confirm = _confirm()
    router = _router()
    pipe = _pipe(
        sender=FakeSender(),
        confirm=confirm,
        router=router,
        hint_catalog_resolver=AsyncMock(return_value=_CATALOG),
    )
    long_text = "давление анализы лекарства врач. " * 60  # > 600 chars

    await pipe.on_text(telegram_id=42, chat_id=10, update_id=5, text=long_text)

    router.route.assert_awaited_once()  # heavy router decides, not the fast-path
    confirm.request_explicit.assert_not_awaited()


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
