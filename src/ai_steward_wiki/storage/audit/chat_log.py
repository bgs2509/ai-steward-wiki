# FILE: src/ai_steward_wiki/storage/audit/chat_log.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Persist (in/out) conversation turns into audit.chat_log and read the
#            recent last-N / 24h window back for the Stage-0 classifier + Stage-1a router (D-033).
#   SCOPE: ChatTurn model, CHAT_LOG_DENYLIST, redact_chat_text, ChatLogWriter
#          (write_in / write_out / read_recent_window).
#   DEPENDS: sqlalchemy.async, ai_steward_wiki.storage.audit.models.ChatLog
#   LINKS: D-033, M-STORAGE-AUDIT, aisw-kml
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ChatTurn - frozen view of one recent chat_log row (direction, text, ts)
#   CHAT_LOG_DENYLIST - D-033 hardcoded write-time secret denylist (sk-ant-/Bearer /password=)
#   CHAT_LOG_WINDOW_LIMIT - D-033 last-N turns (20) read back for context
#   CHAT_LOG_WINDOW_HOURS - D-033 recent-window age bound (24h)
#   redact_chat_text - drop denylisted secret tokens before persisting plaintext
#   ChatLogWriter - thin audit.db adapter: write_in / write_out / read_recent_window
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - aisw-kml: wire D-033 chat_log read/write (was scaffold-only).
# END_CHANGE_SUMMARY

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.storage.audit.models import ChatLog

__all__ = [
    "CHAT_LOG_DENYLIST",
    "CHAT_LOG_WINDOW_HOURS",
    "CHAT_LOG_WINDOW_LIMIT",
    "ChatLogWriter",
    "ChatTurn",
    "redact_chat_text",
]

# D-033 §"PII / redaction" п.3 — minimal hardcoded denylist applied at write time
# (full PII policy is Q-E-33, separate wave). Patterns redact the token PLUS its
# trailing value so a leaked secret never lands in plaintext.
CHAT_LOG_DENYLIST: tuple[str, ...] = ("sk-ant-", "Bearer ", "password=")

# D-033 §"Access pattern" п.1 — last-20 turns within a 24h window.
CHAT_LOG_WINDOW_LIMIT = 20
CHAT_LOG_WINDOW_HOURS = 24

_REDACTED = "[redacted]"
# token + the contiguous non-space run that follows it (the secret value).
_DENYLIST_RE = re.compile(
    "|".join(re.escape(p) + r"\S*" for p in CHAT_LOG_DENYLIST),
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """One recent chat_log row, oldest-to-newest as fed to the classifier/router."""

    direction: str  # "in" | "out"
    text: str
    created_at_utc: datetime


# START_CONTRACT: redact_chat_text
#   PURPOSE: Strip D-033-denylisted secret tokens (and their values) from text before persist.
#   INPUTS: { text: str - the raw inbound/outbound message text }
#   OUTPUTS: { str - text with each denylisted secret replaced by [redacted] }
#   SIDE_EFFECTS: none (pure)
#   LINKS: D-033 §"PII / redaction" п.3
# END_CONTRACT: redact_chat_text
def redact_chat_text(text: str) -> str:
    if not text:
        return text
    return _DENYLIST_RE.sub(_REDACTED, text)


def _utcnow_naive() -> datetime:
    # D-006 invariant: all DB datetimes are UTC, stored tz-naive (matches the
    # rest of audit.db's created_at_utc columns).
    return datetime.now(UTC).replace(tzinfo=None)


class ChatLogWriter:
    """audit.db adapter for the D-033 conversation buffer.

    The dispatcher stays stateless — it owns no buffer; it writes each turn and
    reads back the recent window per call.
    """

    def __init__(self, audit_maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = audit_maker

    # START_CONTRACT: write_in
    #   PURPOSE: Persist one inbound user turn (after STT/OCR), denylist-redacted.
    #   INPUTS: { telegram_id, chat_id, text, kind='text', now: datetime|None }
    #   OUTPUTS: { None }
    #   SIDE_EFFECTS: INSERT one chat_log(direction='in') row.
    #   LINKS: D-033 §"Что пишется" п.1
    # END_CONTRACT: write_in
    async def write_in(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        text: str,
        kind: str = "text",
        now: datetime | None = None,
    ) -> None:
        await self._write(
            telegram_id=telegram_id,
            chat_id=chat_id,
            direction="in",
            text=text,
            kind=kind,
            now=now,
        )

    # START_CONTRACT: write_out
    #   PURPOSE: Persist one final outbound bot reply (no streaming frames), denylist-redacted.
    #   INPUTS: { telegram_id, chat_id, text, kind='text', now: datetime|None }
    #   OUTPUTS: { None }
    #   SIDE_EFFECTS: INSERT one chat_log(direction='out') row.
    #   LINKS: D-033 §"Что пишется" п.2
    # END_CONTRACT: write_out
    async def write_out(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        text: str,
        kind: str = "text",
        now: datetime | None = None,
    ) -> None:
        await self._write(
            telegram_id=telegram_id,
            chat_id=chat_id,
            direction="out",
            text=text,
            kind=kind,
            now=now,
        )

    async def _write(
        self,
        *,
        telegram_id: int,
        chat_id: int,
        direction: str,
        text: str,
        kind: str,
        now: datetime | None,
    ) -> None:
        redacted = redact_chat_text(text)
        async with self._maker() as session, session.begin():
            session.add(
                ChatLog(
                    telegram_id=telegram_id,
                    chat_id=chat_id,
                    direction=direction,
                    kind=kind,
                    text=redacted,
                    created_at_utc=now or _utcnow_naive(),
                )
            )

    # START_CONTRACT: read_recent_window
    #   PURPOSE: Read the last-N (24h) turns for a user, oldest-first, for classifier/router context.
    #   INPUTS: { telegram_id, limit=CHAT_LOG_WINDOW_LIMIT, window_hours=24, now: datetime|None }
    #   OUTPUTS: { list[ChatTurn] - chronological (oldest-first), at most `limit` rows }
    #   SIDE_EFFECTS: one read-only DB transaction.
    #   LINKS: D-033 §"Access pattern" п.1
    # END_CONTRACT: read_recent_window
    async def read_recent_window(
        self,
        telegram_id: int,
        *,
        limit: int = CHAT_LOG_WINDOW_LIMIT,
        window_hours: int = CHAT_LOG_WINDOW_HOURS,
        now: datetime | None = None,
    ) -> list[ChatTurn]:
        cutoff = (now or _utcnow_naive()) - timedelta(hours=window_hours)
        async with self._maker() as session:
            # SELECT … ORDER BY ts DESC LIMIT N (newest N within the window), then
            # reverse to chronological order for prompt rendering.
            rows = (
                await session.execute(
                    select(ChatLog.direction, ChatLog.text, ChatLog.created_at_utc)
                    .where(
                        ChatLog.telegram_id == telegram_id,
                        ChatLog.created_at_utc > cutoff,
                    )
                    .order_by(ChatLog.created_at_utc.desc(), ChatLog.id.desc())
                    .limit(limit)
                )
            ).all()
        turns = [ChatTurn(direction=r[0], text=r[1] or "", created_at_utc=r[2]) for r in rows]
        turns.reverse()
        return turns
