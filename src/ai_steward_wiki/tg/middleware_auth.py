# FILE: src/ai_steward_wiki/tg/middleware_auth.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: aiogram 3 outer-middleware enforcing in-memory allowlist (D-031).
#            Rejects updates from unknown telegram_id with a Russian one-liner
#            and logs `auth.deny`. Allowed updates carry the UserRecord into
#            handler data under key "user_record".
#   SCOPE: AllowlistMiddleware(allowlist).
#   DEPENDS: aiogram.BaseMiddleware, ai_steward_wiki.auth.allowlist.Allowlist,
#            structlog
#   LINKS: D-031, D-042, M-TG-TEXT, M-AUTH-USERS
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   AllowlistMiddleware - aiogram BaseMiddleware that gates updates by telegram_id
#   DENY_TEXT_RU - Russian one-liner sent on deny
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.2 - chunk 12: allow `/start` from unknown ids to reach onboarding handler
# END_CHANGE_SUMMARY

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from ai_steward_wiki.auth.allowlist import Allowlist

__all__ = [
    "DENY_TEXT_RU",
    "AllowlistMiddleware",
]

_log = structlog.get_logger("tg.auth")

DENY_TEXT_RU = "Извини, доступ к этому боту ограничен."

_START_COMMAND_PREFIX = "/start"


def _is_start_command(event: TelegramObject) -> bool:
    """True if the (Message inside an) event is `/start` (with optional payload)."""
    # Drill into Update→message if needed.
    msg = event if hasattr(event, "text") else getattr(event, "message", None)
    text = getattr(msg, "text", None)
    if not isinstance(text, str):
        return False
    head = text.strip().split(maxsplit=1)[0]
    # Strip @bot_name suffix if Telegram added it.
    head = head.split("@", maxsplit=1)[0]
    return head == _START_COMMAND_PREFIX


def _extract_telegram_user(event: TelegramObject) -> tuple[int | None, int | None]:
    """Return (telegram_id, chat_id) extracted from any aiogram event."""
    user = getattr(event, "from_user", None)
    chat = getattr(event, "chat", None)
    telegram_id = getattr(user, "id", None) if user else None
    chat_id = getattr(chat, "id", None) if chat else None
    # aiogram wraps message inside Update; introspect once.
    if telegram_id is None:
        inner = getattr(event, "message", None) or getattr(event, "callback_query", None)
        if inner is not None:
            return _extract_telegram_user(inner)
    return telegram_id, chat_id


class AllowlistMiddleware(BaseMiddleware):
    """Outer middleware gating non-allowed telegram_ids with a Russian one-liner.

    Side-effects:
      - on deny: tries to `event.answer(DENY_TEXT_RU)` if event supports it,
        emits `auth.deny` structlog event, returns None (stops chain);
      - on allow: injects `user_record` into handler data and proceeds.
    """

    def __init__(self, allowlist: Allowlist) -> None:
        self._allowlist = allowlist

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        telegram_id, chat_id = _extract_telegram_user(event)
        if telegram_id is None:
            # Service event (e.g. poll update) — pass through without gate.
            return await handler(event, data)

        record = self._allowlist.get_user(telegram_id)
        if record is None:
            # /start is the onboarding entry point — let it through so the
            # handler can record a pending_users row. All other commands
            # remain gated. (Chunk 12 / M-ONBOARD-ADMIN.)
            if _is_start_command(event):
                _log.info(
                    "auth.deny.bypass_start",
                    telegram_id=telegram_id,
                    chat_id=chat_id,
                )
                data["telegram_id"] = telegram_id
                data["user_record"] = None
                data["is_pending"] = True
                return await handler(event, data)

            _log.info(
                "auth.deny",
                telegram_id=telegram_id,
                chat_id=chat_id,
                event_type=type(event).__name__,
            )
            answer = getattr(event, "answer", None)
            if callable(answer):
                try:
                    await answer(DENY_TEXT_RU)
                except Exception:
                    _log.warning("auth.deny.reply_failed", telegram_id=telegram_id)
            return None

        data["user_record"] = record
        data["telegram_id"] = telegram_id
        data["is_pending"] = False
        return await handler(event, data)
