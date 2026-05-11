# FILE: src/ai_steward_wiki/tg/confirm.py
# VERSION: 0.0.1
# START_MODULE_CONTRACT
#   PURPOSE: Graduated 3-tier confirmation flow (D-023) — auto / implicit /
#            explicit. Explicit-level draft is persisted in
#            sessions.pending_confirms with 10-minute TTL and resolved
#            transactionally (race-safe vs expiry).
#   SCOPE: ConfirmLevel, ConfirmAction, ConfirmRecord, PendingConfirmDraft,
#          ConfirmationService (auto_ack, implicit_ack, request_explicit,
#          resolve, expire_due, get_pending), payload_hash helper,
#          build_explicit_keyboard.
#   DEPENDS: aiogram.types, SQLAlchemy.async, ai_steward_wiki.tg.bot.TgSender,
#            ai_steward_wiki.storage.sessions.models.PendingConfirm, structlog
#   LINKS: D-023, M-TG-TEXT
#   ROLE: RUNTIME
#   MAP_MODE: EXPORTS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   ConfirmLevel - Literal[auto|implicit|explicit]
#   ConfirmAction - Literal[confirm|correct|cancel]
#   ConfirmStatus - Literal[pending|confirmed|corrected|cancelled|expired]
#   DEFAULT_TTL_SEC - 10-minute default TTL (D-023)
#   BTN_CONFIRM - inline-button label for confirm
#   BTN_CORRECT - inline-button label for correct
#   BTN_CANCEL - inline-button label for cancel
#   PendingConfirmDraft - frozen Pydantic-like dataclass holding draft state
#   ConfirmRecord - returned from request_explicit (id, payload_hash, recap msg id)
#   compute_payload_hash - canonical-json sha256 of draft dict
#   build_explicit_keyboard - 3-button InlineKeyboardMarkup
#   ConfirmationService - main facade
# END_MODULE_MAP
#
# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.0.1 - chunk 10: D-023 graduated confirmation flow
# END_CHANGE_SUMMARY

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_steward_wiki.storage.sessions.models import PendingConfirm
from ai_steward_wiki.tg.bot import TgSender

if TYPE_CHECKING:
    pass

_log = structlog.get_logger("tg.confirm")

ConfirmLevel = Literal["auto", "implicit", "explicit"]
ConfirmAction = Literal["confirm", "correct", "cancel"]
ConfirmStatus = Literal["pending", "confirmed", "corrected", "cancelled", "expired"]

DEFAULT_TTL_SEC = 10 * 60  # D-023 — 10 minutes
BTN_CONFIRM = "\u2705 Подтвердить"
BTN_CORRECT = "\u270f\ufe0f Изменить"
BTN_CANCEL = "\u274c Отмена"


def _utcnow_naive() -> datetime:
    """Return current UTC datetime as naive (DB convention — store UTC as naive)."""
    return datetime.now(UTC).replace(tzinfo=None)


def compute_payload_hash(draft: dict[str, Any]) -> str:
    """Canonical JSON sha256 of the draft (key-sorted, compact)."""
    blob = json.dumps(draft, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_explicit_keyboard(pending_id: int) -> Any:
    """Return aiogram InlineKeyboardMarkup with 3 graduated-confirm buttons.

    Callback data: ``confirm:<pending_id>:<action>``.
    """
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_CONFIRM, callback_data=f"confirm:{pending_id}:confirm")],
            [InlineKeyboardButton(text=BTN_CORRECT, callback_data=f"confirm:{pending_id}:correct")],
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data=f"confirm:{pending_id}:cancel")],
        ]
    )


@dataclass(frozen=True)
class PendingConfirmDraft:
    telegram_id: int
    chat_id: int
    category: str
    draft: dict[str, Any]
    recap_text: str
    ttl_sec: int = DEFAULT_TTL_SEC


@dataclass(frozen=True)
class ConfirmRecord:
    pending_id: int
    payload_hash: str
    recap_message_id: int
    expires_at_utc: datetime


