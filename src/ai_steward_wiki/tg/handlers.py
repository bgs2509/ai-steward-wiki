# FILE: src/ai_steward_wiki/tg/handlers.py
# VERSION: 0.0.3
# START_MODULE_CONTRACT
#   PURPOSE: aiogram Router that adapts Telegram message/callback events to
#            the MessagePipeline Protocol. Handlers stay thin: extract IDs,
#            download bytes when needed, delegate; no business logic here.
#   SCOPE: build_router(pipeline) -> Router. Seven handlers: text, voice,
#          photo, audio, video_note, document, confirm callback. Audio and
#          video_note route to on_voice (STT path); photo forwards caption.
#          Private helper _download_bytes for file-id payloads.
#   DEPENDS: aiogram (Router, F, Bot, types), structlog,
#            ai_steward_wiki.tg.pipeline.MessagePipeline
#   LINKS: M-TG-HANDLERS-WIRING (chunk 19), D-022, D-023, D-031, D-042
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   CONFIRM_CALLBACK_PREFIX - "confirm:" callback_data prefix (D-023)
#   build_router - factory wiring 7 handlers to a MessagePipeline
#   parse_confirm_callback - parse `confirm:<pending_id>:<action>` payload
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.3 - aisw-b2x: audio and document handlers also forward
#                message.caption (to on_voice / on_document) so it reaches Stage-1.
#   PREVIOUS:    v0.0.2 - aisw-ahv (media chunk 3): add F.audio + F.video_note
#                handlers (route to on_voice STT path); photo handler forwards
#                message.caption to on_photo (D-022).
#                v0.0.1 - chunk 19: initial Router wiring delegating to pipeline
# END_CHANGE_SUMMARY

from __future__ import annotations

import io
from typing import TYPE_CHECKING, cast

import structlog

from ai_steward_wiki.tg.pipeline import ConfirmKeyboardAction, MessagePipeline

__all__ = [
    "CONFIRM_CALLBACK_PREFIX",
    "build_router",
    "parse_confirm_callback",
]

if TYPE_CHECKING:
    from aiogram import Router

_log = structlog.get_logger("tg.handlers")

CONFIRM_CALLBACK_PREFIX = "confirm:"
_VALID_ACTIONS: frozenset[str] = frozenset({"confirm", "correct", "cancel"})


def parse_confirm_callback(data: str) -> tuple[int, ConfirmKeyboardAction] | None:
    """Parse ``confirm:<pending_id>:<action>``; return None on malformed input."""
    if not data.startswith(CONFIRM_CALLBACK_PREFIX):
        return None
    parts = data.split(":")
    if len(parts) != 3:
        return None
    try:
        pending_id = int(parts[1])
    except ValueError:
        return None
    action = parts[2]
    if action not in _VALID_ACTIONS:
        return None
    return pending_id, cast(ConfirmKeyboardAction, action)


async def _download_bytes(bot: object, file_id: str) -> bytes:
    """Download a file by file_id and return its raw bytes.

    aiogram 3: `bot.get_file(file_id)` → `bot.download(file)` returns a
    BinaryIO-like; we read it into memory (MVP — small media only).
    """
    file = await bot.get_file(file_id)  # type: ignore[attr-defined]
    buf = io.BytesIO()
    await bot.download(file, destination=buf)  # type: ignore[attr-defined]
    return buf.getvalue()


