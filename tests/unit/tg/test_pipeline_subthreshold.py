"""RED-first coverage for the sub-threshold clarify gate (aisw-xi8, DEC-2, FR-10)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_steward_wiki.classifier.schema import Intent
from ai_steward_wiki.tg.pipeline import DefaultPipeline
from tests.helpers.classifier_factory import make_classifier_result
from tests.unit.tg.conftest import FakeSender


def _make_idem() -> MagicMock:
    idem = MagicMock()
    idem.check_update_id = AsyncMock(return_value=True)
    idem.check_content = AsyncMock(return_value=("b" * 64, None))
    idem.record_dedup_choice = AsyncMock(return_value=None)
    return idem


def _pipe(
    sender: FakeSender, intent: Intent, confidence: float
) -> tuple[DefaultPipeline, MagicMock]:
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=make_classifier_result(intent, confidence=confidence)
    )
    runner = MagicMock()
    runner.run = AsyncMock(side_effect=AssertionError("generic runner must NEVER be reached"))
    router = MagicMock()
    router.route = AsyncMock(side_effect=AssertionError("router must NEVER be reached"))
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=MagicMock(),
        classifier=classifier,
        runner=runner,
        output=MagicMock(),
        router=router,
    )
    return pipe, runner


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", [Intent.JOB, Intent.ADMIN])
async def test_subthreshold_job_admin_gets_ru_clarify_and_never_reaches_runner(
    intent: Intent,
) -> None:
    sender = FakeSender()
    pipe, runner = _pipe(sender, intent, confidence=0.5)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="сделай что-то важное")

    runner.run.assert_not_awaited()
    assert (
        "уточни" in sender.sends[0]["text"].lower() or "не понял" in sender.sends[0]["text"].lower()
    )


@pytest.mark.asyncio
async def test_subthreshold_gate_logs_anchor(capsys: pytest.CaptureFixture[str]) -> None:
    sender = FakeSender()
    pipe, _ = _pipe(sender, Intent.JOB, confidence=0.3)

    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="сделай что-то")

    out = capsys.readouterr().out
    assert "tg.pipeline.subthreshold.clarify" in out


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", [Intent.WIKI, Intent.WEB, Intent.UNKNOWN])
async def test_subthreshold_non_destructive_intents_proceed_normally(intent: Intent) -> None:
    """Low confidence on WIKI/WEB/UNKNOWN is EXEMPT from the gate — these are
    never destructive/write-capable in the way job/admin are, so they proceed
    to the generic runner. CHAT is exempt too but is covered by its own test
    below — CHAT unconditionally short-circuits with a canned smalltalk reply
    (aisw-df4, unrelated to confidence) and never touches the runner at all."""
    sender = FakeSender()
    classifier = MagicMock()
    classifier.classify = AsyncMock(return_value=make_classifier_result(intent, confidence=0.1))
    runner = MagicMock()
    runner.run = AsyncMock(
        return_value=__import__(
            "ai_steward_wiki.tg.pipeline", fromlist=["WikiRunOutcome"]
        ).WikiRunOutcome(run_id="r", text="ok", latency_ms=1)
    )
    output = MagicMock()
    output.deliver = AsyncMock(return_value=None)
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=MagicMock(),
        classifier=classifier,
        runner=runner,
        output=output,
        router=None,
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="что-то")
    runner.run.assert_awaited_once()  # proceeded — gate did not intercept


@pytest.mark.asyncio
async def test_subthreshold_chat_gets_smalltalk_reply_not_clarify() -> None:
    """CHAT is EXEMPT from the gate too, but (unlike WIKI/WEB/UNKNOWN) it never
    reaches the runner at all — it always short-circuits with the canned
    smalltalk reply (aisw-df4), regardless of confidence."""
    sender = FakeSender()
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=make_classifier_result(Intent.CHAT, confidence=0.1)
    )
    runner = MagicMock()
    runner.run = AsyncMock(side_effect=AssertionError("generic runner must NEVER be reached"))
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=MagicMock(),
        classifier=classifier,
        runner=runner,
        output=MagicMock(),
        router=None,
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="ты дурак?")
    runner.run.assert_not_awaited()
    assert "уточни" not in sender.sends[0]["text"].lower()  # not the subthreshold gate


@pytest.mark.asyncio
async def test_job_above_threshold_proceeds_past_gate() -> None:
    sender = FakeSender()
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=make_classifier_result(Intent.JOB, action="list", confidence=0.95)
    )
    pipe = DefaultPipeline(
        sender=sender,
        idempotency=_make_idem(),
        confirmation=MagicMock(),
        classifier=classifier,
        runner=MagicMock(),
        output=MagicMock(),
        router=None,
    )
    await pipe.on_text(telegram_id=1, chat_id=10, update_id=2, text="какие у меня напоминания")
    # Reaches _handle_job's Task-C1.4 stub (not the subthreshold clarify line).
    assert "уточни" not in sender.sends[0]["text"].lower()
