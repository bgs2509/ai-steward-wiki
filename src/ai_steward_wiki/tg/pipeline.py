# FILE: src/ai_steward_wiki/tg/pipeline.py
# VERSION: 0.2.0
# START_MODULE_CONTRACT
#   PURPOSE: Coordinator over already-built ingest blocks. Aiogram routers
#            delegate here so handler functions stay framework-thin and the
#            orchestration is unit-testable without a live Telegram bot.
#            v0.1.0 (chunk 20): wires Classifier (Stage-0) → Inbox L2 dedup →
#            WikiRunner (Stage-1a/1b) → OutputDelivery into on_text and
#            on_voice. Building blocks pre-existed; this module owns the
#            composition + safe-by-default error handling at the boundary.
#   SCOPE: MessagePipeline Protocol + Classifier/WikiRunner/OutputDelivery
#          Protocols + WikiRunOutcome dataclass + DefaultPipeline
#          implementation with optional injection (None → ack fallback).
#   DEPENDS: ai_steward_wiki.classifier.schema (ClassifierResult, Intent,
#            ClassifierError),
#            ai_steward_wiki.inbox.idempotency.IdempotencyService,
#            ai_steward_wiki.inbox.staging.MediaRef,
#            ai_steward_wiki.tg.voice.VoiceHandler (optional),
#            ai_steward_wiki.tg.photo.PhotoIngestor (optional),
#            ai_steward_wiki.tg.confirm.ConfirmationService,
#            ai_steward_wiki.tg.bot.TgSender, structlog
#   LINKS: M-TG-PIPELINE-CLASSIFIER (chunk 20), M-TG-HANDLERS-WIRING
#          (chunk 19), D-016, D-017, D-018, DEC-TPC-1..6
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ACK_TEXT_RU - default ack copy (fallback when classifier/runner/output missing)
#   ACK_VOICE_RU - prefix for voice-transcript reply (fallback)
#   ACK_PHOTO_RU - ack for staged photo
#   ACK_DOC_RU - ack for staged document
#   ACK_DEDUP_RU - reply on L2 dedup hit
#   ACK_CLASSIFY_ERR_RU - safe ack on classifier failure
#   ACK_RUNNER_ERR_RU - safe ack on runner failure
#   ConfirmKeyboardAction - Literal[confirm|correct|cancel]
#   Classifier - Protocol (Stage-0 wrapper, narrow API)
#   WikiRunOutcome - frozen dataclass returned by WikiRunner.run
#   WikiRunner - Protocol (Stage-1a/1b wrapper, narrow API)
#   OutputDelivery - Protocol (deliver_output wrapper)
#   MessagePipeline - Protocol for the 5 entry points used by handlers
#   DefaultPipeline - concrete coordinator wiring existing building blocks
#   StreamingDelivery - Protocol for slow-path race+stream wrapper (chunk 21)
#   DefaultStreamingDelivery - default race-and-stream impl over StreamEditor
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.2.0 - chunk 21: StreamingDelivery race+stream wrapper
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import uuid4

import structlog

from ai_steward_wiki.classifier.schema import (
    ClassifierError,
    ClassifierResult,
    Intent,
)
from ai_steward_wiki.inbox.idempotency import IdempotencyService
from ai_steward_wiki.tg.bot import TgSender
from ai_steward_wiki.tg.confirm import ConfirmationService
from ai_steward_wiki.tg.photo import PhotoIngestor
from ai_steward_wiki.tg.voice import VoiceHandler
from ai_steward_wiki.wiki.runner import WikiRunnerError

__all__ = [
    "ACK_CLASSIFY_ERR_RU",
    "ACK_DEDUP_RU",
    "ACK_DOC_RU",
    "ACK_PHOTO_RU",
    "ACK_RUNNER_ERR_RU",
    "ACK_TEXT_RU",
    "ACK_VOICE_RU",
    "Classifier",
    "ConfirmKeyboardAction",
    "DefaultPipeline",
    "DefaultStreamingDelivery",
    "MessagePipeline",
    "OutputDelivery",
    "StreamingDelivery",
    "WikiRunOutcome",
    "WikiRunner",
]

STREAMING_PLACEHOLDER_RU = "\u23f3 Думаю\u2026"
STREAMING_TIMEOUT_S = 5.0

