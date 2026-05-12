# FILE: src/ai_steward_wiki/tg/handlers.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: aiogram Router that adapts Telegram message/callback events to
#            the MessagePipeline Protocol (+ the bot's first slash commands).
#            Handlers stay thin: extract IDs, download bytes when needed,
#            delegate; no business logic here.
#   SCOPE: build_router(pipeline) -> Router. Message handlers: /digest_now,
#          /expand <section> (aisw-269 — first Command-filter handlers; reach
#          scheduler.firing accessors), text, voice, photo, audio, video_note,
#          document; confirm callback. Audio and video_note route to on_voice
#          (STT path); photo forwards caption. Private helper _download_bytes
#          for file-id payloads. (The text handler already excludes "/"-prefixed
#          messages, so command order is irrelevant.)
#   DEPENDS: aiogram (Router, F, Bot, filters.Command, types), structlog,
#            ai_steward_wiki.tg.pipeline.MessagePipeline,
#            ai_steward_wiki.scheduler.firing (list_owner_digest_job_ids /
#            fire_digest_job / run_section_expand / DigestNotInitialisedError)
#   LINKS: M-TG-HANDLERS-WIRING (chunk 19), M-SCHEDULER-FIRING, D-022, D-023,
#          D-024, D-031, D-042, ADR-025, aisw-269
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   CONFIRM_CALLBACK_PREFIX - "confirm:" callback_data prefix (D-023)
#   EXPAND_SECTION_KEYS - the four /expand <section> keys (mirror D-024 headers)
#   build_router - factory wiring the slash commands + message/callback handlers to a MessagePipeline
#   parse_confirm_callback - parse `confirm:<pending_id>:<action>` payload
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - aisw-269 (Inbox-WIKI Phase-D.b.2b): the bot's first
#                slash commands — /digest_now (run the owner's enabled digest_job
#                rows via firing.fire_digest_job; 0 → ru hint; per-job errors
#                caught) and /expand <section> (today|meds|trackers|wiki →
#                firing.run_section_expand → message.answer; bad key → usage).
#                New anchors tg.command.digest_now(.empty/.job_failed/.done) +
#                tg.command.expand(.bad_section/.delivered/.failed).
#   PREVIOUS:    v0.0.4 - aisw-3dr: video_note handler stages as ext=mp4/mime=video/mp4,
#                audio handler derives ext from mime/file_name — no more .ogg for mp4.
#   PREVIOUS:    v0.0.3 - aisw-b2x: audio and document handlers also forward
#                message.caption (to on_voice / on_document) so it reaches Stage-1.
#                v0.0.2 - aisw-ahv (media chunk 3): add F.audio + F.video_note
#                handlers (route to on_voice STT path); photo handler forwards
#                message.caption to on_photo (D-022).
#                v0.0.1 - chunk 19: initial Router wiring delegating to pipeline
# END_CHANGE_SUMMARY

from __future__ import annotations

import io
from typing import TYPE_CHECKING, cast

import structlog

from ai_steward_wiki.scheduler import firing
from ai_steward_wiki.tg.pipeline import ConfirmKeyboardAction, MessagePipeline

__all__ = [
    "CONFIRM_CALLBACK_PREFIX",
    "EXPAND_SECTION_KEYS",
    "build_router",
    "parse_confirm_callback",
]

if TYPE_CHECKING:
    from aiogram import Router

_log = structlog.get_logger("tg.handlers")

CONFIRM_CALLBACK_PREFIX = "confirm:"
_VALID_ACTIONS: frozenset[str] = frozenset({"confirm", "correct", "cancel"})

# /expand <section> — the four keys mirror the D-024 <b>-headers in prompts/digest.md.
EXPAND_SECTION_KEYS: tuple[str, ...] = ("today", "meds", "trackers", "wiki")

# ru user-facing strings for the slash commands (D-032: ru-only, no i18n).
_DIGEST_NOW_NONE_RU = (
    "У тебя пока нет настроенной сводки. Создай, например: «делай сводку каждый день в 9»."  # noqa: RUF001
)
_DIGEST_UNAVAILABLE_RU = "Сводки сейчас недоступны — попробуй позже."
_EXPAND_USAGE_RU = "Используй: /expand <раздел> — today | meds | trackers | wiki"
_EXPAND_NO_WIKI_RU = "У тебя пока нет ни одной WIKI для детализации."  # noqa: RUF001
_EXPAND_EMPTY_RU = "По этому разделу за период ничего нет."
_GENERIC_ERR_RU = "Что-то пошло не так. Попробуй ещё раз чуть позже."

