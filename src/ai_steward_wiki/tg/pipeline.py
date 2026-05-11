# FILE: src/ai_steward_wiki/tg/pipeline.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Coordinator over already-built ingest blocks. Aiogram routers
#            delegate here so handler functions stay framework-thin and the
#            orchestration is unit-testable without a live Telegram bot.
#            MVP scope: L1 update_id dedup → friendly ack. Voice path attaches
#            a transcript; photo/document paths stage media. Full
#            classifier→runner→deliver_output composition lands in the next
#            chunk via the seams (`MessagePipeline.run_text`, etc.).
#   SCOPE: MessagePipeline Protocol + DefaultPipeline implementation.
#   DEPENDS: ai_steward_wiki.inbox.idempotency.IdempotencyService,
#            ai_steward_wiki.inbox.staging.MediaRef,
#            ai_steward_wiki.tg.voice.VoiceHandler (optional),
#            ai_steward_wiki.tg.photo.PhotoIngestor (optional),
#            ai_steward_wiki.tg.confirm.ConfirmationService,
#            ai_steward_wiki.tg.bot.TgSender, structlog
#   LINKS: M-TG-HANDLERS-WIRING (chunk 19), D-018, D-022, D-023
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ACK_TEXT_RU - default ack copy
#   ACK_VOICE_RU - prefix for voice-transcript reply
#   ACK_PHOTO_RU - ack for staged photo
#   ACK_DOC_RU - ack for staged document
#   ConfirmKeyboardAction - Literal[confirm|correct|cancel]
#   MessagePipeline - Protocol for the 5 entry points used by handlers
#   DefaultPipeline - concrete coordinator wiring existing building blocks
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 19: initial MessagePipeline coordinator
# END_CHANGE_SUMMARY

from __future__ import annotations

from typing import Literal, Protocol
from uuid import uuid4

import structlog

from ai_steward_wiki.inbox.idempotency import IdempotencyService
from ai_steward_wiki.tg.bot import TgSender
from ai_steward_wiki.tg.confirm import ConfirmationService
from ai_steward_wiki.tg.photo import PhotoIngestor
from ai_steward_wiki.tg.voice import VoiceHandler

__all__ = [
    "ACK_DOC_RU",
    "ACK_PHOTO_RU",
    "ACK_TEXT_RU",
    "ACK_VOICE_RU",
    "ConfirmKeyboardAction",
    "DefaultPipeline",
    "MessagePipeline",
]

_log = structlog.get_logger("tg.pipeline")

ACK_TEXT_RU = "Принято."
ACK_VOICE_RU = "Распознано:"
ACK_PHOTO_RU = "Фото получено."
ACK_DOC_RU = "Файл получен."

ConfirmKeyboardAction = Literal["confirm", "correct", "cancel"]


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
    ) -> None:
        self._sender = sender
        self._idem = idempotency
        self._confirm = confirmation
        self._voice = voice
        self._photo = photo

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
        await self._sender.send_message(chat_id, ACK_TEXT_RU)

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
        body = f"{ACK_VOICE_RU}\n{transcript.text}" if transcript.text else ACK_TEXT_RU
        await self._sender.send_message(chat_id, body)

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
        # Document staging is reserved for a future chunk (D-022 raw/files lane).
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