_log = structlog.get_logger("tg.pipeline")

ACK_TEXT_RU = "Принято."
ACK_VOICE_RU = "Распознано:"
ACK_PHOTO_RU = "Фото получено."
ACK_DOC_RU = "Файл получен."
ACK_DEDUP_RU = "Уже видел такое сообщение — повторно не запускаю."
ACK_CLASSIFY_ERR_RU = "Не удалось распознать запрос, попробуйте ещё раз."  # noqa: RUF001
ACK_RUNNER_ERR_RU = "Задача заняла слишком много времени, попробуйте позже."

ConfirmKeyboardAction = Literal["confirm", "correct", "cancel"]


class Classifier(Protocol):
    """Stage-0 Haiku classifier wrapper (D-016 + DEC-TPC-1)."""

    async def classify(self, text: str, *, correlation_id: str) -> ClassifierResult: ...


@dataclass(frozen=True, slots=True)
class WikiRunOutcome:
    """Aggregated Stage-1a/1b runner result returned to the pipeline (DEC-TPC-2)."""

    run_id: str
    text: str
    latency_ms: int


class WikiRunner(Protocol):
    """Stage-1a/1b Sonnet runner wrapper (D-017 + DEC-TPC-2 + DEC-TPS-2)."""

    async def run(
        self,
        *,
        text: str,
        owner_telegram_id: int,
        correlation_id: str,
        intent: Intent,
        on_event: Callable[[object], Awaitable[None]] | None = None,
    ) -> WikiRunOutcome: ...


class StreamingDelivery(Protocol):
    """Slow-path streaming wrapper (D-026 + DEC-TPS-3..5).

    Implementations race the runner against a 5s timer; if the runner
    completes first the fast path is taken and a single deliver is issued.
    Otherwise a placeholder is sent and assistant text streamed in-place.
    """

    async def run_and_deliver(
        self,
        *,
        runner: WikiRunner,
        output: OutputDelivery,
        chat_id: int,
        telegram_id: int,
        text: str,
        intent: Intent,
        correlation_id: str,
    ) -> WikiRunOutcome: ...


class OutputDelivery(Protocol):
    """Hybrid-policy reply delivery wrapper (D-025 + DEC-TPC-1)."""

    async def deliver(
        self,
        *,
        chat_id: int,
        telegram_id: int,
        run_id: str,
        text: str,
    ) -> None: ...


class MessagePipeline(Protocol):
    """Entry points called by aiogram handler functions in handlers.py."""

    async def on_text(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        text: str,
    ) -> None: ...

    async def on_voice(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        audio_bytes: bytes,
    ) -> None: ...

    async def on_photo(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        photo_bytes: bytes,
        mime: str,
    ) -> None: ...

    async def on_document(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        doc_bytes: bytes,
        mime: str,
        filename: str,
    ) -> None: ...

    async def on_confirm_callback(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        pending_id: int,
        action: ConfirmKeyboardAction,
    ) -> None: ...