class ConfirmationService:
    """Graduated 3-tier confirmation facade (D-023)."""

    def __init__(
        self,
        sender: TgSender,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sender = sender
        self._sessions = session_maker

    async def auto_ack(self, chat_id: int, line: str) -> int:
        """Level=auto — send a single Russian ack line. Returns sent message_id."""
        msg = await self._sender.send_message(chat_id, line)
        _log.info("tg.confirm.auto_ack", chat_id=chat_id, message_id=msg.message_id)
        return msg.message_id

    async def implicit_ack(
        self,
        chat_id: int,
        recap: str,
        *,
        keyboard: Any | None = None,
    ) -> int:
        """Level=implicit — recap + optional keyboard; non-blocking."""
        msg = await self._sender.send_message(chat_id, recap, reply_markup=keyboard)
        _log.info("tg.confirm.implicit_ack", chat_id=chat_id, message_id=msg.message_id)
        return msg.message_id

    async def request_explicit(self, draft: PendingConfirmDraft) -> ConfirmRecord:
        """Level=explicit — persist pending row + send recap + keyboard.

        Idempotent on (telegram_id, payload_hash) in 'pending' status: returns
        the existing record without sending a new recap.
        """
        payload_hash = compute_payload_hash(draft.draft)
        now = _utcnow_naive()
        expires = now + timedelta(seconds=draft.ttl_sec)

        # Check duplicate first.
        async with self._sessions() as session:
            existing = await session.execute(
                select(PendingConfirm).where(
                    PendingConfirm.telegram_id == draft.telegram_id,
                    PendingConfirm.payload_hash == payload_hash,
                    PendingConfirm.status == "pending",
                )
            )
            row = existing.scalars().first()
            if row is not None:
                _log.info(
                    "tg.confirm.explicit.dup",
                    telegram_id=draft.telegram_id,
                    payload_hash=payload_hash,
                    pending_id=row.id,
                )
                return ConfirmRecord(
                    pending_id=row.id,
                    payload_hash=payload_hash,
                    recap_message_id=row.recap_message_id or 0,
                    expires_at_utc=row.expires_at_utc,
                )

        # New pending — insert first to obtain pending_id, then send.
        async with self._sessions() as session, session.begin():
            new_row = PendingConfirm(
                telegram_id=draft.telegram_id,
                payload_hash=payload_hash,
                expires_at_utc=expires,
                created_at_utc=now,
                status="pending",
                category=draft.category,
                chat_id=draft.chat_id,
                draft_json=json.dumps(draft.draft, ensure_ascii=False, sort_keys=True),
            )
            session.add(new_row)
            await session.flush()
            pending_id = new_row.id

        keyboard = build_explicit_keyboard(pending_id)
        sent = await self._sender.send_message(
            draft.chat_id, draft.recap_text, reply_markup=keyboard
        )

        async with self._sessions() as session, session.begin():
            await session.execute(
                update(PendingConfirm)
                .where(PendingConfirm.id == pending_id)
                .values(recap_message_id=sent.message_id)
            )

        _log.info(
            "tg.confirm.explicit.requested",
            telegram_id=draft.telegram_id,
            chat_id=draft.chat_id,
            pending_id=pending_id,
            payload_hash=payload_hash,
            category=draft.category,
            recap_message_id=sent.message_id,
        )
        return ConfirmRecord(
            pending_id=pending_id,
            payload_hash=payload_hash,
            recap_message_id=sent.message_id,
            expires_at_utc=expires,
        )

    async def resolve(
        self, telegram_id: int, pending_id: int, action: ConfirmAction
    ) -> ConfirmStatus | None:
        """Resolve a pending row. Race-safe: UPDATE … WHERE status='pending'.

        Returns the new status or None if the row was already resolved/expired.
        """
        _action_to_status: dict[ConfirmAction, ConfirmStatus] = {
            "confirm": "confirmed",
            "correct": "corrected",
            "cancel": "cancelled",
        }
        new_status: ConfirmStatus = _action_to_status[action]

        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(PendingConfirm)
                .where(
                    PendingConfirm.id == pending_id,
                    PendingConfirm.telegram_id == telegram_id,
                    PendingConfirm.status == "pending",
                )
                .values(status=new_status)
            )
            if result.rowcount == 0:
                _log.info(
                    "tg.confirm.resolve.noop",
                    telegram_id=telegram_id,
                    pending_id=pending_id,
                    action=action,
                )
                return None

        _log.info(
            "tg.confirm.resolved",
            telegram_id=telegram_id,
            pending_id=pending_id,
            action=action,
            status=new_status,
        )
        return new_status

    async def expire_due(self, now_utc: datetime | None = None) -> int:
        """Flip stale pending rows to 'expired'. Returns count flipped."""
        cutoff = (now_utc or _utcnow_naive()).replace(tzinfo=None)
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(PendingConfirm)
                .where(
                    PendingConfirm.status == "pending",
                    PendingConfirm.expires_at_utc <= cutoff,
                )
                .values(status="expired")
            )
            n = result.rowcount or 0
        if n:
            _log.info("tg.confirm.expired", n=n)
        return n

    async def get_pending(self, pending_id: int) -> PendingConfirm | None:
        """Read-only fetch by id (returns detached row)."""
        async with self._sessions() as session:
            row = await session.get(PendingConfirm, pending_id)
            if row is not None:
                session.expunge(row)
            return row