# Map common audio MIME types to a staging-file extension (D-022). Unknown →
# fall back to the file_name suffix, else "mp3".
_AUDIO_MIME_TO_EXT: dict[str, str] = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "aac",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
}


def _audio_ext_for(mime: str | None, file_name: str | None) -> str:
    """Pick a sane staging extension for an audio file (alnum, ≤8 chars)."""
    if mime:
        ext = _AUDIO_MIME_TO_EXT.get(mime.lower())
        if ext is not None:
            return ext
    if file_name and "." in file_name:
        suffix = file_name.rsplit(".", 1)[-1].lower()
        if suffix.isalnum() and 1 <= len(suffix) <= 8:
            return suffix
    return "mp3"


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
    from aiogram.filters import Command
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

    @router.message(Command("digest_now"))
    async def _on_digest_now(message: Message) -> None:
        # START_BLOCK_HANDLER_DIGEST_NOW
        # aisw-269: ad-hoc digest — run each of the owner's enabled digest_job rows
        # through the existing fire_digest_job pipeline (lock/audit/3-strike reused).
        if message.from_user is None or message.chat is None:
            _log.debug("tg.handlers.digest_now.skip_missing_fields")
            return
        owner = message.from_user.id
        try:
            ids = await firing.list_owner_digest_job_ids(owner)
        except firing.DigestNotInitialisedError:
            _log.warning("tg.command.digest_now.unavailable", owner_telegram_id=owner)
            await message.answer(_DIGEST_UNAVAILABLE_RU)
            return
        except Exception as exc:  # defensive: never bubble to the dispatcher
            _log.warning(
                "tg.command.digest_now.failed",
                owner_telegram_id=owner,
                error_class=type(exc).__name__,
            )
            await message.answer(_GENERIC_ERR_RU)
            return
        if not ids:
            _log.info("tg.command.digest_now.empty", owner_telegram_id=owner)
            await message.answer(_DIGEST_NOW_NONE_RU)
            return
        _log.info("tg.command.digest_now", owner_telegram_id=owner, n_jobs=len(ids))
        for job_id in ids:
            try:
                await firing.fire_digest_job(job_id)
            except Exception as exc:  # one bad job must not stop the rest
                _log.warning(
                    "tg.command.digest_now.job_failed",
                    job_id=job_id,
                    error_class=type(exc).__name__,
                )
        _log.info("tg.command.digest_now.done", owner_telegram_id=owner, n_jobs=len(ids))
        # END_BLOCK_HANDLER_DIGEST_NOW

    @router.message(Command("expand"))
    async def _on_expand(message: Message) -> None:
        # START_BLOCK_HANDLER_EXPAND
        # aisw-269: on-demand section detail — re-run Claude scoped to one section.
        if message.from_user is None or message.chat is None or message.text is None:
            _log.debug("tg.handlers.expand.skip_missing_fields")
            return
        owner = message.from_user.id
        parts = message.text.split(maxsplit=1)
        section = parts[1].strip().lower() if len(parts) > 1 else ""
        if section not in EXPAND_SECTION_KEYS:
            _log.info("tg.command.expand.bad_section", owner_telegram_id=owner, got=section)
            await message.answer(_EXPAND_USAGE_RU)
            return
        try:
            text = await firing.run_section_expand(owner, section)
        except firing.DigestNotInitialisedError:
            _log.warning("tg.command.expand.unavailable", owner_telegram_id=owner)
            await message.answer(_DIGEST_UNAVAILABLE_RU)
            return
        except Exception as exc:  # defensive: never bubble to the dispatcher
            _log.warning(
                "tg.command.expand.failed",
                owner_telegram_id=owner,
                section=section,
                error_class=type(exc).__name__,
            )
            await message.answer(_GENERIC_ERR_RU)
            return
        if text is None:
            await message.answer(_EXPAND_NO_WIKI_RU)
            return
        body = text.strip() or _EXPAND_EMPTY_RU
        await message.answer(body)
        _log.info(
            "tg.command.expand.delivered",
            owner_telegram_id=owner,
            section=section,
            chars=len(body),
        )
        # END_BLOCK_HANDLER_EXPAND

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
        audio_mime = message.audio.mime_type or "audio/mpeg"
        await pipeline.on_voice(
            telegram_id=message.from_user.id,
            chat_id=message.chat.id,
            update_id=message.message_id,
            audio_bytes=data,
            caption=message.caption,
            ext=_audio_ext_for(message.audio.mime_type, message.audio.file_name),
            mime=audio_mime,
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
            ext="mp4",
            mime="video/mp4",
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