def build_router(pipeline: MessagePipeline) -> Router:
    """Construct an aiogram Router wired to ``pipeline``."""
    from aiogram import F, Router
    from aiogram.types import CallbackQuery, Message

    router = Router(name="m-tg-handlers-wiring")

    @router.message(F.text & ~F.text.startswith("/"))
    async def _on_text(message: Message) -> None:
        # START_BLOCK_HANDLER_TEXT
        if message.from_user is None or message.text is None or message.chat is None:
            _log.debug("tg.handlers.text.skip_missing_fields")
            return
        await pipeline.on_text(
            telegram_id=message.from_user.id,
            chat_id=message.chat.id,
            update_id=message.message_id,
            text=message.text,
        )
        # END_BLOCK_HANDLER_TEXT

    @router.message(F.voice)
    async def _on_voice(message: Message) -> None:
        # START_BLOCK_HANDLER_VOICE
        if (
            message.from_user is None
            or message.voice is None
            or message.chat is None
            or message.bot is None
        ):
            _log.debug("tg.handlers.voice.skip_missing_fields")
            return
        audio = await _download_bytes(message.bot, message.voice.file_id)
        await pipeline.on_voice(
            telegram_id=message.from_user.id,
            chat_id=message.chat.id,
            update_id=message.message_id,
            audio_bytes=audio,
        )
        # END_BLOCK_HANDLER_VOICE

    @router.message(F.photo)
    async def _on_photo(message: Message) -> None:
        # START_BLOCK_HANDLER_PHOTO
        if (
            message.from_user is None
            or not message.photo
            or message.chat is None
            or message.bot is None
        ):
            _log.debug("tg.handlers.photo.skip_missing_fields")
            return
        # Telegram delivers photo as a list of sizes — take the largest (last).
        photo = message.photo[-1]
        data = await _download_bytes(message.bot, photo.file_id)
        await pipeline.on_photo(
            telegram_id=message.from_user.id,
            chat_id=message.chat.id,
            update_id=message.message_id,
            photo_bytes=data,
            mime="image/jpeg",  # TG re-encodes photos to JPEG (D-022)
            caption=message.caption,
        )
        # END_BLOCK_HANDLER_PHOTO

    @router.message(F.audio)
    async def _on_audio(message: Message) -> None:
        # START_BLOCK_HANDLER_AUDIO
        # Audio files (mp3/ogg/m4a) take the same STT path as voice messages (D-022).
        if (
            message.from_user is None
            or message.audio is None
            or message.chat is None
            or message.bot is None
        ):
            _log.debug("tg.handlers.audio.skip_missing_fields")
            return
        data = await _download_bytes(message.bot, message.audio.file_id)
        await pipeline.on_voice(
            telegram_id=message.from_user.id,
            chat_id=message.chat.id,
            update_id=message.message_id,
            audio_bytes=data,
            caption=message.caption,
        )
        # END_BLOCK_HANDLER_AUDIO

    @router.message(F.video_note)
    async def _on_video_note(message: Message) -> None:
        # START_BLOCK_HANDLER_VIDEO_NOTE
        # Video notes (mp4) carry an audio track; faster-whisper/pyav demuxes it,
        # so they route through the same STT path as voice (D-022).
        if (
            message.from_user is None
            or message.video_note is None
            or message.chat is None
            or message.bot is None
        ):
            _log.debug("tg.handlers.video_note.skip_missing_fields")
            return
        data = await _download_bytes(message.bot, message.video_note.file_id)
        await pipeline.on_voice(
            telegram_id=message.from_user.id,
            chat_id=message.chat.id,
            update_id=message.message_id,
            audio_bytes=data,
        )
        # END_BLOCK_HANDLER_VIDEO_NOTE

    @router.message(F.document)
    async def _on_document(message: Message) -> None:
        # START_BLOCK_HANDLER_DOCUMENT
        if (
            message.from_user is None
            or message.document is None
            or message.chat is None
            or message.bot is None
        ):
            _log.debug("tg.handlers.document.skip_missing_fields")
            return
        doc = message.document
        data = await _download_bytes(message.bot, doc.file_id)
        await pipeline.on_document(
            telegram_id=message.from_user.id,
            chat_id=message.chat.id,
            update_id=message.message_id,
            doc_bytes=data,
            mime=doc.mime_type or "application/octet-stream",
            filename=doc.file_name or "unnamed",
            caption=message.caption,
        )
        # END_BLOCK_HANDLER_DOCUMENT

    @router.callback_query(F.data.startswith(CONFIRM_CALLBACK_PREFIX))
    async def _on_confirm(callback: CallbackQuery) -> None:
        # START_BLOCK_HANDLER_CONFIRM_CB
        if callback.from_user is None or callback.data is None or callback.message is None:
            _log.debug("tg.handlers.callback.skip_missing_fields")
            await callback.answer()
            return
        parsed = parse_confirm_callback(callback.data)
        if parsed is None:
            _log.info("tg.handlers.callback.malformed", data=callback.data)
            await callback.answer()
            return
        pending_id, action = parsed
        await pipeline.on_confirm_callback(
            telegram_id=callback.from_user.id,
            chat_id=callback.message.chat.id,
            pending_id=pending_id,
            action=action,
        )
        await callback.answer()
        # END_BLOCK_HANDLER_CONFIRM_CB

    return router