class DefaultPipeline:
    """Default coordinator. Composes building blocks; safe-by-default acks."""

    def __init__(
        self,
        *,
        sender: TgSender,
        idempotency: IdempotencyService,
        confirmation: ConfirmationService,
        voice: VoiceHandler | None = None,
        photo: PhotoIngestor | None = None,
        classifier: Classifier | None = None,
        runner: WikiRunner | None = None,
        output: OutputDelivery | None = None,
        streaming: StreamingDelivery | None = None,
    ) -> None:
        self._sender = sender
        self._idem = idempotency
        self._confirm = confirmation
        self._voice = voice
        self._photo = photo
        self._classifier = classifier
        self._runner = runner
        self._output = output
        self._streaming = streaming

    async def _l1_check(self, *, update_id: int, telegram_id: int, kind: str) -> bool:
        """Return True iff the update is new (proceed). Logs on duplicate."""
        is_new = await self._idem.check_update_id(update_id)
        if not is_new:
            _log.info(
                "tg.pipeline.skip.l1_dup",
                update_id=update_id,
                telegram_id=telegram_id,
                kind=kind,
            )
        return is_new

    def _full_pipeline_available(self) -> bool:
        """True iff Classifier+WikiRunner+OutputDelivery are all wired."""
        return (
            self._classifier is not None and self._runner is not None and self._output is not None
        )

    # START_BLOCK_TEXT_PIPELINE
    async def _run_text_pipeline(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        text: str,
        source: Literal["text", "voice"],
    ) -> None:
        """Shared body: L2 dedup → classify → run → deliver. Errors → safe acks."""
        assert self._classifier is not None
        assert self._runner is not None
        assert self._output is not None

        correlation_id = f"tg-{update_id}-{telegram_id}"

        sha256, match = await self._idem.check_content(telegram_id, "text", text)
        if match is not None:
            _log.info(
                "tg.pipeline.inbox.l2_dedup_hit",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                kind="text",
                sha8=sha256[:8],
                source=source,
            )
            await self._idem.record_dedup_choice(sha256, telegram_id, "auto_skip")
            await self._sender.send_message(chat_id, ACK_DEDUP_RU)
            return

        _log.info(
            "tg.pipeline.classify.begin",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            chars=len(text),
            source=source,
        )
        try:
            result = await self._classifier.classify(text, correlation_id=correlation_id)
        except ClassifierError:
            _log.exception(
                "tg.pipeline.classify.error",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                error_class="ClassifierError",
            )
            await self._sender.send_message(chat_id, ACK_CLASSIFY_ERR_RU)
            return

        _log.info(
            "tg.pipeline.classify.done",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            intent=result.intent.value,
            confidence=result.confidence,
            latency_ms=result.latency_ms,
        )

        _log.info(
            "tg.pipeline.runner.dispatched",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            intent=result.intent.value,
        )
        try:
            if self._streaming is not None and source == "text":
                outcome = await self._streaming.run_and_deliver(
                    runner=self._runner,
                    output=self._output,
                    chat_id=chat_id,
                    telegram_id=telegram_id,
                    text=text,
                    intent=result.intent,
                    correlation_id=correlation_id,
                )
                _log.info(
                    "tg.pipeline.runner.completed",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                    run_id=outcome.run_id,
                    chars=len(outcome.text),
                    latency_ms=outcome.latency_ms,
                )
                _log.info(
                    "tg.pipeline.deliver.sent",
                    correlation_id=correlation_id,
                    telegram_id=telegram_id,
                    run_id=outcome.run_id,
                    chars=len(outcome.text or ACK_TEXT_RU),
                    streamed=True,
                )
                return
            outcome = await self._runner.run(
                text=text,
                owner_telegram_id=telegram_id,
                correlation_id=correlation_id,
                intent=result.intent,
            )
        except WikiRunnerError:
            _log.exception(
                "tg.pipeline.runner.error",
                correlation_id=correlation_id,
                telegram_id=telegram_id,
                error_class="WikiRunnerError",
            )
            await self._sender.send_message(chat_id, ACK_RUNNER_ERR_RU)
            return

        _log.info(
            "tg.pipeline.runner.completed",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            run_id=outcome.run_id,
            chars=len(outcome.text),
            latency_ms=outcome.latency_ms,
        )

        # Empty assistant output → safe fallback (avoid silent zero-byte reply).
        reply_text = outcome.text if outcome.text else ACK_TEXT_RU

        await self._output.deliver(
            chat_id=chat_id,
            telegram_id=telegram_id,
            run_id=outcome.run_id,
            text=reply_text,
        )
        _log.info(
            "tg.pipeline.deliver.sent",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            run_id=outcome.run_id,
            chars=len(reply_text),
        )

    # END_BLOCK_TEXT_PIPELINE

    async def on_text(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        text: str,
    ) -> None:
        if not await self._l1_check(update_id=update_id, telegram_id=telegram_id, kind="text"):
            return
        _log.info(
            "tg.pipeline.text",
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            chars=len(text),
        )
        if not self._full_pipeline_available():
            await self._sender.send_message(chat_id, ACK_TEXT_RU)
            return
        await self._run_text_pipeline(
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            text=text,
            source="text",
        )

    async def on_voice(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        audio_bytes: bytes,
    ) -> None:
        if not await self._l1_check(update_id=update_id, telegram_id=telegram_id, kind="voice"):
            return
        if self._voice is None:
            _log.warning("tg.pipeline.voice.no_handler", telegram_id=telegram_id)
            await self._sender.send_message(chat_id, ACK_TEXT_RU)
            return
        run_id = f"voice-{uuid4().hex[:12]}"
        ref, transcript = await self._voice.handle(audio_bytes, run_id=run_id)
        _log.info(
            "tg.pipeline.voice",
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            run_id=run_id,
            sha256=ref.sha256,
            lang=transcript.lang,
            chars=len(transcript.text),
        )
        if not transcript.text:
            await self._sender.send_message(chat_id, ACK_TEXT_RU)
            return
        if not self._full_pipeline_available():
            body = f"{ACK_VOICE_RU}\n{transcript.text}"
            await self._sender.send_message(chat_id, body)
            return
        await self._run_text_pipeline(
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            text=transcript.text,
            source="voice",
        )

    async def on_photo(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        photo_bytes: bytes,
        mime: str,
    ) -> None:
        if not await self._l1_check(update_id=update_id, telegram_id=telegram_id, kind="photo"):
            return
        if self._photo is None:
            _log.warning("tg.pipeline.photo.no_handler", telegram_id=telegram_id)
            await self._sender.send_message(chat_id, ACK_PHOTO_RU)
            return
        run_id = f"photo-{uuid4().hex[:12]}"
        ref = self._photo.handle(photo_bytes, run_id=run_id, mime=mime)
        _log.info(
            "tg.pipeline.photo",
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            run_id=run_id,
            sha256=ref.sha256,
            ext=ref.ext,
        )
        await self._sender.send_message(chat_id, ACK_PHOTO_RU)

    async def on_document(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        update_id: int,
        doc_bytes: bytes,
        mime: str,
        filename: str,
    ) -> None:
        if not await self._l1_check(update_id=update_id, telegram_id=telegram_id, kind="document"):
            return
        # Document staging is reserved for a future chunk (chunk 22 M-TG-DOCUMENT-FULL, D-022).
        # MVP: log + ack so the wire is observable in journald.
        _log.info(
            "tg.pipeline.document",
            telegram_id=telegram_id,
            chat_id=chat_id,
            update_id=update_id,
            mime=mime,
            filename=filename,
            size=len(doc_bytes),
        )
        await self._sender.send_message(chat_id, ACK_DOC_RU)

    async def on_confirm_callback(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        pending_id: int,
        action: ConfirmKeyboardAction,
    ) -> None:
        status = await self._confirm.resolve(telegram_id, pending_id, action)
        _log.info(
            "tg.pipeline.confirm",
            telegram_id=telegram_id,
            chat_id=chat_id,
            pending_id=pending_id,
            action=action,
            status=status,
        )


