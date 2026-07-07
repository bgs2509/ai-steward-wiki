"""Unit tests for the smalltalk/chitchat dispatch in DefaultPipeline (aisw-df4)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from ai_steward_wiki.classifier.schema import ClassifierResult, Intent
from ai_steward_wiki.tg.pipeline import CHAT_REPLY_RU, DefaultPipeline
from tests.unit.tg.conftest import FakeSender


def _classifier(*, confidence: float = 0.95) -> MagicMock:
    cls = MagicMock()
    cls.classify = AsyncMock(
        return_value=ClassifierResult(
            intent=Intent.CHAT,
            confidence=confidence,
            distilled_payload={},
            backend="fake",
            model="m",
            prompt_semver="1.0.0",
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


def _confirm() -> MagicMock:
    c = MagicMock()
    c.request_explicit = AsyncMock(return_value=MagicMock(pending_id=1))
    return c


def _runner() -> MagicMock:
    from ai_steward_wiki.tg.pipeline import WikiRunOutcome

    r = MagicMock()
    r.run = AsyncMock(
        return_value=WikiRunOutcome(run_id="run-x", text="should-not-run", latency_ms=1)
    )
    return r


def _pipe(*, sender: FakeSender, classifier: MagicMock, runner: MagicMock, confirm: MagicMock):
    return DefaultPipeline(
        sender=sender,
        idempotency=_idem(),
        confirmation=confirm,
        classifier=classifier,
        runner=runner,
        output=MagicMock(deliver=AsyncMock(return_value=None)),
    )


async def test_chat_intent_replies_and_returns() -> None:
    """A chat-classified message gets a short ru reply; no WIKI run, no confirm."""
    sender = FakeSender()
    runner = _runner()
    confirm = _confirm()
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(),
        runner=runner,
        confirm=confirm,
    )
    await pipe.on_text(telegram_id=42, chat_id=42, update_id=1, text="ты дурак?")

    assert sender.sends, "a reply must be sent"
    assert sender.sends[-1]["text"] == CHAT_REPLY_RU
    runner.run.assert_not_awaited()
    confirm.request_explicit.assert_not_awaited()


async def test_chat_never_emits_digest_unparseable(
    capsys: Any,
) -> None:
    """Casual «расскажи что-нибудь» must NOT trip the digest fast-path."""
    sender = FakeSender()
    pipe = _pipe(
        sender=sender,
        classifier=_classifier(),
        runner=_runner(),
        confirm=_confirm(),
    )
    await pipe.on_text(
        telegram_id=42, chat_id=42, update_id=1, text="расскажи что-нибудь интересное"
    )
    out = capsys.readouterr().out
    assert "tg.pipeline.digest.unparseable" not in out
    assert "tg.pipeline.chat.replied" in out
    assert sender.sends[-1]["text"] == CHAT_REPLY_RU
