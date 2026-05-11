# FILE: src/ai_steward_wiki/tg/bot.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Build aiogram 3 Bot + Dispatcher with allowlist middleware wired
#            as outer-middleware. Production entry point for TG runtime.
#   SCOPE: build_bot(token), build_dispatcher(allowlist), TgSender Protocol
#          surface used by output/stream_edit, AiogramSender adapter.
#   DEPENDS: aiogram (Bot, Dispatcher, types), ai_steward_wiki.auth.allowlist,
#            ai_steward_wiki.tg.middleware_auth
#   LINKS: D-031, D-042, M-TG-TEXT
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   SentMessage - Protocol exposing .message_id (covers aiogram Message)
#   TgSender - Protocol for send_message / edit_message_text / send_document
#   AiogramSender - thin adapter wrapping aiogram.Bot to satisfy TgSender
#   build_bot - construct aiogram.Bot with HTML default parse_mode
#   build_dispatcher - construct Dispatcher and register AllowlistMiddleware
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 10: initial dispatcher factory + sender adapter
# END_CHANGE_SUMMARY

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from ai_steward_wiki.auth.allowlist import Allowlist
from ai_steward_wiki.tg.middleware_auth import AllowlistMiddleware

__all__ = [
    "AiogramSender",
    "SentMessage",
    "TgSender",
    "build_bot",
    "build_dispatcher",
]

if TYPE_CHECKING:
    from aiogram import Bot, Dispatcher


@runtime_checkable
class SentMessage(Protocol):
    """Minimal contract of an aiogram.types.Message for our send/edit calls."""

    message_id: int


class TgSender(Protocol):
    """Narrow surface of aiogram Bot used by output/stream_edit layers."""

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        reply_markup: object | None = None,
    ) -> SentMessage: ...

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        reply_markup: object | None = None,
    ) -> None: ...

    async def send_document(
        self,
        chat_id: int,
        *,
        path: Path,
        caption: str | None = None,
    ) -> SentMessage: ...


class AiogramSender:
    """Adapter — bridges aiogram.Bot into the TgSender Protocol surface."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        reply_markup: object | None = None,
    ) -> SentMessage:
        msg = await self._bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,  # type: ignore[arg-type]
        )
        return msg  # type: ignore[return-value]

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        reply_markup: object | None = None,
    ) -> None:
        await self._bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,  # type: ignore[arg-type]
        )

    async def send_document(
        self,
        chat_id: int,
        *,
        path: Path,
        caption: str | None = None,
    ) -> SentMessage:
        from aiogram.types import FSInputFile

        msg = await self._bot.send_document(
            chat_id=chat_id,
            document=FSInputFile(path=str(path)),
            caption=caption,
        )
        return msg  # type: ignore[return-value]


def build_bot(token: str) -> Bot:
    """Construct an aiogram.Bot with HTML default parse_mode (D-024)."""
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dispatcher(
    allowlist: Allowlist,
    *,
    pipeline: object | None = None,
) -> Dispatcher:
    """Build Dispatcher with allowlist middleware and (optional) handlers router.

    ``pipeline`` is a :class:`ai_steward_wiki.tg.pipeline.MessagePipeline`
    (typed loosely with ``object | None`` to avoid an import cycle).
    """
    from aiogram import Dispatcher

    dp = Dispatcher()
    mw = AllowlistMiddleware(allowlist)
    # Outer-middleware so denial happens before any router.
    dp.update.outer_middleware(mw)

    if pipeline is not None:
        from ai_steward_wiki.tg.handlers import build_router
        from ai_steward_wiki.tg.pipeline import MessagePipeline

        dp.include_router(build_router(cast("MessagePipeline", pipeline)))
    return dp
