# FILE: src/ai_steward_wiki/tg/middleware_correlation.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: aiogram outer-middleware that binds correlation_id + identity fields into structlog contextvars for one TG Update.
#   SCOPE: CorrelationMiddleware.__call__(handler, event, data) — generate uuid4, bind dual sources (structlog.contextvars + legacy ContextVar), log tg.update.received, clear on exit (success or exception).
#   DEPENDS: aiogram.BaseMiddleware, ai_steward_wiki.logging_setup (bind_correlation_id, reset_correlation_id, get_logger), ai_steward_wiki.logging_events.TG_UPDATE_RECEIVED, structlog.contextvars
#   LINKS: M-TG-MIDDLEWARE, M-FOUNDATION-LOGGING
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   CorrelationMiddleware - outer-middleware owning per-Update correlation_id + identity field binding
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - initial CorrelationMiddleware (uuid4 + dual binding + tg.update.received)
# END_CHANGE_SUMMARY
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware

from ai_steward_wiki.logging_events import TG_UPDATE_RECEIVED
from ai_steward_wiki.logging_setup import (
    bind_correlation_id,
    get_logger,
    reset_correlation_id,
)

__all__ = ["CorrelationMiddleware"]

_log = get_logger(__name__)


def _safe_telegram_id(event: Any) -> int | None:
    msg = getattr(event, "message", None)
    if msg is None:
        return None
    user = getattr(msg, "from_user", None)
    return getattr(user, "id", None) if user is not None else None


def _safe_chat_id(event: Any) -> int | None:
    msg = getattr(event, "message", None)
    if msg is None:
        return None
    chat = getattr(msg, "chat", None)
    return getattr(chat, "id", None) if chat is not None else None


class CorrelationMiddleware(BaseMiddleware):
    """Outer-middleware: bind correlation_id + identity per Update.

    Registered BEFORE other outer-middlewares (e.g. AllowlistMiddleware) so
    that downstream events inherit the binding via ``merge_contextvars``.
    """

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        cid = str(uuid.uuid4())
        token = bind_correlation_id(cid)
        telegram_id = _safe_telegram_id(event)
        chat_id = _safe_chat_id(event)
        update_id = getattr(event, "update_id", None)
        try:
            with structlog.contextvars.bound_contextvars(
                correlation_id=cid,
                telegram_id=telegram_id,
                chat_id=chat_id,
                update_id=update_id,
            ):
                _log.info(TG_UPDATE_RECEIVED)
                return await handler(event, data)
        finally:
            reset_correlation_id(token)
