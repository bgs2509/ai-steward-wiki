"""Shared fakes for tests/unit/tg."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FakeMessage:
    message_id: int


@dataclass
class FakeSender:
    """In-memory recorder for TgSender Protocol."""

    _next_id: int = 1000
    sends: list[dict[str, Any]] = field(default_factory=list)
    edits: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    fail_edits: int = 0

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        reply_markup: object | None = None,
    ) -> FakeMessage:
        self._next_id += 1
        self.sends.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
                "message_id": self._next_id,
            }
        )
        return FakeMessage(message_id=self._next_id)

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        reply_markup: object | None = None,
    ) -> None:
        if self.fail_edits > 0:
            self.fail_edits -= 1
            raise RuntimeError("fake edit failure")
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )

    async def send_document(
        self,
        chat_id: int,
        *,
        path: Path,
        caption: str | None = None,
    ) -> FakeMessage:
        self._next_id += 1
        self.documents.append(
            {"chat_id": chat_id, "path": str(path), "caption": caption, "message_id": self._next_id}
        )
        return FakeMessage(message_id=self._next_id)

    def last_reply_markup_pending_id(self) -> int:
        """Extract <pending_id> from the last sent message's inline keyboard
        callback_data (format 'confirm:<id>:...' or 'jobpick:<id>:...')."""
        markup = self.sends[-1]["reply_markup"]
        first_button = markup.inline_keyboard[0][0]
        return int(first_button.callback_data.split(":")[1])


@dataclass
class FakeClock:
    """Monotonic-style fake clock — tests `advance()` it manually."""

    now: float = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt
