# FILE: src/ai_steward_wiki/tg/stream_edit.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: D-026 streaming edits — edit one TG message with throttle 1.5s OR
#            Δ≥50 chars (whichever first), chain-split at 4000 chars,
#            HTML-balanced segments, final-flush guarantee on stream end /
#            exception / cancel.
#   SCOPE: StreamEditor (feed/finalize/aclose), Clock Protocol seam, default
#          MonotonicClock.
#   DEPENDS: asyncio, ai_steward_wiki.tg.bot.TgSender,
#            ai_steward_wiki.tg.output.HtmlBalancer, structlog
#   LINKS: D-026, M-TG-TEXT
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   Clock - Protocol with monotonic() float
#   MonotonicClock - default real-time clock
#   StreamEditor - throttle + chain-split + final-flush wrapper
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 10: D-026 streaming editor
# END_CHANGE_SUMMARY

from __future__ import annotations

import time
from typing import Protocol

import structlog

from ai_steward_wiki.tg.bot import TgSender
from ai_steward_wiki.tg.output import HtmlBalancer

__all__ = [
    "Clock",
    "MonotonicClock",
    "StreamEditor",
]

_log = structlog.get_logger("tg.stream_edit")


class Clock(Protocol):
    def monotonic(self) -> float: ...


class MonotonicClock:
    def monotonic(self) -> float:
        return time.monotonic()


class StreamEditor:
    """Throttled single-message editor with chain-split + final-flush.

    Design (D-026):
      - ``feed(chunk)`` appends to buffer; if either throttle condition fires
        (Δ chars ≥ delta_chars OR elapsed ≥ tick_s since last edit), edit.
      - On approaching ``chain_threshold`` chars in current message, finalize
        the current message (balanced tags + footer), send a new placeholder,
        switch edit target. Footer carries `(i)` index.
      - ``finalize()`` is idempotent: emits canonical balanced final state with
        a `(N/N)` footer; safe to call from `finally`.

    NOT a background-task; the synchronous "feed + decide" approach gives
    deterministic test behaviour without timers. The tick condition is checked
    on each `feed` against an injected ``Clock`` — production uses real time;
    tests use a fake.
    """

    PLACEHOLDER_PREFIX = "\u23f3 "  # ⏳
    CONTINUE_FOOTER_FMT = "({i}) \u2026 \u23f3 продолжаю в следующем\u2026"
    FINAL_FOOTER_FMT = "({i}/{n})"

    def __init__(
        self,
        *,
        sender: TgSender,
        chat_id: int,
        first_message_id: int,
        tick_s: float = 1.5,
        delta_chars: int = 50,
        chain_threshold: int = 4000,
        clock: Clock | None = None,
    ) -> None:
        self._sender = sender
        self._chat_id = chat_id
        self._current_msg_id = first_message_id
        self._tick_s = tick_s
        self._delta_chars = delta_chars
        self._chain_threshold = chain_threshold
        self._clock = clock or MonotonicClock()
        self._buffer = ""
        self._last_edited_len = 0
        self._last_edit_t = self._clock.monotonic()
        self._segment_idx = 1  # current message index in chain (1-based)
        self._total_segments = 1
        self._finalized = False

    @property
    def buffer(self) -> str:
        return self._buffer

    @property
    def segment_idx(self) -> int:
        return self._segment_idx

    @property
    def current_message_id(self) -> int:
        return self._current_msg_id

    def _should_edit(self) -> bool:
        new_chars = len(self._buffer) - self._last_edited_len
        if new_chars <= 0:
            return False
        if new_chars >= self._delta_chars:
            return True
        return (self._clock.monotonic() - self._last_edit_t) >= self._tick_s

    @staticmethod
    def _balance(text: str) -> str:
        balancer = HtmlBalancer()
        closed, _ = balancer.balance_segment(text)
        return closed

    async def feed(self, chunk: str) -> None:
        """Append `chunk` to buffer; emit edits / chain-splits per policy."""
        if self._finalized:
            raise RuntimeError("StreamEditor already finalized")
        if not chunk:
            return
        self._buffer += chunk

        # Chain-split first: keep current msg below threshold.
        while len(self._buffer) >= self._chain_threshold:
            head = self._buffer[: self._chain_threshold]
            tail = self._buffer[self._chain_threshold :]
            finalized_head = (
                self._balance(head) + "\n" + self.CONTINUE_FOOTER_FMT.format(i=self._segment_idx)
            )
            await self._sender.edit_message_text(
                self._chat_id, self._current_msg_id, finalized_head
            )
            placeholder = f"{self.PLACEHOLDER_PREFIX}({self._segment_idx + 1}) продолжение\u2026"
            new_msg = await self._sender.send_message(self._chat_id, placeholder)
            self._current_msg_id = new_msg.message_id
            self._segment_idx += 1
            self._total_segments = self._segment_idx
            self._buffer = tail
            self._last_edited_len = 0
            self._last_edit_t = self._clock.monotonic()
            _log.info(
                "tg.stream.chain_split",
                chat_id=self._chat_id,
                new_message_id=self._current_msg_id,
                segment_idx=self._segment_idx,
            )

        # Throttled in-place edit.
        if self._should_edit():
            await self._sender.edit_message_text(
                self._chat_id, self._current_msg_id, self._balance(self._buffer)
            )
            self._last_edited_len = len(self._buffer)
            self._last_edit_t = self._clock.monotonic()
            _log.debug(
                "tg.stream.tick",
                chat_id=self._chat_id,
                message_id=self._current_msg_id,
                size=len(self._buffer),
            )

    async def finalize(self) -> None:
        """Emit canonical balanced final state with (i/N) footer.

        Idempotent: subsequent calls are no-ops. Swallows non-fatal sender
        errors (logs `tg.stream.final_flush_failed`); the caller has already
        persisted full text via deliver_output, so TG delivery is best-effort.
        """
        if self._finalized:
            return
        self._finalized = True
        body = self._balance(self._buffer)
        footer = self.FINAL_FOOTER_FMT.format(i=self._segment_idx, n=self._total_segments)
        final_text = f"{body}\n{footer}" if self._total_segments > 1 else body
        try:
            await self._sender.edit_message_text(self._chat_id, self._current_msg_id, final_text)
            _log.info(
                "tg.stream.finalized",
                chat_id=self._chat_id,
                message_id=self._current_msg_id,
                segment_idx=self._segment_idx,
                total_segments=self._total_segments,
                size=len(self._buffer),
            )
        except Exception as exc:
            _log.warning(
                "tg.stream.final_flush_failed",
                chat_id=self._chat_id,
                message_id=self._current_msg_id,
                error=type(exc).__name__,
            )
