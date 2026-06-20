# FILE: src/ai_steward_wiki/tg/middleware_correlation.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: aiogram outer-middleware that binds correlation_id + identity fields into structlog contextvars for one TG Update, and emits the handler lifecycle exit event.
#   SCOPE: CorrelationMiddleware.__call__(handler, event, data) — generate uuid4, bind dual sources (structlog.contextvars + legacy ContextVar), log tg.update.received on entry, run handler, emit tg.update.handled (duration_ms, failed) + tg.update.handler_slow over threshold, clear on exit (success or exception).
#   DEPENDS: aiogram.BaseMiddleware, ai_steward_wiki.logging_setup (bind_correlation_id, reset_correlation_id, get_logger), ai_steward_wiki.logging_events (TG_UPDATE_RECEIVED, TG_UPDATE_HANDLED, TG_UPDATE_HANDLER_SLOW), structlog.contextvars, time
#   LINKS: M-TG-MIDDLEWARE, M-FOUNDATION-LOGGING, aisw-xbc
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   CorrelationMiddleware - outer-middleware owning per-Update correlation_id + identity binding + handler lifecycle exit event
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - aisw-xbc: emit tg.update.handled (duration_ms, failed) + tg.update.handler_slow over threshold ("received without handled" => stuck handler)
#   PREVIOUS:    v0.0.1 - initial CorrelationMiddleware (uuid4 + dual binding + tg.update.received)
# END_CHANGE_SUMMARY
from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware

from ai_steward_wiki.logging_events import (
    TG_UPDATE_HANDLED,
    TG_UPDATE_HANDLER_SLOW,
    TG_UPDATE_RECEIVED,
)
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

    def __init__(self, *, handler_slow_threshold_ms: int = 5000) -> None:
        self._handler_slow_threshold_ms = handler_slow_threshold_ms

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
                # START_BLOCK_HANDLER_LIFECYCLE (aisw-xbc)
                # tg.update.handled ALWAYS fires (success or failure) so that a
                # 'received without handled' for an update_id means a STUCK handler.
                t0 = time.perf_counter_ns()
                failed = False
                try:
                    return await handler(event, data)
                except BaseException:
                    failed = True
                    raise
                finally:
                    duration_ms = (time.perf_counter_ns() - t0) // 1_000_000
                    _log.info(TG_UPDATE_HANDLED, duration_ms=int(duration_ms), failed=failed)
                    if duration_ms > self._handler_slow_threshold_ms:
                        _log.warning(TG_UPDATE_HANDLER_SLOW, duration_ms=int(duration_ms))
                # END_BLOCK_HANDLER_LIFECYCLE
        finally:
            reset_correlation_id(token)