class DefaultStreamingDelivery:
    """Race-and-stream wrapper (DEC-TPS-1..5).

    Behaviour:
      - Launches ``runner.run`` with an on_event callback that captures
        StreamEvent objects into an internal list.
      - Waits up to ``timeout_s`` for the runner to finish. If it does,
        fast path: single ``output.deliver``.
      - Otherwise sends ``placeholder_text``, constructs StreamEditor over the
        placeholder, replays buffered chunks, then live-feeds subsequent ones.
      - On runner completion finalizes the editor and calls ``output.deliver``
        with the final aggregated text.
      - On runner exception finalizes editor (best-effort) and re-raises so
        the parent pipeline maps to ACK_RUNNER_ERR_RU.
    """

    def __init__(
        self,
        *,
        sender: TgSender,
        timeout_s: float = STREAMING_TIMEOUT_S,
        placeholder_text: str = STREAMING_PLACEHOLDER_RU,
        stream_editor_factory: Callable[..., object] | None = None,
    ) -> None:
        self._sender = sender
        self._timeout_s = timeout_s
        self._placeholder_text = placeholder_text
        self._stream_editor_factory = stream_editor_factory

    def _make_editor(self, *, chat_id: int, message_id: int) -> object:
        if self._stream_editor_factory is not None:
            return self._stream_editor_factory(
                sender=self._sender, chat_id=chat_id, first_message_id=message_id
            )
        # Local import to keep stream_edit dependency lazy / test-friendly.
        from ai_steward_wiki.tg.stream_edit import StreamEditor

        return StreamEditor(sender=self._sender, chat_id=chat_id, first_message_id=message_id)

    async def run_and_deliver(
        self,
        *,
        runner: WikiRunner,
        output: OutputDelivery,
        chat_id: int,
        telegram_id: int,
        text: str,
        intent: Intent,
        correlation_id: str,
    ) -> WikiRunOutcome:
        from ai_steward_wiki.wiki.runner import aggregate_text  # lazy import

        buffered: list[object] = []
        live_editor: object | None = None

        async def on_event(ev: object) -> None:
            if live_editor is None:
                buffered.append(ev)
                return
            text_piece = _event_text(ev)
            if text_piece:
                await live_editor.feed(text_piece)  # type: ignore[attr-defined]

        runner_task = asyncio.create_task(
            runner.run(
                text=text,
                owner_telegram_id=telegram_id,
                correlation_id=correlation_id,
                intent=intent,
                on_event=on_event,
            )
        )

        try:
            outcome = await asyncio.wait_for(asyncio.shield(runner_task), timeout=self._timeout_s)
            # Fast path.
            reply_text = outcome.text if outcome.text else ACK_TEXT_RU
            await output.deliver(
                chat_id=chat_id,
                telegram_id=telegram_id,
                run_id=outcome.run_id,
                text=reply_text,
            )
            return outcome
        except TimeoutError:
            pass

        # Slow path: send placeholder + start streaming.
        placeholder = await self._sender.send_message(chat_id, self._placeholder_text)
        live_editor = self._make_editor(chat_id=chat_id, message_id=placeholder.message_id)
        _log.info(
            "tg.pipeline.stream.begin",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            chat_id=chat_id,
            message_id=placeholder.message_id,
        )

        # Replay any chunks that arrived during the race.
        for ev in buffered:
            text_piece = _event_text(ev)
            if text_piece:
                try:
                    await live_editor.feed(text_piece)  # type: ignore[attr-defined]
                    _log.info(
                        "tg.pipeline.stream.chunk",
                        correlation_id=correlation_id,
                        chars=len(text_piece),
                        replayed=True,
                    )
                except Exception as exc:
                    _log.warning(
                        "tg.pipeline.stream.error",
                        correlation_id=correlation_id,
                        error=type(exc).__name__,
                    )

        try:
            outcome = await runner_task
        except Exception:
            # Finalize editor best-effort, then re-raise.
            try:
                await live_editor.finalize()  # type: ignore[attr-defined]
            except Exception as exc:
                _log.warning(
                    "tg.pipeline.stream.error",
                    correlation_id=correlation_id,
                    error=type(exc).__name__,
                    phase="finalize_on_runner_exception",
                )
            raise

        # Build final text from the streamed events (DEC-TPS-4) — fall back
        # to outcome.text or ACK_TEXT_RU if events lacked assistant content.
        from ai_steward_wiki.wiki.streaming import StreamEvent

        final_text = aggregate_text([e for e in buffered if isinstance(e, StreamEvent)])
        if not final_text:
            final_text = outcome.text or ACK_TEXT_RU

        try:
            await live_editor.finalize()  # type: ignore[attr-defined]
        except Exception as exc:
            _log.warning(
                "tg.pipeline.stream.error",
                correlation_id=correlation_id,
                error=type(exc).__name__,
                phase="finalize",
            )

        await output.deliver(
            chat_id=chat_id,
            telegram_id=telegram_id,
            run_id=outcome.run_id,
            text=final_text,
        )
        _log.info(
            "tg.pipeline.stream.final",
            correlation_id=correlation_id,
            telegram_id=telegram_id,
            run_id=outcome.run_id,
            chars=len(final_text),
        )
        return WikiRunOutcome(run_id=outcome.run_id, text=final_text, latency_ms=outcome.latency_ms)


def _event_text(ev: object) -> str:
    """Extract text fragment from a StreamEvent (best effort)."""
    payload = getattr(ev, "payload", None)
    if not isinstance(payload, dict):
        return ""
    if getattr(ev, "type", None) != "assistant_chunk":
        return ""
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    t = item.get("text")
                    if isinstance(t, str):
                        parts.append(t)
            if parts:
                return "".join(parts)
    delta = payload.get("delta")
    if isinstance(delta, dict):
        t = delta.get("text")
        if isinstance(t, str):
            return t
    t = payload.get("text")
    if isinstance(t, str):
        return t
    return ""
