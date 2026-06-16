# FILE: src/ai_steward_wiki/tg/aggregator.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Debounce-aggregate a burst of consecutive text messages from one chat
#            into a single classify/route/ingest call (fixes Telegram splitting a
#            long paste into multiple messages, each routed to a different WIKI).
#   SCOPE: InboxAggregator (per-chat buffer + 3s debounce + "Думаю…" loader life-
#          cycle), LoaderControl Protocol + Fake, ProcessText callback type.
#          TEXT-ONLY (voice/photo aggregation deferred to aisw-90t).
#   DEPENDS: asyncio, structlog
#   LINKS: M-TG-PIPELINE, aisw-378, D-041
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   LoaderControl - Protocol; async post(chat_id)->msg_id|None, delete(chat_id,msg_id)
#   ProcessText - Callable[[telegram_id, chat_id, update_id, text], Awaitable[None]]
#   InboxAggregator - submit(...) buffers + debounces + flushes one combined on_text
#   FakeLoaderControl - test double recording post/delete calls
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-378: per-chat debounce aggregation + loader lifecycle.
# END_CHANGE_SUMMARY

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import structlog

_log = structlog.get_logger("tg.aggregator")

__all__ = [
    "FakeLoaderControl",
    "InboxAggregator",
    "LoaderControl",
    "ProcessText",
]


@runtime_checkable
class LoaderControl(Protocol):
    """Posts / removes the transient "Думаю…" loader message."""

    async def post(self, chat_id: int) -> int | None: ...
    async def delete(self, chat_id: int, message_id: int) -> None: ...


class ProcessText(Protocol):
    """The downstream sink — the existing pipeline.on_text."""

    async def __call__(
        self, *, telegram_id: int, chat_id: int, update_id: int, text: str
    ) -> None: ...


@dataclass
class _ChatBuf:
    telegram_id: int
    items: list[tuple[int, str]] = field(default_factory=list)  # (update_id, text)
    timer: asyncio.Task[None] | None = None
    loader_msg_id: int | None = None
    epoch: int = 0  # bumped per submit; the flush task checks it to avoid stale firing


class InboxAggregator:
    """Collect a burst of text messages per chat, then flush them as one input.

    Each `submit` (re)starts a debounce window; when the window elapses with no new
    message, the buffered texts are concatenated in message-id order and handed to
    `process` (pipeline.on_text) ONCE. A "Думаю…" loader is reposted on every message
    so it stays the latest chat message, and removed just before processing.
    """

    def __init__(
        self,
        *,
        process: ProcessText,
        loader: LoaderControl,
        delay_s: float = 3.0,
    ) -> None:
        self._process = process
        self._loader = loader
        self._delay_s = delay_s
        self._chats: dict[int, _ChatBuf] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self, chat_id: int) -> asyncio.Lock:
        lock = self._locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[chat_id] = lock
        return lock

    async def submit(self, *, telegram_id: int, chat_id: int, update_id: int, text: str) -> None:
        async with self._lock(chat_id):
            buf = self._chats.get(chat_id)
            if buf is None:
                buf = _ChatBuf(telegram_id=telegram_id)
                self._chats[chat_id] = buf
            buf.telegram_id = telegram_id
            buf.items.append((update_id, text))
            buf.epoch += 1
            epoch = buf.epoch
            if buf.timer is not None:
                buf.timer.cancel()
            # Repost the loader so it sits BELOW the user's newest message
            # (editMessageText would leave it stranded above the new message).
            if buf.loader_msg_id is not None:
                await self._safe_delete(chat_id, buf.loader_msg_id)
            buf.loader_msg_id = await self._safe_post(chat_id)
            buf.timer = asyncio.create_task(self._flush_after(chat_id, epoch))
            _log.info(
                "tg.aggregator.buffered",
                chat_id=chat_id,
                telegram_id=telegram_id,
                buffered=len(buf.items),
                epoch=epoch,
            )

    async def _flush_after(self, chat_id: int, epoch: int) -> None:
        try:
            await asyncio.sleep(self._delay_s)
        except asyncio.CancelledError:
            return
        await self._flush(chat_id, epoch)

    async def _flush(self, chat_id: int, epoch: int) -> None:
        async with self._lock(chat_id):
            buf = self._chats.get(chat_id)
            # Superseded by a newer submit (or already flushed) → ignore.
            if buf is None or buf.epoch != epoch or not buf.items:
                return
            items = sorted(buf.items, key=lambda it: it[0])
            loader_id = buf.loader_msg_id
            telegram_id = buf.telegram_id
            del self._chats[chat_id]

        combined = "\n\n".join(text for _, text in items)
        first_update_id = items[0][0]
        _log.info(
            "tg.aggregator.flush",
            chat_id=chat_id,
            telegram_id=telegram_id,
            n_parts=len(items),
            chars=len(combined),
        )
        # Keep the "Думаю…" loader up THROUGH processing (classify+route can take
        # seconds), then remove it once the real reply/confirm card has landed.
        try:
            await self._process(
                telegram_id=telegram_id,
                chat_id=chat_id,
                update_id=first_update_id,
                text=combined,
            )
        finally:
            if loader_id is not None:
                await self._safe_delete(chat_id, loader_id)

    async def _safe_post(self, chat_id: int) -> int | None:
        try:
            return await self._loader.post(chat_id)
        except Exception as exc:  # loader is cosmetic — never block aggregation
            _log.warning(
                "tg.aggregator.loader_post_failed", chat_id=chat_id, error=type(exc).__name__
            )
            return None

    async def _safe_delete(self, chat_id: int, message_id: int) -> None:
        try:
            await self._loader.delete(chat_id, message_id)
        except Exception as exc:
            _log.warning(
                "tg.aggregator.loader_delete_failed", chat_id=chat_id, error=type(exc).__name__
            )


@dataclass
class FakeLoaderControl:
    """Test double: hands out incrementing message ids, records post/delete calls."""

    posted: list[int] = field(default_factory=list)
    deleted: list[int] = field(default_factory=list)
    _next_id: int = 1000

    async def post(self, chat_id: int) -> int | None:
        self._next_id += 1
        self.posted.append(self._next_id)
        return self._next_id

    async def delete(self, chat_id: int, message_id: int) -> None:
        self.deleted.append(message_id)
