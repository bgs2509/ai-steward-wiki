# FILE: src/ai_steward_wiki/tg/bot.py
# VERSION: 0.1.1
# START_MODULE_CONTRACT
#   PURPOSE: Build aiogram 3 Bot + Dispatcher with correlation + allowlist middlewares wired as outer-middlewares. Production entry point for TG runtime.
#   SCOPE: build_bot(token), build_dispatcher(allowlist), TgSender Protocol
#          surface used by output/stream_edit, AiogramSender adapter.
#   DEPENDS: aiogram (Bot, Dispatcher, types), ai_steward_wiki.auth.allowlist,
#            ai_steward_wiki.tg.middleware_auth,
#            ai_steward_wiki.tg.middleware_correlation
#   LINKS: D-031, D-042, M-TG-TEXT, M-TG-MIDDLEWARE, M-FOUNDATION-LOGGING
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
#   register_bot_commands - publish native TG ≡ menu (set_my_commands)
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.1 - aisw-er6: register CorrelationMiddleware BEFORE AllowlistMiddleware so downstream events inherit correlation_id + identity bindings.
#   PREVIOUS:    v0.1.0 - aisw-s5i: register_bot_commands publishes 6 commands
#                (/start, /help, /manual, /digest_now, /expand, /digest_sections)
#                via bot.set_my_commands at runtime startup.
#   PREVIOUS:    v0.0.1 - chunk 10: initial dispatcher factory + sender adapter
# END_CHANGE_SUMMARY

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from ai_steward_wiki.auth.allowlist import Allowlist
from ai_steward_wiki.tg.middleware_auth import AllowlistMiddleware
from ai_steward_wiki.tg.middleware_correlation import CorrelationMiddleware

__all__ = [
    "AiogramSender",
    "SentMessage",
    "TgSender",
    "build_bot",
    "build_dispatcher",
    "register_bot_commands",
]

import structlog

_log = structlog.get_logger("tg.bot")

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
    templates_dir: Path | None = None,
    on_start_unknown: object | None = None,
    get_user_tz: object | None = None,
    aggregator: object | None = None,
) -> Dispatcher:
    """Build Dispatcher with allowlist middleware and (optional) handlers router.

    ``pipeline`` is a :class:`ai_steward_wiki.tg.pipeline.MessagePipeline`
    (typed loosely with ``object | None`` to avoid an import cycle).
    ``templates_dir`` + ``on_start_unknown`` (aisw-s5i) are forwarded into
    ``build_router`` so /start, /help, /manual handlers can find their
    templates and (for unknown ids) record a pending_users row.
    """
    from collections.abc import Awaitable, Callable

    from aiogram import Dispatcher

    dp = Dispatcher()
    # CorrelationMiddleware MUST be registered first so downstream events
    # (including AllowlistMiddleware deny events) inherit correlation_id +
    # identity bindings via merge_contextvars.
    dp.update.outer_middleware(CorrelationMiddleware())
    mw = AllowlistMiddleware(allowlist)
    # Outer-middleware so denial happens before any router.
    dp.update.outer_middleware(mw)

    if pipeline is not None:
        from ai_steward_wiki.tg.aggregator import InboxAggregator
        from ai_steward_wiki.tg.handlers import build_router
        from ai_steward_wiki.tg.pipeline import MessagePipeline

        dp.include_router(
            build_router(
                cast("MessagePipeline", pipeline),
                templates_dir=templates_dir,
                on_start_unknown=cast("Callable[..., Awaitable[None]] | None", on_start_unknown),
                get_user_tz=cast("Callable[[int], Awaitable[str]] | None", get_user_tz),
                aggregator=cast("InboxAggregator | None", aggregator),
            )
        )
    return dp


# START_CONTRACT: register_bot_commands
#   PURPOSE: Publish the bot's command list so Telegram clients show the
#            native `≡` menu next to the message input (aisw-s5i Phase D).
#   INPUTS: { bot: aiogram.Bot - the running Bot instance }
#   OUTPUTS: { None }
#   SIDE_EFFECTS: bot.set_my_commands call (Telegram API write).
#   LINKS: D-032 (ru-only), M-TG-HANDLERS (the 7 commands incl. /cron_add)
# END_CONTRACT: register_bot_commands
async def register_bot_commands(bot: Bot) -> None:
    # START_BLOCK_REGISTER_BOT_COMMANDS
    from aiogram.types import BotCommand

    commands = [
        BotCommand(command="start", description="Знакомство и приветствие"),
        BotCommand(command="help", description="Что умеет бот и список команд"),
        BotCommand(command="manual", description="Расширенные сценарии и примеры"),
        BotCommand(command="digest_now", description="Сделать сводку сейчас"),
        BotCommand(command="expand", description="Развернуть раздел сводки"),
        BotCommand(command="digest_sections", description="Настроить разделы сводки"),
        BotCommand(command="cron_add", description="Повторяющийся запуск по расписанию"),
    ]
    _log.info("runtime.bot.commands.registered", n_commands=len(commands))
    await bot.set_my_commands(commands)
    # END_BLOCK_REGISTER_BOT_COMMANDS
